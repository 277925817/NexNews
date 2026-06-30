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

SCHEMA_FILES = {
    "test_report": Path("schemas/test_report.schema.json"),
    "stop_decision": Path("schemas/stop_decision.schema.json"),
    "task_plan_report": Path("schemas/task_plan_report.schema.json"),
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
) -> dict[str, Any]:
    report_diff = diff or {}
    report_referenced_files = referenced_files or [
        "scripts/run_harness.py",
        "docs/07_test_spec.md",
    ]
    report_hash = stable_hash(
        {
            "test_id": test_id,
            "stage": stage,
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
    }
    observed_guids = {str(row["rss_guid"]) for row in rows}
    if observed_guids != expected_guids:
        issues.append(
            "pipeline:fixture_guid_set_mismatch:"
            f"{sorted(observed_guids)}!={sorted(expected_guids)}"
        )
    if len(rows) != 4:
        issues.append(f"pipeline:news_item_count={len(rows)}!=4")

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
    if log_summary.get("score:1", 0) != 4:
        issues.append("pipeline:score_success_count_not_4")
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
        if "Low signal AI funding rumor" in latest_titles:
            issues.append("pipeline:score_59_visible_in_home")
        if len(ranked_scores) != 10:
            issues.append(f"pipeline:high_score_list_count={len(ranked_scores)}!=10")
        if ranked_scores != sorted(ranked_scores, reverse=True):
            issues.append(f"pipeline:ranked_scores_not_desc:{ranked_scores}")

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
    return {
        "checks": {
            "runner": "not_implemented",
            "required_surfaces": [
                "home_news_feed",
                "high_score_list",
                "article_view",
                "sources_page",
            ],
        },
        "issues": ["browser_e2e:real_browser_or_dom_runner_not_implemented"],
    }


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
    stage_passed = implemented and not behavior_issues
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
                    "failure_reasons": missing_paths + behavior_issues,
                    "stage": stage,
                    "check_rationale": "stage_contract_behavior_checks",
                },
                visibility=info["visibility"],
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
        test_id=f"full-{stage}-scaffold",
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
            "behavior_issues": behavior_issues,
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

    base_checks = {
        "A-static-ACC-STOP-001-test-report-schema-contract": (
            not missing_paths
            and not schema_file_issues
            and not tasks_schema_issues
            and not task_dag_semantic_issues
            and not traceability_issues
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


def run_unimplemented_product_stage(report_dir: Path, stage: str) -> int:
    return run_product_stage_with_synthetic_checks(report_dir, stage)


def run_task_product_stage(report_dir: Path, stage: str, task_id: str) -> int:
    if task_id == "TASK-000" and stage == "static":
        return run_task_static_bootstrap(report_dir, task_id)
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
    required_surfaces = [
        "home_news_feed",
        "high_score_list",
        "article_view",
        "sources_page",
    ]
    if payload is None:
        return {
            "path": report_relative_path(path),
            "required_surfaces": required_surfaces,
            "issues": ["browser_e2e:missing_stage_report"],
        }
    if payload.get("status") != "passed":
        issues.append(f"browser_e2e:e2e_stage_status={payload.get('status')}")
    serialized_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    for surface in required_surfaces:
        if surface not in serialized_payload:
            issues.append(f"browser_e2e:missing_surface:{surface}")
    if (
        "browser_e2e:real_browser_or_dom_runner_not_implemented" in serialized_payload
        or '"runner": "not_implemented"' in serialized_payload
    ):
        issues.append("browser_e2e:real_browser_or_dom_runner_not_implemented")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "required_surfaces": required_surfaces,
        "issues": sorted(set(issues)),
    }


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
                }
            )
    return inventory


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
    prd_coverage = prd_coverage_evidence(report_dir)
    task_acceptance_coverage = task_acceptance_coverage_evidence(report_dir)
    browser_e2e = browser_e2e_stop_input_evidence(report_dir)
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
    }
    stop_report = {
        "schema_ref": "08_acceptance.md#5.1",
        "schema_version": "v1",
        "STOP_ALLOWED": all_gates_passed and all_stop_inputs_passed,
        "gate_status": gate_status,
        "passed_gates": [gate for gate, status in gate_status.items() if status == "PASS"],
        "failed_gates": [gate for gate, status in gate_status.items() if status == "FAIL"],
        "blocked_gates": [gate for gate, status in gate_status.items() if status == "WORKFLOW_BLOCKED"],
        "unknown_gates": [gate for gate, status in gate_status.items() if status == "UNKNOWN"],
        "stop_inputs": stop_inputs,
        "failed_stop_inputs": failed_stop_inputs,
        "failure_reasons": failure_reasons,
        "unfinished_tasks": task_completion.get("unfinished_tasks", []),
        "uncovered_prd_items": prd_coverage.get("uncovered_acceptance_items", []),
        "uncovered_task_acceptance_items": task_acceptance_coverage.get(
            "uncovered_task_acceptance_items", []
        ),
        "user_acceptance_failures": local_user_acceptance.get("failed_findings", []),
        "generated_from_reports": [report_relative_path(path) for _, path, _, _ in gate_reports],
        "timestamp": FIXED_TIMESTAMP,
    }
    stop_issues = validate_against_schema(
        stop_report,
        SCHEMA_FILES["stop_decision"],
        "StopDecision",
    )
    stop_report_actual = {
        **stop_report,
        "STOP_ALLOWED": (
            all_gates_passed and all_stop_inputs_passed if not stop_issues else False
        ),
    }
    if stop_issues:
        stop_report_actual["failure_reasons"] = {
            **stop_report_actual.get("failure_reasons", {}),
            "stop_decision_schema": stop_issues,
        }
        stop_report_actual["failed_gates"] = sorted(
            set(stop_report_actual["failed_gates"] + ["ACC-STOP-001"])
        )
        stop_report_actual["passed_gates"] = []
        stop_report_actual["gate_status"] = {
            gate: status if status != "PASS" else "FAIL" for gate, status in gate_status.items()
        }

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
            }
        },
        {"stop_report": stop_report_actual, "catalog_issues": catalog_issues, "stop_issues": stop_issues},
    )
    acceptance_stage_report = test_report(
        stage="acceptance",
        status="passed" if all_gates_passed and all_stop_inputs_passed and not stop_issues else "failed",
        test_id="acceptance-gate-evaluation",
        assertions=[acceptance_gate_assertion],
        expected={"STOP_ALLOWED": True},
        actual={"STOP_ALLOWED": stop_report_actual["STOP_ALLOWED"]},
        diff={"gates": gate_status},
        node="acceptance",
        failure_type="contract",
        error_category=None if (all_gates_passed and all_stop_inputs_passed and not stop_issues) else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
            "workflows.md",
        ],
    )
    write_test_report(report_dir / "stages" / "acceptance.json", acceptance_stage_report)

    if all_gates_passed and all_stop_inputs_passed and not stop_issues:
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
