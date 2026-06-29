# workflows.md

## 1. Overview（简述系统作用）

本文档定义 AI 新闻聚合系统 MVP 的本地开发工作流状态机。

目标是让 Codex 可以按确定性流程自动执行：

```text
Plan -> Implement -> Test -> Review -> Fix -> Re-test -> Summarize -> Iterate
```

直到 `docs/08_acceptance.md` 中 `ACC-STOP-001` 到 `ACC-STOP-010` 全部为 `PASS`，并且 `STOP_ALLOWED = true`。

本 workflow 只依赖当前仓库、项目文档、本地测试、fixture、mock 和 fixed clock。不得依赖 GitHub Actions、外部 CI、真实 RSS、真实网页、真实 LLM、生产数据库、网络时间或人工主观判断。

Source of truth:

| Area | Source |
| --- | --- |
| Product behavior | `docs/01_prd.md` |
| Architecture boundary | `docs/02_arch.md` |
| UI behavior | `docs/03_ui_spec.md` |
| Data facts | `docs/04_data_model.md` |
| API contract | `docs/05_api_contract.md` |
| Development rules | `docs/06_dev_rules.md` |
| Test execution | `docs/07_test_spec.md` |
| Harness command interface | `harness.md` |
| Stop gate | `docs/08_acceptance.md` |

Operational runbook: `docs/09_harness_runbook.md` is a procedure guide for Codex sessions. It is not a higher-priority source of truth.

MVP task source:

- Primary task queue: `tasks.md`
- If `tasks.md` is missing, Codex must create it from unresolved implementation gaps, failed test stages, and failed acceptance gates.
- A task is complete only when its scoped tests pass and it does not cause any acceptance regression.
- Tasks with `acceptance_gate: none` are workflow housekeeping tasks only. They are ignored by acceptance gate coverage and cannot satisfy any `ACC-STOP-*` gate.

`tasks.md` may use the MVP YAML DAG shape. In that shape:

- `dag.nodes[*]` are the canonical task records.
- `id` is the task id used by the workflow.
- `source`, `acceptance_gate` and `test_scope` may be arrays; a scalar value is treated as a one-item array.
- `acceptance_gate: ["ACC-STOP-001", "..."]` means the task may provide coverage for every listed gate only after the task is `passed` and has evidence and a test report.
- `test_scope: ["unit", "integration"]` means every listed stage is required for that task-scoped run.
- `acceptance` is a full-workflow gate-evaluation stage only. It is not valid in a task-scoped `test_scope`, and `python3 scripts/run_harness.py --stage acceptance --task-id ...` must fail with structured evidence.
- `depends_on` is a hard readiness gate: a task is actionable only when every dependency is `passed`.
- `plan_report` stores the persisted task plan path created during `PLAN`.
- `SUMMARIZE` updates the matching YAML node fields in `tasks.md`; it must not rewrite the task queue into another format.

Minimal task record format:

```markdown
## TASK-001 Short task title

- status: pending | in_progress | passed | task_blocked
- source: docs/01_prd.md | docs/02_arch.md | docs/03_ui_spec.md | docs/04_data_model.md | docs/05_api_contract.md | docs/06_dev_rules.md | docs/07_test_spec.md | docs/08_acceptance.md
- acceptance_gate: ACC-STOP-001 | ACC-STOP-002 | ... | none
- priority: acceptance_gate_failures | api_contract_failures | data_model_violations | test_failures | ui_failures | refactor_tasks
- test_scope: static | unit | contract | api | integration | replay | snapshot | e2e
- active_state: none | PLAN | IMPLEMENT | TEST | REVIEW | FIX | RE_TEST | SUMMARIZE
- last_updated_state: INIT | LOAD_TASKS | PLAN | IMPLEMENT | TEST | REVIEW | FIX | RE_TEST | SUMMARIZE | ACCEPTANCE | ITERATE | TASK_BLOCKED | WORKFLOW_BLOCKED | ENV_BLOCKED | DONE | none
- attempts: 0
- evidence: path/to/report.json
- test_report: path/to/test-report.json
- plan_report: path/to/plan-report.json
- intentionally_out_of_scope: false
- blocker: none
```

Task plans must be persisted as machine-readable reports before implementation:

```json
{
  "schema_ref": "workflows.md#TaskPlanReport",
  "schema_version": "v1",
  "task_id": "TASK-000",
  "scope": "smallest implementation slice for the selected task",
  "files": ["path/to/file"],
  "test_stages": ["static"],
  "test_commands": ["python3 scripts/run_harness.py --stage static --task-id TASK-000 --report-dir reports"],
  "rollback_boundary": "files and behavior that may be reverted together",
  "acceptance_gate_impact": ["ACC-STOP-001"],
  "timestamp": "ISO8601"
}
```

`TaskPlanReport` path:

```text
reports/tasks/<task_id>/plan.json
```

Machine-checkable JSON Schema:

```text
schemas/task_plan_report.schema.json
```

## 2. State Machine Definition（核心）

### 2.1 State Enum

```yaml
workflow_state_machine:
  initial_state: INIT
  terminal_success_state: DONE
  terminal_success_condition:
    source: docs/08_acceptance.md
    field: STOP_ALLOWED
    expected: true
  task_retry_limit: 3
  task_priority_order:
    - acceptance_gate_failures
    - api_contract_failures
    - data_model_violations
    - test_failures
    - ui_failures
    - refactor_tasks
  test_stage_order:
    - static
    - unit
    - contract
    - api
    - integration
    - replay
    - snapshot
    - e2e
  states:
    - INIT
    - LOAD_TASKS
    - PLAN
    - IMPLEMENT
    - TEST
    - REVIEW
    - FIX
    - RE_TEST
    - SUMMARIZE
    - ACCEPTANCE
    - ITERATE
    - TASK_BLOCKED
    - WORKFLOW_BLOCKED
    - ENV_BLOCKED
    - DONE
```

### 2.2 High-Level Flow

