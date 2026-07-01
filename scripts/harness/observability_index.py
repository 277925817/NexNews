"""Derived observability indexes for local harness events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def increment_nested(counter: dict[str, dict[str, int]], key: Any, status: Any) -> None:
    key_text = str(key or "unknown")
    status_text = str(status or "unknown")
    counter.setdefault(key_text, {})
    counter[key_text][status_text] = counter[key_text].get(status_text, 0) + 1


def build_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    reports_by_stage_status: dict[str, dict[str, int]] = {}
    assertions_by_status: dict[str, int] = {}
    failures_by_stage: dict[str, int] = {}
    failures_by_node: dict[str, int] = {}
    for event in events:
        if event["event"] == "test_report_written":
            increment_nested(reports_by_stage_status, event["stage"], event["status"])
        if event["event"] == "assertion_recorded":
            status = str(event["status"] or "unknown")
            assertions_by_status[status] = assertions_by_status.get(status, 0) + 1
        if event["status"] in {"failed", "flaky", "skipped"}:
            stage = str(event["stage"] or "unknown")
            node = str(event["node"] or "unknown")
            failures_by_stage[stage] = failures_by_stage.get(stage, 0) + 1
            failures_by_node[node] = failures_by_node.get(node, 0) + 1
    return {
        "schema_version": "v1",
        "event_count": len(events),
        "reports_by_stage_status": reports_by_stage_status,
        "assertions_by_status": assertions_by_status,
        "failures_by_stage": failures_by_stage,
        "failures_by_node": failures_by_node,
    }


def build_traces(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        trace_id = str(event["trace_id"] or "unknown")
        grouped.setdefault(trace_id, []).append(event)
    return [
        {
            "trace_id": trace_id,
            "event_count": len(trace_events),
            "stages": sorted({str(event["stage"]) for event in trace_events if event["stage"]}),
            "statuses": sorted({str(event["status"]) for event in trace_events if event["status"]}),
            "events": [
                {
                    "event": event["event"],
                    "stage": event["stage"],
                    "task_id": event["task_id"],
                    "gate": event["gate"],
                    "assertion_id": event["assertion_id"],
                    "status": event["status"],
                }
                for event in trace_events
            ],
        }
        for trace_id, trace_events in sorted(grouped.items())
    ]


def build_index(events: list[dict[str, Any]]) -> dict[str, Any]:
    reports = [event for event in events if event["event"] == "test_report_written"]
    failures = [
        event
        for event in events
        if event["status"] in {"failed", "flaky", "skipped"}
        and event["event"] == "assertion_recorded"
    ]
    gates: dict[str, str] = {}
    tasks: dict[str, dict[str, int]] = {}
    for event in reports:
        if event["gate"]:
            gates[str(event["gate"])] = str(event["status"] or "unknown")
        if event["task_id"]:
            task_id = str(event["task_id"])
            tasks.setdefault(task_id, {"reports": 0, "failures": 0})
            tasks[task_id]["reports"] += 1
            if event["status"] != "passed":
                tasks[task_id]["failures"] += 1
    return {
        "schema_version": "v1",
        "report_count": len(reports),
        "failure_count": len(failures),
        "gates": gates,
        "tasks": tasks,
        "failed_assertions": failures,
        "traces": sorted({str(event["trace_id"]) for event in events if event["trace_id"]}),
    }


def refresh_derived(report_dir: Path, events: list[dict[str, Any]]) -> None:
    observability_dir = report_dir / "observability"
    observability_dir.mkdir(parents=True, exist_ok=True)
    (observability_dir / "metrics.json").write_text(
        json.dumps(build_metrics(events), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    traces = build_traces(events)
    (observability_dir / "traces.jsonl").write_text(
        "".join(json.dumps(trace, sort_keys=True, ensure_ascii=False) + "\n" for trace in traces),
        encoding="utf-8",
    )
    (observability_dir / "index.json").write_text(
        json.dumps(build_index(events), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
