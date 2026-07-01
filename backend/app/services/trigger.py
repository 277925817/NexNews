"""Deterministic local refresh trigger helpers."""

from __future__ import annotations

import sqlite3

from backend.app.services.pipeline import (
    FIXED_NOW,
    run_fixture_pipeline_summary,
    run_live_pipeline_summary,
)


SCHEDULED_REFRESH_TIMES = {"09:00", "18:00"}


def scheduled_time_key(now: str) -> str:
    return now[11:16]


def run_manual_refresh(
    conn: sqlite3.Connection,
    *,
    is_running: bool = False,
    now: str = FIXED_NOW,
    use_live_data: bool = False,
    allow_live_network: bool = False,
    allow_live_llm: bool = False,
    request_timeout_seconds: float = 12,
    request_retry_count: int = 3,
    request_retry_backoff_seconds: float = 0.5,
) -> dict[str, object]:
    if is_running:
        return {"started": False, "reason": "already_running", "summary": None}
    if use_live_data:
        summary = run_live_pipeline_summary(
            conn,
            now=now,
            allow_live_network=allow_live_network,
            allow_live_llm=allow_live_llm,
            request_timeout_seconds=request_timeout_seconds,
            request_retry_count=request_retry_count,
            request_retry_backoff_seconds=request_retry_backoff_seconds,
        )
    else:
        summary = run_fixture_pipeline_summary(conn, now=now)
    return {
        "started": True,
        "reason": "manual",
        "summary": summary,
    }


def run_scheduled_refresh(
    conn: sqlite3.Connection,
    *,
    now: str,
    is_running: bool = False,
    use_live_data: bool = False,
    allow_live_network: bool = False,
    allow_live_llm: bool = False,
    request_timeout_seconds: float = 12,
    request_retry_count: int = 3,
    request_retry_backoff_seconds: float = 0.5,
) -> dict[str, object]:
    if scheduled_time_key(now) not in SCHEDULED_REFRESH_TIMES:
        return {"started": False, "reason": "not_scheduled_time", "summary": None}
    result = run_manual_refresh(
        conn,
        is_running=is_running,
        now=now,
        use_live_data=use_live_data,
        allow_live_network=allow_live_network,
        allow_live_llm=allow_live_llm,
        request_timeout_seconds=request_timeout_seconds,
        request_retry_count=request_retry_count,
        request_retry_backoff_seconds=request_retry_backoff_seconds,
    )
    if result["started"]:
        result["reason"] = "scheduled"
    return result