```text
INIT
  -> LOAD_TASKS
  -> PLAN
  -> IMPLEMENT
  -> TEST
  -> REVIEW
  -> SUMMARIZE
  -> LOAD_TASKS

On test or review failure:
  TEST/REVIEW -> FIX -> RE_TEST -> REVIEW -> SUMMARIZE

When all tasks are terminal:
  LOAD_TASKS triggers ACCEPTANCE
  if every task status is passed or task_blocked
  AND no pending/in_progress task exists -> ACCEPTANCE
  else continue task loop

ACCEPTANCE always performs full gate validation.
Previous task status or previous gate status never substitutes for rerunning `docs/08_acceptance.md`.

If acceptance fails:
  ACCEPTANCE -> ITERATE -> LOAD_TASKS

If acceptance passes with strict DONE guard:
  ACCEPTANCE -> DONE

If a task exceeds retry limit:
  FIX/RE_TEST -> TASK_BLOCKED
  unresolved task blocker remains TASK_BLOCKED
  explicit resolve_task_blocker with evidence -> LOAD_TASKS
```

### 2.3 Determinism Rules

- Task order must be stable: sort by `task_priority_order`, then by task id ascending inside the same priority bucket. The tie-breaker must always be `task_id` ascending.
- If `tasks.md` contains DAG dependencies, `LOAD_TASKS` must first filter out tasks whose `depends_on` tasks are not `passed`; blocked dependencies do not make the dependent task actionable.
- If a task lacks `priority`, `LOAD_TASKS` must derive it with one deterministic rule: failed acceptance gate mapping first; otherwise failed test stage order; otherwise canonical doc order `docs/01_prd.md -> docs/08_acceptance.md`; otherwise `refactor_tasks`. Do not use semantic guessing or multi-source fallback.
- For fields other than `priority`, missing task fields must be filled with explicit defaults, not inferred values: `status: pending`, `active_state: none`, `last_updated_state: none`, `acceptance_gate: none`, `attempts: 0`, `evidence: none`, `test_report: none`, `plan_report: none`, `intentionally_out_of_scope: false`, `blocker: none`.
- Test stage order must follow `docs/07_test_spec.md#2.13`: `static -> unit -> contract -> api -> integration -> replay -> snapshot -> e2e`.
- Each test stage must start from clean isolated state.
- Tests and acceptance must use fixture, mock and fixed clock.
- Structured reports are the source of truth. Free-form logs are diagnostic only.
- If evidence is missing, malformed or not machine-readable, the state result is `FAIL`, `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED`, never `PASS`.
- Acceptance entry is intentionally lightweight but strict. It only means the workflow should start stop-gate validation: `tasks.md` is loaded, `tasks.count > 0`, every task is in a terminal state (`passed` or `task_blocked`), no task is `pending` or `in_progress`, and no task has `active_state` in `FIX` or `RE_TEST`. Evidence, reports, mandatory assertions and gate coverage are validated inside `ACCEPTANCE`, not before it.
- Acceptance validation is mandatory on every entry: every `ACC-STOP-*` gate must be revalidated inside `ACCEPTANCE` with structured evidence, linked reports and required assertions.

## 3. States Description（逐个 state 定义）

### INIT

| Field | Definition |
| --- | --- |
| entry condition | Workflow starts, or Codex resumes an unfinished workflow. |
| actions | Read `docs/01_prd.md` to `docs/08_acceptance.md` and `harness.md`; detect available local commands; verify workspace can run local tests; check whether `tasks.md` exists. |
| exit condition | Required source documents are readable and workflow inputs are known. |
| failure handling | If a required source document is missing or unreadable, enter `WORKFLOW_BLOCKED`. If local test commands cannot run because the local environment is unavailable, enter `ENV_BLOCKED`. If local test commands are missing but can be implemented in the repo, create tasks according to `docs/07_test_spec.md`. |

### LOAD_TASKS

| Field | Definition |
| --- | --- |
| entry condition | `INIT` completed, a task was summarized, or acceptance failed and new tasks must be loaded. |
| actions | Load all of `tasks.md`; fill missing fields with explicit defaults; normalize task status and missing priority; order actionable tasks by `task_priority_order`, then task id ascending; verify task ids are unique; map each task to source docs, test scope and acceptance gate. |
| exit condition | One actionable task is selected, or acceptance entry is triggered. Acceptance entry means `tasks.md` is loaded, `tasks.count > 0`, every task is `passed` or `task_blocked`, no task is `pending` or `in_progress`, and no task has `active_state` in `FIX` or `RE_TEST`. It does not inspect evidence, report contents, assertion coverage or gate coverage. |
| failure handling | If `tasks.md` is missing, generate an MVP `tasks.md` from failed/missing acceptance gates. If task records, priority derivation or task selection cannot be normalized deterministically, enter `WORKFLOW_BLOCKED`. If no actionable task exists while any task is non-terminal, enter `WORKFLOW_BLOCKED`, not `ACCEPTANCE`. |

### PLAN

| Field | Definition |
| --- | --- |
| entry condition | A `pending` or previously failed task is selected. |
| actions | Read the task source documents; identify files likely to change; define smallest implementation slice; define expected test stage and acceptance gate impact; persist `TaskPlanReport` to `reports/tasks/<task_id>/plan.json`; update the task node `plan_report` field with that path. |
| exit condition | A deterministic task plan exists with scope, files, test commands/stages and rollback boundary, and the `TaskPlanReport` is parseable. |
| failure handling | If scope cannot be derived from documents, mark the task `task_blocked` with the missing decision and enter `TASK_BLOCKED`. If the plan report cannot be written or parsed, enter `WORKFLOW_BLOCKED`. |

### IMPLEMENT

| Field | Definition |
| --- | --- |
| entry condition | `PLAN` produced a scoped implementation plan. |
| actions | Modify only files required by the task; preserve unrelated user changes; keep implementation aligned with `docs/06_dev_rules.md`; update contract docs when behavior changes. |
| exit condition | Code or documentation changes for the task are complete and ready for local tests. |
| failure handling | If `plan_report` is missing or malformed, return to `PLAN`. If implementation exposes a contract conflict, stop coding that slice and return to `PLAN`. If the conflict is between documents, apply the priority order from `docs/06_dev_rules.md`. |

### TEST

