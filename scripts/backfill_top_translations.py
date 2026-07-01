#!/usr/bin/env python3
"""Backfill Chinese translations for the top-scored local acceptance news."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.config import get_live_runtime_config  # noqa: E402
from backend.app.db import connect, initialize_database  # noqa: E402
from backend.app.services.pipeline import (  # noqa: E402
    backfill_top_scored_translations,
    utcnow_iso,
)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use the configured live LLM to translate at least the top-scored "
            "local SQLite news before manual acceptance."
        )
    )
    parser.add_argument("--db", default=str(ROOT_DIR / "rss.sqlite3"))
    parser.add_argument("--target", type=positive_int, default=100)
    parser.add_argument("--max-rounds", type=positive_int, default=1)
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("--retry-count", type=int)
    parser.add_argument("--concurrency", type=positive_int)
    return parser.parse_args()


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
    concurrency = args.concurrency or config.live_llm_concurrency

    conn = connect(args.db)
    initialize_database(conn)
    summaries: list[dict[str, int]] = []
    try:
        for _round in range(args.max_rounds):
            summary = backfill_top_scored_translations(
                conn,
                target_count=args.target,
                now=utcnow_iso(),
                live_llm_base_url=config.llm_base_url,
                live_llm_api_key=config.llm_api_key,
                live_llm_model=config.llm_model,
                live_llm_timeout_seconds=timeout_seconds,
                live_llm_retry_count=retry_count,
                live_llm_concurrency=concurrency,
            )
            summaries.append(summary)
            required_count = min(summary["target_count"], summary["top_item_count"])
            if summary["top_translated_count"] >= required_count:
                break
            if summary["requested_count"] == 0:
                break
    finally:
        conn.close()

    final = summaries[-1] if summaries else {
        "target_count": args.target,
        "top_item_count": 0,
        "top_translated_count": 0,
        "top_untranslated_count": 0,
    }
    required_count = min(final["target_count"], final["top_item_count"])
    status = "passed" if final["top_translated_count"] >= required_count else "failed"
    print(
        json.dumps(
            {
                "status": status,
                "database": str(Path(args.db).resolve()),
                "target_count": args.target,
                "required_translated_count": required_count,
                "rounds": summaries,
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
