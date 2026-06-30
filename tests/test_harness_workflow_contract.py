import json
import subprocess
import sys
import importlib.util
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


def load_harness_module():
    spec = importlib.util.spec_from_file_location(
        "run_harness",
        ROOT / "scripts" / "run_harness.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_unimplemented_product_stage_cannot_pass_with_synthetic_report(tmp_path):
    report_dir = tmp_path / "reports"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_harness.py",
            "--stage",
            "unit",
            "--report-dir",
            str(report_dir),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    report = json.loads((report_dir / "stages" / "unit.json").read_text())

    assert result.returncode != 0
    assert report["status"] == "failed"
    assert report["test_id"] == "full-unit-synthetic-blocked"
    assert "synthetic_stage_report_blocked" in report["failure_reasons"]
    assert all(assertion["status"] == "failed" for assertion in report["assertions"])


def test_passed_product_stage_report_cannot_use_scaffold_or_synthetic_test_id():
    harness = load_harness_module()
    report = harness.test_report(
        stage="api",
        status="passed",
        test_id="full-api-scaffold",
        assertions=[
            harness.assertion(
                "A-api-ACC-STOP-004-refresh-contract",
                "passed",
                {"behavior": "real api assertions"},
                {"behavior": "placeholder scaffold"},
                {},
                visibility="public_surface",
            )
        ],
        expected={"stage": "api"},
        actual={"stage": "api"},
        referenced_files=["scripts/run_harness.py"],
    )

    issues = harness.validate_test_report(report)

    assert "synthetic_or_scaffold_report_cannot_pass" in issues


def test_round_summary_schema_requires_round_end_decision():
    schema = json.loads((ROOT / "schemas/round_summary_report.schema.json").read_text())
    validator = Draft202012Validator(schema)

    report = {
        "schema_ref": "workflows.md#RoundSummaryReport",
        "schema_version": "v1",
        "task_id": "TASK-001",
        "round_index": 1,
        "completed_round_count": 1,
        "completed_work": ["Created the minimal runtime skeleton."],
        "prd_items": ["docs/02_arch.md#6"],
        "changed_files": ["backend/app/main.py"],
        "test_results": [
            {
                "stage": "static",
                "status": "passed",
                "report": "reports/tasks/TASK-001/static.json",
                "commands": [
                    "python3 scripts/run_harness.py --stage static --task-id TASK-001 --report-dir reports"
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
            "report": "reports/tasks/TASK-001/review.json",
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
            "report": "reports/tasks/TASK-001/fix_optimize.json",
            "blocking_findings_resolved": True,
            "optimization_rationale": "No scoped optimization was required.",
            "changed_files": [],
            "retest_reports": ["reports/tasks/TASK-001/static.json"],
            "regression_detected": False,
        },
        "issues_found_and_fixed": ["none"],
        "current_system_completion": "Runtime skeleton complete.",
        "remaining_gaps_and_risks": ["Product pipeline is not implemented."],
        "next_round_goal": "Implement local config fixtures mocks.",
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
                    "evidence_paths": ["reports/tasks/TASK-001/static.json"],
                },
                "critical_security_blocking_risks": {
                    "status": "pass",
                    "decision": "check_next_branch",
                    "evidence_paths": ["reports/tasks/TASK-001/review.json"],
                },
                "prd_core_flow": {
                    "status": "fail",
                    "decision": "implement_prd_core_submodule",
                    "evidence_paths": ["reports/tasks/TASK-001/summary.json"],
                },
                "quality_gates": {
                    "status": "not_checked",
                    "decision": "check_next_branch",
                    "evidence_paths": ["reports/tasks/TASK-001/summary.json"],
                },
                "stop_conditions": {
                    "status": "not_checked",
                    "decision": "continue_next_round",
                    "evidence_paths": ["reports/tasks/TASK-001/summary.json"],
                },
            },
            "selected_next_state": "LOAD_TASKS",
            "selected_next_target": "TASK-003",
            "selected_reason": "PRD core flow remains incomplete.",
        },
        "timestamp": "2026-06-28T09:00:00Z",
    }

    assert list(validator.iter_errors(report)) == []

    incomplete_report = dict(report)
    incomplete_report.pop("round_end_decision")

    errors = list(validator.iter_errors(incomplete_report))
    assert any("round_end_decision" in error.message for error in errors)

    done_report = dict(report)
    done_report["round_end_decision"] = {
        **report["round_end_decision"],
        "selected_next_state": "DONE",
    }

    done_errors = list(validator.iter_errors(done_report))
    assert any("DONE" in error.message for error in done_errors)


