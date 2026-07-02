import ast
import json
import subprocess
import sys
import importlib.util
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


def load_harness_module():
    spec = importlib.util.spec_from_file_location(
        "harness_executor",
        ROOT / "scripts" / "harness" / "executor.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_task_026a_harness_helpers_follow_function_line_limit():
    source = (ROOT / "scripts" / "harness" / "executor.py").read_text()
    module = ast.parse(source)
    target_names = {"sample_round_summary_report", "run_task_026a_static"}
    function_lengths = {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name in target_names
    }

    assert function_lengths.keys() == target_names
    assert all(length <= 60 for length in function_lengths.values()), function_lengths


def test_task_026b_harness_owner_follows_function_line_limit():
    source = (ROOT / "scripts" / "harness" / "executor.py").read_text()
    module = ast.parse(source)
    target_names = {"run_task_026b_unit"}
    function_lengths = {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name in target_names
    }

    assert function_lengths.keys() == target_names
    assert all(length <= 60 for length in function_lengths.values()), function_lengths


def test_run_harness_entrypoint_is_thin_cli_adapter():
    source = (ROOT / "scripts" / "run_harness.py").read_text()
    assert len(source.splitlines()) <= 300
    assert "scripts.harness.executor" in source
    assert "argparse" in source


def test_observability_artifacts_are_written_with_test_reports(tmp_path):
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
    events_path = report_dir / "observability" / "events.jsonl"
    metrics_path = report_dir / "observability" / "metrics.json"
    traces_path = report_dir / "observability" / "traces.jsonl"
    index_path = report_dir / "observability" / "index.json"

    assert result.returncode == 0, report
    assert events_path.exists()
    assert metrics_path.exists()
    assert traces_path.exists()
    assert index_path.exists()
    assert "observability/events.jsonl" in report["artifact_paths"]
    assert "observability/metrics.json" in report["artifact_paths"]
    event = json.loads(events_path.read_text().splitlines()[0])
    assert set(event) == {
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
    }
    metrics = json.loads(metrics_path.read_text())
    assert metrics["reports_by_stage_status"]["unit"]["passed"] == 1


def test_harness_inspect_failures_reports_failed_assertions(tmp_path):
    report_dir = tmp_path / "reports"
    stage_dir = report_dir / "stages"
    stage_dir.mkdir(parents=True)
    failed_report = {
        "schema_ref": "07_test_spec.md#6",
        "schema_version": "v2",
        "test_id": "diagnostic-api-regression",
        "stage": "api",
        "status": "failed",
        "failure_type": "api",
        "error_category": "validation",
        "trace_id": "inspect-trace-1",
        "fixture_set": "mvp_acceptance_fixture@v1",
        "mock_set": "mvp_mock@v2_ai_value_filter",
        "clock_source": "fixed_clock_fixture@v1",
        "fixture_version": "mvp_acceptance_fixture@v1",
        "mock_version": "mvp_mock@v2_ai_value_filter",
        "commands": ["python3 scripts/run_harness.py --stage api --report-dir reports"],
        "case_count": 1,
        "passed_count": 0,
        "failed_count": 1,
        "skipped_count": 0,
        "pass_rate": 0,
        "failure_reasons": ["A-api-ACC-STOP-004-refresh-contract"],
        "repair_status": "unresolved",
        "regression_detected": True,
        "referenced_files": ["backend/app/main.py"],
        "data_hash": "sha256:" + "0" * 64,
        "artifact_paths": ["stages/api.json"],
        "assertions": [
            {
                "id": "A-api-ACC-STOP-004-refresh-contract",
                "type": "api_response",
                "visibility": "public_surface",
                "status": "failed",
                "expected": {"status": 200},
                "actual": {"status": 500},
                "diff": {"status": "500 != 200"},
                "leak_detection": {
                    "method": "structured_field_scan",
                    "target": "api_json",
                    "forbidden_field_count": 0,
                    "sensitive_content_count": 0,
                    "matched_paths": [],
                },
            }
        ],
        "expected": {"status": 200},
        "actual": {"status": 500},
        "diff": {"status": "500 != 200"},
        "node": "API",
        "timestamp": "2026-06-28T09:00:00Z",
    }
    (stage_dir / "api.json").write_text(json.dumps(failed_report))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness_inspect.py",
            "--report-dir",
            str(report_dir),
            "failures",
            "--json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["failures"][0]["assertion_id"] == "A-api-ACC-STOP-004-refresh-contract"
    assert payload["failures"][0]["trace_id"] == "inspect-trace-1"
    assert payload["failures"][0]["rerun_command"] == (
        f"{sys.executable} scripts/run_harness.py --stage api --report-dir {report_dir}"
    )


def test_full_unit_stage_materializes_without_synthetic_report(tmp_path):
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

    assert result.returncode == 0
    assert report["status"] == "passed"
    assert report["test_id"] == "full-unit-materialized"
    assert "synthetic_stage_report_blocked" not in report["failure_reasons"]
    assert all(assertion["status"] == "passed" for assertion in report["assertions"])


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


def test_frontend_endpoint_evidence_ignores_generated_outputs(tmp_path, monkeypatch):
    harness = load_harness_module()
    (tmp_path / "frontend" / "src" / "api").mkdir(parents=True)
    (tmp_path / "frontend" / "node_modules" / "vite").mkdir(parents=True)
    (tmp_path / "frontend" / "dist" / "assets").mkdir(parents=True)
    (tmp_path / "index.html").write_text('/frontend/src/main.tsx')
    (tmp_path / "frontend" / "index.html").write_text('/src/main.tsx')
    (tmp_path / "frontend" / "vite.config.ts").write_text("react()")
    (tmp_path / "frontend" / "src" / "api" / "news.ts").write_text(
        "fetch('/api/home'); fetch('/api/refresh'); fetch('/api/sources'); fetch('/api/news/1')"
    )
    (tmp_path / "frontend" / "node_modules" / "vite" / "internal.js").write_text(
        "const legacy = '/rss'"
    )
    (tmp_path / "frontend" / "dist" / "assets" / "app.js").write_text(
        "const legacy = '/api/feeds'"
    )

    monkeypatch.chdir(tmp_path)

    evidence = harness.frontend_endpoint_evidence()

    assert evidence["issues"] == []
    assert not any("/node_modules/" in path for path in evidence["scanned_files"])
    assert not any("/dist/" in path for path in evidence["scanned_files"])


def test_local_user_acceptance_requires_deployed_browser_smoke(tmp_path):
    harness = load_harness_module()
    report_dir = tmp_path / "reports"
    e2e_assertions = [
        harness.assertion(
            assertion_id,
            "passed",
            {"surface": surface},
            {"surface": surface},
            {},
            visibility="public_surface",
        )
        for surface, assertion_ids in harness.E2E_SURFACE_ASSERTION_MAP.items()
        for assertion_id in assertion_ids
    ]
    e2e_report = harness.test_report(
        stage="e2e",
        status="passed",
        test_id="deployed-smoke-e2e-fixture",
        assertions=e2e_assertions,
        expected={"surfaces": "covered"},
        actual={"surfaces": "covered"},
        referenced_files=["scripts/run_harness.py"],
    )
    e2e_path = report_dir / "stages" / "e2e.json"
    e2e_path.parent.mkdir(parents=True)
    e2e_path.write_text(json.dumps(e2e_report))

    harness.ensure_local_user_acceptance_report(report_dir)

    local_report = json.loads(
        (report_dir / "acceptance" / "local_user_acceptance.json").read_text()
    )
    summaries = [finding["summary"] for finding in local_report["failed_findings"]]
    assert local_report["status"] == "failed"
    assert "deployed_browser_smoke:missing_report" in summaries


def write_passing_e2e_and_deployed_smoke(harness, report_dir: Path) -> None:
    e2e_assertions = [
        harness.assertion(
            assertion_id,
            "passed",
            {"surface": surface},
            {"surface": surface},
            {},
            visibility="public_surface",
        )
        for surface, assertion_ids in harness.E2E_SURFACE_ASSERTION_MAP.items()
        for assertion_id in assertion_ids
    ]
    e2e_report = harness.test_report(
        stage="e2e",
        status="passed",
        test_id="local-acceptance-preservation-e2e-fixture",
        assertions=e2e_assertions,
        expected={"surfaces": "covered"},
        actual={"surfaces": "covered"},
        referenced_files=["scripts/run_harness.py"],
    )
    e2e_path = report_dir / "stages" / "e2e.json"
    e2e_path.parent.mkdir(parents=True)
    e2e_path.write_text(json.dumps(e2e_report))

    smoke_path = report_dir / "acceptance" / "deployed_browser_smoke.json"
    smoke_path.parent.mkdir(parents=True)
    smoke_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "local_url": harness.DEPLOYED_BROWSER_SMOKE_URL,
                "port": harness.DEPLOYED_BROWSER_SMOKE_PORT,
                "checked_surfaces": harness.E2E_REQUIRED_SURFACES,
                "failed_findings": [],
                "browser": {
                    "http_status": 200,
                    "api_home_status": 200,
                    "root_child_count": 1,
                    "body_text_length": 100,
                    "app_shell_exists": True,
                    "news_card_count": 1,
                    "rank_item_count": 1,
                    "console_error_count": 0,
                    "page_error_count": 0,
                    "screenshot_path": "acceptance/deployed_browser_smoke.png",
                },
            }
        )
    )


