"""Stable catalog interface for harness assertion metadata."""

from __future__ import annotations

from typing import Any


def mandatory_assertion_catalog() -> tuple[dict[str, dict[str, str]], list[str]]:
    from scripts.harness import executor

    return executor.mandatory_assertion_catalog()


def mandatory_assertion_traceability_matrix() -> tuple[dict[str, dict[str, str]], list[str]]:
    from scripts.harness import executor

    return executor.mandatory_assertion_traceability_matrix()


def catalog_assertion_metadata() -> dict[str, dict[str, str]]:
    from scripts.harness import executor

    return executor.catalog_assertion_metadata()


def assertion_candidates_metadata(report_dir: Any, assertion_ids: list[str]) -> dict[str, Any]:
    from scripts.harness import executor

    return executor.assertion_candidates_metadata(report_dir, assertion_ids)