def test_review_and_fix_optimize_schemas_enforce_passed_evidence():
    review_schema = json.loads((ROOT / "schemas/review_report.schema.json").read_text())
    fix_schema = json.loads((ROOT / "schemas/fix_optimize_report.schema.json").read_text())
    review_validator = Draft202012Validator(review_schema)
    fix_validator = Draft202012Validator(fix_schema)

    review_report = {
        "schema_ref": "workflows.md#ReviewReport",
        "schema_version": "v1",
        "task_id": "TASK-026A",
        "status": "passed",
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
        "referenced_files": ["schemas/review_report.schema.json"],
        "timestamp": "2026-06-28T09:00:00Z",
    }
    assert list(review_validator.iter_errors(review_report)) == []

    bad_review = {
        **review_report,
        "blocking_findings": ["unresolved issue"],
    }
    assert list(review_validator.iter_errors(bad_review))

    fix_report = {
        "schema_ref": "workflows.md#FixOptimizeReport",
        "schema_version": "v1",
        "task_id": "TASK-026A",
        "status": "passed",
        "blocking_findings_resolved": True,
        "optimization_rationale": "No scoped optimization was required.",
        "changed_files": [],
        "retest_reports": ["reports/tasks/TASK-026A/static.json"],
        "regression_detected": False,
        "referenced_files": ["schemas/fix_optimize_report.schema.json"],
        "timestamp": "2026-06-28T09:00:00Z",
    }
    assert list(fix_validator.iter_errors(fix_report)) == []

    bad_fix = {
        **fix_report,
        "retest_reports": [],
    }
    assert list(fix_validator.iter_errors(bad_fix))


def test_stop_decision_schema_requires_round_evidence_for_stop_allowed():
    harness = load_harness_module()
    schema = json.loads((ROOT / "schemas/stop_decision.schema.json").read_text())
    validator = Draft202012Validator(schema)

    missing_round_evidence = harness.sample_stop_decision_report(
        include_round_evidence=False
    )
    assert list(validator.iter_errors(missing_round_evidence))

    bad_round_policy = harness.sample_stop_decision_report(
        stop_allowed=True,
        round_policy_status="FAIL",
        include_round_evidence=True,
    )
    assert list(validator.iter_errors(bad_round_policy))


def test_coverage_schemas_reject_passed_reports_with_uncovered_items():
    prd_schema = json.loads((ROOT / "schemas/prd_coverage.schema.json").read_text())
    task_schema = json.loads((ROOT / "schemas/task_acceptance_coverage.schema.json").read_text())
    prd_validator = Draft202012Validator(prd_schema)
    task_validator = Draft202012Validator(task_schema)

    prd_report = {
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
        "timestamp": "2026-06-28T09:00:00Z",
    }
    assert list(prd_validator.iter_errors(prd_report))

    task_report = {
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
        "timestamp": "2026-06-28T09:00:00Z",
    }
    assert list(task_validator.iter_errors(task_report))


def test_task_scoped_hardening_commands_pass(tmp_path):
    report_dir = tmp_path / "reports"
    commands = [
        ["--stage", "static", "--task-id", "TASK-026A"],
        ["--stage", "unit", "--task-id", "TASK-026B"],
        ["--stage", "unit", "--task-id", "TASK-026C"],
    ]

    for command in commands:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/run_harness.py",
                *command,
                "--report-dir",
                str(report_dir),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr + result.stdout
