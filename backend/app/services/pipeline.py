"""Deterministic fixture-backed refresh pipeline for local acceptance runs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup


FIXED_NOW = "2026-06-28T09:00:00Z"
ROOT_DIR = Path(__file__).resolve().parents[3]
RSS_FIXTURE_PATH = ROOT_DIR / "fixtures" / "rss" / "feeds.json"
ARTICLE_MAP_PATH = ROOT_DIR / "fixtures" / "articles" / "article_map.json"
SCORING_FIXTURE_PATH = ROOT_DIR / "fixtures" / "llm" / "scoring.json"
TRANSLATION_FIXTURE_PATH = ROOT_DIR / "fixtures" / "llm" / "translation.json"
TRACE_ID = "refresh-fixture-20260628T090000Z"
SELECTION_THRESHOLD = 60


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            urlencode(query),
            "",
        )
    )


def log_processing(
    conn: sqlite3.Connection,
    *,
    source_id: int | None = None,
    news_item_id: int | None = None,
    stage: str,
    success: int,
    error: str | None = None,
    now: str = FIXED_NOW,
    trace_id: str = TRACE_ID,
) -> None:
    conn.execute(
        """
        INSERT INTO processing_log (
          source_id, news_item_id, stage, success, error, trace_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, news_item_id, stage, success, error, trace_id, now),
    )


