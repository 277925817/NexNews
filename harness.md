# harness.md

## 1. Purpose

This file defines the local execution interface for the Codex Harness.

`workflows.md` defines state transitions. `docs/07_test_spec.md` defines test semantics. `docs/08_acceptance.md` defines stop gates. This file defines how those stages must be executed locally and where machine-readable evidence must be written.

For session-level startup, resume, failure triage and pre-acceptance procedure, use `docs/09_harness_runbook.md`. If this command contract conflicts with that runbook, this file wins.

If a required command in this file is missing, the workflow state is `WORKFLOW_BLOCKED` unless the missing command is explicitly in the selected task scope. If the command exists but cannot run because the environment is unavailable, the workflow state is `ENV_BLOCKED`.

## 2. Stage Enum

Harness stages are exactly:

```text
static
unit
contract
api
integration
replay
snapshot
e2e
acceptance
```

The required acceptance stages are:

```text
static -> unit -> contract -> api -> integration -> replay -> snapshot -> e2e
```

`acceptance` is a gate evaluation stage. It consumes reports from the required stages and emits ACC-STOP gate reports.

## 3. Command Surface

All Harness commands must be runnable from the repository root.

```bash
python3 scripts/run_harness.py --stage static --report-dir reports
python3 scripts/run_harness.py --stage unit --report-dir reports
python3 scripts/run_harness.py --stage contract --report-dir reports
python3 scripts/run_harness.py --stage api --report-dir reports
python3 scripts/run_harness.py --stage integration --report-dir reports
python3 scripts/run_harness.py --stage replay --report-dir reports
python3 scripts/run_harness.py --stage snapshot --report-dir reports
python3 scripts/run_harness.py --stage e2e --report-dir reports
python3 scripts/run_harness.py --stage acceptance --report-dir reports
```

Task-scoped runs add a task id and write task evidence instead of full-stage evidence. Task-scoped runs are allowed only for product verification stages: `static`, `unit`, `contract`, `api`, `integration`, `replay`, `snapshot`, and `e2e`.

```bash
python3 scripts/run_harness.py --stage static --task-id TASK-000 --report-dir reports
```

`python3 scripts/run_harness.py --stage acceptance --task-id TASK-000 --report-dir reports` is invalid and must fail with a structured TestReport.

`TASK-000` is responsible for creating the first runnable harness command surface and schema validation. `TASK-003` is responsible for fixture/mock inputs. `TASK-021` is responsible for implementing the acceptance evaluator. `TASK-025` is responsible for full-stage report materialization.

## 4. Report Paths

Each full-stage command without `--task-id` must write one stage-level report:

```text
reports/stages/<stage>.json
```

Full-stage reports are the only stage reports that final acceptance may consume. Task-level reports prove individual task slices and never substitute for `reports/stages/<stage>.json`.

Each task-scoped command with `--task-id` must write task evidence:

```text
reports/tasks/<task_id>/<stage>.json
```

The workflow `PLAN` state must write one task plan report:

```text
reports/tasks/<task_id>/plan.json
```

`plan.json` conforms to `workflows.md#TaskPlanReport`. It is produced by the workflow state machine, not by `scripts/run_harness.py`.

Acceptance must write one report per required gate:

```text
reports/acceptance/ACC-STOP-001.json
reports/acceptance/ACC-STOP-002.json
reports/acceptance/ACC-STOP-003.json
reports/acceptance/ACC-STOP-004.json
reports/acceptance/ACC-STOP-005.json
reports/acceptance/ACC-STOP-006.json
reports/acceptance/ACC-STOP-007.json
reports/acceptance/ACC-STOP-008.json
reports/acceptance/ACC-STOP-009.json
reports/acceptance/ACC-STOP-010.json
reports/acceptance/STOP_ALLOWED.json
```

Stage-level, task-level and ACC-STOP gate reports must conform to `docs/07_test_spec.md#6` TestReport `v2`, including `referenced_files`, `data_hash`, `artifact_paths`, and assertion `visibility`.

Assertions that prove stop-gate coverage must use the stable assertion IDs defined by `docs/07_test_spec.md#2.16`. Full acceptance may count a mandatory assertion only from a full-stage report under `reports/stages/<stage>.json` or an ACC-STOP report under `reports/acceptance/ACC-STOP-*.json`; task-level reports are progress evidence only.

`reports/acceptance/STOP_ALLOWED.json` must conform to `docs/08_acceptance.md#5.1 StopDecisionReport`; it is a stop-decision summary, not a `TestReport`.

`StopDecisionReport.generated_from_reports` must use stable relative paths, never absolute paths. Paths may be repo-relative when the default `reports` directory is used, such as `reports/acceptance/ACC-STOP-001.json`, or report-dir-relative when `--report-dir` points elsewhere, such as `acceptance/ACC-STOP-001.json`.

Machine-checkable schemas live in `schemas/`:

```text
schemas/test_report.schema.json
schemas/stop_decision.schema.json
schemas/task_plan_report.schema.json
schemas/tasks.schema.json
```

## 5. Deterministic Inputs

Harness runs must use only:

- `fixtures/rss/`
- `fixtures/articles/`
- `fixtures/llm/`
- `fixtures/sources/`
- `fixtures/clock/`
- temporary SQLite databases created under the test runner's temporary directory

Harness runs must not use:

- live RSS
- live webpages
- live LLM APIs
- production databases
- network time
- current system time as a business assertion input

## 6. Failure Routing

On failure, the stage report must include:

- `stage`
- `failure_type`
- `error_category`
- `node`
- `trace_id`
- `fixture_version`
- `mock_version`
- `expected`
- `actual`
- `diff`

Codex must use those structured fields before reading free-form logs.

## 7. Stop Gate

Codex may claim delivery only after:

1. All required stage reports exist and have `status = passed`.
2. All ACC-STOP reports exist and have `status = passed`.
3. `reports/acceptance/STOP_ALLOWED.json` contains `STOP_ALLOWED = true`.
4. `tasks.md` maps required gates only to passed tasks with existing evidence and test reports.

Any missing report is a failed or blocked gate, never a pass.
