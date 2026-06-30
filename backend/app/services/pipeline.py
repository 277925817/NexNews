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
SCORING_RETRY_MAX = 2


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


def is_valid_rss_item(item: object) -> bool:
    return (
        isinstance(item, dict)
        and bool(item.get("guid"))
        and bool(item.get("link"))
        and bool(item.get("title"))
    )


def news_item_exists(conn: sqlite3.Connection, canonical_url: str) -> bool:
    row = conn.execute(
        "SELECT id FROM news_item WHERE canonical_url = ?",
        (canonical_url,),
    ).fetchone()
    return row is not None


def ingest_feed_items(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    feed: dict[str, object],
    seen_canonical_urls: set[str],
    now: str,
) -> int:
    inserted_count = 0
    items = feed.get("items", [])
    if not isinstance(items, list):
        return inserted_count

    for item in items:
        if not is_valid_rss_item(item):
            continue
        canonical_url = canonicalize_url(str(item["link"]))
        if canonical_url in seen_canonical_urls or news_item_exists(conn, canonical_url):
            continue
        seen_canonical_urls.add(canonical_url)
        insert_raw_item(conn, source_id=source_id, item=item, canonical_url=canonical_url, now=now)
        inserted_count += 1
    return inserted_count


def ingest_fixture_rss(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> dict[str, int]:
    rss_payload = read_json(fixture_root / "fixtures" / "rss" / "feeds.json")
    feeds_by_url = fixture_feeds(rss_payload)
    seen_canonical_urls: set[str] = set()
    result = {"source_success_count": 0, "source_failure_count": 0, "inserted_count": 0}

    for source in active_sources(conn):
        source_id = int(source["id"])
        feed = feeds_by_url.get(str(source["rss_url"]))
        if not feed or feed.get("status") != "success":
            result["source_failure_count"] += 1
            error = str(feed.get("error") if feed else "missing_fixture")
            log_processing(conn, source_id=source_id, stage="crawl", success=0, error=error, now=now)
            continue
        result["source_success_count"] += 1
        log_processing(conn, source_id=source_id, stage="crawl", success=1, now=now)
        result["inserted_count"] += ingest_feed_items(
            conn, source_id=source_id, feed=feed, seen_canonical_urls=seen_canonical_urls, now=now
        )
    conn.commit()
    return result


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


def build_scoring_request(record: dict[str, object]) -> dict[str, str]:
    return {
        "title": str(record.get("original_title") or ""),
        "summary": str(record.get("content_raw") or ""),
        "source": str(record.get("source_name") or ""),
        "published_at": str(record.get("published_at") or ""),
        "original_link": str(record.get("original_url") or ""),
    }


def validate_scoring_response(response: object) -> tuple[int | None, str | None]:
    if not isinstance(response, dict):
        return None, "validation_llm_error"
    score = response.get("score")
    reason = response.get("reason")
    if not isinstance(score, int) or score < 0 or score > 100:
        return None, "validation_llm_error"
    if not isinstance(reason, str) or not reason.strip():
        return None, "validation_llm_error"
    return score, None


def apply_missing_summary_penalty(score: int, request: dict[str, str]) -> int:
    if request["summary"].strip():
        return score
    return max(0, score - 20)


def scoring_timeout_error(guid: str, scoring_payload: dict[str, object]) -> str | None:
    timeout_cases = scoring_payload.get("timeout_cases", {})
    if not isinstance(timeout_cases, dict) or guid not in timeout_cases:
        return None
    case = timeout_cases.get(guid)
    if isinstance(case, dict):
        return str(case.get("error") or "timeout")
    return "timeout"


def scoring_response_for_guid(guid: str, scoring_payload: dict[str, object]) -> object:
    scores = scoring_payload.get("scores", {})
    if isinstance(scores, dict) and guid in scores:
        return scores[guid]
    invalid_cases = scoring_payload.get("invalid_cases", {})
    if isinstance(invalid_cases, dict) and guid in invalid_cases:
        case = invalid_cases[guid]
        return case.get("response") if isinstance(case, dict) else None
    return None


def score_request_with_fixture(
    guid: str,
    request: dict[str, str],
    scoring_payload: dict[str, object],
) -> dict[str, object]:
    if not request["title"].strip() or not request["original_link"].strip():
        return {"score": 0, "error": None, "retry_count": 0}

    timeout_error = scoring_timeout_error(guid, scoring_payload)
    if timeout_error:
        return {"score": None, "error": timeout_error, "retry_count": SCORING_RETRY_MAX}

    score, error = validate_scoring_response(scoring_response_for_guid(guid, scoring_payload))
    if error:
        return {"score": None, "error": error, "retry_count": SCORING_RETRY_MAX}
    return {
        "score": apply_missing_summary_penalty(int(score), request),
        "error": None,
        "retry_count": 0,
    }


def score_rss_item(
    item: dict[str, object],
    *,
    source_name: str,
    scoring_payload: dict[str, object],
) -> dict[str, object]:
    request = build_scoring_request(
        {
            "original_title": item.get("title"),
            "content_raw": item.get("summary") or "",
            "source_name": source_name,
            "published_at": item.get("published_at"),
            "original_url": item.get("link"),
        }
    )
    return score_request_with_fixture(str(item.get("guid") or ""), request, scoring_payload)


def raw_news_for_scoring(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return conn.execute(
        """
        SELECT
          news_item.id, news_item.rss_guid, news_item.original_title,
          news_item.content_raw, news_item.published_at, news_item.original_url,
          source.name AS source_name
        FROM news_item
        JOIN source ON source.id = news_item.source_id
        WHERE news_item.pipeline_state = 'raw'
        ORDER BY news_item.id ASC
        """
    ).fetchall()


def score_raw_news(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> dict[str, int]:
    scoring_payload = read_json(fixture_root / "fixtures" / "llm" / "scoring.json")
    result = {"scored_count": 0, "failed_count": 0, "selected_count": 0}
    for row in raw_news_for_scoring(conn):
        request = build_scoring_request(row)
        score_result = score_request_with_fixture(str(row["rss_guid"] or ""), request, scoring_payload)
        score = score_result["score"]
        if isinstance(score, int):
            selected = apply_score(conn, news_item_id=int(row["id"]), score=score, now=now)
            result["scored_count"] += 1
            result["selected_count"] += 1 if selected else 0
            continue
        result["failed_count"] += 1
        error = str(score_result["error"] or "validation_llm_error")
        log_processing(conn, news_item_id=int(row["id"]), stage="score", success=0, error=error, now=now)
    conn.commit()
    return result


def apply_score(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    score: int,
    now: str,
) -> bool:
    is_selected = score_is_selected(score)
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


def score_is_selected(score: int, threshold: int = SELECTION_THRESHOLD) -> bool:
    return score >= threshold


def selected_fetch_candidates(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return conn.execute(
        """
        SELECT
          id, rss_guid, canonical_url, score, pipeline_state, is_selected,
          content_full, published_at
        FROM news_item
        WHERE pipeline_state = 'scored'
          AND is_selected = 1
          AND score >= ?
        ORDER BY score DESC, published_at DESC, id ASC
        """,
        (SELECTION_THRESHOLD,),
    ).fetchall()


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


def fetch_selected_content(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> dict[str, int]:
    article_payload = read_json(fixture_root / "fixtures" / "articles" / "article_map.json")
    articles_by_url = article_records(article_payload)
    result = {"fetched_count": 0, "content_full_count": 0, "fallback_count": 0, "failed_count": 0}
    for row in selected_fetch_candidates(conn):
        fetched = fetch_content(
            conn,
            news_item_id=int(row["id"]),
            canonical_url=str(row["canonical_url"]),
            article_map=articles_by_url,
            fixture_root=fixture_root,
            now=now,
        )
        if not fetched:
            result["failed_count"] += 1
            continue
        result["fetched_count"] += 1
        stored = conn.execute("SELECT content_full FROM news_item WHERE id = ?", (row["id"],)).fetchone()
        if stored and stored["content_full"]:
            result["content_full_count"] += 1
        else:
            result["fallback_count"] += 1
    conn.commit()
    return result


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


def build_translation_request(record: dict[str, object]) -> dict[str, object]:
    content_full = str(record.get("content_full") or "")
    content_raw = str(record.get("content_raw") or "")
    return {
        "original_title": str(record.get("original_title") or ""),
        "original_summary": content_raw,
        "original_content": content_full or content_raw,
        "source": str(record.get("source_name") or ""),
        "score": int(record.get("score") or 0),
    }


def has_valid_translation_record(record: dict[str, object] | None) -> bool:
    required_fields = ("title_zh", "summary_zh", "content_zh", "category_zh")
    return bool(
        record
        and all(isinstance(record.get(field), str) and record[field].strip() for field in required_fields)
    )


def fetched_news_for_translation(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return conn.execute(
        """
        SELECT
          news_item.id, news_item.rss_guid, news_item.original_title,
          news_item.content_raw, news_item.content_full, news_item.score,
          source.name AS source_name
        FROM news_item
        JOIN source ON source.id = news_item.source_id
        WHERE news_item.pipeline_state = 'fetched'
          AND (news_item.content_full IS NOT NULL OR news_item.content_raw IS NOT NULL)
        ORDER BY news_item.id ASC
        """
    ).fetchall()


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
    if has_valid_translation_record(record):
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


def translate_fetched_content(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> dict[str, int]:
    translation_payload = read_json(fixture_root / "fixtures" / "llm" / "translation.json")
    pending_guids = pending_translation_guids(translation_payload)
    result = {"translated_count": 0, "failed_count": 0, "pending_count": 0}
    for row in fetched_news_for_translation(conn):
        guid = str(row["rss_guid"] or "")
        if guid in pending_guids:
            result["pending_count"] += 1
            continue
        build_translation_request(row)
        apply_translation(conn, news_item_id=int(row["id"]), guid=guid, translation_payload=translation_payload, now=now)
        stored = conn.execute(
            "SELECT title_zh, summary_zh, content_zh, has_translate_failed FROM news_item WHERE id = ?",
            (row["id"],),
        ).fetchone()
        if stored and stored["title_zh"] and stored["summary_zh"] and stored["content_zh"]:
            result["translated_count"] += 1
        elif stored and stored["has_translate_failed"] == 1:
            result["failed_count"] += 1
    conn.commit()
    return result


def rss_fixture_item_count(conn: sqlite3.Connection, fixture_root: Path = ROOT_DIR) -> int:
    rss_payload = read_json(fixture_root / "fixtures" / "rss" / "feeds.json")
    feeds_by_url = fixture_feeds(rss_payload)
    item_count = 0
    for source in active_sources(conn):
        feed = feeds_by_url.get(str(source["rss_url"]))
        items = feed.get("items", []) if feed and feed.get("status") == "success" else []
        item_count += len(items) if isinstance(items, list) else 0
    return item_count


def processing_failure_details(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT stage, error, COUNT(*) AS count
        FROM processing_log
        WHERE success = 0
        GROUP BY stage, error
        ORDER BY stage ASC, error ASC
        """
    ).fetchall()
    return {f"{row['stage']}:{row['error']}": int(row["count"]) for row in rows}


def run_fixture_pipeline_summary(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> dict[str, object]:
    started_at = now
    rss_item_count = rss_fixture_item_count(conn, fixture_root)
    ingest_result = ingest_fixture_rss(conn, fixture_root=fixture_root, now=now)
    score_result = score_raw_news(conn, fixture_root=fixture_root, now=now)
    fetch_result = fetch_selected_content(conn, fixture_root=fixture_root, now=now)
    translate_result = translate_fetched_content(conn, fixture_root=fixture_root, now=now)
    return {
        "started_at": started_at,
        "finished_at": now,
        "source_success_count": ingest_result["source_success_count"],
        "source_failure_count": ingest_result["source_failure_count"],
        "rss_item_count": rss_item_count,
        "new_item_count": ingest_result["inserted_count"],
        "scored_item_count": score_result["scored_count"],
        "selected_item_count": score_result["selected_count"],
        "fetched_item_count": fetch_result["fetched_count"],
        "translated_item_count": translate_result["translated_count"],
        "failure_details": processing_failure_details(conn),
    }


def refresh_payloads(fixture_root: Path) -> tuple[
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
    dict[str, object],
    dict[str, object],
]:
    rss_payload = read_json(fixture_root / "fixtures" / "rss" / "feeds.json")
    article_payload = read_json(fixture_root / "fixtures" / "articles" / "article_map.json")
    scoring_payload = read_json(fixture_root / "fixtures" / "llm" / "scoring.json")
    translation_payload = read_json(fixture_root / "fixtures" / "llm" / "translation.json")
    return fixture_feeds(rss_payload), article_records(article_payload), scoring_payload, translation_payload


def process_refresh_item(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    source_name: str,
    item: dict[str, object],
    seen_canonical_urls: set[str],
    articles_by_url: dict[str, dict[str, object]],
    scoring_payload: dict[str, object],
    translation_payload: dict[str, object],
    fixture_root: Path,
    now: str,
) -> None:
    canonical_url = canonicalize_url(str(item["link"]))
    if canonical_url in seen_canonical_urls:
        return
    seen_canonical_urls.add(canonical_url)
    news_item_id = insert_raw_item(conn, source_id=source_id, item=item, canonical_url=canonical_url, now=now)
    score_result = score_rss_item(item, source_name=source_name, scoring_payload=scoring_payload)
    score = score_result["score"]
    if not isinstance(score, int):
        error = str(score_result["error"] or "validation_llm_error")
        log_processing(conn, news_item_id=news_item_id, stage="score", success=0, error=error, now=now)
        return
    if not apply_score(conn, news_item_id=news_item_id, score=score, now=now):
        return
    fetched = fetch_content(
        conn,
        news_item_id=news_item_id,
        canonical_url=canonical_url,
        article_map=articles_by_url,
        fixture_root=fixture_root,
        now=now,
    )
    if fetched:
        apply_translation(
            conn,
            news_item_id=news_item_id,
            guid=str(item["guid"]),
            translation_payload=translation_payload,
            now=now,
        )


def process_refresh_source(
    conn: sqlite3.Connection,
    *,
    source: dict[str, object],
    feed: dict[str, object] | None,
    seen_canonical_urls: set[str],
    articles_by_url: dict[str, dict[str, object]],
    scoring_payload: dict[str, object],
    translation_payload: dict[str, object],
    fixture_root: Path,
    now: str,
) -> None:
    source_id = int(source["id"])
    source_name = str(source.get("name") or "")
    if not feed or feed.get("status") != "success":
        error = str(feed.get("error") if feed else "missing_fixture")
        log_processing(conn, source_id=source_id, stage="crawl", success=0, error=error, now=now)
        return
    log_processing(conn, source_id=source_id, stage="crawl", success=1, now=now)
    items = feed.get("items", [])
    for item in items if isinstance(items, list) else []:
        if is_valid_rss_item(item):
            process_refresh_item(
                conn,
                source_id=source_id,
                source_name=source_name,
                item=item,
                seen_canonical_urls=seen_canonical_urls,
                articles_by_url=articles_by_url,
                scoring_payload=scoring_payload,
                translation_payload=translation_payload,
                fixture_root=fixture_root,
                now=now,
            )


def run_fixture_refresh(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> None:
    feeds_by_url, articles_by_url, scoring_payload, translation_payload = refresh_payloads(fixture_root)
    seen_canonical_urls: set[str] = set()

    for source in active_sources(conn):
        process_refresh_source(
            conn,
            source=source,
            feed=feeds_by_url.get(str(source["rss_url"])),
            seen_canonical_urls=seen_canonical_urls,
            articles_by_url=articles_by_url,
            scoring_payload=scoring_payload,
            translation_payload=translation_payload,
            fixture_root=fixture_root,
            now=now,
        )
    conn.commit()
