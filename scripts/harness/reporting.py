"""Report file writing helpers for the local harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.harness.observability import (
    observability_artifact_paths,
    record_test_report,
)
from scripts.harness.reports import relative_report_path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def unique_paths(paths: list[str]) -> list[str]:
    observed: dict[str, None] = {}
    for path in paths:
        observed.setdefault(path, None)
    return list(observed)


def write_test_report_payload(path: Path, payload: dict[str, Any]) -> None:
    payload["artifact_paths"] = unique_paths(
        [relative_report_path(path), *observability_artifact_paths(path)]
    )
    write_json(path, payload)
    record_test_report(path, payload)