| Field | Definition |
| --- | --- |
| entry condition | `IMPLEMENT` completed or `RE_TEST` requires a full affected stage run. |
| actions | Run runtime/data verification only: execute tests defined by `docs/07_test_spec.md` in deterministic order. Stop downstream stages on first failed stage, mark those downstream stages as `SKIPPED`, persist partial structured `TestReport` objects matching `docs/07_test_spec.md#6`, and target only the failed stage for the next fix. `SKIPPED` stages do not count toward `PASS`; a skipped required stage is acceptance failure. |
| exit condition | Required scoped tests pass, or the first failing stage emits a structured failure report. A task-scoped pass requires every stage declared for that task run to execute, every mandatory assertion that belongs to the current task/stage scope to execute, no skipped required assertion in that scope, and at least one machine-verified assertion per active stage. An active stage is a stage with mandatory assertions in the current run scope. Behavior-only stages with no mandatory assertions may pass only as structured non-assertion evidence and cannot by themselves satisfy an acceptance gate. The complete mandatory assertion catalog is enforced only by full-stage materialization and workflow `ACCEPTANCE`. `100%` total assertion coverage is a soft target, not a hard PASS condition. |
| failure handling | If tests fail, route by `stage`, `failure_type`, `error_category`, `node` and `trace_id`, then enter `FIX`. If reports are missing or invalid, treat this as `ACC-STOP-001` failure and enter `FIX`. |

### REVIEW

| Field | Definition |
| --- | --- |
| entry condition | Scoped machine tests passed and the task is not `task_blocked`. |
| actions | Run static design verification only: check code structure against `docs/02_arch.md`, `docs/03_ui_spec.md`, `docs/04_data_model.md`, `docs/05_api_contract.md` and `docs/06_dev_rules.md`; check architecture fit, schema/document diffs, dependency graph boundaries, internal field leaks and non-goal features. Treat API contract, data model and UI checks as static design/spec alignment only. Do not execute code or validate runtime outputs, API JSON responses, DB state, DOM snapshots, logs or generated reports. Do not treat review as a substitute for tests. |
| exit condition | Review finds no blocking issue, or produces a structured list of required fixes. |
| failure handling | If review fails, enter `FIX`. If review reveals a task-local conflict that cannot be resolved from priority rules, mark task `task_blocked` and enter `TASK_BLOCKED`. If review reveals workflow metadata or document consistency cannot be interpreted deterministically, enter `WORKFLOW_BLOCKED`. |

### FIX

| Field | Definition |
| --- | --- |
| entry condition | `TEST` or `REVIEW` produced a failure. |
| actions | Before changing code, record root cause hypothesis, evidence reference from the structured test/review report, and change isolation boundary. Then apply the smallest fix that addresses the highest-priority failure; increment task attempt count; avoid broad refactors; update tests or docs only when the source contract requires it. |
| exit condition | A fix is ready for regression testing, or retry limit is exceeded. |
| failure handling | If attempts exceed `task_retry_limit = 3`, mark task `task_blocked` and enter `TASK_BLOCKED`. If a fix creates a higher-priority failure, abandon that fix path and return to `PLAN`. |

### RE_TEST

| Field | Definition |
| --- | --- |
| entry condition | `FIX` completed within retry limit. |
| actions | First rerun the failed test stage; if it passes, rerun all affected earlier and later stages required by `docs/07_test_spec.md`; persist new structured reports. |
| exit condition | Regression scope passes and task can return to `REVIEW`, or failure persists and task returns to `FIX`. |
| failure handling | If the same failure persists, increment attempts and return to `FIX`. If a new failure appears, route the new failure by priority from `docs/07_test_spec.md#2.14`. |

### SUMMARIZE

| Field | Definition |
| --- | --- |
| entry condition | `REVIEW` passed for a task that is not `task_blocked`. |
| actions | Update only the MVP task summary fields in `tasks.md`: task status, `active_state: none`, evidence path, test report path and acceptance mapping through `acceptance_gate`. |
| exit condition | Task state is persisted as `passed`. |
| failure handling | If `tasks.md` cannot be updated, enter `WORKFLOW_BLOCKED` with a persistence failure. Missing task persistence must never advance to `ACCEPTANCE`. |

### ACCEPTANCE

| Field | Definition |
| --- | --- |
| entry condition | Acceptance entry is triggered: `tasks.md` is loaded, `tasks.count > 0`, every task is `passed` or `task_blocked`, no task is `pending` or `in_progress`, and no task has `active_state` in `FIX` or `RE_TEST`. |
| actions | Create one immutable `tasks_snapshot = load_tasks("tasks.md")` and `tasks_hash_before = hash_file("tasks.md")` at entry. Run exactly one full gate command: `python3 scripts/run_harness.py --stage acceptance --report-dir reports`. Do not pass `--task-id`. Always evaluate all required gates in `docs/08_acceptance.md`: `ACC-STOP-001` to `ACC-STOP-010`, using only `tasks_snapshot` for task-derived evidence and existing full-stage reports under `reports/stages/<stage>.json`. This is where gate coverage, existing evidence, linked test reports, mandatory assertions and leak checks are validated. Gate coverage may use only tasks where `status == passed`, `evidence` exists and `test_report` exists. `task_blocked` tasks must not contribute to any gate coverage. Before `DONE`, recompute `tasks_hash_after = hash_file("tasks.md")` and require `tasks_hash_before == tasks_hash_after`. Use only structured evidence allowed by `docs/08_acceptance.md#3`. Never reuse previous task status or previous gate status as a substitute for full gate validation. |
| exit condition | Every gate has status `PASS`, `FAIL`, `UNKNOWN`, `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED`, and `STOP_ALLOWED` has been computed. |
| failure handling | If any gate is `FAIL` or `UNKNOWN`, enter `ITERATE` with the failed or unproven gate evidence. If a task-local unresolved blocker prevents a gate from being proven, enter `TASK_BLOCKED`. If workflow metadata, task records or report generation logic are inconsistent, enter `WORKFLOW_BLOCKED`. If the local environment cannot execute required verification, enter `ENV_BLOCKED`. Missing evidence is a failed gate unless the evidence generator itself is unavailable. |

