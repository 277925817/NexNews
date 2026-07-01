"""SQLite helpers for the RSS aggregation MVP."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES_PATH = ROOT_DIR / "fixtures" / "sources" / "default_sources.json"
FIXED_SEED_TIME = "2026-06-28T06:00:00Z"
LEGACY_OPENAI_GPT_4_1_ORIGINAL_URL = "https://openai.com/index/introducing-gpt-4-1-in-the-api/?utm_medium=rss"
LEGACY_OPENAI_GPT_4_1_CANONICAL_URL = "https://openai.com/index/introducing-gpt-4-1-in-the-api/"
ARCHIVAL_OPENAI_GPT_4_1_ORIGINAL_URL = "https://openai.com/index/gpt-4-1?utm_medium=rss"
ARCHIVAL_OPENAI_GPT_4_1_CANONICAL_URL = "https://openai.com/index/gpt-4-1"
CURRENT_OPENAI_TRANSLATED_FIXTURE_ORIGINAL_URL = "https://openai.com/index/introducing-life-sci-bench/"
CURRENT_OPENAI_TRANSLATED_FIXTURE_CANONICAL_URL = "https://openai.com/index/introducing-life-sci-bench/"
CURRENT_OPENAI_TRANSLATED_FIXTURE_TITLE = "Introducing LifeSciBench"
CURRENT_OPENAI_TRANSLATED_FIXTURE_PUBLISHED_AT = "2026-06-17T00:00:00Z"
CURRENT_OPENAI_TRANSLATED_FIXTURE_SUMMARY = (
    "OpenAI introduces LifeSciBench, a benchmark for evaluating AI systems on real-world life science research tasks."
)
CURRENT_OPENAI_TRANSLATED_FIXTURE_TITLE_ZH = "OpenAI 发布 LifeSciBench 生命科学基准"
CURRENT_OPENAI_TRANSLATED_FIXTURE_SUMMARY_ZH = (
    "OpenAI 发布 LifeSciBench 生命科学基准，用专家任务评估 AI 系统处理真实科研不确定性的能力。"
)
CURRENT_OPENAI_TRANSLATED_FIXTURE_CONTENT_ZH = (
    "OpenAI 发布 LifeSciBench 生命科学基准，重点评估 AI 系统能否处理真实研究中的复杂判断。"
    "任务覆盖不完整证据、实验设计、转化风险和下一步决策，而不是只考察单一事实回忆。\n\n"
    "这个基准由具有生命科学经验的专家编写和审阅，帮助研究团队观察模型在科研工作流中的可靠性。"
    "它也让评测更接近药物发现和生物医学研究场景，便于发现模型在推理、取舍和证据整合上的薄弱环节。"
)

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  rss_url TEXT NOT NULL UNIQUE,
  is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
  deleted_at TEXT,
  fetch_frequency TEXT NOT NULL DEFAULT 'twice_daily' CHECK (fetch_frequency = 'twice_daily'),
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_is_enabled ON source(is_enabled);

CREATE TABLE IF NOT EXISTS news_item (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES source(id),
  rss_guid TEXT,
  original_url TEXT NOT NULL,
  canonical_url TEXT NOT NULL UNIQUE,
  discussion_url TEXT,
  original_title TEXT NOT NULL,
  published_at TEXT NOT NULL,
  score INTEGER CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
  pipeline_state TEXT NOT NULL CHECK (pipeline_state IN ('raw', 'scored', 'fetched')),
  is_selected INTEGER NOT NULL DEFAULT 0 CHECK (is_selected IN (0, 1)),
  content_raw TEXT,
  content_full TEXT,
  title_zh TEXT,
  summary_zh TEXT,
  content_zh TEXT,
  has_translate_failed INTEGER NOT NULL DEFAULT 0 CHECK (has_translate_failed IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (pipeline_state != 'scored' OR score IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_news_item_source_id ON news_item(source_id);
CREATE INDEX IF NOT EXISTS idx_news_item_pipeline_state ON news_item(pipeline_state);
CREATE INDEX IF NOT EXISTS idx_news_item_published_at ON news_item(published_at);
CREATE INDEX IF NOT EXISTS idx_news_item_score ON news_item(score);

CREATE TABLE IF NOT EXISTS processing_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER REFERENCES source(id),
  news_item_id INTEGER REFERENCES news_item(id),
  stage TEXT NOT NULL CHECK (stage IN ('crawl', 'score', 'fetch', 'translate')),
  success INTEGER NOT NULL CHECK (success IN (0, 1)),
  error TEXT,
  trace_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK (
    (source_id IS NOT NULL AND news_item_id IS NULL) OR
    (source_id IS NULL AND news_item_id IS NOT NULL)
  ),
  CHECK (
    (stage = 'crawl' AND source_id IS NOT NULL) OR
    (stage IN ('score', 'fetch', 'translate') AND news_item_id IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_processing_log_source_stage ON processing_log(source_id, stage);
CREATE INDEX IF NOT EXISTS idx_processing_log_news_stage_success ON processing_log(news_item_id, stage, success);
CREATE INDEX IF NOT EXISTS idx_processing_log_trace_id ON processing_log(trace_id);
CREATE INDEX IF NOT EXISTS idx_processing_log_created_at ON processing_log(created_at);
"""