def test_local_user_acceptance_preserves_unresolved_user_findings(tmp_path):
    harness = load_harness_module()
    report_dir = tmp_path / "reports"
    write_passing_e2e_and_deployed_smoke(harness, report_dir)

    local_path = report_dir / "acceptance" / "local_user_acceptance.json"
    local_path.write_text(
        json.dumps(
            {
                "schema_ref": "workflows.md#LocalUserAcceptanceReport",
                "schema_version": "v1",
                "status": "failed",
                "local_url": harness.DEPLOYED_BROWSER_SMOKE_URL,
                "port": harness.DEPLOYED_BROWSER_SMOKE_PORT,
                "database": {"kind": "sqlite"},
                "checked_surfaces": harness.E2E_REQUIRED_SURFACES,
                "failed_findings": [
                    {
                        "id": "LUAF-unresolved-original-link",
                        "surface": "article_view",
                        "severity": "critical",
                        "summary": "Original link is still a placeholder.",
                        "evidence": "manual acceptance",
                        "regression_assertion_id": "A-api-ACC-STOP-004-original-url-real-link",
                    }
                ],
                "timestamp": "2026-06-30T14:34:25Z",
            }
        )
    )

    harness.ensure_local_user_acceptance_report(report_dir)

    local_report = json.loads(local_path.read_text())
    assert local_report["status"] == "failed"
    assert [finding["id"] for finding in local_report["failed_findings"]] == [
        "LUAF-unresolved-original-link"
    ]