### ITERATE

| Field | Definition |
| --- | --- |
| entry condition | `ACCEPTANCE` did not produce `STOP_ALLOWED = true`. |
| actions | Do only three things: extract failed acceptance gates, map each failed gate to an existing task or create one new task, and order tasks by priority. If no actionable task results from that mapping, rebuild the MVP task queue from failed acceptance gates, missing test coverage and unverified contract fields, compare `rebuilt_tasks_hash` with `previous_tasks_hash`, then order it by priority. Do not pause tasks, resolve dependencies, schedule work, run tests or implement task lifecycle logic. Those concerns belong in `tasks.md` and the surrounding state transitions. |
| exit condition | New or updated tasks are available for `LOAD_TASKS`. |
| failure handling | If `rebuilt_tasks_hash == previous_tasks_hash`, classify loop type as `missing_task_mapping`, `unresolved_contract_gap` or `test_coverage_gap`, then enter `WORKFLOW_BLOCKED` for loop prevention. If no actionable task exists after the rebuild because a task-local decision is missing, enter `TASK_BLOCKED`. If no actionable task exists after the rebuild because workflow metadata is inconsistent, enter `WORKFLOW_BLOCKED`. If no actionable task exists after the rebuild because the local environment cannot run required verification, enter `ENV_BLOCKED`. |

### TASK_BLOCKED

| Field | Definition |
| --- | --- |
| entry condition | A specific task cannot proceed because retry limit was exceeded, required task input is missing, or task-local requirements conflict beyond priority rules. |
| actions | Record blocker reason, failed gate, failed test stage, evidence path, attempted fixes and required decision. Do not generate repair tasks automatically. |
| exit condition | Either unresolved blocker remains `TASK_BLOCKED`, or explicit `resolve_task_blocker` with evidence re-enters at `LOAD_TASKS`. |
| failure handling | `TASK_BLOCKED` is recoverable only through explicit blocker resolution. It never satisfies acceptance coverage and never counts as `DONE`. |

### WORKFLOW_BLOCKED

| Field | Definition |
| --- | --- |
| entry condition | Workflow metadata, task records, document availability, report generation logic, or state-machine consistency prevents deterministic execution. |
| actions | Record workflow-level blocker, affected state, failed invariant and required workflow/document repair. Do not mark any product task as passed. |
| exit condition | Either unresolved blocker remains `WORKFLOW_BLOCKED`, or explicit `resolve_workflow_blocker` with evidence re-enters at `LOAD_TASKS`. |
| failure handling | `WORKFLOW_BLOCKED` is recoverable after workflow/document repair, but it is never a pass condition and must not be skipped by `ITERATE`. |

### ENV_BLOCKED

| Field | Definition |
| --- | --- |
| entry condition | The local environment cannot run required commands or verification, and the cause cannot be fixed by editing repository files. |
| actions | Record missing environment capability, command, dependency or permission. Do not generate product repair tasks. |
| exit condition | Terminal for the current autonomous run unless the environment changes externally. |
| failure handling | `ENV_BLOCKED` requires manual intervention or external environment change. It must not re-enter the normal workflow automatically. |

### DONE

| Field | Definition |
| --- | --- |
| entry condition | `ACCEPTANCE` reads `STOP_ALLOWED == true` from `docs/08_acceptance.md`, every required gate is mapped only to `passed` tasks with existing evidence and test report, and any `task_blocked` task has `acceptance_gate: none`. |
| actions | Produce final delivery summary with changed files, required gate statuses, evidence paths and confirmation that all gates passed. |
| exit condition | Workflow stops successfully as a terminal irreversible state. |
| failure handling | No transition is allowed after `DONE`. A later acceptance failure proof must start a new workflow iteration from `ITERATE`; it must not mutate the completed run's terminal state. |

## 4. Transition Rules（状态流转规则）

| From | Condition | To |
| --- | --- | --- |
| `INIT` | Source documents readable | `LOAD_TASKS` |
| `INIT` | Required source document missing or unreadable | `WORKFLOW_BLOCKED` |
| `INIT` | Local environment cannot run required commands | `ENV_BLOCKED` |
| `LOAD_TASKS` | Actionable task exists after priority ordering | `PLAN` |
| `LOAD_TASKS` | `tasks.count > 0`, all tasks are terminal (`passed` or `task_blocked`), no task is `pending` or `in_progress`, and no task has `active_state` in `FIX` or `RE_TEST` | `ACCEPTANCE` |
| `LOAD_TASKS` | No actionable task can be selected while any task is non-terminal | `WORKFLOW_BLOCKED` |
| `LOAD_TASKS` | Malformed task records cannot be normalized | `WORKFLOW_BLOCKED` |
| `PLAN` | Plan produced | `IMPLEMENT` |
| `PLAN` | Scope cannot be resolved | `TASK_BLOCKED` |
| `IMPLEMENT` | Scoped change complete | `TEST` |
| `IMPLEMENT` | Contract conflict found | `PLAN` |
| `TEST` | Scoped tests pass with mandatory assertions executed and no skipped required assertions | `REVIEW` |
| `TEST` | Test fails or report invalid | `FIX` |
| `REVIEW` | Review passes | `SUMMARIZE` |
| `REVIEW` | Review fails | `FIX` |
| `REVIEW` | Unresolvable task-local conflict | `TASK_BLOCKED` |
| `REVIEW` | Unresolvable workflow/document conflict | `WORKFLOW_BLOCKED` |
| `FIX` | Fix complete and attempts <= 3 | `RE_TEST` |
| `FIX` | Attempts > 3 | `TASK_BLOCKED` |
| `RE_TEST` | Regression passes with mandatory assertions executed and no skipped required assertions | `REVIEW` |
| `RE_TEST` | Regression fails and attempts <= 3 | `FIX` |
| `RE_TEST` | Attempts > 3 | `TASK_BLOCKED` |
| `TASK_BLOCKED` | Task blocker unresolved | `TASK_BLOCKED` |
| `TASK_BLOCKED` | Explicit `resolve_task_blocker` completed with evidence | `LOAD_TASKS` |
| `WORKFLOW_BLOCKED` | Workflow blocker unresolved | `WORKFLOW_BLOCKED` |
| `WORKFLOW_BLOCKED` | Explicit `resolve_workflow_blocker` completed with evidence | `LOAD_TASKS` |
| `ENV_BLOCKED` | Environment unchanged | `ENV_BLOCKED` |
| `SUMMARIZE` | Task persisted as `passed` | `LOAD_TASKS` |
| `ACCEPTANCE` | `STOP_ALLOWED == true`, `tasks_hash_before == tasks_hash_after`, same `tasks_snapshot` used for gate evaluation and DONE guard, all required gates map only to `passed` tasks with existing evidence and test report, all `task_blocked` tasks have `acceptance_gate: none`, and no required stage is `SKIPPED` | `DONE` |
| `ACCEPTANCE` | Any gate failed or unknown | `ITERATE` |
| `ACCEPTANCE` | Task-local blocker prevents gate proof | `TASK_BLOCKED` |
| `ACCEPTANCE` | Workflow/report metadata blocker prevents gate proof | `WORKFLOW_BLOCKED` |
| `ACCEPTANCE` | Environment blocker prevents gate proof | `ENV_BLOCKED` |
| `ITERATE` | Tasks mapped or rebuilt from failed gates | `LOAD_TASKS` |
| `ITERATE` | No actionable task exists after rebuild because task input is missing | `TASK_BLOCKED` |
| `ITERATE` | No actionable task exists after rebuild because workflow metadata is inconsistent | `WORKFLOW_BLOCKED` |
| `ITERATE` | No actionable task exists after rebuild because environment cannot verify | `ENV_BLOCKED` |

