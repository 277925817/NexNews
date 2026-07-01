import ast
import sqlite3
from pathlib import Path

import pytest

from backend.app.db import initialize_database, seed_default_sources

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DEFAULT_SOURCE_URLS = {
    "https://developers.openai.com/rss.xml",
    "https://openai.com/news/rss.xml",
    "https://dreyx.com/digest/rss",
    "https://news.ycombinator.com/rss",
    "https://hnrss.org/frontpage",
    "https://hnrss.org/newest",
    "https://hnrss.org/bestcomments",
}


def test_initialize_database_follows_function_line_limit():
    source = (ROOT / "backend/app/db.py").read_text()
    module = ast.parse(source)
    function_lengths = {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "initialize_database"
    }

    assert function_lengths == {"initialize_database": function_lengths["initialize_database"]}
    assert function_lengths["initialize_database"] <= 60, function_lengths


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def test_initialize_database_creates_mvp_core_tables_and_columns():
    conn = sqlite3.connect(":memory:")
    initialize_database(conn)

    assert table_names(conn) == {"source", "news_item", "processing_log"}
    assert column_names(conn, "source") >= {
        "id",
        "name",
        "rss_url",
        "is_enabled",
        "deleted_at",
        "fetch_frequency",
        "created_at",
    }
    assert column_names(conn, "news_item") >= {
        "id",
        "source_id",
        "canonical_url",
        "discussion_url",
        "pipeline_state",
        "is_selected",
        "content_raw",
        "content_full",
        "title_zh",
        "summary_zh",
        "content_zh",
        "has_translate_failed",
    }
    assert column_names(conn, "processing_log") >= {
        "id",
        "source_id",
        "news_item_id",
        "stage",
        "success",
        "error",
        "trace_id",
        "created_at",
    }


def test_initialize_database_enforces_key_constraints():
    conn = sqlite3.connect(":memory:")
    initialize_database(conn)

    conn.execute(
        """
        INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES ('Example', 'https://example.com/rss.xml', 1, 'twice_daily', '2026-06-28T06:00:00Z')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at)
            VALUES ('Duplicate', 'https://example.com/rss.xml', 1, 'twice_daily', '2026-06-28T06:01:00Z')
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO news_item (
              source_id, original_url, canonical_url, original_title,
              published_at, pipeline_state, created_at, updated_at
            )
            VALUES (1, 'https://example.com/1', 'https://example.com/1', 'Title',
                    '2026-06-28T07:00:00Z', 'translated', '2026-06-28T09:00:00Z',
                    '2026-06-28T09:00:00Z')
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO processing_log (
              source_id, news_item_id, stage, success, trace_id, created_at
            )
            VALUES (1, 1, 'crawl', 1, 'trace', '2026-06-28T09:00:00Z')
            """
        )


def test_initialize_database_enforces_canonical_url_and_stage_owner_constraints():
    conn = sqlite3.connect(":memory:")
    initialize_database(conn)

    conn.execute(
        """
        INSERT INTO source (id, name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES (1, 'Example', 'https://example.com/rss.xml', 1, 'twice_daily', '2026-06-28T06:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO news_item (
          id, source_id, original_url, canonical_url, original_title,
          published_at, pipeline_state, created_at, updated_at
        )
        VALUES (1, 1, 'https://example.com/1', 'https://example.com/1', 'Title',
                '2026-06-28T07:00:00Z', 'raw', '2026-06-28T09:00:00Z',
                '2026-06-28T09:00:00Z')
        """
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO news_item (
              source_id, original_url, canonical_url, original_title,
              published_at, pipeline_state, created_at, updated_at
            )
            VALUES (1, 'https://example.com/duplicate', 'https://example.com/1', 'Duplicate',
                    '2026-06-28T07:01:00Z', 'raw', '2026-06-28T09:00:00Z',
                    '2026-06-28T09:00:00Z')
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO processing_log (
              news_item_id, stage, success, trace_id, created_at
            )
            VALUES (1, 'crawl', 1, 'trace', '2026-06-28T09:00:00Z')
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO processing_log (
              source_id, stage, success, trace_id, created_at
            )
            VALUES (1, 'score', 1, 'trace', '2026-06-28T09:00:00Z')
            """
        )


def test_initialize_database_migrates_legacy_hn_item_links_to_internal_discussion_url():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE source (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          rss_url TEXT NOT NULL UNIQUE,
          is_enabled INTEGER NOT NULL DEFAULT 1,
          deleted_at TEXT,
          fetch_frequency TEXT NOT NULL DEFAULT 'twice_daily',
          created_at TEXT NOT NULL
        );

        CREATE TABLE news_item (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_id INTEGER NOT NULL REFERENCES source(id),
          rss_guid TEXT,
          original_url TEXT NOT NULL,
          canonical_url TEXT NOT NULL UNIQUE,
          original_title TEXT NOT NULL,
          published_at TEXT NOT NULL,
          score INTEGER,
          pipeline_state TEXT NOT NULL,
          is_selected INTEGER NOT NULL DEFAULT 0,
          content_raw TEXT,
          content_full TEXT,
          title_zh TEXT,
          summary_zh TEXT,
          content_zh TEXT,
          has_translate_failed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO source (id, name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES (1, 'Hacker News', 'https://news.ycombinator.com/rss', 1, 'twice_daily', '2026-06-28T06:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO news_item (
          id, source_id, rss_guid, original_url, canonical_url, original_title,
          published_at, score, pipeline_state, is_selected, content_raw,
          created_at, updated_at
        )
        VALUES (
          1, 1, 'legacy-hn', 'https://news.ycombinator.com/item?id=44254968',
          'https://news.ycombinator.com/item?id=44254968', 'Legacy HN',
          '2026-06-28T07:00:00Z', 95, 'fetched', 1, 'Legacy summary',
          '2026-06-28T09:00:00Z', '2026-06-28T09:00:00Z'
        )
        """
    )

    initialize_database(conn)

    row = conn.execute(
        """
        SELECT original_url, discussion_url, is_selected
        FROM news_item
        WHERE rss_guid = 'legacy-hn'
        """
    ).fetchone()
    assert row == (
        "https://news.ycombinator.com/item?id=44254968",
        "https://news.ycombinator.com/item?id=44254968",
        0,
    )


