import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_harness(report_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/run_harness.py",
            *args,
            "--report-dir",
            str(report_dir),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_task_021_static_and_unit_harness_reports_pass(tmp_path):
    report_dir = tmp_path / "reports"

    static_result = run_harness(
        report_dir,
        "--stage",
        "static",
        "--task-id",
        "TASK-021",
    )
    unit_result = run_harness(
        report_dir,
        "--stage",
        "unit",
        "--task-id",
        "TASK-021",
    )

    static_report = read_json(report_dir / "tasks" / "TASK-021" / "static.json")
    unit_report = read_json(report_dir / "tasks" / "TASK-021" / "unit.json")

    assert static_result.returncode == 0, static_report
    assert unit_result.returncode == 0, unit_report
    assert static_report["status"] == "passed"
    assert unit_report["status"] == "passed"


def test_task_scoped_acceptance_is_invalid_and_structured(tmp_path):
    report_dir = tmp_path / "reports"

    result = run_harness(
        report_dir,
        "--stage",
        "acceptance",
        "--task-id",
        "TASK-021",
    )

    report = read_json(report_dir / "tasks" / "TASK-021" / "acceptance.json")
    assert result.returncode != 0
    assert report["stage"] == "acceptance"
    assert report["status"] == "failed"
    assert report["assertions"][0]["id"] == "task_scoped_acceptance_forbidden"


def test_full_acceptance_keeps_stop_disallowed_without_stage_evidence(tmp_path):
    report_dir = tmp_path / "reports"

    result = run_harness(report_dir, "--stage", "acceptance")

    stop_report = read_json(report_dir / "acceptance" / "STOP_ALLOWED.json")
    assert result.returncode != 0
    assert stop_report["STOP_ALLOWED"] is False
    assert stop_report["stop_inputs"]["task_completion_status"] == "PASS"
    assert stop_report["stop_inputs"]["browser_e2e_status"] == "FAIL"
    assert "browser_e2e_status" in stop_report["failed_stop_inputs"]
    assert "browser_e2e:missing_stage_report" in stop_report["failure_reasons"]["browser_e2e_status"]
    assert stop_report["unfinished_tasks"] == []
    assert all(
        path.startswith("acceptance/ACC-STOP-") for path in stop_report["generated_from_reports"]
    )
    assert not any(
        (report_dir / "stages" / f"{stage}.json").exists()
        for stage in ["static", "unit", "contract", "api", "integration", "replay", "snapshot", "e2e"]
    )