def test_local_user_acceptance_clears_finding_after_regression_assertion_passes(tmp_path):
    harness = load_harness_module()
    report_dir = tmp_path / "reports"
    write_passing_e2e_and_deployed_smoke(harness, report_dir)

    api_report = harness.test_report(
        stage="api",
        status="passed",
        test_id="original-url-regression-fixture",
        assertions=[
            harness.assertion(
                "A-api-ACC-STOP-004-original-url-real-link",
                "passed",
                {"original_url": "non_placeholder"},
                {"original_url": "non_placeholder"},
                {},
                visibility="public_surface",
            )
        ],
        expected={"original_url": "non_placeholder"},
        actual={"original_url": "non_placeholder"},
        referenced_files=["scripts/run_harness.py"],
    )
    api_path = report_dir / "stages" / "api.json"
    api_path.parent.mkdir(parents=True, exist_ok=True)
    api_path.write_text(json.dumps(api_report))

    local_path = report_dir / "acceptance" / "local_user_acceptance.json"
    local_path.write_text(
        json.dumps(
            {
                "schema_ref": "workflows.md#LocalUserAcceptanceReport",
                "schema_version": "v1",
                "status": "failed",
                "local_url": harness.DEPLOYED_BROWSER_SMOKE_URL,
                "port": harness.DEPLOYED_BROWSER_SMOKE_PORT,
                "database": {"kind": "sqlite"},
                "checked_surfaces": harness.E2E_REQUIRED_SURFACES,
                "failed_findings": [
                    {
                        "id": "LUAF-resolved-original-link",
                        "surface": "article_view",
                        "severity": "critical",
                        "summary": "Original link is still a placeholder.",
                        "evidence": "manual acceptance",
                        "regression_assertion_id": "A-api-ACC-STOP-004-original-url-real-link",
                    }
                ],
                "timestamp": "2026-06-30T14:34:25Z",
            }
        )
    )

    harness.ensure_local_user_acceptance_report(report_dir)

    local_report = json.loads(local_path.read_text())
    assert local_report["status"] == "passed"
    assert local_report["failed_findings"] == []