def test_initialize_database_migrates_archival_openai_fixture_to_current_article():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE source (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          rss_url TEXT NOT NULL UNIQUE,
          is_enabled INTEGER NOT NULL DEFAULT 1,
          deleted_at TEXT,
          fetch_frequency TEXT NOT NULL DEFAULT 'twice_daily',
          created_at TEXT NOT NULL
        );

        CREATE TABLE news_item (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_id INTEGER NOT NULL REFERENCES source(id),
          rss_guid TEXT,
          original_url TEXT NOT NULL,
          canonical_url TEXT NOT NULL UNIQUE,
          original_title TEXT NOT NULL,
          published_at TEXT NOT NULL,
          score INTEGER,
          pipeline_state TEXT NOT NULL,
          is_selected INTEGER NOT NULL DEFAULT 0,
          content_raw TEXT,
          content_full TEXT,
          title_zh TEXT,
          summary_zh TEXT,
          content_zh TEXT,
          has_translate_failed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO source (id, name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES (1, 'OpenAI', 'https://openai.com/news/rss.xml', 1, 'twice_daily', '2026-06-28T06:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO news_item (
          id, source_id, rss_guid, original_url, canonical_url, original_title,
          published_at, score, pipeline_state, is_selected, content_raw,
          created_at, updated_at
        )
        VALUES (
          1, 1, 'fixture-translated-96',
          'https://openai.com/index/gpt-4-1?utm_medium=rss',
          'https://openai.com/index/gpt-4-1',
          'New AI model released', '2026-06-28T07:00:00Z', 96, 'fetched', 1,
          'RSS summary', '2026-06-28T09:00:00Z', '2026-06-28T09:00:00Z'
        )
        """
    )

    initialize_database(conn)

    row = conn.execute(
        """
        SELECT original_url, canonical_url, original_title, published_at, title_zh, is_selected
        FROM news_item
        WHERE rss_guid = 'fixture-translated-96'
        """
    ).fetchone()
    assert row == (
        "https://openai.com/index/introducing-life-sci-bench/",
        "https://openai.com/index/introducing-life-sci-bench/",
        "Introducing LifeSciBench",
        "2026-06-17T00:00:00Z",
        "OpenAI 发布 LifeSciBench 生命科学基准",
        1,
    )


def test_seed_default_sources_is_idempotent():
    conn = sqlite3.connect(":memory:")
    initialize_database(conn)

    seed_default_sources(conn)
    seed_default_sources(conn)

    rows = conn.execute("SELECT rss_url, is_enabled, deleted_at FROM source").fetchall()
    assert len(rows) == 7
    assert {row[0] for row in rows} == EXPECTED_DEFAULT_SOURCE_URLS
    assert all(row[1] == 1 for row in rows)
    assert all(row[2] is None for row in rows)
