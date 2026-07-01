"""Diagnostic queries over harness reports and local observability artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scripts.harness.observability import gate_from, read_events


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def report_path_label(report_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(report_dir).as_posix()
    except ValueError:
        return path.as_posix()


def task_id_from_path(path: Path) -> str | None:
    parts = path.parts
    if "tasks" not in parts:
        return None
    index = parts.index("tasks")
    if len(parts) <= index + 1:
        return None
    return parts[index + 1]


def iter_report_paths(report_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in (
        "stages/*.json",
        "acceptance/ACC-STOP-*.json",
        "acceptance/STOP_ALLOWED.json",
        "acceptance/*coverage.json",
        "tasks/*/*.json",
    ):
        paths.extend(report_dir.glob(pattern))
    return sorted(set(paths))


def load_reports(report_dir: Path) -> list[dict[str, Any]]:
    reports = []
    for path in iter_report_paths(report_dir):
        payload = read_json(path)
        if payload is None:
            continue
        payload["_path"] = report_path_label(report_dir, path)
        payload["_task_id"] = task_id_from_path(path)
        reports.append(payload)
    return reports


def rerun_command(report_dir: Path, stage: str | None, task_id: str | None) -> str:
    if not stage:
        return ""
    command = f"{sys.executable} scripts/run_harness.py --stage {stage}"
    if task_id:
        command += f" --task-id {task_id}"
    command += f" --report-dir {report_dir}"
    return command


def failure_records(report_dir: Path) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for report in load_reports(report_dir):
        stage = report.get("stage")
        task_id = report.get("_task_id")
        report_gate = gate_from(str(report.get("test_id") or ""), str(report.get("_path") or ""))
        assertions = report.get("assertions") if isinstance(report.get("assertions"), list) else []
        for assertion in assertions:
            if not isinstance(assertion, dict) or assertion.get("status") == "passed":
                continue
            failures.append(
                {
                    "report_path": report.get("_path"),
                    "stage": stage,
                    "task_id": task_id,
                    "gate": gate_from(str(assertion.get("id") or ""), report_gate),
                    "assertion_id": assertion.get("id"),
                    "status": assertion.get("status"),
                    "trace_id": report.get("trace_id"),
                    "node": report.get("node"),
                    "failure_type": report.get("failure_type"),
                    "error_category": report.get("error_category"),
                    "referenced_files": report.get("referenced_files") or [],
                    "artifact_paths": report.get("artifact_paths") or [],
                    "rerun_command": rerun_command(report_dir, str(stage), task_id),
                    "diff": assertion.get("diff") or {},
                }
            )
        if report.get("status") not in {"passed", None} and not assertions:
            failures.append(
                {
                    "report_path": report.get("_path"),
                    "stage": stage,
                    "task_id": task_id,
                    "gate": report_gate,
                    "assertion_id": None,
                    "status": report.get("status"),
                    "trace_id": report.get("trace_id"),
                    "node": report.get("node"),
                    "failure_type": report.get("failure_type"),
                    "error_category": report.get("error_category"),
                    "referenced_files": report.get("referenced_files") or [],
                    "artifact_paths": report.get("artifact_paths") or [],
                    "rerun_command": rerun_command(report_dir, str(stage), task_id),
                    "diff": report.get("diff") or {},
                }
            )
    return sorted(
        failures,
        key=lambda item: (
            str(item.get("stage") or ""),
            str(item.get("gate") or ""),
            str(item.get("assertion_id") or ""),
        ),
    )


def summary(report_dir: Path) -> dict[str, Any]:
    reports = load_reports(report_dir)
    stop = read_json(report_dir / "acceptance" / "STOP_ALLOWED.json") or {}
    metrics = read_json(report_dir / "observability" / "metrics.json") or {}
    stage_statuses = {
        str(report.get("stage")): report.get("status")
        for report in reports
        if report.get("_path", "").startswith("stages/")
    }
    return {
        "report_dir": report_dir.as_posix(),
        "STOP_ALLOWED": stop.get("STOP_ALLOWED"),
        "gate_status": stop.get("gate_status") or {},
        "stop_inputs": stop.get("stop_inputs") or {},
        "stage_statuses": stage_statuses,
        "failure_count": len(failure_records(report_dir)),
        "metrics": metrics,
    }


def gate(report_dir: Path, gate_id: str) -> dict[str, Any]:
    path = report_dir / "acceptance" / f"{gate_id}.json"
    payload = read_json(path) or {}
    stop = read_json(report_dir / "acceptance" / "STOP_ALLOWED.json") or {}
    return {
        "gate": gate_id,
        "status": (stop.get("gate_status") or {}).get(gate_id, payload.get("status")),
        "report_path": report_path_label(report_dir, path),
        "assertions": payload.get("assertions") or [],
        "failure_reasons": (payload.get("diff") or {}).get("reasons", []),
    }


def task(report_dir: Path, task_id: str) -> dict[str, Any]:
    reports = [
        report
        for report in load_reports(report_dir)
        if report.get("_path", "").startswith(f"tasks/{task_id}/")
    ]
    return {
        "task_id": task_id,
        "reports": [
            {
                "path": report.get("_path"),
                "stage": report.get("stage"),
                "status": report.get("status"),
                "trace_id": report.get("trace_id"),
            }
            for report in reports
        ],
        "failures": [item for item in failure_records(report_dir) if item.get("task_id") == task_id],
    }


def trace(report_dir: Path, trace_id: str) -> dict[str, Any]:
    events = [event for event in read_events(report_dir) if event.get("trace_id") == trace_id]
    reports = [
        {
            "path": report.get("_path"),
            "stage": report.get("stage"),
            "status": report.get("status"),
            "task_id": report.get("_task_id"),
        }
        for report in load_reports(report_dir)
        if report.get("trace_id") == trace_id
    ]
    return {"trace_id": trace_id, "events": events, "reports": reports}


def to_human(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