def connect(path: str) -> sqlite3.Connection:
    """Create a deterministic SQLite connection for local runs."""

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_factory(cursor, row):  # pragma: no cover - thin compatibility shim
    return dict(zip([c[0] for c in cursor.description], row))


def initialize_database(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    migrate_news_item_discussion_url(conn)
    migrate_legacy_openai_gpt_4_1_url(conn)
    conn.commit()


def sqlite_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] if isinstance(row, dict) else row[1] for row in rows}


def migrate_news_item_discussion_url(conn: sqlite3.Connection) -> None:
    if "discussion_url" not in sqlite_table_columns(conn, "news_item"):
        conn.execute("ALTER TABLE news_item ADD COLUMN discussion_url TEXT")
    conn.execute(
        """
        UPDATE news_item
        SET discussion_url = original_url,
            is_selected = 0
        WHERE discussion_url IS NULL
          AND lower(original_url) LIKE 'https://news.ycombinator.com/item?id=%'
        """
    )


def migrate_legacy_openai_gpt_4_1_url(conn: sqlite3.Connection) -> None:
    target_exists = conn.execute(
        "SELECT 1 FROM news_item WHERE canonical_url = ?",
        (CURRENT_OPENAI_TRANSLATED_FIXTURE_CANONICAL_URL,),
    ).fetchone()
    legacy_where = """
        rss_guid = 'fixture-translated-96'
        AND (
          lower(canonical_url) IN (?, ?)
          OR lower(original_url) IN (?, ?)
        )
    """
    legacy_values = (
        LEGACY_OPENAI_GPT_4_1_CANONICAL_URL,
        ARCHIVAL_OPENAI_GPT_4_1_CANONICAL_URL,
        LEGACY_OPENAI_GPT_4_1_ORIGINAL_URL,
        ARCHIVAL_OPENAI_GPT_4_1_ORIGINAL_URL,
    )
    if target_exists is not None:
        conn.execute(f"UPDATE news_item SET is_selected = 0 WHERE {legacy_where}", legacy_values)
        return
    conn.execute(
        f"""
        UPDATE news_item
        SET original_url = ?,
            canonical_url = ?,
            original_title = ?,
            published_at = ?,
            content_raw = ?,
            content_full = ?,
            title_zh = ?,
            summary_zh = ?,
            content_zh = ?
        WHERE {legacy_where}
        """,
        (
            CURRENT_OPENAI_TRANSLATED_FIXTURE_ORIGINAL_URL,
            CURRENT_OPENAI_TRANSLATED_FIXTURE_CANONICAL_URL,
            CURRENT_OPENAI_TRANSLATED_FIXTURE_TITLE,
            CURRENT_OPENAI_TRANSLATED_FIXTURE_PUBLISHED_AT,
            CURRENT_OPENAI_TRANSLATED_FIXTURE_SUMMARY,
            CURRENT_OPENAI_TRANSLATED_FIXTURE_SUMMARY,
            CURRENT_OPENAI_TRANSLATED_FIXTURE_TITLE_ZH,
            CURRENT_OPENAI_TRANSLATED_FIXTURE_SUMMARY_ZH,
            CURRENT_OPENAI_TRANSLATED_FIXTURE_CONTENT_ZH,
            *legacy_values,
        ),
    )


def source_name_from_url(url: str) -> str:
    hostname = urlparse(url).hostname or url
    return hostname.replace("www.", "").replace(".com", "").replace(".", " ").title()


def seed_default_sources(
    conn: sqlite3.Connection,
    fixture_path: Path = DEFAULT_SOURCES_PATH,
) -> None:
    payload = json.loads(fixture_path.read_text())
    for index, rss_url in enumerate(payload["sources"], start=1):
        conn.execute(
            """
            INSERT OR IGNORE INTO source (
              name, rss_url, is_enabled, deleted_at, fetch_frequency, created_at
            )
            VALUES (?, ?, 1, NULL, 'twice_daily', ?)
            """,
            (
                source_name_from_url(rss_url),
                rss_url,
                f"2026-06-28T06:{index:02d}:00Z",
            ),
        )
    conn.commit()