## 5. Failure Handling Strategy（失败策略）

### 5.1 Failure Priority

When multiple failures exist, fix the highest-priority issue first:

```text
Static rule violation
-> Contract violation
-> Data model violation
-> Data leakage violation
-> Replay inconsistency
-> API behavior mismatch
-> Integration mismatch
-> Snapshot diff
-> UI visual regression
```

This follows `docs/07_test_spec.md#2.14`.

### 5.2 Retry Policy

- Each task has `task_retry_limit = 3` fix attempts.
- A retry must be based on structured report fields, not free-form guessing.
- After each fix, rerun the failed stage before broader regression.
- If the same failure persists after 3 attempts, mark the task `task_blocked`.
- `TASK_BLOCKED` tasks do not automatically transition. They remain `TASK_BLOCKED` until an explicit `resolve_task_blocker` action provides evidence. They do not enter `REVIEW`, `SUMMARIZE` or `ITERATE`, do not satisfy gate coverage and do not allow final delivery.
- `WORKFLOW_BLOCKED` may recover only after explicit workflow/document repair evidence.
- `ENV_BLOCKED` is terminal for the autonomous run unless the environment changes externally.

### 5.3 Report Policy

All test and acceptance results must produce machine-readable evidence:

- Test reports must follow `docs/07_test_spec.md#6`.
- Harness commands and report paths must follow `harness.md`.
- Acceptance gate reports must map to `ACC-STOP-001` through `ACC-STOP-010`.
- Missing report means failure.
- Invalid schema means failure.
- `failed`, `flaky` or `skipped` required reports mean failure.
- Every stage declared for the run must execute. Acceptance and full-regression runs must execute all stages in `test_stage_order`.
- Full-regression materialization runs the required product stages without `--task-id` and writes `reports/stages/static.json` through `reports/stages/e2e.json`. Task-scoped runs write only `reports/tasks/<task_id>/<stage>.json` and must not be used as substitutes for full-stage stop evidence.
- On first stage failure, downstream stages must be marked `SKIPPED`, not `NOT_RUN`, omitted or treated as `PASS`.
- `SKIPPED` stages do not count toward `PASS`. If `stage` is in the required stages defined by `docs/07_test_spec.md#2.13`, `SKIPPED == FAIL` for acceptance and blocks `STOP_ALLOWED`, whether the skip was produced by `TEST` or `RE_TEST`.
- Partial reports from the failed run must be persisted and must identify the single failed stage that becomes the next fix target.
- Every mandatory assertion defined in `docs/07_test_spec.md` for the executed active stage must execute.
- Every mandatory assertion ID defined in `docs/07_test_spec.md#2.16` must appear with `status = passed` in allowed full-stage or ACC-STOP evidence before `STOP_ALLOWED = true`.
- Skipped required assertions are failure, not neutral evidence.
- Each active stage must contain at least one machine-verified assertion.
- Behavior-only stages with no mandatory assertions must emit structured non-assertion evidence and cannot alone satisfy an acceptance gate.
- `100%` assertion coverage remains a target and warning signal, but it is not a hard PASS condition for MVP.

### 5.4 Fix Policy

Codex must:

- State a root cause hypothesis before editing.
- Cite the structured report evidence that supports the hypothesis.
- Define the change isolation boundary before editing.
- Fix the smallest failing unit first.
- Keep the fix inside the declared change isolation boundary.
- `FIX` may only modify files explicitly listed in both the current `plan_report.files` and the structured failure report `referenced_files`. If `plan_report` is missing, unreadable or malformed, return to `PLAN`. If the structured failure report lacks a parseable `referenced_files` array, enter `WORKFLOW_BLOCKED`. If the intersection is empty after a valid plan report and valid `referenced_files` are loaded, return to `PLAN` or enter `TASK_BLOCKED`; do not widen the fix boundary.
- Preserve unrelated user changes.
- Do not modify unrelated modules.
- Do not modify already passed test areas unless the root cause evidence proves they are the source of the failure.
- Avoid speculative refactors.
- Update contract documents when behavior changes.
- Reject fixes that introduce non-goal features.
- Prefer deterministic tests over manual inspection.

Codex must not:

- Skip failing tests to reach green.
- Treat screenshots or logs as pass evidence unless backed by structured assertions.
- Depend on real RSS, real HTML, real LLM, production DB or current time.
- Claim delivery while any acceptance gate is not `PASS`.

### 5.5 Test And Review Boundary

