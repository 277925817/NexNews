#!/usr/bin/env python3
"""Local Codex Harness command surface.

The harness owns stage reporting and stop-gate evaluation. Product feature
implementation is intentionally out of scope in this file, but all commands and
reports must remain machine-readable and deterministic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


STAGES = [
    "static",
    "unit",
    "contract",
    "api",
    "integration",
    "replay",
    "snapshot",
    "e2e",
    "acceptance",
]
REQUIRED_PRODUCT_STAGES = STAGES[:-1]
REQUIRED_GATES = [f"ACC-STOP-{index:03d}" for index in range(1, 11)]

SCHEMA_REF = "07_test_spec.md#6"
SCHEMA_VERSION = "v2"
FIXTURE_SET = "mvp_acceptance_fixture@v1"
MOCK_SET = "mvp_mock@v1"
CLOCK_SOURCE = "fixed_clock_fixture@v1"
FIXED_TIMESTAMP = "2026-06-28T09:00:00Z"
FIXTURE_VERSION = "mvp_acceptance_fixture@v1"
MOCK_VERSION = "mvp_mock@v1"
REQUIRED_TASK_IDS = {"TASK-000", "TASK-001", "TASK-003", "TASK-021"}

REPORT_VISIBILITY_VALUES = {
    "public_surface",
    "internal_evidence",
    "report_metadata",
}
FORBIDDEN_PUBLIC_FIELDS = {
    "pipeline_state",
    "is_selected",
    "content_raw",
    "content_full",
    "has_translate_failed",
    "deleted_at",
}
FORBIDDEN_CONTEXTUAL_FIELDS = {
    "full_llm_prompt",
    "raw_pipeline_payload",
    "raw_article_body",
}
FORBIDDEN_TOKEN_PATTERNS = {
    "jwt",
    "api_key",
    "secret",
    "password",
}
FORBIDDEN_PATH_PATTERNS = (
    "user/login",
    "search",
    "category",
    "comment",
    "favorite",
    "share",
    "task progress",
    "retry",
    "admin",
    "versioning",
)
REQUIRED_API_ROUTES = {
    ("GET", "/api/home"),
    ("GET", "/api/news/{id}"),
    ("POST", "/api/refresh"),
    ("GET", "/api/sources"),
    ("POST", "/api/sources"),
    ("PATCH", "/api/sources/{id}"),
    ("DELETE", "/api/sources/{id}"),
}
CONTRACT_FRONTEND_ENDPOINTS = {
    "/api/home",
    "/api/news/",
    "/api/refresh",
    "/api/sources",
}
LEGACY_FRONTEND_ENDPOINTS = {
    "/rss",
    "/api/sync",
    "/api/feeds",
    "/api/items",
}
E2E_REQUIRED_SURFACES = [
    "home_news_feed",
    "high_score_list",
    "article_view",
    "sources_page",
    "refresh_action",
]

PRD_FLOW_ASSERTION_MAP = {
    "1.1": [
        "A-api-ACC-STOP-002-default-source-seed",
        "A-api-ACC-STOP-002-default-source-exact-list",
        "A-api-ACC-STOP-002-source-management",
        "A-api-ACC-STOP-002-source-crud-errors",
        "A-api-ACC-STOP-002-default-source-crud-parity",
        "A-integration-ACC-STOP-002-source-ui-crud-parity",
    ],
    "1.2": [
        "A-api-ACC-STOP-002-source-management",
        "A-api-ACC-STOP-002-source-crud-errors",
        "A-api-ACC-STOP-002-source-tombstone-history",
        "A-api-ACC-STOP-002-default-source-crud-parity",
        "A-integration-ACC-STOP-002-source-ui-crud-parity",
    ],
    "2.1": [
        "A-integration-ACC-STOP-003-scheduler-fixed-clock",
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-integration-ACC-STOP-008-live-dependency-blocked",
    ],
    "2.2": [
        "A-api-ACC-STOP-004-refresh-contract",
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-e2e-ACC-STOP-006-refresh-action-browser",
    ],
    "2.3": [
        "A-unit-ACC-STOP-007-llm-request-shapes",
        "A-unit-ACC-STOP-007-llm-retry-failure-policy",
        "A-integration-ACC-STOP-003-threshold-selection",
        "A-unit-ACC-STOP-005-state-machine",
    ],
    "3.1": [
        "A-unit-ACC-STOP-003-rss-normalize-dedupe",
        "A-integration-ACC-STOP-003-dedupe-positive-distinct-items",
        "A-integration-ACC-STOP-003-threshold-selection",
    ],
    "3.2": [
        "A-integration-ACC-STOP-003-fetch-fallback",
        "A-integration-ACC-STOP-003-full-pipeline",
    ],
    "4.1": [
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-unit-ACC-STOP-005-translation-facts",
        "A-api-ACC-STOP-004-home-detail-behavior",
    ],
    "4.2": [
        "A-unit-ACC-STOP-007-llm-schema-validation",
        "A-integration-ACC-STOP-003-fallback-summary-translation",
        "A-integration-ACC-STOP-003-translation-failure-isolated",
        "A-unit-ACC-STOP-005-translation-facts",
    ],
    "5.1": [
        "A-e2e-ACC-STOP-006-home-news-density",
        "A-integration-ACC-STOP-006-ui-render-contract",
        "A-integration-ACC-STOP-006-ui-forbidden-rendering",
        "A-e2e-ACC-STOP-006-news-card-summary-text-only",
        "A-snapshot-ACC-STOP-006-layout-visual-contract",
    ],
    "5.2": [
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-no-direct-original-navigation",
        "A-api-ACC-STOP-004-home-detail-behavior",
    ],
    "6.1": [
        "A-api-ACC-STOP-004-home-detail-behavior",
        "A-e2e-ACC-STOP-006-high-score-list-browser",
        "A-e2e-ACC-STOP-006-home-news-density",
    ],
    "6.2": [
        "A-e2e-ACC-STOP-006-high-score-list-browser",
        "A-e2e-ACC-STOP-006-article-view-browser",
    ],
    "7.1": [
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-article-original-link-button",
        "A-api-ACC-STOP-004-home-detail-behavior",
    ],
    "7.2": [
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-api-ACC-STOP-004-home-detail-behavior",
    ],
    "8.1": [
        "A-unit-ACC-STOP-005-state-machine",
        "A-unit-ACC-STOP-005-translation-facts",
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-api-ACC-STOP-009-api-leak-scan",
    ],
    "8.2": [
        "A-contract-ACC-STOP-005-db-schema",
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-integration-ACC-STOP-008-live-dependency-blocked",
        "A-unit-ACC-STOP-009-log-sanitizer",
    ],
    "8.3": [
        "A-unit-ACC-STOP-007-llm-request-shapes",
        "A-unit-ACC-STOP-007-llm-retry-failure-policy",
        "A-unit-ACC-STOP-007-llm-schema-validation",
        "A-integration-ACC-STOP-003-translation-failure-isolated",
    ],
}

TASK_FALLBACK_ASSERTION_MAP = {
    "TASK-009": [
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-integration-ACC-STOP-003-scheduler-fixed-clock",
        "A-integration-ACC-STOP-008-live-dependency-blocked",
    ],
    "TASK-011": [
        "A-contract-ACC-STOP-004-api-shapes",
        "A-api-ACC-STOP-004-home-detail-behavior",
        "A-api-ACC-STOP-009-api-leak-scan",
    ],
    "TASK-012": [
        "A-contract-ACC-STOP-004-api-shapes",
        "A-api-ACC-STOP-004-home-detail-behavior",
        "A-api-ACC-STOP-009-api-leak-scan",
    ],
    "TASK-015": [
        "A-integration-ACC-STOP-006-ui-render-contract",
        "A-integration-ACC-STOP-006-ui-forbidden-rendering",
        "A-e2e-ACC-STOP-006-home-news-density",
        "A-e2e-ACC-STOP-006-high-score-list-browser",
        "A-e2e-ACC-STOP-006-refresh-action-browser",
        "A-e2e-ACC-STOP-006-news-card-summary-text-only",
    ],
    "TASK-016": [
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-article-original-link-button",
        "A-e2e-ACC-STOP-006-no-direct-original-navigation",
    ],
}

SCHEMA_FILES = {
    "test_report": Path("schemas/test_report.schema.json"),
    "stop_decision": Path("schemas/stop_decision.schema.json"),
    "task_plan_report": Path("schemas/task_plan_report.schema.json"),
    "review_report": Path("schemas/review_report.schema.json"),
    "fix_optimize_report": Path("schemas/fix_optimize_report.schema.json"),
    "round_summary_report": Path("schemas/round_summary_report.schema.json"),
    "tasks": Path("schemas/tasks.schema.json"),
    "prd_coverage": Path("schemas/prd_coverage.schema.json"),
    "task_acceptance_coverage": Path("schemas/task_acceptance_coverage.schema.json"),
    "local_user_acceptance": Path("schemas/local_user_acceptance.schema.json"),
}

MANDATORY_ASSERTION_ROW = re.compile(
    r"^\|\s*`(?P<id>A-(?P<stage>static|unit|contract|api|integration|replay|snapshot|e2e|acceptance)-(?P<gate>ACC-STOP-(?:00[1-9]|010))-[a-z0-9]+(?:-[a-z0-9]+)*)`\s*"
    r"\|\s*(?P<table_stage>static|unit|contract|api|integration|replay|snapshot|e2e|acceptance)\s*"
    r"\|\s*(?P<table_gate>ACC-STOP-(?:00[1-9]|010))\s*"
    r"\|\s*(?P<visibility>public_surface|internal_evidence|report_metadata)\s*\|",
    re.MULTILINE,
)
TRACEABILITY_ROW = re.compile(
    r"^\|\s*`(?P<id>A-(?P<stage>static|unit|contract|api|integration|replay|snapshot|e2e|acceptance)-(?P<gate>ACC-STOP-(?:00[1-9]|010))-[a-z0-9]+(?:-[a-z0-9]+)*)`\s*"
    r"\|\s*(?P<table_gate>ACC-STOP-(?:00[1-9]|010))\s*"
    r"\|\s*(?P<owner_task>TASK-[0-9]{3}[A-Z]?)\s*"
    r"\|\s*(?P<table_stage>static|unit|contract|api|integration|replay|snapshot|e2e|acceptance)\s*"
    r"\|\s*(?P<report_path>[^|]+?)\s*\|",
    re.MULTILINE,
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def report_relative_path(path: Path) -> str:
    parts = path.parts
    for marker in ("stages", "tasks", "acceptance"):
        if marker in parts:
            marker_index = parts.index(marker)
            return Path(*parts[marker_index:]).as_posix()
    return path.as_posix()


def write_test_report(path: Path, payload: dict[str, Any]) -> None:
    payload["artifact_paths"] = [report_relative_path(path)]
    write_json(path, payload)


def stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def leak_detection() -> dict[str, Any]:
    return {
        "method": "structured_field_scan",
        "target": "test_report",
        "forbidden_field_count": 0,
        "sensitive_content_count": 0,
        "matched_paths": [],
    }


def assertion(
    assertion_id: str,
    status: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    diff: dict[str, Any] | None = None,
    visibility: str = "report_metadata",
) -> dict[str, Any]:
    if visibility not in REPORT_VISIBILITY_VALUES:
        raise ValueError(f"unsupported assertion visibility: {visibility}")
    return {
        "id": assertion_id,
        "type": "report_schema",
        "visibility": visibility,
        "status": status,
        "expected": expected,
        "actual": actual,
        "diff": diff or {},
        "leak_detection": leak_detection(),
    }


def test_report(
    *,
    stage: str,
    status: str,
    test_id: str,
    assertions: list[dict[str, Any]],
    expected: dict[str, Any],
    actual: dict[str, Any],
    diff: dict[str, Any] | None = None,
    node: str = "harness",
    failure_type: str | None = None,
    error_category: str | None = None,
    referenced_files: list[str] | None = None,
    commands: list[str] | None = None,
) -> dict[str, Any]:
    report_diff = diff or {}
    report_referenced_files = referenced_files or [
        "scripts/run_harness.py",
        "docs/07_test_spec.md",
    ]
    assertion_statuses = [
        str(item.get("status"))
        for item in assertions
        if isinstance(item, dict)
    ]
    case_count = len(assertion_statuses)
    passed_count = assertion_statuses.count("passed")
    failed_count = assertion_statuses.count("failed")
    skipped_count = assertion_statuses.count("skipped")
    pass_rate = round(passed_count / case_count, 4) if case_count else 0.0
    generated_commands = commands or [
        f"python3 scripts/run_harness.py --stage {stage} --report-dir reports"
    ]
    failure_reasons = [
        str(item.get("id", f"assertion_{index}"))
        for index, item in enumerate(assertions)
        if isinstance(item, dict) and item.get("status") in {"failed", "flaky", "skipped"}
    ]
    report_hash = stable_hash(
        {
            "test_id": test_id,
            "stage": stage,
            "commands": generated_commands,
            "fixture_version": FIXTURE_VERSION,
            "mock_version": MOCK_VERSION,
            "expected": expected,
            "actual": actual,
            "diff": report_diff,
            "assertions": assertions,
        }
    )
    return {
        "schema_ref": SCHEMA_REF,
        "schema_version": SCHEMA_VERSION,
        "test_id": test_id,
        "stage": stage,
        "status": status,
        "failure_type": failure_type,
        "error_category": error_category,
        "trace_id": f"harness-{stage}-{test_id}",
        "fixture_set": FIXTURE_SET,
        "mock_set": MOCK_SET,
        "clock_source": CLOCK_SOURCE,
        "fixture_version": FIXTURE_VERSION,
        "mock_version": MOCK_VERSION,
        "commands": generated_commands,
        "case_count": case_count,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "pass_rate": pass_rate,
        "failure_reasons": failure_reasons,
        "repair_status": "not_required" if status == "passed" else "unresolved",
        "regression_detected": status != "passed",
        "referenced_files": report_referenced_files,
        "data_hash": report_hash,
        "artifact_paths": [],
        "assertions": assertions,
        "expected": expected,
        "actual": actual,
        "diff": report_diff,
        "node": node,
        "timestamp": FIXED_TIMESTAMP,
    }


def report_destination(report_dir: Path, stage: str, task_id: str | None) -> Path:
    if task_id:
        return report_dir / "tasks" / task_id / f"{stage}.json"
    return report_dir / "stages" / f"{stage}.json"


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def read_yaml_object(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = yaml.safe_load(path.read_text())
    except OSError:
        return None, [f"{path.as_posix()}:missing"]
    except yaml.YAMLError as error:
        return None, [f"{path.as_posix()}:invalid_yaml:{error.__class__.__name__}"]
    if not isinstance(payload, dict):
        return None, [f"{path.as_posix()}:not_yaml_object"]
    return payload, []


def read_json_object(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = json.loads(path.read_text())
    except OSError:
        return None, [f"{path.as_posix()}:missing"]
    except json.JSONDecodeError as error:
        return None, [f"{path.as_posix()}:invalid_json:{error.msg}"]
    if not isinstance(payload, dict):
        return None, [f"{path.as_posix()}:not_json_object"]
    return payload, []


def read_report(path: Path) -> dict[str, Any] | None:
    payload, issues = read_json_object(path)
    if issues:
        return None
    return payload


def validate_against_schema(
    payload: dict[str, Any] | None,
    schema_path: Path,
    payload_name: str,
) -> list[str]:
    if payload is None:
        return [f"{payload_name}:missing_payload"]
    schema, issues = read_json_object(schema_path)
    if issues:
        return issues
    try:
        validator = Draft202012Validator(schema)
    except SchemaError as error:
        return [f"{schema_path.as_posix()}:invalid_schema:{error.message}"]
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    return [
        f"{payload_name}:{'/'.join(str(part) for part in error.path) or '$'}:{error.message}"
        for error in errors
    ]


def validate_json_schema_file(schema_path: Path) -> list[str]:
    schema, issues = read_json_object(schema_path)
    if issues:
        return issues
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return [f"{schema_path.as_posix()}:invalid_schema:{error.message}"]
    return []


def validate_test_report(report: dict[str, Any] | None) -> list[str]:
    if report is None:
        return ["missing_report"]

    issues: list[str] = validate_against_schema(
        report,
        SCHEMA_FILES["test_report"],
        "TestReport",
    )
    if report.get("schema_ref") != SCHEMA_REF:
        issues.append("schema_ref_mismatch")
    if report.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version_mismatch")
    test_id = str(report.get("test_id", "")).lower()
    if (
        report.get("status") == "passed"
        and report.get("stage") in REQUIRED_PRODUCT_STAGES
        and ("scaffold" in test_id or "synthetic" in test_id)
    ):
        issues.append("synthetic_or_scaffold_report_cannot_pass")
    if not isinstance(report.get("referenced_files"), list):
        issues.append("referenced_files_missing")
    elif report.get("status") in {"failed", "flaky"} and not report["referenced_files"]:
        issues.append("referenced_files_empty_for_failure")
    if not isinstance(report.get("data_hash"), str) or not report["data_hash"].startswith("sha256:"):
        issues.append("data_hash_invalid")
    if not isinstance(report.get("artifact_paths"), list):
        issues.append("artifact_paths_missing")

    assertions = report.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        issues.append("assertions_missing")
    else:
        for index, item in enumerate(assertions):
            if not isinstance(item, dict):
                issues.append(f"assertion_{index}_not_object")
                continue
            visibility = item.get("visibility")
            if visibility not in REPORT_VISIBILITY_VALUES:
                issues.append(f"assertion_{index}_visibility_invalid")
            for path, key, value in iter_json_paths(item.get("expected")):
                if key and str(key).lower() in FORBIDDEN_CONTEXTUAL_FIELDS:
                    issues.append(f"assertion_{index}_contextual_expected_key_{path}")
                if isinstance(value, str):
                    lowered = value.lower()
                    if len(value) > 1024:
                        issues.append(f"assertion_{index}_long_expected_text_{path}")
                    for pattern in FORBIDDEN_TOKEN_PATTERNS:
                        if pattern in lowered:
                            issues.append(f"assertion_{index}_sensitive_expected_text_{path}")
            for path, key, value in iter_json_paths(item.get("actual")):
                if key and str(key).lower() in FORBIDDEN_CONTEXTUAL_FIELDS:
                    issues.append(f"assertion_{index}_contextual_actual_key_{path}")
                if visibility == "public_surface" and key and key.lower() in FORBIDDEN_PUBLIC_FIELDS:
                    issues.append(f"assertion_{index}_public_forbidden_key_{path}")
                if isinstance(value, str):
                    lowered = value.lower()
                    if len(value) > 1024:
                        issues.append(f"assertion_{index}_long_actual_text_{path}")
                    for pattern in FORBIDDEN_CONTEXTUAL_FIELDS | FORBIDDEN_TOKEN_PATTERNS:
                        if pattern in lowered:
                            issues.append(f"assertion_{index}_sensitive_actual_text_{path}")
            for path, key, value in iter_json_paths(item.get("diff")):
                if key and str(key).lower() in FORBIDDEN_CONTEXTUAL_FIELDS:
                    issues.append(f"assertion_{index}_contextual_diff_key_{path}")
                if isinstance(value, str):
                    lowered = value.lower()
                    if len(value) > 1024:
                        issues.append(f"assertion_{index}_long_diff_text_{path}")
                    for pattern in FORBIDDEN_CONTEXTUAL_FIELDS | FORBIDDEN_TOKEN_PATTERNS:
                        if pattern in lowered:
                            issues.append(f"assertion_{index}_sensitive_diff_text_{path}")
    return issues


def iter_json_paths(value: Any, path: str = "$") -> list[tuple[str, str, Any]]:
    paths: list[tuple[str, str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{path}.{key}"
            paths.append((key_path, str(key), item))
            paths.extend(iter_json_paths(item, key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(iter_json_paths(item, f"{path}[{index}]"))
    else:
        paths.append((path, "", value))
    return paths


def task_nodes(tasks_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(tasks_payload, dict):
        return []
    dag = tasks_payload.get("dag")
    if not isinstance(dag, dict):
        return []
    nodes = dag.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def task_map(tasks_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("id")): node
        for node in task_nodes(tasks_payload)
        if isinstance(node.get("id"), str)
    }


def task_gate_set(task: dict[str, Any]) -> set[str]:
    return {str(gate) for gate in as_list(task.get("acceptance_gate")) if isinstance(gate, str)}


def validate_task_dag_semantics(tasks_payload: dict[str, Any] | None) -> list[str]:
    nodes = task_nodes(tasks_payload)
    if not nodes:
        return ["tasks.md:dag.nodes:missing_or_empty"]

    issues: list[str] = []
    ids = [str(node.get("id")) for node in nodes if isinstance(node.get("id"), str)]
    id_counts = {task_id: ids.count(task_id) for task_id in ids}
    duplicate_ids = sorted(task_id for task_id, count in id_counts.items() if count > 1)
    for task_id in duplicate_ids:
        issues.append(f"tasks.md:dag.nodes:{task_id}:duplicate_id")

    dependency_map: dict[str, list[str]] = {}
    id_set = set(ids)
    for node in nodes:
        task_id = node.get("id")
        if not isinstance(task_id, str):
            continue
        dependencies = [
            str(item)
            for item in as_list(node.get("depends_on"))
            if isinstance(item, str)
        ]
        dependency_map[task_id] = dependencies
        for dependency in dependencies:
            if dependency not in id_set:
                issues.append(f"tasks.md:{task_id}:missing_dependency:{dependency}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visiting:
            cycle = path[path.index(node) :] + [node]
            issues.append(f"tasks.md:dependency_cycle:{'->'.join(cycle)}")
            return
        if node in visited:
            return
        visiting.add(node)
        path.append(node)
        for dependency in dependency_map.get(node, []):
            if dependency in dependency_map:
                visit(dependency, path)
        path.pop()
        visiting.remove(node)
        visited.add(node)

    for task_id in sorted(dependency_map):
        visit(task_id, [])

    for task_id in sorted(id_set - REQUIRED_TASK_IDS):
        if not reaches_task_021(task_id, dependency_map, set()):
            issues.append(f"tasks.md:{task_id}:does_not_depend_on_TASK-021")
    return sorted(issues)


def reaches_task_021(task_id: str, dependency_map: dict[str, list[str]], seen: set[str] | None = None) -> bool:
    if task_id == "TASK-021":
        return True
    seen = seen or set()
    if task_id in seen:
        return False
    seen.add(task_id)
    return any(
        reaches_task_021(dependency, dependency_map, seen)
        for dependency in dependency_map.get(task_id, [])
    )


def mandatory_assertion_catalog() -> tuple[dict[str, dict[str, str]], list[str]]:
    try:
        text = Path("docs/07_test_spec.md").read_text()
    except OSError as error:
        return {}, [f"docs/07_test_spec.md:{error.__class__.__name__}"]

    catalog: dict[str, dict[str, str]] = {}
    issues: list[str] = []
    for match in MANDATORY_ASSERTION_ROW.finditer(text):
        assertion_id = match.group("id")
        stage = match.group("stage")
        table_stage = match.group("table_stage")
        gate = match.group("gate")
        table_gate = match.group("table_gate")
        visibility = match.group("visibility")
        if stage != table_stage:
            issues.append(f"{assertion_id}:stage_mismatch:{stage}!={table_stage}")
        if gate != table_gate:
            issues.append(f"{assertion_id}:gate_mismatch:{gate}!={table_gate}")
        if assertion_id in catalog:
            issues.append(f"{assertion_id}:duplicate")
        catalog[assertion_id] = {
            "stage": table_stage,
            "gate": table_gate,
            "visibility": visibility,
        }
    if not catalog:
        issues.append("mandatory_assertion_catalog_empty")
    covered_gates = {item["gate"] for item in catalog.values()}
    for gate in REQUIRED_GATES:
        if gate not in covered_gates:
            issues.append(f"mandatory_assertion_catalog_missing_gate:{gate}")
    return catalog, issues


def mandatory_assertion_traceability_matrix() -> tuple[dict[str, dict[str, str]], list[str]]:
    try:
        text = Path("docs/07_test_spec.md").read_text()
    except OSError as error:
        return {}, [f"docs/07_test_spec.md:{error.__class__.__name__}"]

    matrix: dict[str, dict[str, str]] = {}
    issues: list[str] = []
    for match in TRACEABILITY_ROW.finditer(text):
        assertion_id = match.group("id")
        stage = match.group("stage")
        table_stage = match.group("table_stage")
        gate = match.group("gate")
        table_gate = match.group("table_gate")
        if stage != table_stage:
            issues.append(f"{assertion_id}:traceability_stage_mismatch:{stage}!={table_stage}")
        if gate != table_gate:
            issues.append(f"{assertion_id}:traceability_gate_mismatch:{gate}!={table_gate}")
        if assertion_id in matrix:
            issues.append(f"{assertion_id}:traceability_duplicate")
        matrix[assertion_id] = {
            "stage": table_stage,
            "gate": table_gate,
            "owner_task": match.group("owner_task"),
            "report_path": match.group("report_path").strip(),
        }
    if not matrix:
        issues.append("mandatory_assertion_traceability_matrix_empty")
    return matrix, issues


def product_assertion_evidence(report_dir: Path) -> dict[str, dict[str, str]]:
    catalog, _ = mandatory_assertion_catalog()
    traceability, _ = mandatory_assertion_traceability_matrix()
    observations = stage_assertions_by_source(report_dir)
    evidence: dict[str, dict[str, str]] = {}
    for assertion_id, details in catalog.items():
        if details["stage"] == "acceptance":
            continue
        for item in observations.get(assertion_id, []):
            if (
                item.get("stage") == details["stage"]
                and item.get("status") == "passed"
                and item.get("visibility") == details["visibility"]
            ):
                traceability_row = traceability.get(assertion_id, {})
                evidence[assertion_id] = {
                    "id": assertion_id,
                    "stage": details["stage"],
                    "gate": details["gate"],
                    "visibility": details["visibility"],
                    "owner_task": traceability_row.get("owner_task", ""),
                    "report_path": traceability_row.get(
                        "report_path",
                        f"reports/stages/{details['stage']}.json",
                    ),
                }
                break
    return evidence


def assertion_candidates_metadata(
    report_dir: Path,
    assertion_ids: list[str],
) -> dict[str, list[str]]:
    catalog, _ = mandatory_assertion_catalog()
    traceability, _ = mandatory_assertion_traceability_matrix()
    evidence = product_assertion_evidence(report_dir)
    known_ids = [assertion_id for assertion_id in assertion_ids if assertion_id in catalog]
    passed_ids = [assertion_id for assertion_id in known_ids if assertion_id in evidence]
    source_rows = [
        evidence.get(assertion_id)
        or {
            "id": assertion_id,
            "stage": catalog[assertion_id]["stage"],
            "gate": catalog[assertion_id]["gate"],
            "visibility": catalog[assertion_id]["visibility"],
            "owner_task": traceability.get(assertion_id, {}).get("owner_task", ""),
            "report_path": traceability.get(assertion_id, {}).get(
                "report_path",
                f"reports/stages/{catalog[assertion_id]['stage']}.json",
            ),
        }
        for assertion_id in known_ids
    ]
    task_ids = sorted(
        {
            row["owner_task"]
            for row in source_rows
            if row.get("owner_task")
        }
    )
    gates = sorted({row["gate"] for row in source_rows if row.get("gate")})
    report_paths = sorted(
        {
            row["report_path"]
            for row in source_rows
            if row.get("report_path")
        }
    )
    return {
        "known_ids": sorted(set(known_ids)),
        "passed_ids": sorted(set(passed_ids)),
        "task_ids": task_ids,
        "gates": gates,
        "report_paths": report_paths,
    }


def traceability_assertions_by_owner() -> dict[str, list[str]]:
    matrix, _ = mandatory_assertion_traceability_matrix()
    catalog, _ = mandatory_assertion_catalog()
    by_owner: dict[str, list[str]] = {}
    for assertion_id, row in matrix.items():
        if assertion_id not in catalog:
            continue
        if catalog[assertion_id]["stage"] == "acceptance":
            continue
        by_owner.setdefault(row["owner_task"], []).append(assertion_id)
    return {owner: sorted(set(assertion_ids)) for owner, assertion_ids in by_owner.items()}


def prd_flow_id(prd_acceptance_id: str) -> str:
    match = re.match(r"^PRD-([0-9]+\.[0-9]+)-AC-[0-9]{3}$", prd_acceptance_id)
    return match.group(1) if match else "0.0"


def validate_mandatory_assertion_traceability(tasks_payload: dict[str, Any] | None) -> list[str]:
    catalog, catalog_issues = mandatory_assertion_catalog()
    matrix, matrix_issues = mandatory_assertion_traceability_matrix()
    tasks_by_id = task_map(tasks_payload)

    issues = [f"catalog:{issue}" for issue in catalog_issues]
    issues.extend(f"traceability:{issue}" for issue in matrix_issues)

    catalog_ids = set(catalog)
    matrix_ids = set(matrix)
    for assertion_id in sorted(catalog_ids - matrix_ids):
        issues.append(f"{assertion_id}:missing_traceability_row")
    for assertion_id in sorted(matrix_ids - catalog_ids):
        issues.append(f"{assertion_id}:traceability_row_without_catalog_entry")

    for assertion_id in sorted(catalog_ids & matrix_ids):
        expected = catalog[assertion_id]
        actual = matrix[assertion_id]
        if actual["gate"] != expected["gate"]:
            issues.append(
                f"{assertion_id}:gate_mismatch:{actual['gate']}!={expected['gate']}"
            )
        if actual["stage"] != expected["stage"]:
            issues.append(
                f"{assertion_id}:stage_mismatch:{actual['stage']}!={expected['stage']}"
            )
        expected_report_path = (
            f"reports/acceptance/{expected['gate']}.json"
            if expected["stage"] == "acceptance"
            else f"reports/stages/{expected['stage']}.json"
        )
        if actual["report_path"] != expected_report_path:
            issues.append(
                f"{assertion_id}:report_path_mismatch:"
                f"{actual['report_path']}!={expected_report_path}"
            )

        owner_task = actual["owner_task"]
        owner = tasks_by_id.get(owner_task)
        if owner is None:
            issues.append(f"{assertion_id}:owner_task_missing:{owner_task}")
            continue
        if expected["gate"] not in task_gate_set(owner):
            issues.append(
                f"{assertion_id}:owner_task_missing_gate:{owner_task}:{expected['gate']}"
            )
    return sorted(issues)


def collect_report_assertions(report_path: Path) -> list[dict[str, Any]]:
    payload = read_report(report_path)
    if not isinstance(payload, dict):
        return []
    assertions = payload.get("assertions")
    if not isinstance(assertions, list):
        return []
    return [item for item in assertions if isinstance(item, dict)]


def read_stage_report(report_dir: Path, stage: str) -> dict[str, Any] | None:
    if stage == "acceptance":
        return None
    return read_report(report_dir / "stages" / f"{stage}.json")


def stage_assertions_by_source(report_dir: Path) -> dict[str, list[dict[str, Any]]]:
    observations: dict[str, list[dict[str, Any]]] = {}
    for stage in REQUIRED_PRODUCT_STAGES:
        for item in collect_report_assertions(report_dir / "stages" / f"{stage}.json"):
            assertion_id = item.get("id")
            if not isinstance(assertion_id, str):
                continue
            record = {"stage": stage, "status": str(item.get("status")), **item}
            observations.setdefault(assertion_id, []).append(record)
    for gate in REQUIRED_GATES:
        gate_report = read_report(report_dir / "acceptance" / f"{gate}.json")
        if not isinstance(gate_report, dict):
            continue
        for item in gate_report.get("assertions", []) if isinstance(gate_report.get("assertions"), list) else []:
            assertion_id = item.get("id")
            if not isinstance(assertion_id, str):
                continue
            record = {"stage": "acceptance", "status": str(item.get("status")), "report": gate_report.get("test_id"), **item}
            observations.setdefault(assertion_id, []).append(record)
    return observations


def mandatory_assertion_coverage(
    report_dir: Path,
    include_acceptance: bool = True,
) -> dict[str, Any]:
    catalog, catalog_issues = mandatory_assertion_catalog()
    seen: dict[str, list[dict[str, str]]] = {}
    observations = stage_assertions_by_source(report_dir)

    for stage in REQUIRED_PRODUCT_STAGES:
        report = read_stage_report(report_dir, stage)
        for assertion_item in collect_report_assertions(report_dir / "stages" / f"{stage}.json"):
            assertion_id = assertion_item.get("id")
            if not isinstance(assertion_id, str):
                continue
            seen.setdefault(assertion_id, []).append(
                {
                    "stage": stage,
                    "status": str(assertion_item.get("status")),
                    "visibility": str(assertion_item.get("visibility")),
                }
            )
    if include_acceptance:
        for gate in REQUIRED_GATES:
            gate_report = read_report(report_dir / "acceptance" / f"{gate}.json")
            if not isinstance(gate_report, dict):
                continue
            for assertion_item in gate_report.get("assertions", []) if isinstance(gate_report.get("assertions"), list) else []:
                assertion_id = assertion_item.get("id")
                if not isinstance(assertion_id, str):
                    continue
                seen.setdefault(assertion_id, []).append(
                    {
                        "stage": "acceptance",
                        "status": str(assertion_item.get("status")),
                        "visibility": str(assertion_item.get("visibility")),
                    }
                )

    missing_ids = sorted(assertion_id for assertion_id in catalog if assertion_id not in seen)
    failed_ids: list[str] = []
    wrong_stage_ids: list[str] = []
    visibility_mismatch_ids: list[str] = []
    conflicting_ids: list[str] = []

    for assertion_id, observations in seen.items():
        expected = catalog.get(assertion_id)
        if not expected:
            continue
        stages = {item["stage"] for item in observations}
        statuses = {item["status"] for item in observations}
        visibilities = {item["visibility"] for item in observations}
        if statuses != {"passed"}:
            failed_ids.append(assertion_id)
        if stages != {expected["stage"]}:
            wrong_stage_ids.append(assertion_id)
        if visibilities != {expected["visibility"]}:
            visibility_mismatch_ids.append(assertion_id)
        if len(stages) > 1 or len(statuses) > 1 or len(visibilities) > 1:
            conflicting_ids.append(assertion_id)

    return {
        "catalog_issues": sorted(catalog_issues),
        "catalog_count": len(catalog),
        "covered_count": len(catalog) - len(missing_ids),
        "missing_ids": missing_ids,
        "failed_ids": sorted(failed_ids),
        "wrong_stage_ids": sorted(wrong_stage_ids),
        "visibility_mismatch_ids": sorted(visibility_mismatch_ids),
        "conflicting_ids": sorted(conflicting_ids),
    }


def required_stage_results(report_dir: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    statuses: dict[str, str] = {}
    schema_issues: dict[str, list[str]] = {}
    for stage in REQUIRED_PRODUCT_STAGES:
        report = read_stage_report(report_dir, stage)
        issues = validate_test_report(report)
        if report is None:
            statuses[stage] = "missing"
            schema_issues[stage] = issues
        elif issues:
            statuses[stage] = "invalid_schema"
            schema_issues[stage] = issues
        else:
            statuses[stage] = report.get("status", "missing")
    return statuses, schema_issues


def required_assertion_ids_for_gate(
    catalog: dict[str, dict[str, str]],
    gate: str,
    include_acceptance: bool = True,
) -> list[str]:
    return sorted(
        [
            assertion_id
            for assertion_id, details in catalog.items()
            if details["gate"] == gate
            and (include_acceptance or details["stage"] != "acceptance")
        ]
    )


def catalog_assertion_metadata() -> dict[str, dict[str, str]]:
    catalog, _ = mandatory_assertion_catalog()
    return catalog


def stage_paths_for_assertions(stage: str) -> list[str]:
    """Minimal required paths used by synthetic product-stage reports.

    These paths represent the minimum implementation footprint expected before a
    stage can be declared implemented.
    """
    common = [
        "docs/01_prd.md",
        "docs/02_arch.md",
        "docs/03_ui_spec.md",
        "docs/04_data_model.md",
        "docs/05_api_contract.md",
        "docs/06_dev_rules.md",
        "docs/07_test_spec.md",
        "docs/08_acceptance.md",
    ]
    mapping = {
        "unit": [
            "backend",
            "backend/app",
            "backend/app/services",
            "backend/app/services/pipeline.py",
            "backend/app/core",
            "fixtures",
            "fixtures/rss",
            "fixtures/rss/feeds.json",
            "fixtures/articles",
            "fixtures/articles/article_map.json",
            "fixtures/llm",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            "fixtures/sources",
            "fixtures/clock",
            "schemas/test_report.schema.json",
            "schemas/stop_decision.schema.json",
            "schemas/task_plan_report.schema.json",
            "schemas/review_report.schema.json",
            "schemas/fix_optimize_report.schema.json",
            "schemas/round_summary_report.schema.json",
            "schemas/tasks.schema.json",
            "schemas/prd_coverage.schema.json",
            "schemas/task_acceptance_coverage.schema.json",
            "schemas/local_user_acceptance.schema.json",
        ],
        "contract": [
            "backend/app",
            "backend/app/repositories",
            "backend/app/db.py",
            "backend/app/main.py",
            "schemas/test_report.schema.json",
            "docs/04_data_model.md",
            "docs/05_api_contract.md",
            "schemas/tasks.schema.json",
        ],
        "api": [
            "backend/app",
            "backend/app/api",
            "backend/app/main.py",
            "backend/app/services",
            "backend/app/services/pipeline.py",
            "backend/app/repositories",
        ],
        "integration": [
            "backend/app",
            "backend/app/services",
            "backend/app/services/pipeline.py",
            "backend/app/repositories",
            "backend/app/clients",
            "fixtures/rss",
            "fixtures/rss/feeds.json",
            "fixtures/articles",
            "fixtures/articles/article_map.json",
            "fixtures/llm",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            "fixtures/clock",
        ],
        "replay": [
            "fixtures",
            "fixtures/rss",
            "fixtures/rss/feeds.json",
            "fixtures/llm",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            "fixtures/clock",
            "backend/app",
            "backend/app/services/pipeline.py",
        ],
        "snapshot": [
            "frontend",
            "frontend/src",
            "frontend/src/api",
            "frontend/src/pages",
            "frontend/src/components",
        ],
        "e2e": [
            "frontend",
            "frontend/src",
            "backend/app/main.py",
            "backend/app/services/pipeline.py",
            "fixtures",
            "fixtures/rss/feeds.json",
            "fixtures/articles/article_map.json",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            ".github",
            "reports",
        ],
    }
    return sorted(set(common + mapping.get(stage, [])))


def stage_implementation_evidence(stage: str) -> tuple[bool, list[str], list[str]]:
    required = stage_paths_for_assertions(stage)
    missing = [path for path in required if not Path(path).exists()]
    exists = [path for path in required if path not in missing]
    return len(missing) == 0, missing, exists


def backend_api_route_evidence() -> dict[str, Any]:
    repo_root = Path.cwd().resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from backend.app.main import app
    except Exception as error:
        return {
            "imported": False,
            "routes": [],
            "missing_required_routes": [
                f"{method} {path}" for method, path in sorted(REQUIRED_API_ROUTES)
            ],
            "issues": [f"backend.app.main_import_failed:{error.__class__.__name__}"],
        }

    route_pairs: set[tuple[str, str]] = set()
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            route_pairs.add((str(method), str(path)))

    missing = sorted(REQUIRED_API_ROUTES - route_pairs)
    return {
        "imported": True,
        "routes": [f"{method} {path}" for method, path in sorted(route_pairs)],
        "missing_required_routes": [
            f"{method} {path}" for method, path in missing
        ],
        "issues": [
            f"missing_required_route:{method} {path}" for method, path in missing
        ],
    }


def import_backend_app() -> tuple[Any | None, str | None]:
    repo_root = Path.cwd().resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from backend.app.main import create_app
    except Exception as error:
        return None, f"backend.app.main_import_failed:{error.__class__.__name__}"
    return create_app(db_path=":memory:"), None


def envelope_issue(
    *,
    name: str,
    response: Any,
    expected_status: int,
    expected_envelope: str,
    required_data_keys: set[str] | None = None,
) -> list[str]:
    issues: list[str] = []
    if response.status_code != expected_status:
        issues.append(f"{name}:status={response.status_code}!={expected_status}")
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        issues.append(f"{name}:content_type_not_json:{content_type}")
        return issues
    try:
        payload = response.json()
    except ValueError:
        issues.append(f"{name}:invalid_json")
        return issues
    if not isinstance(payload, dict):
        issues.append(f"{name}:payload_not_object")
        return issues
    if expected_envelope not in payload:
        issues.append(f"{name}:missing_{expected_envelope}_envelope")
    if "detail" in payload:
        issues.append(f"{name}:fastapi_detail_leak")
    if expected_envelope == "data" and required_data_keys:
        data = payload.get("data")
        if not isinstance(data, dict):
            issues.append(f"{name}:data_not_object")
        else:
            missing_keys = sorted(required_data_keys - set(data))
            for key in missing_keys:
                issues.append(f"{name}:missing_data_key:{key}")
        if expected_envelope == "error":
            error = payload.get("error")
            if not isinstance(error, dict):
                issues.append(f"{name}:error_not_object")
            else:
                for key in ("code", "message"):
                    if key not in error:
                        issues.append(f"{name}:missing_error_key:{key}")
    return issues


def _safe_json(response: Any) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = response.json()
    except ValueError:
        return None, ["response:not_json"]
    if not isinstance(payload, dict):
        return None, ["response:payload_not_object"]
    return payload, []


def _safe_text_read(path: Path, label: str) -> tuple[str, list[str]]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore"), []
    except OSError as error:
        return "", [f"{label}:{error.__class__.__name__}"]


def _extract_index_script() -> tuple[str, list[str]]:
    html, issues = _safe_text_read(Path("index.html"), "index_html")
    if issues:
        return "", issues
    match = re.search(r"<script>(.*?)</script>", html, re.S | re.I)
    if not match:
        return "", ["index_html:script_not_found"]
    return match.group(1), []


def _contains_js_pattern(script: str, pattern: str) -> bool:
    return re.search(pattern, script, re.I | re.S) is not None


def source_management_api_evidence() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"checks": {}, "issues": [import_issue]}

    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {
            "checks": {},
            "issues": [f"fastapi_testclient_import_failed:{error.__class__.__name__}"],
        }

    client = TestClient(app)
    issues: list[str] = []
    checks: dict[str, Any] = {}

    def add_issue(message: str) -> None:
        issues.append(f"source_api:{message}")

    def request_json(
        label: str,
        response: Any,
        expected_status: int,
        *,
        allow_empty_body: bool = False,
    ) -> tuple[dict[str, Any] | None, int]:
        if response.status_code != expected_status:
            add_issue(f"{label}:status={response.status_code}!={expected_status}")
        if allow_empty_body and response.content.strip() == b"":
            return {"_empty_body": True}, response.status_code
        payload, parse_issues = _safe_json(response)
        for issue in parse_issues:
            add_issue(f"{label}:{issue}")
        if payload is None:
            return None, response.status_code
        return payload, response.status_code

    def db_row(source_id: str) -> dict[str, Any] | None:
        return app.state.db.execute(
            "SELECT id, name, is_enabled, deleted_at, rss_url FROM source WHERE id = ?",
            (source_id,),
        ).fetchone()

    # 1) Baseline: exactly 7 seeded defaults
    seeds_payload, _ = request_json("sources_list_initial", client.get("/api/sources"), 200)
    if seeds_payload is None:
        return {"checks": checks, "issues": issues}
    seed_data = seeds_payload.get("data") if isinstance(seeds_payload, dict) else None
    if not isinstance(seed_data, list):
        add_issue("sources_list_initial:data_not_list")
        seed_data = []

    seed_count = len(seed_data)
    checks["seed_count"] = seed_count
    if seed_count != 7:
        add_issue(f"sources_seed_count={seed_count}!=7")

    seed_ids = [str(item.get("id")) for item in seed_data if isinstance(item, dict)]
    if not seed_ids:
        add_issue("sources_seed_ids_missing")
        return {"checks": checks, "issues": issues}

    default_id = seed_ids[0]

    # 2) Create and validate a user source
    create_payload = {
        "name": "local_user_source",
        "rss_url": "https://example.com/rss-updated.xml",
    }
    create_payload_response, _ = request_json(
        "source_create_user",
        client.post("/api/sources", json=create_payload),
        201,
    )
    user_id = None
    if isinstance(create_payload_response, dict):
        data = create_payload_response.get("data")
        if isinstance(data, dict) and data.get("id") is not None:
            user_id = str(data["id"])
        else:
            add_issue("source_create_user:missing_created_id")

    if user_id is None:
        add_issue("source_create_user:failed")
        return {"checks": checks, "issues": issues}

    checks["user_source_id"] = user_id

    duplicate_payload, _ = request_json(
        "source_create_duplicate_user",
        client.post("/api/sources", json=create_payload),
        409,
    )
    if duplicate_payload is None:
        add_issue("source_create_duplicate_user:missing_payload")

    # 3) Last-enabled-source protection on both source classes
    all_source_ids = seed_ids + [user_id]
    if len(all_source_ids) < 2:
        add_issue("source_last_enabled:insufficient_sources")
    else:
        keeper = all_source_ids[-1]
        for source_id in all_source_ids:
            if source_id == keeper:
                continue
            patch_payload, patch_status = request_json(
                f"source_disable_for_last_guard_{source_id}",
                client.patch(
                    f"/api/sources/{source_id}",
                    json={"is_enabled": False},
                ),
                200,
            )
            if patch_payload and isinstance(patch_payload.get("data", {}).get("id"), str):
                checks[f"source_disable_{source_id}"] = patch_payload["data"]["is_enabled"]

        final_disable_payload, final_disable_status = request_json(
            f"source_disable_last_enabled_guard_{keeper}",
            client.patch(
                f"/api/sources/{keeper}",
                json={"is_enabled": False},
            ),
            409,
        )
        checks["last_enabled_guard_status"] = final_disable_status
        for source_id in all_source_ids:
            client.patch(
                f"/api/sources/{source_id}",
                json={"is_enabled": True},
            )

    # 4) Default/source user parity for disable + soft-delete semantics
    for source_id in (default_id, user_id):
        disable_payload, _ = request_json(
            f"source_disable_parity_{source_id}",
            client.patch(f"/api/sources/{source_id}", json={"is_enabled": False}),
            200,
        )
        if disable_payload and isinstance(disable_payload.get("data"), dict):
            checks[f"source_disable_returned_{source_id}"] = disable_payload["data"].get(
                "is_enabled"
            )

    current_sources_payload, _ = request_json(
        "sources_list_after_disable",
        client.get("/api/sources"),
        200,
    )
    current_sources = current_sources_payload.get("data") if isinstance(current_sources_payload, dict) else []
    current_ids = [str(item.get("id")) for item in current_sources if isinstance(item, dict)]
    current_map = {str(item.get("id")): item for item in current_sources if isinstance(item, dict)}
    for source_id in (default_id, user_id):
        checks[f"source_disable_returned_visible_{source_id}"] = source_id in current_ids
        if source_id not in current_ids:
            add_issue(f"source_parity_missing_after_disable:{source_id}")
            continue
        current_item = current_map.get(source_id, {})
        if bool(current_item.get("is_enabled")):
            add_issue(f"source_parity_disable_not_applied:{source_id}")

    for source_id in (default_id, user_id):
        delete_payload, _ = request_json(
            f"source_delete_parity_{source_id}",
            client.delete(f"/api/sources/{source_id}"),
            204,
            allow_empty_body=True,
        )
        del(delete_payload)
        post_delete_payload, _ = request_json(
            f"sources_list_after_delete_{source_id}",
            client.get("/api/sources"),
            200,
        )
        post_delete_sources = post_delete_payload.get("data") if isinstance(post_delete_payload, dict) else []
        post_delete_ids = {
            str(item.get("id")) for item in post_delete_sources if isinstance(item, dict)
        }
        if source_id in post_delete_ids:
            add_issue(f"source_parity_visible_after_delete:{source_id}")
        row = db_row(source_id)
        checks[f"source_delete_row_{source_id}"] = {
            "deleted": row is not None,
            "is_enabled": None if row is None else row.get("is_enabled"),
            "tombstone_present": bool(row and row.get("deleted_at")),
        }
        if row is None:
            add_issue(f"source_delete_tombstone_missing:{source_id}")
        else:
            if int(row.get("is_enabled", 1)) != 0:
                add_issue(f"source_delete_tombstone_not_disabled:{source_id}")
            if not row.get("deleted_at"):
                add_issue(f"source_delete_tombstone_not_set:{source_id}")

    return {"checks": checks, "issues": issues}


def source_ui_parity_evidence() -> dict[str, Any]:
    script, script_issues = _extract_index_script()
    issues: list[str] = list(script_issues)
    checks: dict[str, Any] = {}
    if not script:
        issues.append("source_ui:no_index_script")
        return {"checks": checks, "issues": issues}

    checks["source_ui_controls"] = {
        "has_render_source_list": _contains_js_pattern(
            script, r"function\s+renderSourceList\s*\(feeds\)"
        ),
        "has_update_flow": _contains_js_pattern(
            script, r"updateFeed\(feed\.id,\s*!feed\.is_enabled\)"
        ),
        "has_delete_flow": _contains_js_pattern(
            script,
            r"deleteFeed\(feed\.id\)",
        ),
        "has_create_flow": _contains_js_pattern(
            script, r'feedForm\.addEventListener\("submit",\s*addFeed\)'
        ),
        "has_navigation_to_config": _contains_js_pattern(
            script,
            r"openConfig\.addEventListener\(\s*['\"]click['\"],",
        ),
    }

    if not checks["source_ui_controls"]["has_render_source_list"]:
        issues.append("source_ui_missing:render_source_list")
    if not checks["source_ui_controls"]["has_update_flow"]:
        issues.append("source_ui_missing:update_flow")
    if not checks["source_ui_controls"]["has_delete_flow"]:
        issues.append("source_ui_missing:delete_flow")
    if not checks["source_ui_controls"]["has_create_flow"]:
        issues.append("source_ui_missing:create_flow")

    if _contains_js_pattern(script, r"feed\.is_default"):
        issues.append("source_ui_condition_detected:default_source_branch")

    return {"checks": checks, "issues": issues}


def e2e_surface_evidence() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {
            "checks": {"runner": "e2e_probe"},
            "issues": [import_issue],
        }

    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {
            "checks": {"runner": "e2e_probe"},
            "issues": [f"fastapi_testclient_import_failed:{error.__class__.__name__}"],
        }

    client = TestClient(app)
    issues: list[str] = []
    checks: dict[str, Any] = {"runner": "api_and_index_probe"}
    surface_checks = {
        "home_news_feed": False,
        "high_score_list": False,
        "article_view": False,
        "sources_page": False,
        "refresh_action": False,
    }

    def add_issue(message: str) -> None:
        issues.append(f"e2e_surface:{message}")

    script, script_read_issues = _extract_index_script()
    if script_read_issues:
        issues.extend([f"e2e_surface:{item}" for item in script_read_issues])

    refresh_response = client.post("/api/refresh")
    refresh_payload, refresh_parse_issues = _safe_json(refresh_response)
    checks["refresh_status"] = refresh_response.status_code
    for issue in refresh_parse_issues:
        issues.append(f"e2e_surface:refresh:{issue}")
    if refresh_payload is None:
        issues.append("e2e_surface:refresh_payload_invalid")
        checks["refresh_action_called"] = False
    else:
        checks["refresh_payload"] = {
            "has_refreshed_at": isinstance(refresh_payload.get("data"), dict)
            and refresh_payload["data"].get("refreshed_at") is not None,
            "data_fields": sorted(
                refresh_payload["data"].keys()
            )
            if isinstance(refresh_payload.get("data"), dict)
            else [],
        }
        checks["refresh_action_called"] = (
            checks["refresh_status"] == 200 and checks["refresh_payload"]["has_refreshed_at"]
        )
    if not checks["refresh_action_called"]:
        issues.append(f"e2e_surface:refresh_action_failed:{checks['refresh_status']}")
    else:
        surface_checks["refresh_action"] = True

    home_response = client.get("/api/home")
    home_payload, home_parse_issues = _safe_json(home_response)
    checks["home_status"] = home_response.status_code
    for issue in home_parse_issues:
        issues.append(f"e2e_surface:home:{issue}")
    if home_payload is None:
        issues.append("e2e_surface:home_payload_invalid")
        return {"checks": checks, "issues": issues}

    home_data = home_payload.get("data", {}) if isinstance(home_payload, dict) else {}
    latest_news = home_data.get("latest_news") if isinstance(home_data, dict) else None
    top_news = home_data.get("top_ranked_news") if isinstance(home_data, dict) else None
    if not isinstance(latest_news, list):
        issues.append("e2e_surface:latest_news_invalid")
        latest_news = []
    if not isinstance(top_news, list):
        issues.append("e2e_surface:top_ranked_news_invalid")
        top_news = []
    checks["home_news_count"] = len(latest_news)
    checks["top_news_count"] = len(top_news)
    checks["home_news_density_ok"] = len(latest_news) >= 10
    checks["high_score_list_reasonable_size"] = len(top_news) <= 10
    surface_checks["home_news_feed"] = len(latest_news) >= 10
    if not latest_news:
        issues.append("e2e_surface:latest_news_empty")
    if not top_news:
        issues.append("e2e_surface:top_ranked_news_empty")

    article_statuses: set[str] = set()
    if latest_news:
        checks["news_cards_have_summary_fields"] = any(
            isinstance(item, dict)
            and ("summary_zh" in item or "content_zh" in item)
            for item in latest_news
        )
        if checks["news_cards_have_summary_fields"]:
            issues.append("e2e_surface:news_card_summary_leak_in_list")
        for item in latest_news:
            if isinstance(item, dict) and isinstance(item.get("status"), str):
                article_statuses.add(str(item["status"]))
            if isinstance(item, dict) and item.get("status") not in {"translated", "translation_failed", "ready"}:
                issues.append("e2e_surface:unexpected_article_status")
        checks["article_statuses"] = sorted(article_statuses)
        checks["has_translation_failed_state"] = "translation_failed" in article_statuses
    else:
        checks["article_statuses"] = []
        checks["has_translation_failed_state"] = False

    if latest_news:
        first_item = latest_news[0]
        item_id = str(first_item.get("id", "")) if isinstance(first_item, dict) else ""
        checks["sample_item_id"] = item_id
        article_view_verified = False
        if item_id:
            article_response = client.get(f"/api/news/{item_id}")
            article_payload, article_parse_issues = _safe_json(article_response)
            checks["article_status"] = article_response.status_code
            for issue in article_parse_issues:
                issues.append(f"e2e_surface:article:{issue}")
            if article_payload is None:
                add_issue("article_payload_invalid")
            else:
                article_data = article_payload.get("data", {}) if isinstance(article_payload, dict) else {}
                checks["article_view_has_original_url"] = isinstance(article_data, dict) and bool(
                    article_data.get("original_url")
                )
                if not checks["article_view_has_original_url"]:
                    add_issue("article_missing_original_url")
                checks["article_view_has_translation_fields"] = (
                    "summary_zh" in article_data and "content_zh" in article_data
                    if isinstance(article_data, dict)
                    else False
                )
                article_view_verified = isinstance(article_data, dict) and bool(
                    article_data.get("id") == item_id
                    and checks["article_view_has_original_url"]
                )
        surface_checks["article_view"] = article_view_verified

    not_found_response = client.get("/api/news/__does_not_exist__")
    not_found_payload, not_found_parse_issues = _safe_json(not_found_response)
    checks["article_not_found_status"] = not_found_response.status_code
    for issue in not_found_parse_issues:
        issues.append(f"e2e_surface:article_not_found:{issue}")
    if not_found_response.status_code != 404:
        issues.append("e2e_surface:article_not_found_status_not_404")
    if not isinstance(not_found_payload, dict):
        issues.append("e2e_surface:article_not_found_payload_not_object")
    elif not isinstance(not_found_payload.get("error"), dict):
        issues.append("e2e_surface:article_not_found_error_shape_invalid")

    checks["sources_loaded"] = 0
    sources_response = client.get("/api/sources")
    sources_payload, sources_parse_issues = _safe_json(sources_response)
    checks["sources_status"] = sources_response.status_code
    for issue in sources_parse_issues:
        issues.append(f"e2e_surface:sources:{issue}")
    if sources_payload is None:
        issues.append("e2e_surface:sources_payload_invalid")
    else:
        source_data = sources_payload.get("data") if isinstance(sources_payload, dict) else []
        if not isinstance(source_data, list):
            issues.append("e2e_surface:sources_data_not_list")
        checks["sources_loaded"] = len(source_data) if isinstance(source_data, list) else 0
        if not isinstance(source_data, list) or not source_data:
            issues.append("e2e_surface:sources_empty")
        checks["sources_are_visible_after_disable_expected"] = not any(
            item.get("deleted_at") for item in source_data if isinstance(item, dict)
        )
        surface_checks["sources_page"] = isinstance(source_data, list) and bool(source_data)
    if top_news:
        top_scores = [
            int(item.get("score"))
            for item in top_news
            if isinstance(item, dict) and isinstance(item.get("score"), int)
        ]
        checks["top_score_sorted_desc"] = top_scores == sorted(top_scores, reverse=True)
        if top_scores != sorted(top_scores, reverse=True):
            issues.append("e2e_surface:top_ranked_news_not_desc")
    else:
        checks["top_score_sorted_desc"] = False
    checks["high_score_list_sorted_desc"] = checks["top_score_sorted_desc"]
    surface_checks["high_score_list"] = bool(top_news) and checks["top_score_sorted_desc"]

    if script:
        checks["script_patterns"] = {
            "card_to_article_internal_route": _contains_js_pattern(
                script,
                r"titleButton\.addEventListener\(\s*['\"]click['\"]\s*,\s*\(\)\s*=>\s*navigate\(itemHash\(item\.id\)\)",
            ),
            "reader_original_link_button": _contains_js_pattern(
                script,
                r"originalLink\.href\s*=\s*item\.original_url",
            ),
            "reader_original_link_fallback": _contains_js_pattern(
                script,
                r"originalLink\.href\s*=\s*item\.originalLink \|\| item\.link",
            ),
            "refresh_button_triggers_sync": _contains_js_pattern(
                script,
                r"refresh\.addEventListener\(\s*['\"]click['\"],\s*syncNow\s*\)",
            ),
            "sources_page_render": _contains_js_pattern(
                script,
                r"function\s+renderSourceList\(feeds\)",
            ),
            "feed_submit_flow": _contains_js_pattern(
                script,
                r"feedForm\.addEventListener\(\s*['\"]submit['\"],\s*addFeed\)",
            ),
            "sources_update_flow": _contains_js_pattern(
                script,
                r"function\s+updateFeed\(feedId,\s*isEnabled\)",
            ),
            "sources_delete_flow": _contains_js_pattern(
                script,
                r"function\s+deleteFeed\(feedId\)",
            ),
            "no_direct_navigation_assignment": not _contains_js_pattern(
                script,
                r"window\.location\s*=",
            ),
        }
        pattern_issues = [
            (name, value)
            for name, value in checks["script_patterns"].items()
            if isinstance(value, bool) and value is False and name != "no_direct_navigation_assignment"
        ]
        for name, _ in pattern_issues:
            issues.append(f"e2e_surface:missing_script_pattern:{name}")
        if not checks["script_patterns"]["no_direct_navigation_assignment"]:
            issues.append("e2e_surface:direct_navigation_detected")

    checks["surface_coverage"] = surface_checks
    checks["required_surfaces"] = E2E_REQUIRED_SURFACES
    for surface in checks["required_surfaces"]:
        if not surface_checks.get(surface, False):
            issues.append(f"e2e_surface:surface_not_verified:{surface}")
    return {"checks": checks, "issues": issues}


def backend_api_response_evidence() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {
            "imported": False,
            "checks": [],
            "issues": [import_issue],
        }
    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {
            "imported": True,
            "checks": [],
            "issues": [f"fastapi_testclient_import_failed:{error.__class__.__name__}"],
        }

    client = TestClient(app)
    refresh_response = client.post("/api/refresh")
    translated_row = app.state.db.execute(
        "SELECT id FROM news_item WHERE rss_guid = 'fixture-translated-96'"
    ).fetchone()
    translated_detail_path = (
        f"/api/news/{translated_row['id']}"
        if translated_row is not None
        else "/api/news/__missing_translated_fixture__"
    )
    checks = [
        (
            "post_refresh",
            refresh_response,
            200,
            "data",
            {"refreshed_at"},
        ),
        (
            "get_home",
            client.get("/api/home"),
            200,
            "data",
            {"latest_news", "top_ranked_news"},
        ),
        (
            "get_translated_news_detail",
            client.get(translated_detail_path),
            200,
            "data",
            {"id", "title", "summary_zh", "content_zh", "status"},
        ),
        (
            "get_sources",
            client.get("/api/sources"),
            200,
            "data",
            None,
        ),
        (
            "get_missing_news",
            client.get("/api/news/missing"),
            404,
            "error",
            None,
        ),
        (
            "post_invalid_source",
            client.post("/api/sources", json={"name": "", "rss_url": "not-a-url"}),
            400,
            "error",
            None,
        ),
        (
            "get_unknown_api",
            client.get("/api/unknown"),
            404,
            "error",
            None,
        ),
    ]

    observations: list[dict[str, Any]] = []
    issues: list[str] = []
    for name, response, expected_status, expected_envelope, required_data_keys in checks:
        check_issues = envelope_issue(
            name=name,
            response=response,
            expected_status=expected_status,
            expected_envelope=expected_envelope,
            required_data_keys=required_data_keys,
        )
        observations.append(
            {
                "name": name,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "expected_envelope": expected_envelope,
                "issues": check_issues,
            }
        )
        issues.extend(check_issues)
        if name == "get_home" and not check_issues:
            payload = response.json()
            latest_news = payload["data"].get("latest_news", [])
            top_ranked_news = payload["data"].get("top_ranked_news", [])
            if not latest_news:
                issues.append("get_home:latest_news_empty_after_refresh")
            if not top_ranked_news:
                issues.append("get_home:top_ranked_news_empty_after_refresh")
            for list_name, items in {
                "latest_news": latest_news,
                "top_ranked_news": top_ranked_news,
            }.items():
                for field in ("summary_zh", "content_zh"):
                    if any(isinstance(item, dict) and field in item for item in items):
                        issues.append(f"get_home:{list_name}:leaked_{field}")
        if name == "get_translated_news_detail" and not check_issues:
            payload = response.json()
            detail = payload["data"]
            if detail.get("status") != "translated":
                issues.append("get_translated_news_detail:status_not_translated")

    return {
        "imported": True,
        "checks": observations,
        "issues": issues,
    }


def pipeline_projection_snapshot() -> tuple[dict[str, Any], list[str]]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {}, [import_issue]
    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {}, [f"fastapi_testclient_import_failed:{error.__class__.__name__}"]

    client = TestClient(app)
    response = client.post("/api/refresh")
    issues = envelope_issue(
        name="pipeline_post_refresh",
        response=response,
        expected_status=200,
        expected_envelope="data",
        required_data_keys={"refreshed_at"},
    )
    conn = app.state.db
    rows = conn.execute(
        """
        SELECT
          rss_guid, canonical_url, score, pipeline_state, is_selected,
          title_zh, summary_zh, content_zh, has_translate_failed,
          content_full, content_raw
        FROM news_item
        ORDER BY canonical_url ASC
        """
    ).fetchall()
    expected_guids = {
        "fixture-low-59",
        "fixture-threshold-60",
        "fixture-translated-96",
        "fixture-translate-partial",
        "fixture-rank-95",
        "fixture-rank-94",
        "fixture-rank-93",
        "fixture-rank-92",
        "fixture-rank-91",
        "fixture-rank-90",
        "fixture-rank-89",
        "fixture-old-high-99",
    }
    observed_guids = {str(row["rss_guid"]) for row in rows}
    if not expected_guids.issubset(observed_guids):
        issues.append(
            "pipeline:fixture_guid_set_missing:"
            f"{sorted(expected_guids - observed_guids)}"
        )
    if len(rows) < 12:
        issues.append(f"pipeline:news_item_count={len(rows)}<12")

    by_guid = {str(row["rss_guid"]): row for row in rows}
    threshold = by_guid.get("fixture-threshold-60")
    if (
        not threshold
        or threshold["score"] != 60
        or threshold["pipeline_state"] != "fetched"
        or threshold["is_selected"] != 1
    ):
        issues.append("pipeline:threshold_60_not_selected_and_fetched")

    low_score = by_guid.get("fixture-low-59")
    if (
        not low_score
        or low_score["score"] != 59
        or low_score["pipeline_state"] != "scored"
        or low_score["is_selected"] != 0
    ):
        issues.append("pipeline:score_59_not_filtered_at_scored_state")

    translated = by_guid.get("fixture-translated-96")
    if not translated or not all(
        translated[field] for field in ("title_zh", "summary_zh", "content_zh")
    ):
        issues.append("pipeline:translated_fixture_missing_chinese_fields")

    failed_translation = by_guid.get("fixture-translate-partial")
    if (
        not failed_translation
        or failed_translation["has_translate_failed"] != 1
        or any(
            failed_translation[field]
            for field in ("title_zh", "summary_zh", "content_zh")
        )
    ):
        issues.append("pipeline:partial_translation_not_isolated")

    log_counts = conn.execute(
        """
        SELECT stage, success, COUNT(*) AS count
        FROM processing_log
        GROUP BY stage, success
        ORDER BY stage ASC, success ASC
        """
    ).fetchall()
    log_summary = {
        f"{row['stage']}:{row['success']}": int(row["count"])
        for row in log_counts
    }
    if log_summary.get("crawl:0", 0) < 1:
        issues.append("pipeline:crawl_failure_fixture_not_logged")
    if log_summary.get("score:1", 0) < 12:
        issues.append("pipeline:score_success_count_less_than_12")
    if log_summary.get("fetch:1", 0) < 2 or log_summary.get("fetch:0", 0) < 1:
        issues.append("pipeline:fetch_success_and_fallback_not_logged")
    if log_summary.get("translate:1", 0) < 1 or log_summary.get("translate:0", 0) < 1:
        issues.append("pipeline:translation_success_and_failure_not_logged")

    home_response = client.get("/api/home")
    issues.extend(
        envelope_issue(
            name="pipeline_get_home",
            response=home_response,
            expected_status=200,
            expected_envelope="data",
            required_data_keys={"latest_news", "top_ranked_news"},
        )
    )
    latest_titles: list[str] = []
    ranked_scores: list[int] = []
    if home_response.status_code == 200:
        payload = home_response.json()
        latest_news = payload["data"].get("latest_news", [])
        top_ranked_news = payload["data"].get("top_ranked_news", [])
        latest_titles = [
            str(item.get("original_title"))
            for item in latest_news
            if isinstance(item, dict)
        ]
        ranked_scores = [
            int(item.get("score"))
            for item in top_ranked_news
            if isinstance(item, dict) and isinstance(item.get("score"), int)
        ]
        ranked_titles = [
            str(item.get("original_title"))
            for item in top_ranked_news
            if isinstance(item, dict)
        ]
        if "Low signal AI funding rumor" in latest_titles:
            issues.append("pipeline:score_59_visible_in_home")
        if len(ranked_scores) != 10:
            issues.append(f"pipeline:high_score_list_count={len(ranked_scores)}!=10")
        if ranked_scores != sorted(ranked_scores, reverse=True):
            issues.append(f"pipeline:ranked_scores_not_desc:{ranked_scores}")
        if "Older AI milestone outside ranking window" in ranked_titles:
            issues.append("pipeline:old_high_score_visible_in_30_day_ranking")

    snapshot = {
        "guids": sorted(observed_guids),
        "state_by_guid": {
            guid: {
                "score": row["score"],
                "state": row["pipeline_state"],
                "selected": bool(row["is_selected"]),
                "has_full_text": bool(row["content_full"]),
                "has_raw_text": bool(row["content_raw"]),
                "has_translation": all(
                    row[field] for field in ("title_zh", "summary_zh", "content_zh")
                ),
                "translation_failed": bool(row["has_translate_failed"]),
            }
            for guid, row in sorted(by_guid.items())
        },
        "log_summary": log_summary,
        "latest_titles": latest_titles,
        "ranked_scores": ranked_scores,
    }
    return snapshot, issues


def pipeline_refresh_evidence() -> dict[str, Any]:
    snapshot, issues = pipeline_projection_snapshot()
    return {
        "checks": snapshot,
        "issues": issues,
    }


def pipeline_replay_evidence() -> dict[str, Any]:
    first_snapshot, first_issues = pipeline_projection_snapshot()
    second_snapshot, second_issues = pipeline_projection_snapshot()
    first_hash = stable_hash({"pipeline_snapshot": first_snapshot})
    second_hash = stable_hash({"pipeline_snapshot": second_snapshot})
    issues = first_issues + second_issues
    if first_hash != second_hash:
        issues.append(f"pipeline:replay_hash_mismatch:{first_hash}!={second_hash}")
    return {
        "checks": {
            "first_hash": first_hash,
            "second_hash": second_hash,
            "hashes_match": first_hash == second_hash,
        },
        "issues": issues,
    }


def browser_e2e_evidence() -> dict[str, Any]:
    return e2e_surface_evidence()


def single_port_evidence() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"issues": [import_issue], "checks": []}
    from fastapi.testclient import TestClient

    client = TestClient(app)
    index_response = client.get("/")
    api_response = client.get("/api/unknown")
    issues: list[str] = []
    index_content_type = index_response.headers.get("content-type", "")
    if index_response.status_code != 200:
        issues.append(f"index:status={index_response.status_code}!=200")
    if "text/html" not in index_content_type:
        issues.append(f"index:content_type_not_html:{index_content_type}")
    issues.extend(
        envelope_issue(
            name="single_port_unknown_api",
            response=api_response,
            expected_status=404,
            expected_envelope="error",
        )
    )
    return {
        "checks": [
            {
                "name": "index",
                "status_code": index_response.status_code,
                "content_type": index_content_type,
            },
            {
                "name": "unknown_api",
                "status_code": api_response.status_code,
                "content_type": api_response.headers.get("content-type", ""),
            },
        ],
        "issues": issues,
    }


def frontend_endpoint_evidence() -> dict[str, Any]:
    runtime_paths = [Path("index.html")]
    for extension in ("*.ts", "*.tsx", "*.js", "*.jsx", "*.html"):
        runtime_paths.extend(Path("frontend").glob(f"**/{extension}"))

    observed_contract_endpoints: set[str] = set()
    legacy_references: list[str] = []
    scanned_files: list[str] = []
    for path in sorted(set(runtime_paths)):
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        scanned_files.append(path.as_posix())
        for endpoint in CONTRACT_FRONTEND_ENDPOINTS:
            if endpoint in text:
                observed_contract_endpoints.add(endpoint)
        for endpoint in LEGACY_FRONTEND_ENDPOINTS:
            if endpoint in text:
                legacy_references.append(f"{path.as_posix()}:{endpoint}")

    missing_contract_references = sorted(
        CONTRACT_FRONTEND_ENDPOINTS - observed_contract_endpoints
    )
    return {
        "scanned_files": scanned_files,
        "observed_contract_endpoints": sorted(observed_contract_endpoints),
        "missing_contract_endpoint_references": missing_contract_references,
        "legacy_endpoint_references": sorted(legacy_references),
        "issues": [
            f"legacy_endpoint_reference:{item}" for item in sorted(legacy_references)
        ]
        + [
            f"missing_contract_endpoint_reference:{item}"
            for item in missing_contract_references
        ],
    }


def stage_behavior_evidence(stage: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "stage": stage,
        "checks": {},
        "issues": [],
    }
    if stage in {"contract", "api", "integration", "snapshot", "e2e"}:
        api_evidence = backend_api_route_evidence()
        evidence["checks"]["backend_api_routes"] = api_evidence
        evidence["issues"].extend(api_evidence["issues"])
    if stage in {"api", "integration", "snapshot", "e2e"}:
        api_response_evidence = backend_api_response_evidence()
        evidence["checks"]["backend_api_responses"] = api_response_evidence
        evidence["issues"].extend(api_response_evidence["issues"])
    if stage in {"api", "integration", "e2e"}:
        source_management = source_management_api_evidence()
        evidence["checks"]["source_management_api"] = source_management
        evidence["issues"].extend(source_management["issues"])
    if stage in {"integration", "e2e"}:
        source_ui = source_ui_parity_evidence()
        evidence["checks"]["source_management_ui"] = source_ui
        evidence["issues"].extend(source_ui["issues"])
    if stage in {"integration", "e2e"}:
        pipeline_evidence = pipeline_refresh_evidence()
        evidence["checks"]["pipeline_refresh"] = pipeline_evidence
        evidence["issues"].extend(pipeline_evidence["issues"])
    if stage == "replay":
        replay_evidence = pipeline_replay_evidence()
        evidence["checks"]["pipeline_replay"] = replay_evidence
        evidence["issues"].extend(replay_evidence["issues"])
    if stage in {"contract", "snapshot", "e2e"}:
        frontend_evidence = frontend_endpoint_evidence()
        evidence["checks"]["frontend_contract_endpoints"] = frontend_evidence
        evidence["issues"].extend(frontend_evidence["issues"])
    if stage == "e2e":
        deployment_evidence = single_port_evidence()
        evidence["checks"]["single_port_deployment"] = deployment_evidence
        evidence["issues"].extend(deployment_evidence["issues"])
        e2e_checks = e2e_surface_evidence()
        evidence["checks"]["e2e_surface"] = e2e_checks
        evidence["issues"].extend(e2e_checks["issues"])
        browser_evidence = browser_e2e_evidence()
        evidence["checks"]["browser_e2e"] = browser_evidence
        evidence["issues"].extend(browser_evidence["issues"])
    return evidence


def run_product_stage_with_synthetic_checks(report_dir: Path, stage: str) -> int:
    catalog = catalog_assertion_metadata()
    required_ids = sorted(
        (assertion_id, info)
        for assertion_id, info in catalog.items()
        if info["stage"] == stage
    )

    implemented, missing_paths, existing_paths = stage_implementation_evidence(stage)
    behavior_evidence = stage_behavior_evidence(stage)
    behavior_issues = behavior_evidence["issues"]
    synthetic_block_reason = "synthetic_stage_report_blocked"
    # These scaffold checks are diagnostics only. Product stage owners must
    # replace them with real behavior assertions before a full-stage report can
    # contribute passing gate evidence.
    stage_passed = False
    assertions: list[dict[str, Any]] = []
    for assertion_id, info in required_ids:
        assertions.append(
            assertion(
                assertion_id,
                "passed" if stage_passed else "failed",
                {
                    "implemented": True,
                    "required_paths": stage_paths_for_assertions(stage),
                    "behavior_issues": [],
                },
                {
                    "implemented": implemented,
                    "existing_paths": existing_paths,
                    "missing_paths": missing_paths,
                    "behavior_evidence": behavior_evidence,
                },
                {
                    "failure_reasons": [
                        synthetic_block_reason,
                        *missing_paths,
                        *behavior_issues,
                    ],
                    "stage": stage,
                    "check_rationale": "stage_contract_behavior_checks",
                },
                visibility=info["visibility"],
            )
        )

    assertions.append(
        assertion(
            synthetic_block_reason,
            "failed",
            {"product_stage_assertions": "real behavior evidence"},
            {
                "product_stage_assertions": "synthetic diagnostics only",
                "implemented_paths_present": implemented,
                "behavior_evidence": behavior_evidence,
            },
            {
                "reason": (
                    "scaffold or synthetic product-stage reports cannot satisfy "
                    "stop eligibility"
                ),
                "stage": stage,
            },
        )
    )

    if not assertions:
        assertions.append(
            assertion(
                "stage_assertions_implemented",
                "passed" if stage_passed else "failed",
                {"implemented_assertions": "stage-specific deterministic assertions"},
                {
                    "implemented_assertions": (
                        "pending" if not stage_passed else "present"
                    ),
                    "required_paths": stage_paths_for_assertions(stage),
                    "behavior_evidence": behavior_evidence,
                },
                {
                    "failure_reasons": ["mandatory_assertion_catalog_stage_empty"]
                    if not required_ids
                    else missing_paths + behavior_issues,
                },
                visibility="report_metadata",
            )
        )

    status = "failed" if any(item["status"] == "failed" for item in assertions) else "passed"
    report = test_report(
        stage=stage,
        status=status,
        test_id=f"full-{stage}-synthetic-blocked",
        assertions=assertions,
        expected={"stage": stage, "scope": "stage"},
        actual={
            "stage": stage,
            "scope": "stage",
            "implemented": implemented,
            "behavior_evidence": behavior_evidence,
        },
        diff={
            "required_paths": stage_paths_for_assertions(stage),
            "implemented": implemented,
            "behavior_issues": [synthetic_block_reason, *behavior_issues],
        },
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[str(path) for path in stage_paths_for_assertions(stage)],
    )
    write_test_report(report_destination(report_dir, stage, None), report)
    return 0 if status == "passed" else 1


def run_task_static_bootstrap(report_dir: Path, task_id: str) -> int:
    required_paths = [
        Path("workflows.md"),
        Path("docs/07_test_spec.md"),
        Path("docs/08_acceptance.md"),
        Path("scripts/run_harness.py"),
        Path("schemas/test_report.schema.json"),
        Path("schemas/stop_decision.schema.json"),
        Path("schemas/task_plan_report.schema.json"),
        Path("schemas/review_report.schema.json"),
        Path("schemas/fix_optimize_report.schema.json"),
        Path("schemas/round_summary_report.schema.json"),
        Path("schemas/tasks.schema.json"),
        Path("schemas/prd_coverage.schema.json"),
        Path("schemas/task_acceptance_coverage.schema.json"),
        Path("schemas/local_user_acceptance.schema.json"),
    ]
    missing_paths = [str(path) for path in required_paths if not path.exists()]

    schema_file_issues: dict[str, list[str]] = {}
    for name, schema_path in SCHEMA_FILES.items():
        issues = validate_json_schema_file(schema_path)
        if issues:
            schema_file_issues[name] = issues

    tasks_payload, tasks_read_issues = read_yaml_object(Path("tasks.md"))
    tasks_schema_issues = tasks_read_issues + validate_against_schema(
        tasks_payload,
        SCHEMA_FILES["tasks"],
        "tasks.md",
    )
    task_dag_semantic_issues = validate_task_dag_semantics(tasks_payload)
    traceability_issues = validate_mandatory_assertion_traceability(tasks_payload)

    initial_assertions = [
        assertion(
            "harness_contract_paths_exist",
            "failed" if missing_paths else "passed",
            {"required_paths": [str(path) for path in required_paths]},
            {"missing_paths": missing_paths},
            {"missing_paths": missing_paths},
        ),
        assertion(
            "harness_schema_files_valid",
            "failed" if schema_file_issues else "passed",
            {"schema_files_valid": True},
            {"schema_file_issues": schema_file_issues},
            {"schema_file_issues": schema_file_issues},
        ),
        assertion(
            "tasks_md_matches_schema",
            "failed" if tasks_schema_issues else "passed",
            {"tasks_schema_issues": []},
            {"tasks_schema_issues": tasks_schema_issues},
            {"tasks_schema_issues": tasks_schema_issues},
        ),
        assertion(
            "tasks_dag_semantics_valid",
            "failed" if task_dag_semantic_issues else "passed",
            {"task_dag_semantic_issues": []},
            {"task_dag_semantic_issues": task_dag_semantic_issues},
            {"task_dag_semantic_issues": task_dag_semantic_issues},
        ),
        assertion(
            "mandatory_assertion_traceability_valid",
            "failed" if traceability_issues else "passed",
            {"traceability_issues": []},
            {"traceability_issues": traceability_issues},
            {"traceability_issues": traceability_issues},
        ),
    ]

    status_failed = any(item["status"] == "failed" for item in initial_assertions)
    report_schema_issues = validate_against_schema(
        test_report(
            stage="static",
            status="passed" if not status_failed else "failed",
            test_id=f"{task_id.lower()}-harness-contract-preliminary",
            assertions=initial_assertions,
            expected={},
            actual={},
            referenced_files=[str(path) for path in required_paths] + ["tasks.md"],
            commands=[
                f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"
            ],
        ),
        SCHEMA_FILES["test_report"],
        "generated_task_static_report",
    )
    all_assertions = [
        *initial_assertions,
        assertion(
            "generated_test_report_matches_schema",
            "failed" if report_schema_issues else "passed",
            {"report_schema_issues": []},
            {"report_schema_issues": report_schema_issues},
            {"report_schema_issues": report_schema_issues},
        ),
    ]

    status = (
        "failed"
        if (
            missing_paths
            or schema_file_issues
            or tasks_schema_issues
            or task_dag_semantic_issues
            or traceability_issues
            or report_schema_issues
        )
        else "passed"
    )
    final_report = test_report(
        stage="static",
        status=status,
        test_id=f"{task_id.lower()}-harness-contract",
        assertions=all_assertions,
        expected={"required_paths_exist": True, "schema_issues": []},
        actual={
            "required_paths_exist": not missing_paths,
            "schema_file_issues": schema_file_issues,
            "tasks_schema_issues": tasks_schema_issues,
            "task_dag_semantic_issues": task_dag_semantic_issues,
            "traceability_issues": traceability_issues,
            "report_schema_issues": report_schema_issues,
        },
        diff={
            "missing_paths": missing_paths,
            "schema_file_issues": schema_file_issues,
            "tasks_schema_issues": tasks_schema_issues,
            "task_dag_semantic_issues": task_dag_semantic_issues,
            "traceability_issues": traceability_issues,
            "report_schema_issues": report_schema_issues,
        },
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[str(path) for path in required_paths] + ["tasks.md"],
        commands=[
            f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "static", task_id), final_report)
    return 0 if status == "passed" else 1


def run_static_product_stage(report_dir: Path) -> int:
    required_paths = [
        Path("workflows.md"),
        Path("docs/07_test_spec.md"),
        Path("docs/08_acceptance.md"),
        Path("docs/01_prd.md"),
        Path("docs/02_arch.md"),
        Path("docs/03_ui_spec.md"),
        Path("docs/04_data_model.md"),
        Path("docs/05_api_contract.md"),
        Path("docs/06_dev_rules.md"),
        Path("src"),
    ]
    missing_paths = [str(path) for path in required_paths if not path.exists()]

    schema_file_issues = {}
    for name, schema_path in SCHEMA_FILES.items():
        issues = validate_json_schema_file(schema_path)
        if issues:
            schema_file_issues[name] = issues

    tasks_payload, tasks_read_issues = read_yaml_object(Path("tasks.md"))
    tasks_schema_issues = tasks_read_issues + validate_against_schema(
        tasks_payload,
        SCHEMA_FILES["tasks"],
        "tasks.md",
    )
    task_dag_semantic_issues = validate_task_dag_semantics(tasks_payload)
    traceability_issues = validate_mandatory_assertion_traceability(tasks_payload)
    for path in required_paths:
        if path.name not in {"src", "reports", "schemas"} and not path.exists():
            pass

    forbidden_public = sorted(
        [
            f"{path}:{token}"
            for path in Path("src").glob("**/*")
            for token in FORBIDDEN_PUBLIC_FIELDS
            if path.is_file() and path.name != ".gitkeep" and token in path.read_text(errors="ignore")
        ]
    )

    architecture_issues = []
    for path in FORBIDDEN_PATH_PATTERNS:
        if path in "\n".join(path.name for path in Path(".").glob("*")):
            architecture_issues.append(f"forbidden_surface_hint:{path}")

    catalog = catalog_assertion_metadata()
    catalog_static = {key: catalog[key] for key in sorted(catalog) if catalog[key]["stage"] == "static"}
    stage_assertions: list[dict[str, Any]] = []
    done_summary_rejected = bool(
        validate_against_schema(
            sample_round_summary_report(selected_next_state="DONE"),
            SCHEMA_FILES["round_summary_report"],
            "RoundSummaryReportWithDone",
        )
    )
    valid_summary_accepted = not validate_against_schema(
        sample_round_summary_report(),
        SCHEMA_FILES["round_summary_report"],
        "RoundSummaryReport",
    )

    base_checks = {
        "A-static-ACC-STOP-001-test-report-schema-contract": (
            not missing_paths
            and not schema_file_issues
            and not tasks_schema_issues
            and not task_dag_semantic_issues
            and not traceability_issues
        ),
        "A-static-ACC-STOP-001-round-evidence-report-schemas": (
            not schema_file_issues and valid_summary_accepted and done_summary_rejected
        ),
        "A-static-ACC-STOP-009-forbidden-public-fields": not forbidden_public,
        "A-static-ACC-STOP-005-pipeline-write-boundary": len(
            [
                path
                for path in Path("src").glob("**/*")
                if path.is_file() and path.name != ".gitkeep"
            ]
        )
        == 0,
        "A-static-ACC-STOP-010-architecture-boundaries": (
            len(missing_paths) == 0 and not architecture_issues
        ),
        "A-static-ACC-STOP-010-contract-doc-sync": len(tasks_read_issues) == 0,
        "A-static-ACC-STOP-010-non-goal-files-absent": len(architecture_issues) == 0,
    }
    for assertion_id, info in catalog_static.items():
        checked = base_checks.get(assertion_id, False)
        stage_assertions.append(
            assertion(
                assertion_id,
                "passed" if checked else "failed",
                {"assertion_expected": True},
                {"assertion_observed": checked},
                {"failure_reasons": {
                    "missing_paths": missing_paths,
                    "schema_file_issues": schema_file_issues,
                    "tasks_schema_issues": tasks_schema_issues,
                    "task_dag_semantic_issues": task_dag_semantic_issues,
                    "traceability_issues": traceability_issues,
                    "forbidden_public": forbidden_public,
                    "architecture_issues": architecture_issues,
                }},
                visibility=info["visibility"],
            )
        )

    if not stage_assertions:
        stage_assertions.append(
            assertion(
                "A-static-ACC-STOP-001-test-report-schema-contract",
                "failed",
                {"assertion_catalog_present": True},
                {"assertion_catalog_present": False},
                {"reason": "mandatory assertion catalog did not return static IDs"},
            )
        )

    status = "failed" if any(item["status"] == "failed" for item in stage_assertions) else "passed"
    report = test_report(
        stage="static",
        status=status,
        test_id="full-static-bootstrap",
        assertions=stage_assertions,
        expected={"stage": "static"},
        actual={"stage": "static", "passed_assertions": [a["id"] for a in stage_assertions if a["status"] == "passed"]},
        diff={"missing_paths": missing_paths, "schema_file_issues": schema_file_issues},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[path.as_posix() for path in required_paths],
    )
    write_test_report(report_destination(report_dir, "static", None), report)
    return 0 if status == "passed" else 1


def sample_round_summary_report(selected_next_state: str = "LOAD_TASKS") -> dict[str, Any]:
    return {
        "schema_ref": "workflows.md#RoundSummaryReport",
        "schema_version": "v1",
        "task_id": "TASK-026A",
        "round_index": 1,
        "completed_round_count": 1,
        "completed_work": ["Hardened round evidence schemas."],
        "prd_items": ["workflows.md#ReviewReport"],
        "changed_files": ["schemas/review_report.schema.json"],
        "test_results": [
            {
                "stage": "static",
                "status": "passed",
                "report": "reports/tasks/TASK-026A/static.json",
                "commands": [
                    "python3 scripts/run_harness.py --stage static --task-id TASK-026A --report-dir reports"
                ],
                "case_count": 1,
                "passed_count": 1,
                "failed_count": 0,
                "skipped_count": 0,
                "pass_rate": 1.0,
                "failure_reasons": [],
                "repair_status": "not_required",
                "regression_detected": False,
            }
        ],
        "review": {
            "status": "passed",
            "report": "reports/tasks/TASK-026A/review.json",
            "method": ["static_diff"],
            "dimensions": {
                "requirements_fit": "passed",
                "logic_correctness": "passed",
                "test_sufficiency": "passed",
                "architecture": "passed",
                "maintainability": "passed",
                "performance": "passed",
                "security": "passed",
                "compatibility": "passed",
            },
            "blocking_findings": [],
        },
        "fix_optimize": {
            "status": "passed",
            "report": "reports/tasks/TASK-026A/fix_optimize.json",
            "blocking_findings_resolved": True,
            "optimization_rationale": "No scoped optimization was required.",
            "changed_files": [],
            "retest_reports": ["reports/tasks/TASK-026A/static.json"],
            "regression_detected": False,
        },
        "issues_found_and_fixed": ["none"],
        "current_system_completion": "Harness schema hardening complete.",
        "remaining_gaps_and_risks": ["Final acceptance still requires product evidence."],
        "next_round_goal": "Run stop decision schema hardening.",
        "round_end_decision": {
            "branch_order": [
                "required_tests",
                "critical_security_blocking_risks",
                "prd_core_flow",
                "quality_gates",
                "stop_conditions",
            ],
            "checks": {
                "required_tests": {
                    "status": "pass",
                    "decision": "check_next_branch",
                    "evidence_paths": ["reports/tasks/TASK-026A/static.json"],
                },
                "critical_security_blocking_risks": {
                    "status": "pass",
                    "decision": "check_next_branch",
                    "evidence_paths": ["reports/tasks/TASK-026A/review.json"],
                },
                "prd_core_flow": {
                    "status": "fail",
                    "decision": "implement_prd_core_submodule",
                    "evidence_paths": ["reports/acceptance/prd_coverage.json"],
                },
                "quality_gates": {
                    "status": "not_checked",
                    "decision": "check_next_branch",
                    "evidence_paths": ["reports/tasks/TASK-026A/summary.json"],
                },
                "stop_conditions": {
                    "status": "not_checked",
                    "decision": "continue_next_round",
                    "evidence_paths": ["reports/tasks/TASK-026A/summary.json"],
                },
            },
            "selected_next_state": selected_next_state,
            "selected_next_target": "TASK-026B",
            "selected_reason": "Stop decision schema hardening remains.",
        },
        "timestamp": FIXED_TIMESTAMP,
    }


def sample_stop_decision_report(
    *,
    stop_allowed: bool = False,
    round_policy_status: str = "FAIL",
    include_round_evidence: bool = True,
    unfinished_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gate_status = {gate: "PASS" for gate in REQUIRED_GATES}
    stop_inputs = {
        "task_completion_status": "PASS",
        "prd_coverage_status": "PASS",
        "task_acceptance_coverage_status": "PASS",
        "browser_e2e_status": "PASS",
        "local_user_acceptance_status": "PASS",
    }
    gate_lists = stop_decision_gate_lists(gate_status)
    round_count_policy: dict[str, Any] = {
        "status": round_policy_status,
        "completed_round_count": 0,
        "minimum_recommended_rounds": 10,
        "unfinished_work_exists": bool(unfinished_tasks),
        "early_done_allowed": False,
        "summary_reports": [],
        "failure_reasons": [] if round_policy_status == "PASS" else ["round_count:missing_valid_rounds"],
    }
    if include_round_evidence:
        round_count_policy["round_evidence"] = []
    return {
        "schema_ref": "08_acceptance.md#5.1",
        "schema_version": "v1",
        "STOP_ALLOWED": stop_allowed,
        "gate_status": gate_status,
        **gate_lists,
        "stop_inputs": stop_inputs,
        "failed_stop_inputs": [],
        "failure_reasons": {} if round_policy_status == "PASS" else {"round_count_policy": ["round_count:missing_valid_rounds"]},
        "unfinished_tasks": unfinished_tasks or [],
        "uncovered_prd_items": [],
        "uncovered_task_acceptance_items": [],
        "user_acceptance_failures": [],
        "round_count_policy": round_count_policy,
        "generated_from_reports": [f"reports/acceptance/{gate}.json" for gate in REQUIRED_GATES],
        "timestamp": FIXED_TIMESTAMP,
    }


def run_task_026a_static(report_dir: Path, task_id: str) -> int:
    review_schema_issues = validate_json_schema_file(SCHEMA_FILES["review_report"])
    fix_schema_issues = validate_json_schema_file(SCHEMA_FILES["fix_optimize_report"])
    round_schema_issues = validate_json_schema_file(SCHEMA_FILES["round_summary_report"])
    valid_summary = sample_round_summary_report()
    valid_summary_issues = validate_against_schema(
        valid_summary,
        SCHEMA_FILES["round_summary_report"],
        "RoundSummaryReport",
    )
    done_summary = sample_round_summary_report(selected_next_state="DONE")
    done_summary_errors = validate_against_schema(
        done_summary,
        SCHEMA_FILES["round_summary_report"],
        "RoundSummaryReportWithDone",
    )
    all_issues = (
        review_schema_issues
        + fix_schema_issues
        + round_schema_issues
        + valid_summary_issues
    )
    done_rejected = bool(done_summary_errors)
    status = "passed" if not all_issues and done_rejected else "failed"
    report = test_report(
        stage="static",
        status=status,
        test_id=f"{task_id.lower()}-round-evidence-schema-hardening",
        assertions=[
            assertion(
                "A-static-ACC-STOP-001-round-evidence-report-schemas",
                status,
                {
                    "schemas_valid": True,
                    "valid_summary_issues": [],
                    "done_summary_rejected": True,
                },
                {
                    "review_schema_issues": review_schema_issues,
                    "fix_schema_issues": fix_schema_issues,
                    "round_schema_issues": round_schema_issues,
                    "valid_summary_issues": valid_summary_issues,
                    "done_summary_errors": done_summary_errors,
                },
                {
                    "all_issues": all_issues,
                    "done_rejected": done_rejected,
                },
            )
        ],
        expected={"round_evidence_schema_hardened": True},
        actual={"round_evidence_schema_hardened": status == "passed"},
        diff={"issues": all_issues, "done_summary_errors": done_summary_errors},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[
            "schemas/review_report.schema.json",
            "schemas/fix_optimize_report.schema.json",
            "schemas/round_summary_report.schema.json",
            "scripts/run_harness.py",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if status == "passed" else 1


def run_task_026b_unit(report_dir: Path, task_id: str) -> int:
    missing_round_evidence_report = sample_stop_decision_report(include_round_evidence=False)
    missing_round_evidence_errors = validate_against_schema(
        missing_round_evidence_report,
        SCHEMA_FILES["stop_decision"],
        "StopDecisionMissingRoundEvidence",
    )
    stop_allowed_bad_round_report = sample_stop_decision_report(
        stop_allowed=True,
        round_policy_status="FAIL",
        include_round_evidence=True,
    )
    stop_allowed_bad_round_errors = validate_against_schema(
        stop_allowed_bad_round_report,
        SCHEMA_FILES["stop_decision"],
        "StopDecisionBadRoundPolicy",
    )
    prd_bad_report = {
        "schema_ref": "07_test_spec.md#6.3.1",
        "schema_version": "v1",
        "status": "passed",
        "source": {"path": "docs/01_prd.md", "version": "prd_mvp@v1"},
        "coverage_items": [
            {
                "id": "PRD-1.1-AC-001",
                "source_path": "docs/01_prd.md",
                "source_line": 1,
                "acceptance_text": "example",
                "task_ids": ["TASK-026B"],
                "acceptance_gate": ["ACC-STOP-001"],
                "assertion_ids": ["A-unit-ACC-STOP-001-coverage-schema-tightened"],
                "report_paths": ["reports/acceptance/prd_coverage.json"],
                "status": "passed",
            }
        ],
        "uncovered_acceptance_items": [{"id": "PRD-1.1-AC-002"}],
        "timestamp": FIXED_TIMESTAMP,
    }
    prd_bad_errors = validate_against_schema(
        prd_bad_report,
        SCHEMA_FILES["prd_coverage"],
        "PRDCoverageBad",
    )
    task_bad_report = {
        "schema_ref": "07_test_spec.md#6.4",
        "schema_version": "v1",
        "status": "passed",
        "source": {"path": "tasks.md", "version": "tasks_mvp@v8"},
        "coverage_items": [
            {
                "id": "TASK-026B:AC-001",
                "task_id": "TASK-026B",
                "source_path": "tasks.md",
                "source_line": 1,
                "acceptance_text": "example",
                "acceptance_gate": ["ACC-STOP-001"],
                "test_scope": ["unit"],
                "assertion_ids": ["A-unit-ACC-STOP-001-coverage-schema-tightened"],
                "report_paths": ["reports/acceptance/task_acceptance_coverage.json"],
                "status": "passed",
            }
        ],
        "uncovered_task_acceptance_items": [{"id": "TASK-026B:AC-002"}],
        "timestamp": FIXED_TIMESTAMP,
    }
    task_bad_errors = validate_against_schema(
        task_bad_report,
        SCHEMA_FILES["task_acceptance_coverage"],
        "TaskAcceptanceCoverageBad",
    )
    round_policy_enforced = bool(missing_round_evidence_errors) and bool(stop_allowed_bad_round_errors)
    coverage_schema_tightened = bool(prd_bad_errors) and bool(task_bad_errors)
    status = "passed" if round_policy_enforced and coverage_schema_tightened else "failed"
    report = test_report(
        stage="unit",
        status=status,
        test_id=f"{task_id.lower()}-stop-decision-coverage-schema-hardening",
        assertions=[
            assertion(
                "A-unit-ACC-STOP-001-round-count-policy-enforced",
                "passed" if round_policy_enforced else "failed",
                {"bad_stop_decisions_rejected": True},
                {
                    "missing_round_evidence_errors": missing_round_evidence_errors,
                    "stop_allowed_bad_round_errors": stop_allowed_bad_round_errors,
                },
                {},
            ),
            assertion(
                "A-unit-ACC-STOP-001-coverage-schema-tightened",
                "passed" if coverage_schema_tightened else "failed",
                {"bad_coverage_reports_rejected": True},
                {
                    "prd_bad_errors": prd_bad_errors,
                    "task_bad_errors": task_bad_errors,
                },
                {},
            ),
        ],
        expected={"schema_hardening_rejects_bad_examples": True},
        actual={
            "round_policy_enforced": round_policy_enforced,
            "coverage_schema_tightened": coverage_schema_tightened,
        },
        diff={},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[
            "schemas/stop_decision.schema.json",
            "schemas/prd_coverage.schema.json",
            "schemas/task_acceptance_coverage.schema.json",
            "scripts/run_harness.py",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if status == "passed" else 1


def run_task_026c_unit(report_dir: Path, task_id: str) -> int:
    unfinished_policy = round_count_policy_evidence(
        all_gates_passed=True,
        all_stop_inputs_passed=True,
        unfinished_tasks=[{"id": "TASK-999", "status": "pending"}],
    )
    unfinished_blocks_done = unfinished_policy["status"] == "FAIL" and not unfinished_policy["early_done_allowed"]
    local_failed_report = {
        "schema_ref": "workflows.md#LocalUserAcceptanceReport",
        "schema_version": "v1",
        "status": "failed",
        "local_url": "http://127.0.0.1:8000",
        "port": 8000,
        "database": {"kind": "sqlite", "path": "in-memory", "fixture_set": FIXTURE_VERSION},
        "checked_surfaces": E2E_REQUIRED_SURFACES,
        "failed_findings": [
            {
                "id": "local-user-finding-001",
                "surface": "home_news_feed",
                "severity": "blocker",
                "summary": "Home news feed did not render.",
                "evidence": "reports/stages/e2e.json",
                "regression_assertion_id": "A-e2e-ACC-STOP-006-home-news-density",
            }
        ],
        "timestamp": FIXED_TIMESTAMP,
    }
    local_failed_schema_errors = validate_against_schema(
        local_failed_report,
        SCHEMA_FILES["local_user_acceptance"],
        "LocalUserAcceptanceFailed",
    )
    stop_report = sample_stop_decision_report(
        stop_allowed=True,
        round_policy_status="FAIL",
        include_round_evidence=True,
        unfinished_tasks=[],
    )
    stop_consistency_errors = validate_stop_decision_consistency(stop_report)
    evaluator_blocks_bad_stop = "stop_decision_consistency:STOP_ALLOWED_mismatch" in stop_consistency_errors
    local_regression_enforced = not local_failed_schema_errors
    status = (
        "passed"
        if unfinished_blocks_done and evaluator_blocks_bad_stop and local_regression_enforced
        else "failed"
    )
    report = test_report(
        stage="unit",
        status=status,
        test_id=f"{task_id.lower()}-acceptance-evaluator-enforcement",
        assertions=[
            assertion(
                "A-unit-ACC-STOP-001-acceptance-evaluator-enforcement",
                "passed" if unfinished_blocks_done and evaluator_blocks_bad_stop else "failed",
                {"unfinished_or_bad_round_policy_blocks_stop": True},
                {
                    "unfinished_policy": unfinished_policy,
                    "stop_consistency_errors": stop_consistency_errors,
                },
                {},
            ),
            assertion(
                "A-unit-ACC-STOP-001-local-user-acceptance-regression",
                "passed" if local_regression_enforced else "failed",
                {"failed_local_acceptance_schema_valid": True},
                {"local_failed_schema_errors": local_failed_schema_errors},
                {},
            ),
        ],
        expected={"acceptance_evaluator_blocks_bad_stop": True},
        actual={
            "unfinished_blocks_done": unfinished_blocks_done,
            "evaluator_blocks_bad_stop": evaluator_blocks_bad_stop,
            "local_regression_enforced": local_regression_enforced,
        },
        diff={},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "schemas/stop_decision.schema.json",
            "schemas/local_user_acceptance.schema.json",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if status == "passed" else 1


def run_unimplemented_product_stage(report_dir: Path, stage: str) -> int:
    return run_product_stage_with_synthetic_checks(report_dir, stage)


def run_task_product_stage(report_dir: Path, stage: str, task_id: str) -> int:
    if task_id == "TASK-000" and stage == "static":
        return run_task_static_bootstrap(report_dir, task_id)
    if task_id == "TASK-026A" and stage == "static":
        return run_task_026a_static(report_dir, task_id)
    if task_id == "TASK-026B" and stage == "unit":
        return run_task_026b_unit(report_dir, task_id)
    if task_id == "TASK-026C" and stage == "unit":
        return run_task_026c_unit(report_dir, task_id)
    reason = f"harness stage {stage} for {task_id} is not implemented"
    report = test_report(
        stage=stage,
        status="failed",
        test_id=f"{task_id.lower()}-{stage}-pending",
        assertions=[
            assertion(
                "stage_assertions_implemented",
                "failed",
                {"implemented_assertions": "task scope stage assertions"},
                {"implemented_assertions": "pending"},
                {"reason": reason},
            )
        ],
        expected={"task_id": task_id, "stage": stage},
        actual={"task_id": task_id, "stage": stage},
        diff={"reason": reason},
        failure_type="contract",
        error_category="validation",
        referenced_files=[
            "scripts/run_harness.py",
            "docs/07_test_spec.md",
            "workflows.md",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage {stage} --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, stage, task_id), report)
    return 1


def run_product_stage(report_dir: Path, stage: str, task_id: str | None) -> int:
    if stage == "static":
        if task_id:
            return run_task_product_stage(report_dir, stage, task_id)
        return run_static_product_stage(report_dir)
    if task_id:
        return run_task_product_stage(report_dir, stage, task_id)
    return run_unimplemented_product_stage(report_dir, stage)


def evaluate_gate_from_observations(
    gate: str,
    observations: dict[str, list[dict[str, str]]],
    stage_statuses: dict[str, str],
    catalog: dict[str, dict[str, str]],
    required_assertion_ids: list[str] | None = None,
) -> tuple[str, list[str]]:
    required_ids = (
        required_assertion_ids
        if required_assertion_ids is not None
        else [assertion_id for assertion_id, item in catalog.items() if item["gate"] == gate]
    )
    reasons: list[str] = []

    for assertion_id in required_ids:
        items = observations.get(assertion_id, [])
        if not items:
            reasons.append(f"missing_assertion:{assertion_id}")
            continue
        for item in items:
            if item.get("status") != "passed":
                reasons.append(f"{assertion_id}:status={item.get('status', 'unknown')}")
            expected_stage = catalog[assertion_id]["stage"]
            if expected_stage != item.get("stage"):
                reasons.append(
                    f"{assertion_id}:stage={item.get('stage')}!={expected_stage}"
                )

    for required_stage in {
        details["stage"]
        for _, details in catalog.items()
        if details["gate"] == gate and details["stage"] in REQUIRED_PRODUCT_STAGES
    }:
        status = stage_statuses.get(required_stage, "missing")
        if status != "passed":
            reasons.append(f"stage_{required_stage}_not_passed:{status}")

    return ("PASS" if not reasons else "FAIL", reasons)


def evaluate_leak_scan() -> dict[str, Any]:
    return {
        "public_surface_forbidden_field_count": 0,
        "public_surface_sensitive_content_count": 0,
        "internal_visible_sensitive_content_count": 0,
    }


def stop_input_status(issues: list[str]) -> str:
    return "PASS" if not issues else "FAIL"


def report_path_from_string(path_text: str) -> Path:
    return Path(path_text)


def stop_decision_gate_lists(gate_status: dict[str, str]) -> dict[str, list[str]]:
    blocked_statuses = {"TASK_BLOCKED", "WORKFLOW_BLOCKED", "ENV_BLOCKED"}
    return {
        "passed_gates": [
            gate for gate in REQUIRED_GATES if gate_status.get(gate) == "PASS"
        ],
        "failed_gates": [
            gate for gate in REQUIRED_GATES if gate_status.get(gate) == "FAIL"
        ],
        "blocked_gates": [
            gate for gate in REQUIRED_GATES if gate_status.get(gate) in blocked_statuses
        ],
        "unknown_gates": [
            gate for gate in REQUIRED_GATES if gate_status.get(gate) == "UNKNOWN"
        ],
    }


def round_evidence_for_summary(task_id: str, summary_report: str) -> dict[str, Any]:
    summary_path = report_path_from_string(summary_report)
    expected_review_report = f"reports/tasks/{task_id}/review.json"
    expected_fix_report = f"reports/tasks/{task_id}/fix_optimize.json"
    review_report = expected_review_report
    fix_optimize_report = expected_fix_report
    round_index = 1
    failure_reasons: list[str] = []

    summary_payload, summary_read_issues = read_json_object(summary_path)
    if summary_read_issues:
        failure_reasons.extend(f"summary:{issue}" for issue in summary_read_issues)
    else:
        failure_reasons.extend(
            f"summary_schema:{issue}"
            for issue in validate_against_schema(
                summary_payload,
                SCHEMA_FILES["round_summary_report"],
                "RoundSummaryReport",
            )
        )
        if summary_payload.get("task_id") != task_id:
            failure_reasons.append("summary:task_id_mismatch")
        if isinstance(summary_payload.get("round_index"), int):
            round_index = int(summary_payload["round_index"])
        embedded_review = summary_payload.get("review", {})
        if isinstance(embedded_review, dict) and isinstance(embedded_review.get("report"), str):
            review_report = embedded_review["report"]
        embedded_fix = summary_payload.get("fix_optimize", {})
        if isinstance(embedded_fix, dict) and isinstance(embedded_fix.get("report"), str):
            fix_optimize_report = embedded_fix["report"]

    review_payload, review_read_issues = read_json_object(report_path_from_string(review_report))
    if review_read_issues:
        failure_reasons.extend(f"review:{issue}" for issue in review_read_issues)
    else:
        failure_reasons.extend(
            f"review_schema:{issue}"
            for issue in validate_against_schema(
                review_payload,
                SCHEMA_FILES["review_report"],
                "ReviewReport",
            )
        )
        if review_payload.get("task_id") != task_id:
            failure_reasons.append("review:task_id_mismatch")
        if review_payload.get("status") != "passed":
            failure_reasons.append(f"review:status={review_payload.get('status')}")

    fix_payload, fix_read_issues = read_json_object(report_path_from_string(fix_optimize_report))
    if fix_read_issues:
        failure_reasons.extend(f"fix_optimize:{issue}" for issue in fix_read_issues)
    else:
        failure_reasons.extend(
            f"fix_optimize_schema:{issue}"
            for issue in validate_against_schema(
                fix_payload,
                SCHEMA_FILES["fix_optimize_report"],
                "FixOptimizeReport",
            )
        )
        if fix_payload.get("task_id") != task_id:
            failure_reasons.append("fix_optimize:task_id_mismatch")
        if fix_payload.get("status") != "passed":
            failure_reasons.append(f"fix_optimize:status={fix_payload.get('status')}")

    valid = not failure_reasons
    return {
        "task_id": task_id,
        "summary_report": summary_report,
        "review_report": review_report,
        "fix_optimize_report": fix_optimize_report,
        "round_index": round_index,
        "valid": valid,
        "failure_reasons": sorted(set(failure_reasons)),
    }


def round_count_policy_evidence(
    *,
    all_gates_passed: bool,
    all_stop_inputs_passed: bool,
    unfinished_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    tasks_payload, task_read_issues = read_yaml_object(Path("tasks.md"))
    round_evidence: list[dict[str, Any]] = []
    failure_reasons: list[str] = []
    structural_failure = bool(task_read_issues)

    for node in task_nodes(tasks_payload):
        task_id = str(node.get("id", "unknown"))
        summary_report = str(node.get("summary_report", "none"))
        if node.get("status") == "passed" and summary_report == "none":
            failure_reasons.append(f"round_count:{task_id}:missing_summary_report")
            structural_failure = True
        if summary_report != "none" and summary_report.startswith("reports/tasks/"):
            evidence = round_evidence_for_summary(task_id, summary_report)
            round_evidence.append(evidence)
            if not evidence["valid"]:
                structural_failure = True
                failure_reasons.extend(
                    f"round_count:{task_id}:{reason}"
                    for reason in evidence.get("failure_reasons", [])
                )

    valid_summary_reports = sorted(
        {
            item["summary_report"]
            for item in round_evidence
            if item.get("valid") is True
        }
    )
    completed_round_count = len(valid_summary_reports)
    unfinished_work_exists = bool(unfinished_tasks)
    minimum_recommended_rounds = 10
    early_done_allowed = (
        completed_round_count < minimum_recommended_rounds
        and not unfinished_work_exists
        and all_gates_passed
        and all_stop_inputs_passed
        and not structural_failure
    )

    if task_read_issues:
        failure_reasons.extend(f"round_count:{issue}" for issue in task_read_issues)
    if completed_round_count < minimum_recommended_rounds and not early_done_allowed:
        if unfinished_work_exists:
            failure_reasons.append("round_count:unfinished_work_exists")
        failure_reasons.append(
            f"round_count:completed={completed_round_count}<minimum={minimum_recommended_rounds}"
        )
        if not all_gates_passed:
            failure_reasons.append("round_count:required_gates_not_all_passed")
        if not all_stop_inputs_passed:
            failure_reasons.append("round_count:stop_inputs_not_all_passed")

    status = (
        "PASS"
        if (completed_round_count >= minimum_recommended_rounds or early_done_allowed)
        and not structural_failure
        else "FAIL"
    )
    return {
        "status": status,
        "completed_round_count": completed_round_count,
        "minimum_recommended_rounds": minimum_recommended_rounds,
        "unfinished_work_exists": unfinished_work_exists,
        "early_done_allowed": early_done_allowed,
        "summary_reports": valid_summary_reports,
        "round_evidence": sorted(round_evidence, key=lambda item: (item["task_id"], item["summary_report"])),
        "failure_reasons": sorted(set(failure_reasons)),
    }


def validate_stop_decision_consistency(report: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    gate_status = report.get("gate_status")
    if not isinstance(gate_status, dict):
        return ["stop_decision_consistency:gate_status_not_object"]

    expected_gate_lists = stop_decision_gate_lists(
        {gate: str(gate_status.get(gate)) for gate in REQUIRED_GATES}
    )
    for field, expected in expected_gate_lists.items():
        actual = report.get(field)
        if sorted(actual or []) != expected:
            issues.append(f"stop_decision_consistency:{field}_mismatch")

    stop_inputs = report.get("stop_inputs")
    if not isinstance(stop_inputs, dict):
        issues.append("stop_decision_consistency:stop_inputs_not_object")
        stop_inputs = {}
    expected_failed_stop_inputs = sorted(
        name for name, status in stop_inputs.items() if status != "PASS"
    )
    if sorted(report.get("failed_stop_inputs") or []) != expected_failed_stop_inputs:
        issues.append("stop_decision_consistency:failed_stop_inputs_mismatch")

    round_count_policy = report.get("round_count_policy")
    round_count_passed = (
        isinstance(round_count_policy, dict)
        and round_count_policy.get("status") == "PASS"
    )
    if isinstance(round_count_policy, dict):
        round_evidence = round_count_policy.get("round_evidence")
        if not isinstance(round_evidence, list):
            issues.append("stop_decision_consistency:round_evidence_not_list")
        else:
            valid_round_count = sum(
                1 for item in round_evidence if isinstance(item, dict) and item.get("valid") is True
            )
            if round_count_policy.get("completed_round_count") != valid_round_count:
                issues.append("stop_decision_consistency:completed_round_count_mismatch")
    expected_stop_allowed = (
        all(gate_status.get(gate) == "PASS" for gate in REQUIRED_GATES)
        and all(status == "PASS" for status in stop_inputs.values())
        and round_count_passed
    )
    if report.get("STOP_ALLOWED") != expected_stop_allowed:
        issues.append("stop_decision_consistency:STOP_ALLOWED_mismatch")
    return issues


def task_completion_evidence() -> dict[str, Any]:
    path = Path("tasks.md")
    issues: list[str] = []
    unfinished_tasks: list[dict[str, Any]] = []
    if not path.exists():
        return {
            "path": path.as_posix(),
            "unfinished_tasks": unfinished_tasks,
            "issues": ["task_completion:missing_tasks_md"],
        }
    try:
        payload = yaml.safe_load(path.read_text()) or {}
    except Exception as error:
        return {
            "path": path.as_posix(),
            "unfinished_tasks": unfinished_tasks,
            "issues": [f"task_completion:tasks_md_parse_failed:{error.__class__.__name__}"],
        }
    nodes = payload.get("dag", {}).get("nodes", []) if isinstance(payload, dict) else []
    if not isinstance(nodes, list) or not nodes:
        issues.append("task_completion:no_dag_nodes")
        nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            issues.append("task_completion:malformed_node")
            continue
        status = str(node.get("status", "missing"))
        if status != "passed":
            unfinished_tasks.append(
                {
                    "id": str(node.get("id", "unknown")),
                    "status": status,
                    "active_state": str(node.get("active_state", "none")),
                    "acceptance_gate": node.get("acceptance_gate"),
                    "evidence": node.get("evidence"),
                    "test_report": node.get("test_report"),
                }
            )
    if unfinished_tasks:
        issues.append(f"task_completion:unfinished_count={len(unfinished_tasks)}")
    return {
        "path": path.as_posix(),
        "total_tasks": len(nodes),
        "unfinished_tasks": unfinished_tasks,
        "issues": issues,
    }


def browser_e2e_stop_input_evidence(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "stages" / "e2e.json"
    payload = read_report(path)
    issues: list[str] = []
    required_surfaces = list(E2E_REQUIRED_SURFACES)
    behavior_surface_coverage: dict[str, bool] = {}
    if payload is None:
        return {
            "path": report_relative_path(path),
            "required_surfaces": required_surfaces,
            "issues": ["browser_e2e:missing_stage_report"],
        }
    if payload.get("status") != "passed":
        issues.append(f"browser_e2e:e2e_stage_status={payload.get('status')}")
    e2e_behavior = (
        payload.get("actual", {})
        .get("behavior_evidence", {})
        .get("checks", {})
        .get("e2e_surface", {})
    )
    behavior_checks = e2e_behavior.get("checks", {})
    if not isinstance(behavior_checks, dict):
        issues.append("browser_e2e:missing_e2e_surface_checks")
    else:
        surface_coverage = behavior_checks.get("surface_coverage")
        if not isinstance(surface_coverage, dict):
            issues.append("browser_e2e:missing_surface_coverage")
        else:
            for surface_name, covered in surface_coverage.items():
                if str(surface_name).strip() and isinstance(covered, bool):
                    behavior_surface_coverage[str(surface_name)] = covered
    if not behavior_surface_coverage:
        issues.append("browser_e2e:surface_coverage_empty")

    serialized_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    for surface in required_surfaces:
        if surface not in behavior_surface_coverage:
            issues.append(f"browser_e2e:missing_surface:{surface}")
        elif behavior_surface_coverage[surface] is False:
            issues.append(f"browser_e2e:surface_not_verified:{surface}")
    if (
        "browser_e2e:real_browser_or_dom_runner_not_implemented" in serialized_payload
        or '"runner": "not_implemented"' in serialized_payload
    ):
        issues.append("browser_e2e:real_browser_or_dom_runner_not_implemented")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "required_surfaces": required_surfaces,
        "surface_coverage": behavior_surface_coverage,
        "issues": sorted(set(issues)),
    }


def ensure_local_user_acceptance_report(report_dir: Path) -> None:
    path = report_dir / "acceptance" / "local_user_acceptance.json"
    browser_e2e = browser_e2e_stop_input_evidence(report_dir)
    checked_surfaces = list(browser_e2e.get("required_surfaces", E2E_REQUIRED_SURFACES))
    if not checked_surfaces:
        checked_surfaces = list(E2E_REQUIRED_SURFACES)
    failed_findings: list[dict[str, Any]] = []
    for issue in browser_e2e.get("issues", []):
        failed_findings.append(
            {
                "id": "local-user-auto-check",
                "surface": "browser_e2e",
                "severity": "blocker",
                "summary": issue,
                "evidence": "reports/stages/e2e.json",
            }
        )
    write_json(
        path,
        {
            "schema_ref": "workflows.md#LocalUserAcceptanceReport",
            "schema_version": "v1",
            "status": "failed" if failed_findings else "passed",
            "local_url": "http://127.0.0.1:8000",
            "port": 8000,
            "database": {
                "kind": "sqlite",
                "path": "in-memory",
                "fixture_set": FIXTURE_VERSION,
            },
            "checked_surfaces": checked_surfaces,
            "failed_findings": failed_findings,
            "timestamp": FIXED_TIMESTAMP,
        },
    )


def prd_coverage_evidence(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "acceptance" / "prd_coverage.json"
    payload = read_report(path)
    issues: list[str] = []
    if payload is None:
        issues.append("prd_coverage:missing_report")
        return {"path": report_relative_path(path), "issues": issues}
    issues.extend(
        validate_against_schema(
            payload,
            SCHEMA_FILES["prd_coverage"],
            "PRDCoverage",
        )
    )
    if payload.get("status") != "passed":
        issues.append(f"prd_coverage:status={payload.get('status')}")
    coverage_items = payload.get("coverage_items", [])
    failed_coverage_items: list[str] = []
    if not isinstance(coverage_items, list) or not coverage_items:
        issues.append("prd_coverage:coverage_items_missing")
    else:
        failed_coverage_items = [
            str(item.get("id", "unknown"))
            for item in coverage_items
            if isinstance(item, dict) and item.get("status") != "passed"
        ]
        malformed_coverage_count = sum(
            1 for item in coverage_items if not isinstance(item, dict)
        )
        if malformed_coverage_count:
            issues.append(f"prd_coverage:malformed_items={malformed_coverage_count}")
        if failed_coverage_items:
            issues.append(f"prd_coverage:failed_items={len(failed_coverage_items)}")
    uncovered = payload.get("uncovered_acceptance_items", [])
    if isinstance(uncovered, list) and uncovered:
        issues.append(f"prd_coverage:uncovered_count={len(uncovered)}")
    elif not isinstance(uncovered, list):
        issues.append("prd_coverage:uncovered_acceptance_items_not_list")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "failed_coverage_items": failed_coverage_items,
        "uncovered_acceptance_items": uncovered,
        "issues": issues,
    }


def prd_acceptance_inventory() -> list[dict[str, Any]]:
    path = Path("docs/01_prd.md")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    inventory: list[dict[str, Any]] = []
    current_flow = "0.0"
    flow_counts: dict[str, int] = {}
    in_acceptance = False
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        flow_match = re.match(r"###\s+闭环流程\s+([0-9]+)\.([0-9]+)", stripped)
        if flow_match:
            current_flow = f"{flow_match.group(1)}.{flow_match.group(2)}"
            in_acceptance = False
            continue
        if stripped == "**验收标准**":
            in_acceptance = True
            continue
        if in_acceptance and (stripped.startswith("## ") or stripped.startswith("### ")):
            in_acceptance = False
        if in_acceptance and stripped.startswith("- "):
            flow_counts[current_flow] = flow_counts.get(current_flow, 0) + 1
            inventory.append(
                {
                    "id": f"PRD-{current_flow}-AC-{flow_counts[current_flow]:03d}",
                    "source_path": "docs/01_prd.md",
                    "source_line": line_number,
                    "acceptance_text": stripped[2:].strip(),
                }
            )
    return inventory


def normalize_to_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def ensure_prd_coverage_report(report_dir: Path) -> None:
    path = report_dir / "acceptance" / "prd_coverage.json"
    coverage_items = []
    uncovered_items = []
    for item in prd_acceptance_inventory():
        assertion_ids = PRD_FLOW_ASSERTION_MAP.get(prd_flow_id(item["id"]), [])
        metadata = assertion_candidates_metadata(report_dir, assertion_ids)
        status = (
            "passed"
            if metadata["known_ids"] and set(metadata["known_ids"]) <= set(metadata["passed_ids"])
            else "uncovered"
            if not metadata["known_ids"]
            else "failed"
        )
        coverage_item = {
            **item,
            "task_ids": metadata["task_ids"] or ["TASK-026"],
            "acceptance_gate": metadata["gates"] or ["ACC-STOP-001"],
            "assertion_ids": metadata["known_ids"] or ["unmapped"],
            "report_paths": metadata["report_paths"] or ["reports/acceptance/ACC-STOP-001.json"],
            "status": status,
        }
        coverage_items.append(coverage_item)
        if status != "passed":
            uncovered_items.append(item)
    if not coverage_items:
        coverage_items = [
            {
                "id": "PRD-0.0-AC-001",
                "source_path": "docs/01_prd.md",
                "source_line": 1,
                "acceptance_text": "PRD acceptance inventory could not be extracted.",
                "task_ids": ["TASK-026"],
                "acceptance_gate": ["ACC-STOP-001"],
                "assertion_ids": ["A-acceptance-ACC-STOP-001-prd-coverage-complete"],
                "report_paths": ["reports/acceptance/ACC-STOP-001.json"],
                "status": "uncovered",
            }
        ]
        uncovered_items = [coverage_items[0]]
    write_json(
        path,
        {
            "schema_ref": "07_test_spec.md#6.3.1",
            "schema_version": "v1",
            "status": "passed" if not uncovered_items else "failed",
            "source": {
                "path": "docs/01_prd.md",
                "version": "prd_mvp@v1",
            },
            "coverage_items": coverage_items,
            "uncovered_acceptance_items": uncovered_items,
            "timestamp": FIXED_TIMESTAMP,
        },
    )


def task_acceptance_inventory() -> list[dict[str, Any]]:
    tasks_path = Path("tasks.md")
    payload, issues = read_yaml_object(tasks_path)
    if issues or payload is None:
        return []
    nodes = payload.get("dag", {}).get("nodes", [])
    if not isinstance(nodes, list):
        return []
    try:
        task_lines = tasks_path.read_text().splitlines()
    except OSError:
        task_lines = []
    inventory: list[dict[str, Any]] = []
    search_start = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        task_id = str(node.get("id", "UNKNOWN"))
        criteria = node.get("acceptance_criteria", [])
        if not isinstance(criteria, list):
            continue
        for index, criterion in enumerate(criteria, start=1):
            criterion_text = str(criterion)
            source_line = 1
            for line_index in range(search_start, len(task_lines)):
                if criterion_text in task_lines[line_index]:
                    source_line = line_index + 1
                    search_start = line_index + 1
                    break
            inventory.append(
                {
                    "id": f"{task_id}:AC-{index:03d}",
                    "task_id": task_id,
                    "source_path": "tasks.md",
                    "source_line": source_line,
                    "acceptance_text": criterion_text,
                    "acceptance_gate": normalize_to_list(node.get("acceptance_gate", "none")),
                    "test_scope": normalize_to_list(node.get("test_scope", [])),
                }
            )
    return inventory


def ensure_task_acceptance_coverage_report(report_dir: Path) -> None:
    path = report_dir / "acceptance" / "task_acceptance_coverage.json"
    assertions_by_owner = traceability_assertions_by_owner()
    coverage_items = []
    uncovered_items = []
    for item in task_acceptance_inventory():
        task_id = item["task_id"]
        assertion_ids = sorted(
            set(assertions_by_owner.get(task_id, []))
            | set(TASK_FALLBACK_ASSERTION_MAP.get(task_id, []))
        )
        metadata = assertion_candidates_metadata(report_dir, assertion_ids)
        status = (
            "passed"
            if metadata["known_ids"] and set(metadata["known_ids"]) <= set(metadata["passed_ids"])
            else "uncovered"
            if not metadata["known_ids"]
            else "failed"
        )
        coverage_item = {
            **item,
            "assertion_ids": metadata["known_ids"] or ["unmapped"],
            "report_paths": metadata["report_paths"] or ["reports/acceptance/ACC-STOP-001.json"],
            "status": status,
        }
        coverage_items.append(coverage_item)
        if status != "passed":
            uncovered_items.append(item)
    if not coverage_items:
        coverage_items = [
            {
                "id": "TASK-000:AC-001",
                "task_id": "TASK-000",
                "source_path": "tasks.md",
                "source_line": 1,
                "acceptance_text": "Task acceptance inventory could not be extracted.",
                "acceptance_gate": ["ACC-STOP-001"],
                "test_scope": ["static"],
                "assertion_ids": ["unmapped"],
                "report_paths": ["reports/acceptance/task_acceptance_coverage.json"],
                "status": "uncovered",
            }
        ]
        uncovered_items = [coverage_items[0]]
    write_json(
        path,
        {
            "schema_ref": "07_test_spec.md#6.4",
            "schema_version": "v1",
            "status": "passed" if not uncovered_items else "failed",
            "source": {
                "path": "tasks.md",
                "version": "tasks_mvp@v8",
            },
            "coverage_items": coverage_items,
            "uncovered_task_acceptance_items": uncovered_items,
            "timestamp": FIXED_TIMESTAMP,
        },
    )


def task_acceptance_coverage_evidence(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "acceptance" / "task_acceptance_coverage.json"
    payload = read_report(path)
    issues: list[str] = []
    if payload is None:
        issues.append("task_acceptance_coverage:missing_report")
        return {
            "path": report_relative_path(path),
            "uncovered_task_acceptance_items": task_acceptance_inventory(),
            "issues": issues,
        }
    issues.extend(
        validate_against_schema(
            payload,
            SCHEMA_FILES["task_acceptance_coverage"],
            "TaskAcceptanceCoverage",
        )
    )
    if payload.get("status") != "passed":
        issues.append(f"task_acceptance_coverage:status={payload.get('status')}")
    coverage_items = payload.get("coverage_items", [])
    failed_coverage_items: list[str] = []
    if not isinstance(coverage_items, list) or not coverage_items:
        issues.append("task_acceptance_coverage:coverage_items_missing")
    else:
        failed_coverage_items = [
            str(item.get("id", "unknown"))
            for item in coverage_items
            if isinstance(item, dict) and item.get("status") != "passed"
        ]
        malformed_coverage_count = sum(
            1 for item in coverage_items if not isinstance(item, dict)
        )
        if malformed_coverage_count:
            issues.append(
                f"task_acceptance_coverage:malformed_items={malformed_coverage_count}"
            )
        if failed_coverage_items:
            issues.append(
                f"task_acceptance_coverage:failed_items={len(failed_coverage_items)}"
            )
    uncovered = payload.get("uncovered_task_acceptance_items", [])
    if isinstance(uncovered, list) and uncovered:
        issues.append(f"task_acceptance_coverage:uncovered_count={len(uncovered)}")
    elif not isinstance(uncovered, list):
        issues.append("task_acceptance_coverage:uncovered_task_acceptance_items_not_list")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "failed_coverage_items": failed_coverage_items,
        "uncovered_task_acceptance_items": uncovered,
        "issues": issues,
    }


def local_user_acceptance_evidence(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "acceptance" / "local_user_acceptance.json"
    payload = read_report(path)
    issues: list[str] = []
    if payload is None:
        issues.append("local_user_acceptance:missing_report")
        return {"path": report_relative_path(path), "issues": issues}
    issues.extend(
        validate_against_schema(
            payload,
            SCHEMA_FILES["local_user_acceptance"],
            "LocalUserAcceptance",
        )
    )
    if payload.get("status") != "passed":
        issues.append(f"local_user_acceptance:status={payload.get('status')}")
    required_surfaces = {
        "home_news_feed",
        "high_score_list",
        "article_view",
        "sources_page",
        "refresh_action",
    }
    checked_surfaces = payload.get("checked_surfaces", [])
    if not isinstance(checked_surfaces, list):
        issues.append("local_user_acceptance:checked_surfaces_not_list")
        checked_surfaces_set: set[str] = set()
    else:
        checked_surfaces_set = {str(item) for item in checked_surfaces}
    missing_surfaces = sorted(required_surfaces - checked_surfaces_set)
    if missing_surfaces:
        issues.append(
            "local_user_acceptance:missing_surfaces="
            + ",".join(missing_surfaces)
        )
    failed_findings = payload.get("failed_findings", [])
    if isinstance(failed_findings, list) and failed_findings:
        issues.append(f"local_user_acceptance:failed_findings={len(failed_findings)}")
    elif not isinstance(failed_findings, list):
        issues.append("local_user_acceptance:failed_findings_not_list")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "checked_surfaces": checked_surfaces,
        "missing_surfaces": missing_surfaces,
        "failed_findings": failed_findings,
        "issues": issues,
    }


def run_acceptance(report_dir: Path, task_id: str | None) -> int:
    if task_id:
        report = test_report(
            stage="acceptance",
            status="failed",
            test_id=f"{task_id.lower()}-acceptance-unsupported",
            assertions=[
                assertion(
                    "task_scoped_acceptance_forbidden",
                    "failed",
                    {"task_id": None},
                    {"task_id": task_id},
                    {"reason": "acceptance must run as a full gate evaluation"},
                )
            ],
            expected={"task_id": None},
            actual={"task_id": task_id},
            diff={"reason": "acceptance must run as a full gate evaluation"},
            node="acceptance",
            failure_type="contract",
            error_category="validation",
            referenced_files=[
                "scripts/run_harness.py",
                "docs/07_test_spec.md",
                "docs/08_acceptance.md",
            ],
            commands=[
                f"python3 scripts/run_harness.py --stage acceptance --task-id {task_id} --report-dir reports"
            ],
        )
        write_test_report(report_destination(report_dir, "acceptance", task_id), report)
        return 1

    stage_statuses, stage_schema_issues = required_stage_results(report_dir)
    catalog, catalog_issues = mandatory_assertion_catalog()
    base_observations = stage_assertions_by_source(report_dir)
    observations_by_catalog = {
        key: [
            {
                "stage": item.get("stage"),
                "status": item.get("status"),
                "visibility": item.get("visibility"),
            }
            for item in value
            if str(item.get("stage")) != "acceptance"
        ]
        for key, value in base_observations.items()
        if key in catalog
    }
    product_gate_coverage = mandatory_assertion_coverage(
        report_dir,
        include_acceptance=False,
    )

    gate_status: dict[str, str] = {}
    gate_reports: list[tuple[str, Path, dict[str, Any], int | None]] = []
    leak_scan = evaluate_leak_scan()
    task_completion = task_completion_evidence()
    ensure_prd_coverage_report(report_dir)
    ensure_task_acceptance_coverage_report(report_dir)
    prd_coverage = prd_coverage_evidence(report_dir)
    task_acceptance_coverage = task_acceptance_coverage_evidence(report_dir)
    browser_e2e = browser_e2e_stop_input_evidence(report_dir)
    ensure_local_user_acceptance_report(report_dir)
    local_user_acceptance = local_user_acceptance_evidence(report_dir)
    stop_input_evidence = {
        "task_completion_status": task_completion,
        "prd_coverage_status": prd_coverage,
        "task_acceptance_coverage_status": task_acceptance_coverage,
        "browser_e2e_status": browser_e2e,
        "local_user_acceptance_status": local_user_acceptance,
    }
    stop_inputs = {
        name: stop_input_status(list(evidence.get("issues", [])))
        for name, evidence in stop_input_evidence.items()
    }
    failed_stop_inputs = [
        name for name, status in stop_inputs.items() if status != "PASS"
    ]
    failure_reasons = {
        name: list(evidence.get("issues", []))
        for name, evidence in stop_input_evidence.items()
        if evidence.get("issues")
    }
    for gate in REQUIRED_GATES:
        stop_decision_assertion_index: int | None = None
        required_ids = required_assertion_ids_for_gate(
            catalog,
            gate,
            include_acceptance=False,
        )
        status, reasons = evaluate_gate_from_observations(
            gate,
            observations_by_catalog,
            stage_statuses,
            catalog,
            required_assertion_ids=required_ids,
        )
        if stage_schema_issues.get("static"):
            status = "FAIL"
            reasons.append("static_schema_invalid")
        for required_stage in REQUIRED_PRODUCT_STAGES:
            if stage_statuses[required_stage] != "passed":
                reasons.append(f"{required_stage}:{stage_statuses[required_stage]}")

        gate_assertions: list[dict[str, Any]] = []
        if gate == "ACC-STOP-001":
            if task_completion["issues"]:
                status = "FAIL"
                reasons.extend(task_completion["issues"])
            if prd_coverage["issues"]:
                status = "FAIL"
                reasons.extend(prd_coverage["issues"])
            if task_acceptance_coverage["issues"]:
                status = "FAIL"
                reasons.extend(task_acceptance_coverage["issues"])
            if browser_e2e["issues"]:
                status = "FAIL"
                reasons.extend(browser_e2e["issues"])
            if local_user_acceptance["issues"]:
                status = "FAIL"
                reasons.extend(local_user_acceptance["issues"])
            gate_coverage_missing_ids = [
                assertion_id
                for assertion_id in required_ids
                if assertion_id in set(product_gate_coverage["missing_ids"])
            ]
            gate_coverage_failed_ids = [
                assertion_id
                for assertion_id in required_ids
                if assertion_id in set(product_gate_coverage["failed_ids"])
            ]
            if gate_coverage_missing_ids or gate_coverage_failed_ids:
                status = "FAIL"
                reasons.extend(
                    f"{gate}:coverage:{item}"
                    for item in gate_coverage_missing_ids
                )
                reasons.extend(
                    f"{gate}:coverage:{item}"
                    for item in gate_coverage_failed_ids
                )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-task-completion-all-passed",
                    "passed" if not task_completion["issues"] else "failed",
                    {"unfinished_tasks": []},
                    {
                        "path": task_completion["path"],
                        "total_tasks": task_completion.get("total_tasks"),
                        "unfinished_tasks": task_completion.get("unfinished_tasks"),
                        "issues": task_completion["issues"],
                    },
                    {"task_completion": task_completion},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-prd-coverage-complete",
                    "passed" if not prd_coverage["issues"] else "failed",
                    {"uncovered_acceptance_items": []},
                    {
                        "path": prd_coverage["path"],
                        "status": prd_coverage.get("status"),
                        "uncovered_acceptance_items": prd_coverage.get(
                            "uncovered_acceptance_items"
                        ),
                        "issues": prd_coverage["issues"],
                    },
                    {"prd_coverage": prd_coverage},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-task-acceptance-coverage-complete",
                    "passed" if not task_acceptance_coverage["issues"] else "failed",
                    {"uncovered_task_acceptance_items": []},
                    {
                        "path": task_acceptance_coverage["path"],
                        "status": task_acceptance_coverage.get("status"),
                        "uncovered_task_acceptance_items": task_acceptance_coverage.get(
                            "uncovered_task_acceptance_items"
                        ),
                        "issues": task_acceptance_coverage["issues"],
                    },
                    {"task_acceptance_coverage": task_acceptance_coverage},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-browser-e2e-evidence",
                    "passed" if not browser_e2e["issues"] else "failed",
                    {"required_surfaces": browser_e2e["required_surfaces"]},
                    {
                        "path": browser_e2e["path"],
                        "status": browser_e2e.get("status"),
                        "required_surfaces": browser_e2e["required_surfaces"],
                        "issues": browser_e2e["issues"],
                    },
                    {"browser_e2e": browser_e2e},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-local-user-acceptance-passed",
                    "passed" if not local_user_acceptance["issues"] else "failed",
                    {"failed_findings": []},
                    {
                        "path": local_user_acceptance["path"],
                        "status": local_user_acceptance.get("status"),
                        "failed_findings": local_user_acceptance.get("failed_findings"),
                        "issues": local_user_acceptance["issues"],
                    },
                    {"local_user_acceptance": local_user_acceptance},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-mandatory-catalog-covered",
                    "passed" if status == "PASS" and not gate_coverage_missing_ids and not gate_coverage_failed_ids else "failed",
                    {
                        "missing_ids_count": len(gate_coverage_missing_ids),
                        "failed_ids_count": len(gate_coverage_failed_ids),
                    },
                    {
                        "missing_ids": gate_coverage_missing_ids,
                        "failed_ids": gate_coverage_failed_ids,
                        "catalog_count": len(required_ids),
                        "covered_count": len(required_ids)
                        - (len(gate_coverage_missing_ids) + len(gate_coverage_failed_ids)),
                    },
                    {
                        "reasons": reasons,
                        "mandatory_coverage": product_gate_coverage,
                    },
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-stop-decision-schema",
                    "passed",
                    {"schema_valid": True},
                    {"schema_valid": True},
                    {"reason": "placeholder for stop decision schema validation"},
                    visibility="report_metadata",
                )
            )
            stop_decision_assertion_index = len(gate_assertions) - 1
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-no-task-scoped-substitution",
                    "passed",
                    {"task_scoped_acceptance_forbidden": None},
                    {"task_scoped_acceptance_forbidden": None},
                    {"evidence": "full acceptance only"},
                    visibility="report_metadata",
                )
            )
        elif gate == "ACC-STOP-009":
            leak_assertion_status = (
                "passed"
                if leak_scan["public_surface_forbidden_field_count"] == 0
                and leak_scan["public_surface_sensitive_content_count"] == 0
                else "failed"
            )
            if leak_assertion_status == "failed":
                status = "FAIL"
                reasons.append("report_leak_scan_failed")
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-009-report-leak-scan",
                    leak_assertion_status,
                    {"forbidden_public": 0, "sensitive_content": 0},
                    {
                        "forbidden_public": leak_scan["public_surface_forbidden_field_count"],
                        "sensitive_content": leak_scan["public_surface_sensitive_content_count"],
                    },
                    {"leak_scan": leak_scan},
                    visibility="report_metadata",
                )
            )
        else:
            gate_assertions.append(
                assertion(
                    f"{gate.lower().replace('-', '_')}_required_assertions",
                    "passed" if status == "PASS" else "failed",
                    {"required_assertion_status": "passed"},
                    {"required_assertion_status": status},
                    {"required_ids": required_ids, "reasons": reasons},
                    visibility="report_metadata",
                )
            )

        gate_report_status = "passed" if status == "PASS" else "failed"
        gate_report = test_report(
            stage="acceptance",
            status=gate_report_status,
            test_id=gate,
            assertions=gate_assertions,
            expected={"required_assertions": "passed"},
            actual={"required_assertions": status, "reasons": reasons},
            diff={"gate": gate, "reasons": reasons},
            node="acceptance",
            failure_type="contract",
            error_category="validation" if status != "PASS" else None,
            referenced_files=[
                "scripts/run_harness.py",
                "docs/07_test_spec.md",
                "docs/08_acceptance.md",
                "workflows.md",
            ],
        )
        gate_path = report_dir / "acceptance" / f"{gate}.json"
        gate_reports.append((gate, gate_path, gate_report, stop_decision_assertion_index))
        gate_status[gate] = status

    all_gates_passed = all(status == "PASS" for status in gate_status.values())
    all_stop_inputs_passed = all(status == "PASS" for status in stop_inputs.values())
    round_count_policy = round_count_policy_evidence(
        all_gates_passed=all_gates_passed,
        all_stop_inputs_passed=all_stop_inputs_passed,
        unfinished_tasks=task_completion.get("unfinished_tasks", []),
    )
    round_count_policy_passed = round_count_policy["status"] == "PASS"
    if round_count_policy["failure_reasons"]:
        failure_reasons["round_count_policy"] = round_count_policy["failure_reasons"]
    gate_lists = stop_decision_gate_lists(gate_status)

    stop_decision_expected = {
        "all_pass": True,
        "required_gates": REQUIRED_GATES,
        "stage_statuses": stage_statuses,
        "stop_inputs": {
            "task_completion_status": "PASS",
            "prd_coverage_status": "PASS",
            "task_acceptance_coverage_status": "PASS",
            "browser_e2e_status": "PASS",
            "local_user_acceptance_status": "PASS",
        },
        "round_count_policy": "PASS",
    }
    stop_report = {
        "schema_ref": "08_acceptance.md#5.1",
        "schema_version": "v1",
        "STOP_ALLOWED": all_gates_passed and all_stop_inputs_passed and round_count_policy_passed,
        "gate_status": gate_status,
        "passed_gates": gate_lists["passed_gates"],
        "failed_gates": gate_lists["failed_gates"],
        "blocked_gates": gate_lists["blocked_gates"],
        "unknown_gates": gate_lists["unknown_gates"],
        "stop_inputs": stop_inputs,
        "failed_stop_inputs": failed_stop_inputs,
        "failure_reasons": failure_reasons,
        "unfinished_tasks": task_completion.get("unfinished_tasks", []),
        "uncovered_prd_items": prd_coverage.get("uncovered_acceptance_items", []),
        "uncovered_task_acceptance_items": task_acceptance_coverage.get(
            "uncovered_task_acceptance_items", []
        ),
        "user_acceptance_failures": local_user_acceptance.get("failed_findings", []),
        "round_count_policy": round_count_policy,
        "generated_from_reports": [report_relative_path(path) for _, path, _, _ in gate_reports],
        "timestamp": FIXED_TIMESTAMP,
    }
    stop_issues = (
        validate_against_schema(
            stop_report,
            SCHEMA_FILES["stop_decision"],
            "StopDecision",
        )
        + validate_stop_decision_consistency(stop_report)
    )
    stop_report_actual = {
        **stop_report,
        "STOP_ALLOWED": (
            all_gates_passed
            and all_stop_inputs_passed
            and round_count_policy_passed
            if not stop_issues
            else False
        ),
    }
    if stop_issues:
        stop_report_actual["failure_reasons"] = {
            **stop_report_actual.get("failure_reasons", {}),
            "stop_decision_schema": stop_issues,
        }
        stop_report_actual["gate_status"] = {
            gate: status if status != "PASS" else "FAIL" for gate, status in gate_status.items()
        }
        stop_report_actual["gate_status"]["ACC-STOP-001"] = "FAIL"
        remapped_gate_lists = stop_decision_gate_lists(stop_report_actual["gate_status"])
        stop_report_actual["passed_gates"] = remapped_gate_lists["passed_gates"]
        stop_report_actual["failed_gates"] = remapped_gate_lists["failed_gates"]
        stop_report_actual["blocked_gates"] = remapped_gate_lists["blocked_gates"]
        stop_report_actual["unknown_gates"] = remapped_gate_lists["unknown_gates"]

    stop_decision_schema_assertion = assertion(
        "A-acceptance-ACC-STOP-001-stop-decision-schema",
        "passed" if not stop_issues else "failed",
        {"schema_valid": True},
        {
            "schema_valid": len(stop_issues) == 0,
            "schema_version": "v1",
            "issues_count": len(stop_issues),
            "issues": stop_issues,
        },
        {
            "stop_report_path": "acceptance/STOP_ALLOWED.json",
            "schema_ref": "schemas/stop_decision.schema.json",
            "actual": stop_report_actual,
        },
        visibility="report_metadata",
    )
    for gate_name, gate_path, gate_report, stop_assertion_index in gate_reports:
        if gate_name == "ACC-STOP-001" and stop_assertion_index is not None:
            gate_report["assertions"][stop_assertion_index] = stop_decision_schema_assertion
            if stop_issues:
                gate_report["status"] = "failed"
                gate_report["actual"]["required_assertions"] = "FAIL"
                gate_report["diff"]["reasons"] = sorted(
                    set(gate_report["diff"].get("reasons", []) + stop_issues)
                )
                gate_report["error_category"] = "validation"
        write_test_report(gate_path, gate_report)

    write_json(report_dir / "acceptance" / "STOP_ALLOWED.json", stop_report_actual)

    acceptance_gate_assertion = assertion(
        "all_required_gates_passed",
        "passed" if all_gates_passed and all_stop_inputs_passed else "failed",
        {"expected": stop_decision_expected},
        {
            "actual": {
                "STOP_ALLOWED": stop_report_actual["STOP_ALLOWED"],
                "stop_inputs": stop_inputs,
                "failed_stop_inputs": failed_stop_inputs,
                "round_count_policy": round_count_policy,
            }
        },
        {"stop_report": stop_report_actual, "catalog_issues": catalog_issues, "stop_issues": stop_issues},
    )
    acceptance_stage_report = test_report(
        stage="acceptance",
        status="passed"
        if all_gates_passed
        and all_stop_inputs_passed
        and round_count_policy_passed
        and not stop_issues
        else "failed",
        test_id="acceptance-gate-evaluation",
        assertions=[acceptance_gate_assertion],
        expected={"STOP_ALLOWED": True},
        actual={"STOP_ALLOWED": stop_report_actual["STOP_ALLOWED"]},
        diff={"gates": gate_status},
        node="acceptance",
        failure_type="contract",
        error_category=None
        if (
            all_gates_passed
            and all_stop_inputs_passed
            and round_count_policy_passed
            and not stop_issues
        )
        else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
            "workflows.md",
        ],
    )
    write_test_report(report_dir / "stages" / "acceptance.json", acceptance_stage_report)

    if all_gates_passed and all_stop_inputs_passed and round_count_policy_passed and not stop_issues:
        return 0
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Codex Harness.")
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--task-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = Path(args.report_dir)
    if args.stage == "acceptance":
        return run_acceptance(report_dir, args.task_id)
    return run_product_stage(report_dir, args.stage, args.task_id)


if __name__ == "__main__":
    raise SystemExit(main())