def test_deployed_browser_smoke_script_records_runtime_assertions():
    script_path = ROOT / "scripts" / "run_deployed_browser_smoke.py"

    assert script_path.exists()

    text = script_path.read_text()
    assert "deployed_browser_smoke.json" in text
    assert "console_error_count" in text
    assert "page_error_count" in text
    assert "root_child_count" in text
    assert "news_card_count" in text
    assert "rank_item_count" in text
    assert "body_background" in text
    assert "app_shell_background" in text
    assert "high_score_card_background" in text
    assert "high_score_card_border_color" in text


def test_e2e_deployed_runtime_probe_flags_api_home_proxy_failure(monkeypatch):
    harness = load_harness_module()

    class FakeResponse:
        def __init__(self, status: int, body: bytes, content_type: str = "application/json"):
            self.status = status
            self._body = body
            self.headers = {"content-type": content_type}

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        url = request.full_url
        if url == "http://127.0.0.1:8010/":
            return FakeResponse(200, b"<!doctype html>", "text/html")
        if url == "http://127.0.0.1:8010/api/home":
            return FakeResponse(500, b"", "text/plain")
        raise AssertionError(url)

    monkeypatch.setattr(harness.urllib.request, "urlopen", fake_urlopen)

    observed = harness.deployed_runtime_http_probe()

    assert observed["checks"]["index_status"] == 200
    assert observed["checks"]["api_home_status"] == 500
    assert "deployed_runtime:api_home_status=500" in observed["issues"]


def test_materialized_e2e_stage_includes_deployed_runtime_probe(monkeypatch):
    harness = load_harness_module()

    monkeypatch.setattr(
        harness,
        "task_024_e2e_observations",
        lambda: {"checks": {"home_news_density": True}, "issues": []},
    )
    monkeypatch.setattr(
        harness,
        "deployed_runtime_http_probe",
        lambda: {
            "checks": {"api_home_status": 500},
            "issues": ["deployed_runtime:api_home_status=500"],
        },
    )

    observed = harness.materialized_stage_behavior("e2e")

    assert observed["checks"]["deployed_runtime_http"]["checks"]["api_home_status"] == 500
    assert "deployed_runtime:api_home_status=500" in observed["issues"]


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
        "source": {"path": "tasks.md", "version": "tasks_mvp@v10"},
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


def test_task_acceptance_coverage_rejects_task_scoped_assertion_ids(tmp_path, monkeypatch):
    harness = load_harness_module()
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr(
        harness,
        "task_acceptance_inventory",
        lambda: [
            {
                "id": "TASK-999:AC-001",
                "task_id": "TASK-999",
                "source_path": "tasks.md",
                "source_line": 1,
                "acceptance_text": "Task-scoped evidence must not satisfy final stop coverage.",
                "acceptance_gate": ["ACC-STOP-003"],
                "test_scope": ["unit"],
            }
        ],
    )
    monkeypatch.setattr(
        harness,
        "traceability_assertions_by_owner",
        lambda: {"TASK-999": ["task-999-unit-local-only"]},
    )
    monkeypatch.setattr(
        harness,
        "TASK_FALLBACK_ASSERTION_MAP",
        {"TASK-999": ["task-999-unit-local-only"]},
    )

    harness.ensure_task_acceptance_coverage_report(report_dir)
    coverage = json.loads(
        (report_dir / "acceptance" / "task_acceptance_coverage.json").read_text()
    )

    assert coverage["status"] == "failed"
    assert coverage["coverage_items"][0]["assertion_ids"] == ["unmapped"]
    assert coverage["uncovered_task_acceptance_items"][0]["id"] == "TASK-999:AC-001"


