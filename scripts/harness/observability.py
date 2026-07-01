"""Local deterministic observability artifacts for harness reports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scripts.harness.observability_index import refresh_derived
from scripts.harness.reports import relative_report_path


EVENT_FIELDS = (
    "run_id",
    "timestamp",
    "event",
    "trace_id",
    "stage",
    "task_id",
    "gate",
    "assertion_id",
    "node",
    "status",
    "failure_type",
    "error_category",
    "referenced_files",
    "artifact_paths",
    "safe_context",
)
OBSERVABILITY_FILENAMES = (
    "events.jsonl",
    "metrics.json",
    "traces.jsonl",
    "index.json",
)
FORBIDDEN_KEY_TOKENS = (
    "full_llm_prompt",
    "raw_pipeline_payload",
    "raw_article_body",
    "content_raw",
    "content_full",
    "fallback raw text",
    "prompt",
    "token",
    "secret",
    "password",
    "api_key",
    "authorization",
)
MAX_SAFE_STRING_LENGTH = 300
GATE_PATTERN = re.compile(r"(ACC-STOP-(?:00[1-9]|010))")


def report_dir_for(path: Path) -> Path:
    parts = path.parts
    for marker in ("acceptance", "stages", "tasks", "observability"):
        if marker in parts:
            marker_index = parts.index(marker)
            if marker_index == 0:
                return Path(".")
            return Path(*parts[:marker_index])
    return path.parent


def observability_dir_for(path: Path) -> Path:
    return report_dir_for(path) / "observability"


def observability_artifact_paths(path: Path) -> list[str]:
    return [
        relative_report_path(observability_dir_for(path) / filename)
        for filename in OBSERVABILITY_FILENAMES
    ]


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = str(key)
            lowered = safe_key.lower()
            if any(token in lowered for token in FORBIDDEN_KEY_TOKENS):
                safe[safe_key] = "[REDACTED]"
            else:
                safe[safe_key] = sanitize(item)
        return safe
    if isinstance(value, list):
        return [sanitize(item) for item in value[:100]]
    if isinstance(value, str):
        if len(value) > MAX_SAFE_STRING_LENGTH:
            return value[:MAX_SAFE_STRING_LENGTH] + "...[truncated]"
        return value
    return value


def gate_from(*values: Any) -> str | None:
    for value in values:
        if not isinstance(value, str):
            continue
        match = GATE_PATTERN.search(value)
        if match:
            return match.group(1)
    return None


def task_id_from_path(path: Path) -> str | None:
    parts = path.parts
    if "tasks" not in parts:
        return None
    index = parts.index("tasks")
    if len(parts) <= index + 1:
        return None
    return parts[index + 1]


def run_id_for(report: dict[str, Any]) -> str:
    trace_id = str(report.get("trace_id") or "harness")
    data_hash = str(report.get("data_hash") or "")
    suffix = data_hash.replace("sha256:", "")[:12] or str(report.get("test_id") or "run")
    return f"{trace_id}:{suffix}"


def event_from_report(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    gate = gate_from(report.get("test_id"))
    return normalize_event(
        {
            "run_id": run_id_for(report),
            "timestamp": report.get("timestamp"),
            "event": "test_report_written",
            "trace_id": report.get("trace_id"),
            "stage": report.get("stage"),
            "task_id": task_id_from_path(path),
            "gate": gate,
            "assertion_id": None,
            "node": report.get("node"),
            "status": report.get("status"),
            "failure_type": report.get("failure_type"),
            "error_category": report.get("error_category"),
            "referenced_files": report.get("referenced_files") or [],
            "artifact_paths": report.get("artifact_paths") or [],
            "safe_context": {
                "test_id": report.get("test_id"),
                "expected": sanitize(report.get("expected") or {}),
                "actual": sanitize(report.get("actual") or {}),
                "diff": sanitize(report.get("diff") or {}),
            },
        }
    )


def events_from_assertions(path: Path, report: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    assertions = report.get("assertions") if isinstance(report.get("assertions"), list) else []
    for item in assertions:
        if not isinstance(item, dict):
            continue
        assertion_id = str(item.get("id") or "")
        events.append(
            normalize_event(
                {
                    "run_id": run_id_for(report),
                    "timestamp": report.get("timestamp"),
                    "event": "assertion_recorded",
                    "trace_id": report.get("trace_id"),
                    "stage": report.get("stage"),
                    "task_id": task_id_from_path(path),
                    "gate": gate_from(assertion_id, report.get("test_id")),
                    "assertion_id": assertion_id or None,
                    "node": report.get("node"),
                    "status": item.get("status"),
                    "failure_type": report.get("failure_type"),
                    "error_category": report.get("error_category"),
                    "referenced_files": report.get("referenced_files") or [],
                    "artifact_paths": report.get("artifact_paths") or [],
                    "safe_context": {
                        "type": item.get("type"),
                        "visibility": item.get("visibility"),
                        "expected": sanitize(item.get("expected") or {}),
                        "actual": sanitize(item.get("actual") or {}),
                        "diff": sanitize(item.get("diff") or {}),
                    },
                }
            )
        )
    return events


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    return {field: event.get(field) for field in EVENT_FIELDS}


def append_events(events_path: Path, events: list[dict[str, Any]]) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as output:
        for event in events:
            output.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")


def read_events(report_dir: Path) -> list[dict[str, Any]]:
    path = report_dir / "observability" / "events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(normalize_event(payload))
    return events


def record_test_report(report_path: Path, report: dict[str, Any]) -> None:
    events = [event_from_report(report_path, report), *events_from_assertions(report_path, report)]
    events_path = observability_dir_for(report_path) / "events.jsonl"
    append_events(events_path, events)
    report_dir = report_dir_for(report_path)
    refresh_derived(report_dir, read_events(report_dir))
