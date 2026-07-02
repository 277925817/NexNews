#!/usr/bin/env python3
"""Re-score existing local news with the live AI value filter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.config import get_live_runtime_config  # noqa: E402
from backend.app.db import connect, initialize_database  # noqa: E402
from backend.app.services.pipeline import (  # noqa: E402
    LIVE_LLM_AVAILABILITY_ERRORS,
    apply_scoring_penalties,
    build_scoring_request,
    log_processing,
    request_live_scoring_rows,
    score_is_selected,
    utcnow_iso,
)


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be >= 0")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use the configured live LLM to re-score existing SQLite news with "
            "the AI relevance + AI value filter. Use --limit 0 to process all "
            "eligible rows."
        )
    )
    parser.add_argument("--db", default=str(ROOT_DIR / "rss.sqlite3"))
    parser.add_argument("--limit", type=non_negative_int, default=250)
    parser.add_argument("--include-raw", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("--retry-count", type=int)
    parser.add_argument("--concurrency", type=positive_int)
    return parser.parse_args()


def rows_for_reclassification(
    conn: sqlite3.Connection,
    *,
    limit: int,
    include_raw: bool,
) -> list[dict[str, object]]:
    state_clause = "news_item.pipeline_state IN ('scored', 'fetched')"
    if include_raw:
        state_clause = "news_item.pipeline_state IN ('raw', 'scored', 'fetched')"
    limit_clause = "LIMIT ?" if limit > 0 else ""
    params: tuple[object, ...] = (limit,) if limit > 0 else ()
    return conn.execute(
        f"""
        SELECT
          news_item.id,
          news_item.rss_guid,
          news_item.original_title,
          news_item.original_url,
          news_item.content_raw,
          news_item.published_at,
          news_item.score AS previous_score,
          news_item.is_selected AS previous_selected,
          news_item.pipeline_state,
          source.name AS source_name
        FROM news_item
        JOIN source ON source.id = news_item.source_id
        WHERE {state_clause}
        ORDER BY
          news_item.is_selected DESC,
          news_item.score DESC,
          news_item.published_at DESC,
          news_item.id DESC
        {limit_clause}
        """,
        params,
    ).fetchall()


def write_reclassification(
    conn: sqlite3.Connection,
    *,
    row: dict[str, object],
    record: dict[str, object],
    now: str,
    dry_run: bool,
) -> dict[str, object]:
    request = build_scoring_request(row)
    adjusted = apply_scoring_penalties(record, request)
    score = int(adjusted["score"])
    is_ai_news = bool(adjusted["is_ai_news"])
    ai_relevance_score = int(adjusted["ai_relevance_score"])
    is_selected = score_is_selected(
        score,
        is_ai_news=is_ai_news,
        ai_relevance_score=ai_relevance_score,
    )
    previous_selected = bool(row["previous_selected"])
    next_state = "scored" if row["pipeline_state"] == "raw" else str(row["pipeline_state"])
    if not dry_run:
        conn.execute(
            """
            UPDATE news_item
            SET score = ?,
                is_ai_news = ?,
                ai_relevance_score = ?,
                is_selected = ?,
                pipeline_state = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                score,
                1 if is_ai_news else 0,
                ai_relevance_score,
                1 if is_selected else 0,
                next_state,
                now,
                int(row["id"]),
            ),
        )
        log_processing(
            conn,
            news_item_id=int(row["id"]),
            stage="score",
            success=1,
            now=now,
        )
    return {
        "id": int(row["id"]),
        "rss_guid": row["rss_guid"],
        "previous_score": row["previous_score"],
        "score": score,
        "is_ai_news": is_ai_news,
        "ai_relevance_score": ai_relevance_score,
        "previous_selected": previous_selected,
        "is_selected": is_selected,
        "selection_changed": previous_selected != is_selected,
    }


def reclassify_rows(
    conn: sqlite3.Connection,
    *,
    rows: list[dict[str, object]],
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
    retry_count: int,
    concurrency: int,
    dry_run: bool,
    now: str,
) -> dict[str, object]:
    scored_rows = request_live_scoring_rows(
        rows,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
        concurrency=concurrency,
    )
    changed: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    unavailable_count = 0
    for row, record, error in scored_rows:
        if record is None:
            if error in LIVE_LLM_AVAILABILITY_ERRORS:
                unavailable_count += 1
            failures.append({"id": int(row["id"]), "rss_guid": row["rss_guid"], "error": error})
            if not dry_run:
                log_processing(
                    conn,
                    news_item_id=int(row["id"]),
                    stage="score",
                    success=0,
                    error=error or "live_scoring_error",
                    now=now,
                )
            continue
        result = write_reclassification(conn, row=row, record=record, now=now, dry_run=dry_run)
        if result["selection_changed"]:
            changed.append(result)
    if not dry_run:
        conn.commit()
    selected_after = conn.execute(
        "SELECT COUNT(*) AS count FROM news_item WHERE is_selected = 1"
    ).fetchone()["count"]
    return {
        "requested_count": len(rows),
        "succeeded_count": len(rows) - len(failures),
        "failed_count": len(failures),
        "llm_unavailable_count": unavailable_count,
        "selection_changed_count": len(changed),
        "selected_after": int(selected_after),
        "changed": changed[:50],
        "failures": failures[:50],
    }


def main() -> int:
    args = parse_args()
    config = get_live_runtime_config(ROOT_DIR)
    missing = [
        name
        for name, value in {
            "LLM_API_KEY": config.llm_api_key,
            "LLM_BASE_URL": config.llm_base_url,
            "LLM_MODEL": config.llm_model,
        }.items()
        if not value
    ]
    if missing:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "llm_config_missing",
                    "missing": missing,
                },
                ensure_ascii=False,
            )
        )
        return 1

    timeout_seconds = args.timeout_seconds or config.llm_request_timeout_seconds
    retry_count = config.live_llm_retry_count if args.retry_count is None else args.retry_count
    concurrency = args.concurrency or config.live_llm_score_concurrency
    now = utcnow_iso()

    conn = connect(args.db)
    initialize_database(conn)
    try:
        selected_before = conn.execute(
            "SELECT COUNT(*) AS count FROM news_item WHERE is_selected = 1"
        ).fetchone()["count"]
        rows = rows_for_reclassification(
            conn,
            limit=args.limit,
            include_raw=args.include_raw,
        )
        summary = reclassify_rows(
            conn,
            rows=rows,
            base_url=str(config.llm_base_url),
            api_key=str(config.llm_api_key),
            model=str(config.llm_model),
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
            concurrency=concurrency,
            dry_run=args.dry_run,
            now=now,
        )
    finally:
        conn.close()

    status = "passed" if summary["failed_count"] == 0 else "failed"
    print(
        json.dumps(
            {
                "status": status,
                "database": str(Path(args.db).resolve()),
                "dry_run": args.dry_run,
                "limit": args.limit,
                "include_raw": args.include_raw,
                "selected_before": int(selected_before),
                **summary,
                "llm_base_url": config.llm_base_url,
                "llm_model": config.llm_model,
                "llm_api_key_set": bool(config.llm_api_key),
                "timeout_seconds": timeout_seconds,
                "retry_count": retry_count,
                "concurrency": concurrency,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
