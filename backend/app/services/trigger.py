"""Deterministic local refresh trigger helpers."""

from __future__ import annotations

import sqlite3

from backend.app.services.pipeline import FIXED_NOW, run_fixture_pipeline_summary


SCHEDULED_REFRESH_TIMES = {"09:00", "18:00"}


def scheduled_time_key(now: str) -> str:
    return now[11:16]


def run_manual_refresh(
    conn: sqlite3.Connection,
    *,
    is_running: bool = False,
    now: str = FIXED_NOW,
) -> dict[str, object]:
    if is_running:
        return {"started": False, "reason": "already_running", "summary": None}
    return {
        "started": True,
        "reason": "manual",
        "summary": run_fixture_pipeline_summary(conn, now=now),
    }


def run_scheduled_refresh(
    conn: sqlite3.Connection,
    *,
    now: str,
    is_running: bool = False,
) -> dict[str, object]:
    if scheduled_time_key(now) not in SCHEDULED_REFRESH_TIMES:
        return {"started": False, "reason": "not_scheduled_time", "summary": None}
    result = run_manual_refresh(conn, is_running=is_running, now=now)
    if result["started"]:
        result["reason"] = "scheduled"
    return result