def test_ai_value_tasks_have_full_stage_traceability():
    harness = load_harness_module()
    by_owner = harness.traceability_assertions_by_owner()

    expected_by_task = {
        "TASK-036": {
            "A-static-ACC-STOP-010-ai-value-doc-sync",
            "A-unit-ACC-STOP-007-ai-value-scoring-schema",
            "A-unit-ACC-STOP-003-ai-value-selection-thresholds",
            "A-integration-ACC-STOP-003-ai-value-filtering",
            "A-api-ACC-STOP-004-ai-value-home-surface",
            "A-api-ACC-STOP-009-ai-value-field-leak-scan",
        },
        "TASK-037": {
            "A-unit-ACC-STOP-007-ai-value-rubric",
            "A-integration-ACC-STOP-003-ai-value-fallback",
        },
    }

    for task_id, expected_ids in expected_by_task.items():
        actual_ids = set(by_owner.get(task_id, [])) | set(
            harness.TASK_FALLBACK_ASSERTION_MAP.get(task_id, [])
        )
        assert expected_ids <= actual_ids
        assert all(assertion_id.startswith("A-") for assertion_id in actual_ids)


def test_round_count_policy_reports_missing_lifecycle_evidence_by_task(monkeypatch):
    harness = load_harness_module()
    payload = {
        "dag": {
            "nodes": [
                {
                    "id": "TASK-999",
                    "status": "passed",
                    "summary_report": "reports/tasks/TASK-999/summary.json",
                }
            ]
        }
    }
    monkeypatch.setattr(harness, "read_yaml_object", lambda path: (payload, []))
    monkeypatch.setattr(
        harness,
        "round_evidence_for_summary",
        lambda task_id, summary_report: {
            "task_id": task_id,
            "summary_report": summary_report,
            "valid": False,
            "failure_reasons": [
                "fix_optimize:reports/tasks/TASK-999/fix_optimize.json:missing",
                "review:reports/tasks/TASK-999/review.json:missing",
                "summary:reports/tasks/TASK-999/summary.json:missing",
            ],
        },
    )

    policy = harness.round_count_policy_evidence(
        all_gates_passed=True,
        all_stop_inputs_passed=True,
        unfinished_tasks=[],
    )

    assert policy["status"] == "FAIL"
    assert "round_count:TASK-999:fix_optimize:reports/tasks/TASK-999/fix_optimize.json:missing" in policy["failure_reasons"]
    assert "round_count:TASK-999:review:reports/tasks/TASK-999/review.json:missing" in policy["failure_reasons"]
    assert "round_count:TASK-999:summary:reports/tasks/TASK-999/summary.json:missing" in policy["failure_reasons"]


def test_task_acceptance_coverage_evidence_groups_failures_by_task(tmp_path):
    harness = load_harness_module()
    report_dir = tmp_path / "reports"
    path = report_dir / "acceptance" / "task_acceptance_coverage.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_ref": "07_test_spec.md#6.4",
                "schema_version": "v1",
                "status": "failed",
                "source": {"path": "tasks.md", "version": "tasks_mvp@v10"},
                "coverage_items": [
                    {
                        "id": "TASK-999:AC-001",
                        "task_id": "TASK-999",
                        "source_path": "tasks.md",
                        "source_line": 1,
                        "acceptance_text": "example",
                        "acceptance_gate": ["ACC-STOP-003"],
                        "test_scope": ["unit"],
                        "assertion_ids": ["unmapped"],
                        "report_paths": ["reports/acceptance/ACC-STOP-001.json"],
                        "status": "uncovered",
                    }
                ],
                "uncovered_task_acceptance_items": [
                    {
                        "id": "TASK-999:AC-001",
                        "task_id": "TASK-999",
                        "source_path": "tasks.md",
                        "source_line": 1,
                        "acceptance_text": "example",
                        "acceptance_gate": ["ACC-STOP-003"],
                        "test_scope": ["unit"],
                    }
                ],
                "timestamp": "2026-06-28T09:00:00Z",
            }
        )
    )

    evidence = harness.task_acceptance_coverage_evidence(report_dir)

    assert "task_acceptance_coverage:TASK-999:failed=TASK-999:AC-001" in evidence["issues"]
    assert "task_acceptance_coverage:TASK-999:uncovered=TASK-999:AC-001" in evidence["issues"]


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