- `TEST` is data correctness verification: run commands, parse structured reports and decide pass/fail from machine evidence.
- `TEST` is the only source of truth for acceptance evaluation.
- `REVIEW` is static design correctness verification: inspect architecture, contracts, code boundaries and source document consistency without executing code.
- `TEST` must not interpret architecture, make semantic judgments, redefine scope or infer pass from intent.
- `REVIEW` may create findings from static diff, schema comparison and dependency graph checks, but it must not run tests, execute code, read runtime output, define new tests, modify assertion logic, replace missing tests, replace missing assertions or override failed reports.
- `REVIEW` must not influence the `TEST` pass/fail decision. Only structured `TEST`/`RE_TEST` reports may decide test status.
- `TEST` must not silently reinterpret architecture intent beyond the assertions defined by `docs/07_test_spec.md`.
- API contract consistency in `REVIEW` means static schema/document comparison only. Runtime request/response validation belongs only to `TEST`.
- `REVIEW` must not infer runtime behavior from static structure. Any runtime behavior claim must be verified in `TEST`.

```yaml
review_scope:
  allowed:
    - static_diff
    - schema_comparison
    - dependency_graph_check
  forbidden:
    - executing_code
    - reading_runtime_output
    - validating_api_json_response
    - validating_database_state
    - validating_dom_snapshot
    - inferring_runtime_behavior
    - influencing_test_decision
    - modifying_assertion_logic
```

### 5.6 Iterate Priority Guard

- `ITERATE` extracts failed acceptance gates from the latest acceptance result.
- `ITERATE` maps each failed gate to an existing task or creates one new task when none exists.
- `ITERATE` orders tasks by priority: acceptance gate failures, API contract failures, data model inconsistencies, test failures, UI mismatches.
- If mapping creates no actionable task, `ITERATE` rebuilds `tasks.md` from failed acceptance gates, missing test coverage and unverified contract fields before classifying any blocked state.
- `ITERATE` must compute a deterministic content hash of rebuilt task records. Hash scope is only `task_id`, `acceptance_gate`, `status`, `priority` and `test_scope`; exclude timestamps, evidence path, test report path, attempt count and other metadata noise. If `rebuilt_tasks_hash == previous_tasks_hash`, enter `WORKFLOW_BLOCKED` to prevent silent rebuild loops.
- When rebuild hash does not change, `ITERATE` must record `loop_type` as one of: `missing_task_mapping`, `unresolved_contract_gap`, `test_coverage_gap`.

## 6. Stop Condition（停止条件）

The workflow may enter `DONE` only when all conditions are true:

- `ACC-STOP-001` to `ACC-STOP-010` are all `PASS`.
- Terminal success condition is exactly `source: docs/08_acceptance.md`, `field: STOP_ALLOWED`, `expected: true`.
- `ACCEPTANCE` and `DONE` must use the same immutable `tasks_snapshot`; `tasks.md` must remain unchanged between acceptance evaluation and `DONE` transition.
- `tasks_hash_before == tasks_hash_after` is required before entering `DONE`.
- All required gates are covered only by tasks where `status == passed`, evidence exists and test report exists.
- `task_blocked` tasks must not contribute to any acceptance gate coverage.
- `task_blocked` is allowed at `DONE` only when `acceptance_gate: none`.
- Each gate-covering task has an existing evidence file and linked test report.
- `tasks.md` is loaded and `tasks.count > 0`.
- No task is `pending` or `in_progress`.
- No task has `active_state` in `FIX` or `RE_TEST`.
- No required report is `failed`, `flaky` or `skipped`.
- No required stage is `SKIPPED`; if `stage` is in the required stages defined by `docs/07_test_spec.md#2.13`, `SKIPPED` is acceptance failure regardless of whether it came from `TEST` or `RE_TEST`.
- No required gate is `UNKNOWN`, `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED`.
- No task mapped to a required acceptance gate remains unresolved as `task_blocked`.
- No required gate is satisfied by `TASK_BLOCKED`, `WORKFLOW_BLOCKED`, `ENV_BLOCKED` or missing mandatory assertions.
- No required assertion is skipped in stop evidence.
- No mandatory assertion ID from `docs/07_test_spec.md#2.16` is missing, failed, flaky, skipped, duplicated with conflicting results, attached to the wrong stage or proven only by a task-scoped report.
- Behavior-only evidence without active assertions cannot be the sole evidence for a required acceptance gate.
- No API/UI/log/report leak is detected.
- All acceptance evidence is structured, parseable and mapped to the required gates.
- `STOP_ALLOWED = true` may be produced only by the workflow `ACCEPTANCE` state running the full acceptance command without `--task-id`.
- Code, docs, tests and generated reports are consistent with `docs/03_ui_spec.md`, `docs/04_data_model.md`, `docs/05_api_contract.md`, `docs/06_dev_rules.md` and `docs/07_test_spec.md`.

Blocked is not a successful stop condition.

If Codex cannot continue because task input, workflow metadata or the local environment is blocked, the workflow must report `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED` explicitly. None of these is delivery complete.

## 7. Execution Loop Example（伪代码）

