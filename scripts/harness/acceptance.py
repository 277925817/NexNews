"""Stable acceptance interface for the local harness."""

from __future__ import annotations

from pathlib import Path


def run_acceptance(report_dir: Path, task_id: str | None) -> int:
    from scripts.harness import executor

    return executor.run_acceptance(report_dir, task_id)


def task_completion_evidence() -> dict:
    from scripts.harness import executor

    return executor.task_completion_evidence()


def browser_e2e_stop_input_evidence(report_dir: Path) -> dict:
    from scripts.harness import executor

    return executor.browser_e2e_stop_input_evidence(report_dir)
