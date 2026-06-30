import sqlite3

import pytest

from backend.app.db import initialize_database, seed_default_sources


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


def test_seed_default_sources_is_idempotent():
    conn = sqlite3.connect(":memory:")
    initialize_database(conn)

    seed_default_sources(conn)
    seed_default_sources(conn)

    rows = conn.execute("SELECT rss_url, is_enabled, deleted_at FROM source").fetchall()
    assert len(rows) == 7
    assert all(row[1] == 1 for row in rows)
    assert all(row[2] is None for row in rows)
