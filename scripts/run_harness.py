#!/usr/bin/env python3
"""Local Codex Harness command surface.

This bootstrap runner intentionally fails product stages until real stage
assertions are implemented. Its job is to make failures structured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
FIXTURE_VERSION = "mvp_acceptance_fixture@v1"
MOCK_VERSION = "mvp_mock@v1"
FIXED_TIMESTAMP = "2026-06-28T09:00:00Z"
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
SCHEMA_FILES = {
    "test_report": Path("schemas/test_report.schema.json"),
    "stop_decision": Path("schemas/stop_decision.schema.json"),
    "task_plan_report": Path("schemas/task_plan_report.schema.json"),
    "tasks": Path("schemas/tasks.schema.json"),
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
    return {
        str(gate)
        for gate in as_list(task.get("acceptance_gate"))
        if isinstance(gate, str)
    }


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

    def visit(task_id: str, path: list[str]) -> None:
        if task_id in visiting:
            cycle = path[path.index(task_id) :] + [task_id]
            issues.append(f"tasks.md:dependency_cycle:{'->'.join(cycle)}")
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        path.append(task_id)
        for dependency in dependency_map.get(task_id, []):
            if dependency in dependency_map:
                visit(dependency, path)
        path.pop()
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(dependency_map):
        visit(task_id, [])

    pre_product_tasks = {"TASK-000", "TASK-001", "TASK-003", "TASK-021"}

    def reaches_task_021(task_id: str, seen: set[str] | None = None) -> bool:
        if task_id == "TASK-021":
            return True
        seen = seen or set()
        if task_id in seen:
            return False
        seen.add(task_id)
        return any(
            reaches_task_021(dependency, seen)
            for dependency in dependency_map.get(task_id, [])
        )

    for task_id in sorted(id_set - pre_product_tasks):
        if not reaches_task_021(task_id):
            issues.append(f"tasks.md:{task_id}:does_not_depend_on_TASK-021")

    return sorted(issues)


def run_task_static_bootstrap(report_dir: Path, task_id: str) -> int:
    required_paths = [
        Path("workflows.md"),
        Path("harness.md"),
        Path("docs/07_test_spec.md"),
        Path("docs/08_acceptance.md"),
        Path("scripts/run_harness.py"),
        SCHEMA_FILES["test_report"],
        SCHEMA_FILES["stop_decision"],
        SCHEMA_FILES["task_plan_report"],
        SCHEMA_FILES["tasks"],
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

    preliminary_status = (
        "failed"
        if (
            missing_paths
            or schema_file_issues
            or tasks_schema_issues
            or task_dag_semantic_issues
            or traceability_issues
        )
        else "passed"
    )
    preliminary_report = test_report(
        stage="static",
        status=preliminary_status,
        test_id=f"{task_id.lower()}-harness-contract",
        assertions=initial_assertions,
        expected={"required_paths_exist": True, "schema_issues": []},
        actual={
            "required_paths_exist": not missing_paths,
            "schema_file_issues": schema_file_issues,
            "tasks_schema_issues": tasks_schema_issues,
            "task_dag_semantic_issues": task_dag_semantic_issues,
            "traceability_issues": traceability_issues,
        },
        diff={
            "missing_paths": missing_paths,
            "schema_file_issues": schema_file_issues,
            "tasks_schema_issues": tasks_schema_issues,
            "task_dag_semantic_issues": task_dag_semantic_issues,
            "traceability_issues": traceability_issues,
        },
        failure_type=None if preliminary_status == "passed" else "contract",
        error_category=None if preliminary_status == "passed" else "validation",
        referenced_files=[str(path) for path in required_paths] + ["tasks.md"],
    )
    report_schema_issues = validate_against_schema(
        preliminary_report,
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
    report = test_report(
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
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if status == "passed" else 1


def run_product_stage(report_dir: Path, stage: str, task_id: str | None) -> int:
    if task_id == "TASK-000" and stage == "static":
        return run_task_static_bootstrap(report_dir, task_id)

    scope = "task" if task_id else "stage"
    expected = {
        "implemented_assertions": "stage-specific deterministic assertions",
        "scope": scope,
    }
    actual = {
        "implemented_assertions": "pending",
        "scope": scope,
        "task_id": task_id,
    }
    report = test_report(
        stage=stage,
        status="failed",
        test_id=f"{(task_id or 'full').lower()}-{stage}-pending",
        assertions=[
            assertion(
                "stage_assertions_implemented",
                "failed",
                expected,
                actual,
                {"reason": "harness stage assertions are not implemented yet"},
            )
        ],
        expected=expected,
        actual=actual,
        diff={"reason": "harness stage assertions are not implemented yet"},
        failure_type="contract",
        error_category="validation",
        referenced_files=[
            "scripts/run_harness.py",
            "docs/07_test_spec.md",
            "harness.md",
        ],
    )
    write_test_report(report_destination(report_dir, stage, task_id), report)
    return 1


def read_report(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


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


def validate_json_schema_file(schema_path: Path) -> list[str]:
    schema, issues = read_json_object(schema_path)
    if issues:
        return issues
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return [f"{schema_path.as_posix()}:invalid_schema:{error.message}"]
    return []


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


def leak_issues_for_assertion(assertion_item: dict[str, Any], index: int) -> list[str]:
    visibility = assertion_item.get("visibility")
    payload = {
        "expected": assertion_item.get("expected"),
        "actual": assertion_item.get("actual"),
        "diff": assertion_item.get("diff"),
    }
    issues: list[str] = []
    for path, key, value in iter_json_paths(payload):
        lowered_key = key.lower()
        if lowered_key in FORBIDDEN_CONTEXTUAL_FIELDS:
            issues.append(f"assertion_{index}_contextual_field_at_{path}")
        for pattern in FORBIDDEN_TOKEN_PATTERNS:
            if pattern in lowered_key:
                issues.append(f"assertion_{index}_sensitive_key_at_{path}")
        if visibility == "public_surface" and lowered_key in FORBIDDEN_PUBLIC_FIELDS:
            issues.append(f"assertion_{index}_public_forbidden_field_at_{path}")
        if isinstance(value, str):
            lowered_value = value.lower()
            if len(value) > 1024:
                issues.append(f"assertion_{index}_long_text_at_{path}")
            for pattern in FORBIDDEN_CONTEXTUAL_FIELDS | FORBIDDEN_TOKEN_PATTERNS:
                if pattern in lowered_value:
                    issues.append(f"assertion_{index}_sensitive_value_at_{path}")
    return issues


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
    data_hash = report.get("data_hash")
    if not isinstance(data_hash, str) or not data_hash.startswith("sha256:"):
        issues.append("data_hash_invalid")
    if not isinstance(report.get("artifact_paths"), list):
        issues.append("artifact_paths_missing")

    assertions = report.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        issues.append("assertions_missing")
    else:
        for index, item in enumerate(assertions):
            visibility = item.get("visibility")
            if visibility not in REPORT_VISIBILITY_VALUES:
                issues.append(f"assertion_{index}_visibility_invalid")
            else:
                issues.extend(leak_issues_for_assertion(item, index))
    return issues


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
        if stage != table_stage:
            issues.append(f"{assertion_id}:stage_mismatch:{stage}!={table_stage}")
        if gate != table_gate:
            issues.append(f"{assertion_id}:gate_mismatch:{gate}!={table_gate}")
        if assertion_id in catalog:
            issues.append(f"{assertion_id}:duplicate")
        catalog[assertion_id] = {
            "stage": table_stage,
            "gate": table_gate,
            "visibility": match.group("visibility"),
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


def validate_mandatory_assertion_traceability(
    tasks_payload: dict[str, Any] | None,
) -> list[str]:
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


def mandatory_assertion_coverage(report_dir: Path) -> dict[str, Any]:
    catalog, catalog_issues = mandatory_assertion_catalog()
    seen: dict[str, list[dict[str, str]]] = {}

    for stage in REQUIRED_PRODUCT_STAGES:
        report = read_report(report_dir / "stages" / f"{stage}.json")
        if not isinstance(report, dict):
            continue
        for assertion_item in report.get("assertions", []):
            if not isinstance(assertion_item, dict):
                continue
            assertion_id = assertion_item.get("id")
            if not isinstance(assertion_id, str):
                continue
            seen.setdefault(assertion_id, []).append(
                {
                    "stage": str(report.get("stage")),
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
        statuses = {item["status"] for item in observations}
        stages = {item["stage"] for item in observations}
        visibilities = {item["visibility"] for item in observations}
        if statuses != {"passed"}:
            failed_ids.append(assertion_id)
        if stages != {expected["stage"]}:
            wrong_stage_ids.append(assertion_id)
        if visibilities != {expected["visibility"]}:
            visibility_mismatch_ids.append(assertion_id)
        if len(statuses) > 1 or len(stages) > 1 or len(visibilities) > 1:
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


def required_stage_results(
    report_dir: Path,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    statuses: dict[str, str] = {}
    schema_issues: dict[str, list[str]] = {}
    for stage in REQUIRED_PRODUCT_STAGES:
        report = read_report(report_dir / "stages" / f"{stage}.json")
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
    mandatory_coverage = mandatory_assertion_coverage(report_dir)
    failed_stage_statuses = {
        stage: status
        for stage, status in stage_statuses.items()
        if status != "passed"
    }

    gate_status: dict[str, str] = {}
    generated_reports: list[str] = []
    for gate in REQUIRED_GATES:
        gate_report_path = report_dir / "acceptance" / f"{gate}.json"
        gate_diff: dict[str, Any] = {
            "failed_or_missing_stages": failed_stage_statuses,
            "schema_issues": stage_schema_issues,
            "mandatory_assertion_coverage": mandatory_coverage,
        }
        if not failed_stage_statuses:
            gate_diff["reason"] = "ACC-STOP gate evaluator is not implemented yet"
        gate_status[gate] = "FAIL"
        gate_report = test_report(
            stage="acceptance",
            status="failed",
            test_id=gate,
            assertions=[
                assertion(
                    "A-acceptance-ACC-STOP-001-mandatory-catalog-covered"
                    if gate == "ACC-STOP-001"
                    else f"{gate.lower()}_required_stage_reports_passed",
                    "failed",
                    {"required_stage_statuses": "passed"},
                    {"required_stage_statuses": stage_statuses},
                    gate_diff,
                )
            ],
            expected={"required_stage_statuses": "passed"},
            actual={"required_stage_statuses": stage_statuses},
            diff=gate_diff,
            node="acceptance",
            failure_type="contract",
            error_category="validation",
            referenced_files=[
                "scripts/run_harness.py",
                "docs/07_test_spec.md",
                "docs/08_acceptance.md",
                "harness.md",
            ],
        )
        write_test_report(gate_report_path, gate_report)
        generated_reports.append(report_relative_path(gate_report_path))

    acceptance_stage_report = test_report(
        stage="acceptance",
        status="failed",
        test_id="acceptance-gate-evaluation",
        assertions=[
            assertion(
                "all_required_gates_passed",
                "failed",
                {"gate_status": "PASS"},
                {"gate_status": gate_status},
                {"failed_gates": REQUIRED_GATES},
            )
        ],
        expected={"gate_status": "PASS"},
        actual={"gate_status": gate_status},
        diff={"failed_gates": REQUIRED_GATES},
        node="acceptance",
        failure_type="contract",
        error_category="validation",
        referenced_files=[
            "scripts/run_harness.py",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
            "harness.md",
        ],
    )
    write_test_report(report_dir / "stages" / "acceptance.json", acceptance_stage_report)

    stop_report = {
        "schema_ref": "08_acceptance.md#5.1",
        "schema_version": "v1",
        "STOP_ALLOWED": False,
        "gate_status": gate_status,
        "passed_gates": [],
        "failed_gates": REQUIRED_GATES,
        "blocked_gates": [],
        "unknown_gates": [],
        "generated_from_reports": generated_reports,
        "timestamp": FIXED_TIMESTAMP,
    }
    write_json(report_dir / "acceptance" / "STOP_ALLOWED.json", stop_report)
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