```python
TASK_RETRY_LIMIT = 3
TASK_PRIORITY_ORDER = [
    "acceptance_gate_failures",
    "api_contract_failures",
    "data_model_violations",
    "test_failures",
    "ui_failures",
    "refactor_tasks",
]
TEST_STAGE_ORDER = [
    "static",
    "unit",
    "contract",
    "api",
    "integration",
    "replay",
    "snapshot",
    "e2e",
]

state = "INIT"

while True:
    if state == "INIT":
        load_source_documents([
            "docs/01_prd.md",
            "docs/02_arch.md",
            "docs/03_ui_spec.md",
            "docs/04_data_model.md",
            "docs/05_api_contract.md",
            "docs/06_dev_rules.md",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
        ])
        ensure_tasks_md_exists_or_create_from_acceptance()
        state = "LOAD_TASKS"

    elif state == "LOAD_TASKS":
        tasks = load_tasks("tasks.md")
        fill_missing_task_fields_with_defaults(
            tasks,
            defaults={
                "status": "pending",
                "active_state": "none",
                "last_updated_state": "none",
                "acceptance_gate": "none",
                "attempts": 0,
                "evidence": "none",
                "test_report": "none",
                "plan_report": "none",
                "intentionally_out_of_scope": False,
                "blocker": "none",
            },
            forbid_inference=True,
        )
        normalize_missing_task_priority(
            tasks,
            rule=[
                "failed_acceptance_gate_mapping",
                "failed_test_stage_order",
                "canonical_doc_order_01_to_08",
                "default_refactor_tasks",
            ],
            forbid_semantic_guessing=True,
        )
        task = next_actionable_task_by_priority(
            tasks,
            priority_order=TASK_PRIORITY_ORDER,
            stable_tiebreaker="task_id",
        )
        if task is not None:
            state = "PLAN"
        elif (
            task_count(tasks) > 0
            and all_tasks_terminal(tasks, allowed=["passed", "task_blocked"])
            and no_pending_or_in_progress(tasks)
            and no_task_active_state_in(tasks, ["FIX", "RE_TEST"])
        ):
            state = "ACCEPTANCE"
        else:
            state = "WORKFLOW_BLOCKED"

    elif state == "PLAN":
        task.active_state = "PLAN"
        task.last_updated_state = "PLAN"
        plan = create_task_plan(task)
        if plan.blocked:
            task.status = "task_blocked"
            task.active_state = "none"
            task.last_updated_state = "TASK_BLOCKED"
            task.blocker = plan.blocker
            state = "TASK_BLOCKED"
        else:
            plan_report = persist_task_plan_report(
                task_id=task.id,
                plan=plan,
                path=f"reports/tasks/{task.id}/plan.json",
            )
            if plan_report.parseable:
                task.plan_report = plan_report.path
                update_tasks_md(task, fields=["plan_report"])
                state = "IMPLEMENT"
            else:
                record_workflow_blocker("task_plan_report_invalid")
                state = "WORKFLOW_BLOCKED"

    elif state == "IMPLEMENT":
        task.active_state = "IMPLEMENT"
        task.last_updated_state = "IMPLEMENT"
        plan_report = load_task_plan_report(task.plan_report)
        if not plan_report.parseable:
            state = "PLAN"
            continue
        apply_scoped_changes(plan_report)
        state = "TEST"

    elif state == "TEST":
        task.active_state = "TEST"
        task.last_updated_state = "TEST"
        test_result = run_07_test_spec_stages(
            order=TEST_STAGE_ORDER,
            scope=task.test_scope,
            strict_mock=True,
        )
        if test_result.failed:
            mark_downstream_stages(
                after_stage=test_result.failed_stage,
                status="SKIPPED",
            )
            treat_skipped_required_stages_as_failure(
                test_result.reports,
                required_stage_source="docs/07_test_spec.md#2.13",
            )
            test_result.fix_target_stage = test_result.failed_stage
        persist_test_reports(test_result.reports)
        if test_result.passed and reports_have_required_assertions(
            test_result.reports,
            require_no_skipped_required_assertions=True,
            minimum_assertions_per_stage=1,
            minimum_assertions_scope="active_stage",
            assertion_catalog="docs/07_test_spec.md",
            require_scope_mandatory_assertions=True,
            required_assertion_scope="task_declared_scope",
            require_all_mandatory_assertions=False,
            assertion_coverage_target_percent=100,
            enforce_assertion_coverage_target=False,
            allow_behavior_only_stages=True,
        ):
            state = "REVIEW"
        else:
            task.failure = route_failure(test_result.highest_priority_failure)
            state = "FIX"

    elif state == "REVIEW":
        task.active_state = "REVIEW"
        task.last_updated_state = "REVIEW"
        review_result = review_static_design_against_architecture_and_contracts(
            task,
            allowed=["static_diff", "schema_comparison", "dependency_graph_check"],
            forbidden=[
                "execute_code",
                "read_runtime_output",
                "infer_runtime_behavior",
                "influence_test_decision",
            ],
        )
        if review_result.passed:
            state = "SUMMARIZE"
        elif review_result.blocked:
            task.status = "task_blocked"
            task.active_state = "none"
            task.last_updated_state = "TASK_BLOCKED"
            task.blocker = review_result.blocker
            state = "TASK_BLOCKED"
        else:
            task.failure = review_result.failure
            state = "FIX"

    elif state == "FIX":
        task.active_state = "FIX"
        task.last_updated_state = "FIX"
        task.attempts += 1
        if task.attempts > TASK_RETRY_LIMIT:
            task.status = "task_blocked"
            task.active_state = "none"
            task.last_updated_state = "TASK_BLOCKED"
            task.blocker = "retry_limit_exceeded"
            state = "TASK_BLOCKED"
        else:
            plan_report = load_task_plan_report(task.plan_report)
            if not plan_report.parseable:
                state = "PLAN"
                continue
            task.root_cause_hypothesis = build_root_cause_hypothesis(
                evidence=task.failure.structured_report_ref,
            )
            if not has_parseable_referenced_files(
                task.failure.structured_report_ref,
            ):
                record_workflow_blocker("failure_report_missing_referenced_files")
                state = "WORKFLOW_BLOCKED"
                continue
            task.change_isolation_boundary = define_change_isolation_boundary(
                allowed_files=intersection(
                    plan_report.files,
                    task.failure.structured_report_ref.referenced_files,
                ),
                on_empty="return_to_PLAN_or_TASK_BLOCKED",
            )
            apply_smallest_fix(task.failure)
            assert_only_files_changed(
                files=intersection(
                    plan_report.files,
                    task.failure.structured_report_ref.referenced_files,
                ),
            )
            state = "RE_TEST"

    elif state == "RE_TEST":
        task.active_state = "RE_TEST"
        task.last_updated_state = "RE_TEST"
        retest_result = rerun_failed_stage_then_affected_stages(task.failure)
        if retest_result.failed:
            mark_downstream_stages(
                after_stage=retest_result.failed_stage,
                status="SKIPPED",
            )
            treat_skipped_required_stages_as_failure(
                retest_result.reports,
                required_stage_source="docs/07_test_spec.md#2.13",
            )
            retest_result.fix_target_stage = retest_result.failed_stage
        persist_test_reports(retest_result.reports)
        if retest_result.passed and reports_have_required_assertions(
            retest_result.reports,
            require_no_skipped_required_assertions=True,
            minimum_assertions_per_stage=1,
            minimum_assertions_scope="active_stage",
            assertion_catalog="docs/07_test_spec.md",
            require_scope_mandatory_assertions=True,
            required_assertion_scope="task_declared_scope",
            require_all_mandatory_assertions=False,
            assertion_coverage_target_percent=100,
            enforce_assertion_coverage_target=False,
            allow_behavior_only_stages=True,
        ):
            state = "REVIEW"
        else:
            task.failure = route_failure(retest_result.highest_priority_failure)
            state = "FIX"

    elif state == "TASK_BLOCKED":
        record_task_blocker(task)
        if task_blocker_resolved_with_evidence(task):
            reopen_task_after_blocker_resolution(task)
            state = "LOAD_TASKS"
        else:
            produce_task_blocked_status(task)
            break

    elif state == "WORKFLOW_BLOCKED":
        record_workflow_blocker()
        if workflow_blocker_resolved_with_evidence():
            state = "LOAD_TASKS"
        else:
            produce_workflow_blocked_status()
            break

    elif state == "ENV_BLOCKED":
        record_environment_blocker()
        produce_environment_blocked_status()
        break

    elif state == "SUMMARIZE":
        task.status = "passed"
        task.active_state = "none"
        task.last_updated_state = "SUMMARIZE"
        task.evidence = latest_evidence_path(task)
        task.test_report = latest_test_report_path(task)
        confirm_acceptance_gate_mapping(task.acceptance_gate)
        update_tasks_md(
            task,
            fields=[
                "status",
                "active_state",
                "last_updated_state",
                "evidence",
                "test_report",
                "acceptance_gate",
            ],
        )
        state = "LOAD_TASKS"

    elif state == "ACCEPTANCE":
        tasks_snapshot = load_tasks("tasks.md")
        tasks_hash_before = hash_file("tasks.md")
        acceptance = run_harness_command(
            command=[
                "python3",
                "scripts/run_harness.py",
                "--stage",
                "acceptance",
                "--report-dir",
                "reports",
            ],
            tasks_snapshot=tasks_snapshot,
            require_full_gate_validation=True,
            forbid_task_id=True,
        )
        persist_acceptance_reports(acceptance.reports)
        tasks_hash_after = hash_file("tasks.md")

        if (
            acceptance.field("STOP_ALLOWED") is True
            and tasks_hash_before == tasks_hash_after
            and task_count(tasks_snapshot) > 0
            and no_task_active_state_in(tasks_snapshot, ["FIX", "RE_TEST"])
            and required_gates_mapped_only_to_passed_tasks_with_evidence_and_reports(
                tasks_snapshot,
            )
            and task_blocked_tasks_do_not_contribute_to_gate_coverage(tasks_snapshot)
            and task_blocked_tasks_have_acceptance_gate_none(tasks_snapshot)
            and no_required_stage_is_skipped(
                acceptance.reports,
                required_stage_source="docs/07_test_spec.md#2.13",
            )
        ):
            state = "DONE"
        elif acceptance.has_task_blocked_gate:
            task = create_task_blocked_gate_record(acceptance.task_blocked_gates)
            state = "TASK_BLOCKED"
        elif acceptance.has_workflow_blocked_gate:
            state = "WORKFLOW_BLOCKED"
        elif acceptance.has_env_blocked_gate:
            state = "ENV_BLOCKED"
        else:
            persist_failed_acceptance_context(acceptance.failed_or_unproven_gates)
            state = "ITERATE"

    elif state == "ITERATE":
        failed_gates = extract_failed_acceptance_gates()
        map_failed_gates_to_existing_or_new_tasks(failed_gates)
        order_tasks_by_priority("tasks.md")
        if not has_actionable_pending_tasks("tasks.md"):
            previous_tasks_hash = hash_task_records(
                "tasks.md",
                fields=[
                    "task_id",
                    "acceptance_gate",
                    "status",
                    "priority",
                    "test_scope",
                ],
            )
            rebuild_tasks_md_from(
                failed_acceptance_gates=failed_gates,
                missing_test_coverage=extract_missing_test_coverage(),
                unverified_contract_fields=extract_unverified_contract_fields(),
            )
            rebuilt_tasks_hash = hash_task_records(
                "tasks.md",
                fields=[
                    "task_id",
                    "acceptance_gate",
                    "status",
                    "priority",
                    "test_scope",
                ],
            )
            if rebuilt_tasks_hash == previous_tasks_hash:
                loop_type = classify_rebuild_loop(
                    failed_acceptance_gates=failed_gates,
                    missing_test_coverage=extract_missing_test_coverage(),
                    unverified_contract_fields=extract_unverified_contract_fields(),
                    allowed=[
                        "missing_task_mapping",
                        "unresolved_contract_gap",
                        "test_coverage_gap",
                    ],
                )
                record_workflow_loop_blocker(loop_type)
                state = "WORKFLOW_BLOCKED"
                continue
            order_tasks_by_priority("tasks.md")
        if has_actionable_pending_tasks("tasks.md"):
            state = "LOAD_TASKS"
        else:
            classify_no_actionable_task_blocker()
            state = classified_blocked_state()

    elif state == "DONE":
        produce_final_delivery_summary()
        mark_terminal_irreversible()
        break
```

## 8. MVP Design Notes（强调简洁性）

- This is a local workflow, not a full CI/CD platform.
- No external queue, worker, dashboard, cloud runner or deployment system is required.
- `tasks.md` is the only MVP task queue.
- `docs/07_test_spec.md` is the only test execution authority.
- `docs/08_acceptance.md` is the only stop-gate authority.
- Structured reports are required so Codex can repair failures deterministically.
- The loop is intentionally simple: one task, one plan, one implementation slice, one test feedback cycle.
- Retry is bounded per task to avoid endless local loops.
- The whole workflow can run repeatedly from a clean checkout using the same fixtures, mocks and fixed clock.
- Industrial quality comes from deterministic gates, contract alignment and refusal to stop before acceptance passes, not from adding heavy CI infrastructure.