def active_sources(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return conn.execute(
        """
        SELECT id, name, rss_url
        FROM source
        WHERE deleted_at IS NULL AND is_enabled = 1
        ORDER BY created_at ASC
        """
    ).fetchall()


def fixture_feeds(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    feeds = payload.get("feeds", [])
    if not isinstance(feeds, list):
        return {}
    return {
        str(feed.get("rss_url")): feed
        for feed in feeds
        if isinstance(feed, dict) and feed.get("rss_url")
    }


def insert_raw_item(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    item: dict[str, object],
    canonical_url: str,
    now: str,
) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO news_item (
          source_id, rss_guid, original_url, canonical_url, original_title,
          published_at, pipeline_state, is_selected, content_raw, created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'raw', 0, ?, ?, ?)
        """,
        (
            source_id,
            str(item["guid"]),
            str(item["link"]),
            canonical_url,
            str(item["title"]),
            str(item["published_at"]),
            str(item.get("summary") or ""),
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM news_item WHERE canonical_url = ?",
        (canonical_url,),
    ).fetchone()
    return int(row["id"])


def score_item(item: dict[str, object], scoring_payload: dict[str, object]) -> int:
    if not str(item.get("title") or "").strip() or not str(item.get("link") or "").strip():
        return 0
    scores = scoring_payload.get("scores", {})
    response = scores.get(str(item["guid"])) if isinstance(scores, dict) else None
    if not isinstance(response, dict):
        return 0
    score = response.get("score")
    if not isinstance(score, int) or score < 0 or score > 100:
        return 0
    return score


def apply_score(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    score: int,
    now: str,
) -> bool:
    is_selected = score >= SELECTION_THRESHOLD
    conn.execute(
        """
        UPDATE news_item
        SET score = ?,
            pipeline_state = 'scored',
            is_selected = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (score, 1 if is_selected else 0, now, news_item_id),
    )
    log_processing(conn, news_item_id=news_item_id, stage="score", success=1, now=now)
    return is_selected


def article_records(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    articles = payload.get("articles", {})
    if not isinstance(articles, dict):
        return {}
    return {
        str(url): record
        for url, record in articles.items()
        if isinstance(record, dict)
    }


def extract_article_text(path: Path) -> str:
    soup = BeautifulSoup(path.read_text(), "html.parser")
    article = soup.find("article") or soup.body
    if article is None:
        return ""
    chunks = [
        node.get_text(" ", strip=True)
        for node in article.find_all(["h1", "h2", "p"])
        if node.get_text(" ", strip=True)
    ]
    if chunks:
        return "\n".join(chunks)
    return article.get_text(" ", strip=True)


def fetch_content(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    canonical_url: str,
    article_map: dict[str, dict[str, object]],
    fixture_root: Path,
    now: str,
) -> bool:
    row = conn.execute(
        "SELECT content_raw FROM news_item WHERE id = ?",
        (news_item_id,),
    ).fetchone()
    content_raw = str(row["content_raw"] or "")
    record = article_map.get(canonical_url)
    content_full: str | None = None
    success = 0
    error = "network"

    if record and record.get("status") == "success":
        article_path = fixture_root / "fixtures" / "articles" / str(record.get("path"))
        content_full = extract_article_text(article_path)
        if content_full:
            success = 1
            error = None
        else:
            error = "parsing"
    elif record and record.get("error"):
        error = str(record["error"])

    if not content_full and not content_raw:
        log_processing(
            conn,
            news_item_id=news_item_id,
            stage="fetch",
            success=0,
            error=error,
            now=now,
        )
        return False

    conn.execute(
        """
        UPDATE news_item
        SET content_full = ?,
            pipeline_state = 'fetched',
            updated_at = ?
        WHERE id = ?
        """,
        (content_full, now, news_item_id),
    )
    log_processing(
        conn,
        news_item_id=news_item_id,
        stage="fetch",
        success=success,
        error=error,
        now=now,
    )
    return True


def translation_records(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    translations = payload.get("translations", {})
    if not isinstance(translations, dict):
        return {}
    return {
        str(guid): record
        for guid, record in translations.items()
        if isinstance(record, dict)
    }


def pending_translation_guids(payload: dict[str, object]) -> set[str]:
    pending = payload.get("pending_guids", [])
    if not isinstance(pending, list):
        return set()
    return {str(item) for item in pending}


def apply_translation(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    guid: str,
    translation_payload: dict[str, object],
    now: str,
) -> None:
    if guid in pending_translation_guids(translation_payload):
        return

    record = translation_records(translation_payload).get(guid)
    required_fields = ("title_zh", "summary_zh", "content_zh")
    if record and all(isinstance(record.get(field), str) and record[field].strip() for field in required_fields):
        conn.execute(
            """
            UPDATE news_item
            SET title_zh = ?,
                summary_zh = ?,
                content_zh = ?,
                has_translate_failed = 0,
                updated_at = ?
            WHERE id = ?
            """,
            (
                str(record["title_zh"]),
                str(record["summary_zh"]),
                str(record["content_zh"]),
                now,
                news_item_id,
            ),
        )
        log_processing(
            conn,
            news_item_id=news_item_id,
            stage="translate",
            success=1,
            now=now,
        )
        return

    conn.execute(
        """
        UPDATE news_item
        SET title_zh = NULL,
            summary_zh = NULL,
            content_zh = NULL,
            has_translate_failed = 1,
            updated_at = ?
        WHERE id = ?
        """,
        (now, news_item_id),
    )
    log_processing(
        conn,
        news_item_id=news_item_id,
        stage="translate",
        success=0,
        error="validation_llm_error",
        now=now,
    )


def run_fixture_refresh(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> None:
    rss_payload = read_json(fixture_root / "fixtures" / "rss" / "feeds.json")
    article_payload = read_json(fixture_root / "fixtures" / "articles" / "article_map.json")
    scoring_payload = read_json(fixture_root / "fixtures" / "llm" / "scoring.json")
    translation_payload = read_json(fixture_root / "fixtures" / "llm" / "translation.json")

    feeds_by_url = fixture_feeds(rss_payload)
    articles_by_url = article_records(article_payload)
    seen_canonical_urls: set[str] = set()

    for source in active_sources(conn):
        source_id = int(source["id"])
        feed = feeds_by_url.get(str(source["rss_url"]))
        if not feed or feed.get("status") != "success":
            log_processing(
                conn,
                source_id=source_id,
                stage="crawl",
                success=0,
                error=str(feed.get("error") if feed else "missing_fixture"),
                now=now,
            )
            continue

        log_processing(conn, source_id=source_id, stage="crawl", success=1, now=now)
        items = feed.get("items", [])
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            if not item.get("guid") or not item.get("link") or not item.get("title"):
                continue
            canonical_url = canonicalize_url(str(item["link"]))
            if canonical_url in seen_canonical_urls:
                continue
            seen_canonical_urls.add(canonical_url)

            news_item_id = insert_raw_item(
                conn,
                source_id=source_id,
                item=item,
                canonical_url=canonical_url,
                now=now,
            )
            score = score_item(item, scoring_payload)
            is_selected = apply_score(
                conn,
                news_item_id=news_item_id,
                score=score,
                now=now,
            )
            if not is_selected:
                continue
            fetched = fetch_content(
                conn,
                news_item_id=news_item_id,
                canonical_url=canonical_url,
                article_map=articles_by_url,
                fixture_root=fixture_root,
                now=now,
            )
            if not fetched:
                continue
            apply_translation(
                conn,
                news_item_id=news_item_id,
                guid=str(item["guid"]),
                translation_payload=translation_payload,
                now=now,
            )
    conn.commit()
