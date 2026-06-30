"""SQLite helpers for the RSS aggregation MVP."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES_PATH = ROOT_DIR / "fixtures" / "sources" / "default_sources.json"
FIXED_SEED_TIME = "2026-06-28T06:00:00Z"

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
    conn.commit()


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
