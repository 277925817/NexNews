# 09_harness_runbook.md

## 1. Purpose

This runbook defines the operational steps for a Codex session that executes the local Harness workflow.

Authority remains unchanged:

- State transitions: `workflows.md`
- Task queue: `tasks.md`
- Harness commands and paths: `harness.md`
- Product, API, data, UI, test and stop truth: `docs/01_prd.md` through `docs/08_acceptance.md`

This file is a procedure guide only. If it conflicts with any authority above, the authority wins.

## 2. Session Start

Every Codex session MUST start from `workflows.md`.

Startup checklist:

1. Read `workflows.md` enough to identify the current state transition.
2. Read `tasks.md` and normalize task defaults from `workflows.md`.
3. Read the selected task's source documents only.
4. Read `harness.md` for commands and report paths.
5. Run a non-product bootstrap check when needed:

```bash
python3 scripts/run_harness.py --stage static --task-id TASK-000 --report-dir /tmp/rss-harness-start
```

Do not start implementation from stale conversation memory alone.

## 3. Resume Rules

When resuming an unfinished workflow:

1. Load `tasks.md`.
2. Prefer any task with `active_state` in `PLAN`, `IMPLEMENT`, `TEST`, `REVIEW`, `FIX`, `RE_TEST` or `SUMMARIZE`.
3. If no active task exists, select the next actionable `pending` task by `workflows.md` priority order.
4. If all tasks are terminal, run full acceptance only after full-stage materialization exists.
5. Never use task-scoped reports as final stop evidence.

If task metadata is missing, fill only explicit defaults from `workflows.md`; do not infer product meaning.

## 4. PLAN Report

Before editing implementation files, write:

```text
reports/tasks/<task_id>/plan.json
```

The plan report MUST conform to `schemas/task_plan_report.schema.json` and include:

- `task_id`
- smallest scoped implementation slice
- files allowed to change
- required test stages and exact commands
- rollback boundary
- acceptance gate impact

The task node in `tasks.md` MUST record the `plan_report` path before entering `IMPLEMENT`.

## 5. Failure Triage

Always route failures from structured reports first.

Use these fields before reading free-form logs:

- `stage`
- `failure_type`
- `error_category`
- `node`
- `trace_id`
- `referenced_files`
- `expected`
- `actual`
- `diff`

Fix boundary:

- `FIX` may edit only files in the intersection of `plan_report.files` and failure `referenced_files`.
- If the failure report has no parseable `referenced_files`, enter `WORKFLOW_BLOCKED`.
- If the intersection is empty, return to `PLAN` or mark a task-local blocker.

## 6. Long-Run Checkpoints

After every task reaches `SUMMARIZE`, `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED`, record a checkpoint before selecting the next task.

Checkpoint evidence MUST be structured enough for a later Codex session to resume without conversation memory:

- active task id and status after the transition
- last workflow state and next expected state
- last harness command that was run
- latest task report or blocking report path
- latest plan report path, if any
- dirty-tree summary from `git status --short`
- timestamp from the fixed clock when the checkpoint belongs to harness evidence, otherwise local diagnostic time is allowed only as non-gate metadata

Resume rule:

- Reload `tasks.md`, the latest checkpoint evidence and the linked structured reports.
- Continue only from those artifacts and `workflows.md`; do not rely on stale conversation memory.
- If checkpoint evidence and `tasks.md` disagree, `tasks.md` and structured reports win and the disagreement must be recorded as workflow metadata evidence.

## 7. Full-Stage Materialization

Before workflow `ACCEPTANCE`, run required product stages without `--task-id` in this exact order:

```bash
python3 scripts/run_harness.py --stage static --report-dir reports
python3 scripts/run_harness.py --stage unit --report-dir reports
python3 scripts/run_harness.py --stage contract --report-dir reports
python3 scripts/run_harness.py --stage api --report-dir reports
python3 scripts/run_harness.py --stage integration --report-dir reports
python3 scripts/run_harness.py --stage replay --report-dir reports
python3 scripts/run_harness.py --stage snapshot --report-dir reports
python3 scripts/run_harness.py --stage e2e --report-dir reports
```

Rules:

- Stop downstream stages after the first failed stage.
- Persist structured reports for executed and skipped stages.
- Mandatory assertion coverage for final stop may come only from `reports/stages/<stage>.json` or `reports/acceptance/ACC-STOP-*.json`.

## 8. Acceptance

Run acceptance only as a full gate evaluation:

```bash
python3 scripts/run_harness.py --stage acceptance --report-dir reports
```

Invalid command:

```bash
python3 scripts/run_harness.py --stage acceptance --task-id TASK-000 --report-dir reports
```

Acceptance may set `STOP_ALLOWED = true` only when:

- every required stage report exists and passed
- every required `ACC-STOP-*` report exists and passed
- every mandatory assertion ID from `docs/07_test_spec.md#2.16` is covered by allowed full-stage or acceptance evidence
- `tasks.md` did not change during acceptance evaluation

## 9. Final Response

A final delivery response must include:

- changed files
- final status of `ACC-STOP-001` through `ACC-STOP-010`
- evidence paths
- whether `STOP_ALLOWED` is true
- unresolved gates or blocked states, if any

Do not claim delivery while any required gate is failed, unknown, blocked or missing evidence.
