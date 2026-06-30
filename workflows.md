# workflows.md

## 1. Overview（简述系统作用）

本文档定义 AI 新闻聚合系统 MVP 的本地开发工作流状态机。

目标是让 Codex 可以按确定性流程自动执行：

```text
Plan -> Implement -> Test -> Review -> Fix -> Re-test -> Summarize -> Iterate
```

直到 `tasks.md` 中每个 `dag.nodes[*].status == passed`、`docs/08_acceptance.md` 中 `ACC-STOP-001` 到 `ACC-STOP-010` 全部为 `PASS`、全部 stop inputs 为 `PASS`，并且 `STOP_ALLOWED = true`。

本 workflow 只依赖当前仓库、项目文档、本地测试、fixture、mock 和 fixed clock。不得依赖 GitHub Actions、外部 CI、真实 RSS、真实网页、真实 LLM、生产数据库、网络时间或人工主观判断。

Source of truth:

| Area | Source |
| --- | --- |
| Workflow, command interface, report paths and loop strategy | `workflows.md` |
| Product behavior | `docs/01_prd.md` |
| Architecture boundary | `docs/02_arch.md` |
| UI behavior | `docs/03_ui_spec.md` |
| Data facts | `docs/04_data_model.md` |
| API contract | `docs/05_api_contract.md` |
| Development rules | `docs/06_dev_rules.md` |
| Test execution | `docs/07_test_spec.md` |
| Stop gate | `docs/08_acceptance.md` |

Control-plane consolidation:

- `workflows.md` is the only local workflow control plane.
- Legacy harness/runbook documents are obsolete and must not be used as workflow truth.
- `scripts/run_harness.py` is an executor for the commands defined below. It must not define completion rules that are absent from `workflows.md`, `docs/07_test_spec.md` or `docs/08_acceptance.md`.
- If command behavior and this file conflict, this file wins and the executor must be fixed.

Command surface:

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

Task-scoped commands add `--task-id TASK-000` and write task evidence under `reports/tasks/<task_id>/<stage>.json`. Task-scoped `acceptance` is invalid.

Report paths:

- Product full-stage reports: `reports/stages/<stage>.json`, where `<stage>` is one of `static`, `unit`, `contract`, `api`, `integration`, `replay`, `snapshot` or `e2e`.
- Task reports: `reports/tasks/<task_id>/<stage>.json`
- Task plans: `reports/tasks/<task_id>/plan.json`
- Task review evidence: `reports/tasks/<task_id>/review.json`
- Task fix/optimize evidence: `reports/tasks/<task_id>/fix_optimize.json`
- Round summaries: `reports/tasks/<task_id>/summary.json`
- Acceptance gates: `reports/acceptance/ACC-STOP-001.json` through `reports/acceptance/ACC-STOP-010.json`
- PRD coverage: `reports/acceptance/prd_coverage.json`
- Task acceptance coverage: `reports/acceptance/task_acceptance_coverage.json`
- Local user acceptance: `reports/acceptance/local_user_acceptance.json`
- Stop decision: `reports/acceptance/STOP_ALLOWED.json`

Acceptance report policy:

- `acceptance` is a gate-evaluation command, not a product verification stage.
- `python3 scripts/run_harness.py --stage acceptance --report-dir reports` writes `reports/acceptance/ACC-STOP-001.json` through `reports/acceptance/ACC-STOP-010.json`, coverage reports and `reports/acceptance/STOP_ALLOWED.json`.
- `reports/stages/acceptance.json` must not be required, and if a compatibility runner creates it, that file is diagnostic only. It cannot satisfy product-stage, mandatory-assertion, PRD coverage, task acceptance coverage, browser E2E or stop-input evidence.

Context recovery and stale evidence policy:

- Chat history is never workflow memory. On every fresh start, resume or long-context recovery, Codex must reconstruct state only from `workflows.md`, `tasks.md`, `docs/01_prd.md` through `docs/08_acceptance.md`, existing structured reports and the current worktree.
- Resume starts at `INIT`: read the source documents, load `tasks.md`, load the latest report paths named by each task node, then derive the next state from persisted `status`, `active_state`, `last_updated_state`, `plan_report`, `test_report`, `summary_report`, blocker fields and acceptance reports.
- If `active_state != none`, Codex may resume that state only when all required prior-state reports exist, parse, match their schemas and are not stale. Otherwise it must rewind to the earliest state whose required evidence is missing or stale, or enter `WORKFLOW_BLOCKED` when the rewind target cannot be determined.
- A report is stale when any source document, task record, fixture/mock/clock version, referenced file, schema file or required input covered by that report changed after the report was produced and the report's `data_hash` does not reflect the current content. Stale evidence is treated the same as missing evidence for `PASS` decisions.
- Every state transition that changes task progress must persist the corresponding task fields or structured report before starting the next state. A process interruption after a persisted transition must be recoverable without relying on hidden in-memory state.

Loop simplification policy:

- The workflow loop is one product loop, not a chain of shallow stage approvals.
- Required product stop-verification stages are exactly `static`, `unit`, `contract`, `api`, `integration`, `replay`, `snapshot` and `e2e`, matching `docs/07_test_spec.md#2.13`.
- `acceptance` is the full gate-evaluation command that consumes those product-stage reports, PRD coverage, task acceptance coverage, browser E2E evidence and local user acceptance. It is not a product verification stage and cannot replace any required product-stage report.
- A shallow pass in any stage, or a stage report without behavior/assertion evidence, cannot substitute for PRD coverage, real API behavior, browser-visible UI behavior, replay/snapshot evidence or user acceptance.
- A stage report may pass only when it contains behavior evidence for the current product slice. Directory existence, placeholder modules, scaffold files, snapshots without behavior assertions and synthetic pass reports do not count toward stop eligibility.
- Any user acceptance failure invalidates the previous `STOP_ALLOWED=true` result and forces `ITERATE`.

PRD coverage policy:

- `docs/01_prd.md` is the primary product requirement input. API, data, UI, development and architecture documents may refine executable details, but they must not silently omit, weaken or replace a clear PRD core requirement.
- If a higher-priority contract document conflicts with a clear PRD requirement, the current implementation must follow the documented priority order for executable safety, and the workflow must create or keep a PRD coverage/document-consistency blocker until the documents are repaired.
- Every checklist-style acceptance statement in `docs/01_prd.md` must map to at least one assertion id in `docs/07_test_spec.md#2.16` or a PRD coverage artifact referenced by `reports/acceptance/ACC-STOP-001.json`.
- `STOP_ALLOWED=true` is forbidden when any PRD acceptance statement is unmapped, unexecuted or only proven by scaffold/synthetic evidence.
- Home page acceptance must prove the news feed and 30-day high-score list using enough fixture data to exercise the PRD, not a sparse smoke sample.
- Browser-visible UI requirements must be proven in a real browser or an equivalent DOM-capable runner. FastAPI `TestClient`, string scanning and API-only snapshots cannot prove final UI acceptance.

Task acceptance coverage policy:

- Every `tasks.md.dag.nodes[*].acceptance_criteria[*]` statement must map to at least one executed structured assertion id and report path in `reports/acceptance/task_acceptance_coverage.json`.
- `STOP_ALLOWED=true` is forbidden when any task acceptance criterion is unmapped, unexecuted, failed, flaky, skipped, mapped only to prose, or missing a report path.
- A task may become `passed` during task-scoped workflow only when its scoped tests pass, but final `DONE` requires the task acceptance coverage stop input to prove every persisted criterion against structured evidence.
- Task-scoped reports may prove task progress; they cannot replace full-stage or ACC-STOP evidence for any criterion that contributes to final stop eligibility.

MVP task source:

- Primary task queue: `tasks.md`
- If `tasks.md` is missing, Codex must create it from unresolved implementation gaps, failed test stages, structured critical/security/blocking risk findings, PRD core-flow gaps, and failed acceptance gates.
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
- `summary_report` stores the persisted round summary path created during `SUMMARIZE`.
- `SUMMARIZE` updates the matching YAML node fields in `tasks.md`; it must not rewrite the task queue into another format.

Minimal task record format:

```markdown
## TASK-001 Short task title

- status: pending | in_progress | passed | task_blocked
- source: docs/01_prd.md | docs/02_arch.md | docs/03_ui_spec.md | docs/04_data_model.md | docs/05_api_contract.md | docs/06_dev_rules.md | docs/07_test_spec.md | docs/08_acceptance.md
- acceptance_gate: ACC-STOP-001 | ACC-STOP-002 | ... | none
- priority: test_failures | critical_bugs | security_risks | blocking_risks | prd_core_flow_gaps | acceptance_gate_failures | api_contract_failures | data_model_violations | ui_failures | refactor_tasks
- test_scope: static | unit | contract | api | integration | replay | snapshot | e2e
- active_state: none | PLAN | IMPLEMENT | TEST | REVIEW | FIX | RE_TEST | FIX_OPTIMIZE | SUMMARIZE
- last_updated_state: INIT | LOAD_TASKS | PLAN | IMPLEMENT | TEST | REVIEW | FIX | RE_TEST | FIX_OPTIMIZE | SUMMARIZE | ACCEPTANCE | ITERATE | TASK_BLOCKED | WORKFLOW_BLOCKED | ENV_BLOCKED | DONE | none
- attempts: 0
- evidence: path/to/report.json
- test_report: path/to/test-report.json
- plan_report: path/to/plan-report.json
- summary_report: path/to/summary-report.json
- intentionally_out_of_scope: false
- blocker: none
```

Task plans must be persisted as machine-readable reports before implementation:

```json
{
  "schema_ref": "workflows.md#TaskPlanReport",
  "schema_version": "v1",
  "task_id": "TASK-000",
  "prd_source": "docs/01_prd.md",
  "unimplemented_prd_items": ["PRD item still not proven by structured evidence"],
  "selected_related_features": ["small related feature selected for this round"],
  "prd_items": ["docs/01_prd.md#section-or-acceptance-id"],
  "scope": "smallest implementation slice for the selected task",
  "deliverable_submodule": "smallest deliverable submodule for this round",
  "round_acceptance_criteria": ["machine-checkable criterion for this round"],
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

Task review reports must be persisted as machine-readable reports before `FIX_OPTIMIZE`:

```json
{
  "schema_ref": "workflows.md#ReviewReport",
  "schema_version": "v1",
  "task_id": "TASK-000",
  "status": "passed",
  "method": ["static_diff", "schema_comparison", "dependency_graph_check"],
  "dimensions": {
    "requirements_fit": "passed",
    "logic_correctness": "passed",
    "test_sufficiency": "passed",
    "architecture": "passed",
    "maintainability": "passed",
    "performance": "passed",
    "security": "passed",
    "compatibility": "passed"
  },
  "blocking_findings": [],
  "referenced_files": ["path/to/file"],
  "timestamp": "ISO8601"
}
```

`ReviewReport` path:

```text
reports/tasks/<task_id>/review.json
```

Task fix/optimize reports must be persisted as machine-readable reports before `SUMMARIZE`:

```json
{
  "schema_ref": "workflows.md#FixOptimizeReport",
  "schema_version": "v1",
  "task_id": "TASK-000",
  "status": "passed",
  "blocking_findings_resolved": true,
  "optimization_rationale": "No scoped optimization was required after review.",
  "changed_files": [],
  "retest_reports": ["reports/tasks/TASK-000/unit.json"],
  "regression_detected": false,
  "referenced_files": ["path/to/file"],
  "timestamp": "ISO8601"
}
```

`FixOptimizeReport` path:

```text
reports/tasks/<task_id>/fix_optimize.json
```

Round summaries must be persisted as machine-readable reports before a task can be marked `passed`:

```json
{
  "schema_ref": "workflows.md#RoundSummaryReport",
  "schema_version": "v1",
  "task_id": "TASK-000",
  "round_index": 1,
  "completed_round_count": 1,
  "completed_work": ["implemented behavior completed in this round"],
  "prd_items": ["docs/01_prd.md#section-or-acceptance-id"],
  "changed_files": ["path/to/file"],
  "test_results": [
    {
      "stage": "unit",
      "status": "passed",
      "report": "reports/tasks/TASK-000/unit.json",
      "commands": ["python3 scripts/run_harness.py --stage unit --task-id TASK-000 --report-dir reports"],
      "case_count": 12,
      "passed_count": 12,
      "failed_count": 0,
      "skipped_count": 0,
      "pass_rate": 1.0,
      "failure_reasons": [],
      "repair_status": "not_required",
      "regression_detected": false
    }
  ],
  "review": {
    "status": "passed",
    "report": "reports/tasks/TASK-000/review.json",
    "method": ["static_diff", "schema_comparison", "dependency_graph_check"],
    "dimensions": {
      "requirements_fit": "passed",
      "logic_correctness": "passed",
      "test_sufficiency": "passed",
      "architecture": "passed",
      "maintainability": "passed",
      "performance": "passed",
      "security": "passed",
      "compatibility": "passed"
    },
    "blocking_findings": []
  },
  "fix_optimize": {
    "status": "passed",
    "report": "reports/tasks/TASK-000/fix_optimize.json",
    "blocking_findings_resolved": true,
    "optimization_rationale": "No scoped optimization was required after review.",
    "changed_files": [],
    "retest_reports": ["reports/tasks/TASK-000/unit.json"],
    "regression_detected": false
  },
  "issues_found_and_fixed": ["blocking issue fixed in this round, or none"],
  "current_system_completion": "machine-derived completion status after this round",
  "remaining_gaps_and_risks": ["remaining gap or risk, or none"],
  "next_round_goal": "one unique and explicit next target, or full ACCEPTANCE when no task remains",
  "round_end_decision": {
    "branch_order": [
      "required_tests",
      "critical_security_blocking_risks",
      "prd_core_flow",
      "quality_gates",
      "stop_conditions"
    ],
    "checks": {
      "required_tests": {
        "status": "pass",
        "decision": "check_next_branch",
        "evidence_paths": ["reports/tasks/TASK-000/unit.json"]
      },
      "critical_security_blocking_risks": {
        "status": "pass",
        "decision": "check_next_branch",
        "evidence_paths": ["reports/tasks/TASK-000/review.json"]
      },
      "prd_core_flow": {
        "status": "fail",
        "decision": "implement_prd_core_submodule",
        "evidence_paths": ["reports/acceptance/prd_coverage.json"]
      },
      "quality_gates": {
        "status": "not_checked",
        "decision": "check_next_branch",
        "evidence_paths": ["reports/tasks/TASK-000/summary.json"]
      },
      "stop_conditions": {
        "status": "not_checked",
        "decision": "continue_next_round",
        "evidence_paths": ["reports/tasks/TASK-000/summary.json"]
      }
    },
    "selected_next_state": "LOAD_TASKS",
    "selected_next_target": "TASK-001",
    "selected_reason": "PRD core flow remains incomplete."
  },
  "timestamp": "ISO8601"
}
```

`RoundSummaryReport` path:

```text
reports/tasks/<task_id>/summary.json
```

Machine-checkable JSON Schema:

```text
schemas/task_plan_report.schema.json
schemas/review_report.schema.json
schemas/fix_optimize_report.schema.json
schemas/round_summary_report.schema.json
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
    required_task_state: all tasks.md dag.nodes[*].status == passed
    required_stop_inputs:
      - task_completion_status
      - prd_coverage_status
      - task_acceptance_coverage_status
      - browser_e2e_status
      - local_user_acceptance_status
  task_retry_limit: 3
  task_priority_order:
    - test_failures
    - critical_bugs
    - security_risks
    - blocking_risks
    - prd_core_flow_gaps
    - acceptance_gate_failures
    - api_contract_failures
    - data_model_violations
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
    - FIX_OPTIMIZE
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
  -> FIX_OPTIMIZE
  -> SUMMARIZE
  -> LOAD_TASKS

On test or review failure:
  TEST/REVIEW -> FIX -> RE_TEST -> REVIEW -> FIX_OPTIMIZE -> SUMMARIZE

On fix/optimize finding:
  FIX_OPTIMIZE -> FIX -> RE_TEST -> REVIEW -> FIX_OPTIMIZE -> SUMMARIZE

When all tasks are passed:
  LOAD_TASKS triggers ACCEPTANCE
  if every task status is passed
  AND no pending/in_progress/task_blocked task exists -> ACCEPTANCE
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
- If a task lacks `priority`, `LOAD_TASKS` must derive it with one deterministic rule: failed test stage order first; otherwise structured review or acceptance findings categorized as `critical_bug`, `security_risk` or `blocking_risk`; otherwise PRD core-flow coverage gaps; otherwise failed acceptance gate mapping; otherwise canonical doc order `docs/01_prd.md -> docs/08_acceptance.md`; otherwise `refactor_tasks`. Do not use semantic guessing or multi-source fallback.
- For fields other than `priority`, missing task fields must be filled with explicit defaults, not inferred values: `status: pending`, `active_state: none`, `last_updated_state: none`, `acceptance_gate: none`, `attempts: 0`, `evidence: none`, `test_report: none`, `plan_report: none`, `summary_report: none`, `intentionally_out_of_scope: false`, `blocker: none`.
- Test stage order must follow `docs/07_test_spec.md#2.13`. Compatibility stages may still run in the historical order `static -> unit -> contract -> api -> integration -> replay -> snapshot -> e2e`, but stop eligibility depends on PRD coverage and browser-visible E2E evidence, not on shallow stage count.
- Each test stage must start from clean isolated state.
- Tests and acceptance must use fixture, mock and fixed clock.
- Structured reports are the source of truth. Free-form logs are diagnostic only.
- If evidence is missing, malformed or not machine-readable, the state result is `FAIL`, `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED`, never `PASS`.
- Acceptance entry is intentionally lightweight but strict. It only means the workflow should start stop-gate validation: `tasks.md` is loaded, `tasks.count > 0`, every task has `status == passed`, no task is `pending`、`in_progress` or `task_blocked`, and no task has `active_state` in `FIX`, `RE_TEST` or `FIX_OPTIMIZE`. Evidence, reports, mandatory assertions and gate coverage are validated inside `ACCEPTANCE`, not before it.
- Acceptance validation is mandatory on every entry: every `ACC-STOP-*` gate must be revalidated inside `ACCEPTANCE` with structured evidence, linked reports and required assertions.
- User acceptance is part of deterministic workflow state. If the user reports a failed local acceptance finding after any `STOP_ALLOWED=true`, that stop decision is stale and must be treated as failed until the finding is converted into a regression assertion and passes.

### 2.4 Mandatory Round Lifecycle

Every product development round must complete the following six steps in order. A round may stop early only by entering `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED`; that blocked result is not a completed round and cannot mark a task `passed`.

For this repository, any requirement that says `prd.md` means the project PRD source `docs/01_prd.md`.

Round count policy:

- The workflow should execute at least 10 completed product development rounds when unfinished work remains.
- The workflow may enter `DONE` before 10 completed rounds only when every stop condition in section 6 is satisfied by the current `ACCEPTANCE` run.
- Completing 10 rounds never authorizes `DONE` by itself. If any stop condition, required gate, stop input, PRD coverage item, task acceptance item, browser E2E input or local user acceptance input is not `PASS`, the workflow must keep iterating.
- Round count is only a progress guard. It must not cause broad scope selection, cosmetic churn, fake work, skipped tests or weakened acceptance evidence.
- Every completed round must persist `round_index` and `completed_round_count` in `RoundSummaryReport`; `ACCEPTANCE` must include a `round_count_policy` object in `STOP_ALLOWED.json`.
- `ACCEPTANCE` must compute valid completed rounds from parseable `RoundSummaryReport`, `ReviewReport` and `FixOptimizeReport` evidence. It must not trust a task summary's self-reported `completed_round_count` unless the linked review/fix evidence is valid.
- `DONE` before 10 completed rounds is valid only when `round_count_policy.early_done_allowed == true`, every required gate and stop input is `PASS`, and the latest `ACCEPTANCE` run proves no unfinished work remains.

| Step | Required state evidence |
| --- | --- |
| 1. Understand and plan | `PLAN` must read `docs/01_prd.md`, compare it with completed task and acceptance evidence, list remaining unimplemented PRD functions, select a small related set of unfinished PRD functions for this round, map exact PRD items, split the work into the smallest deliverable submodule, define round acceptance criteria and persist `reports/tasks/<task_id>/plan.json`. If only one unfinished PRD function remains, `PLAN` must record that exception explicitly. |
| 2. Implement code | `IMPLEMENT` must write or modify real runnable code and any required tests/docs inside the scoped plan files only. It must keep directory, module and responsibility boundaries clear, avoid duplicate logic, hidden dependencies and meaningless coupling, and reject fake implementations, placeholder implementations and new `TODO` markers. |
| 3. Automated testing | `TEST` must verify that tests were added or updated for new or changed logic, execute the relevant deterministic tests, persist structured results, and cover normal flow, invalid input, boundary conditions and regression risk for the current scope. A required test that cannot run because of the environment must produce explicit `ENV_BLOCKED` evidence or structured substitute diagnostics; it must never be reported as `PASS`. |
| 4. Code review | `REVIEW` must produce a structured self-check for PRD fit, logic correctness, test sufficiency, architecture, maintainability, performance, security and compatibility, persist `reports/tasks/<task_id>/review.json`, and expose its path plus dimension results in `RoundSummaryReport.review`. Review remains static/design verification and must not replace machine test evidence. |
| 5. Fix and optimize | `FIX_OPTIMIZE` is mandatory after `REVIEW`. It must confirm all blocking test and review findings from the round are fixed, perform necessary scoped maintainability/module-boundary optimization or record a no-op optimization rationale, rerun or validate relevant tests after any change, persist `reports/tasks/<task_id>/fix_optimize.json`, expose its path in `RoundSummaryReport.fix_optimize`, and confirm no new regression before `SUMMARIZE`. |
| 6. Round summary | `SUMMARIZE` must persist `reports/tasks/<task_id>/summary.json` with round index, completed round count, completed work, PRD items, changed files, test results, review evidence, fix/optimize evidence, issues found and fixed, current system completion, remaining gaps/risks, one unique next-round goal and `round_end_decision` evidence for the exact branch order in section 5.7 before marking the task `passed`. |

### 2.5 Round Output Contract

After each completed round, Codex must output a human-readable summary in exactly this Markdown structure, in addition to the machine-readable `RoundSummaryReport`:

```markdown
## Round X

### 1. 本轮目标
- 对应 PRD 条目：
- 最小交付子模块：
- 本轮验收标准：

### 2. 开发计划
1. 
2. 
3. 

### 3. 修改内容
- 新增文件：
- 修改文件：
- 删除文件：
- 核心实现说明：

### 4. 测试结果
- 测试命令：
- 用例总数：
- 通过数量：
- 失败数量：
- 跳过数量：
- 测试通过率：
- 边界与回归验证：

### 5. 代码评审
- PRD 符合度：
- 逻辑正确性：
- 架构与可维护性：
- 性能风险：
- 安全风险：

### 6. 发现与修复
- 发现问题：
- 修复内容：
- 修复后验证：

### 7. 当前状态
- 当前系统完成度：XX%
- 已完成模块：
- 剩余缺口：
- 已知风险：

### 8. 下一轮计划
- 下一轮唯一目标：
- 选择该目标的原因：
```

## 3. States Description（逐个 state 定义）

### INIT

| Field | Definition |
| --- | --- |
| entry condition | Workflow starts, or Codex resumes an unfinished workflow. |
| actions | Read `docs/01_prd.md` to `docs/08_acceptance.md` and `workflows.md`; detect available local commands defined in this file; verify workspace can run local tests; check whether `tasks.md` exists. |
| exit condition | Required source documents are readable and workflow inputs are known. |
| failure handling | If a required source document is missing or unreadable, enter `WORKFLOW_BLOCKED`. If local test commands cannot run because the local environment is unavailable, enter `ENV_BLOCKED`. If local test commands are missing but can be implemented in the repo, create tasks according to `docs/07_test_spec.md`. |

### LOAD_TASKS

| Field | Definition |
| --- | --- |
| entry condition | `INIT` completed, a task was summarized, or acceptance failed and new tasks must be loaded. |
| actions | Load all of `tasks.md`; fill missing fields with explicit defaults; normalize task status and missing priority; order actionable tasks by `task_priority_order`, then task id ascending; verify task ids are unique; map each task to source docs, test scope and acceptance gate. |
| exit condition | One actionable task is selected, or acceptance entry is triggered. Acceptance entry means `tasks.md` is loaded, `tasks.count > 0`, every task has `status == passed`, no task is `pending`、`in_progress` or `task_blocked`, and no task has `active_state` in `FIX`, `RE_TEST` or `FIX_OPTIMIZE`. It does not inspect evidence, report contents, assertion coverage or gate coverage. |
| failure handling | If `tasks.md` is missing, generate an MVP `tasks.md` from unresolved implementation gaps, failed test stages, structured critical/security/blocking risk findings, PRD core-flow gaps and failed/missing acceptance gates. If task records, priority derivation or task selection cannot be normalized deterministically, enter `WORKFLOW_BLOCKED`. If no actionable task exists while any task is non-terminal, enter `WORKFLOW_BLOCKED`, not `ACCEPTANCE`. |

### PLAN

| Field | Definition |
| --- | --- |
| entry condition | A `pending` or previously failed task is selected. |
| actions | Read `docs/01_prd.md` and the task source documents; compare PRD requirements with completed task, report and acceptance evidence; list remaining unimplemented PRD functions; select a small related set of unfinished PRD functions for this round; map exact PRD items; define the smallest deliverable submodule; define round acceptance criteria, expected test stages and acceptance gate impact; persist `TaskPlanReport` to `reports/tasks/<task_id>/plan.json`; update the task node `plan_report` field with that path. |
| exit condition | A deterministic task plan exists with `prd_source`, remaining PRD items, selected related features, mapped PRD items, deliverable submodule, acceptance criteria, scope, files, test commands/stages and rollback boundary, and the `TaskPlanReport` is parseable. |
| failure handling | If scope cannot be derived from documents, mark the task `task_blocked` with the missing decision and enter `TASK_BLOCKED`. If the plan report cannot be written or parsed, enter `WORKFLOW_BLOCKED`. |

### IMPLEMENT

| Field | Definition |
| --- | --- |
| entry condition | `PLAN` produced a scoped implementation plan. |
| actions | Modify only files required by the task; write or modify real runnable code plus required tests/docs for the scoped goal; preserve unrelated user changes; keep implementation aligned with `docs/06_dev_rules.md`; preserve clear directory, module and responsibility boundaries; avoid duplicate logic, hidden dependencies and meaningless coupling; update contract docs when behavior changes; reject fake implementations, placeholder implementations and new `TODO` markers. |
| exit condition | Real runnable code, required tests/docs and contract updates for the task are complete inside the planned scope and ready for local tests. |
| failure handling | If `plan_report` is missing or malformed, return to `PLAN`. If implementation exposes a contract conflict, stop coding that slice and return to `PLAN`. If the conflict is between documents, apply the priority order from `docs/06_dev_rules.md`. |

### TEST

| Field | Definition |
| --- | --- |
| entry condition | `IMPLEMENT` completed or `RE_TEST` requires a full affected stage run. |
| actions | Run runtime/data verification only: verify tests were added or updated for new or modified logic; execute tests defined by `docs/07_test_spec.md` in deterministic order; cover normal flow, invalid input, boundary conditions and regression risk for the current scope through structured assertions or explicit non-assertion evidence; stop downstream stages on first failed stage, mark those downstream stages as `SKIPPED`, persist partial structured `TestReport` objects matching `docs/07_test_spec.md#6`, and target only the failed stage for the next fix. `SKIPPED` stages do not count toward `PASS`; a skipped required stage is acceptance failure. |
| exit condition | Required scoped tests pass, or the first failing stage emits a structured failure report. A task-scoped pass requires every stage declared for that task run to execute, every mandatory assertion that belongs to the current task/stage scope to execute, no skipped required assertion in that scope, at least one machine-verified assertion per active stage, and explicit coverage or evidence for normal flow, invalid input, boundary conditions and regression risk. An active stage is a stage with mandatory assertions in the current run scope. Behavior-only stages with no mandatory assertions may pass only as structured non-assertion evidence and cannot by themselves satisfy an acceptance gate. The complete mandatory assertion catalog is enforced only by full-stage materialization and workflow `ACCEPTANCE`. `100%` total assertion coverage is a soft target, not a hard PASS condition. |
| failure handling | If tests fail, route by `stage`, `failure_type`, `error_category`, `node` and `trace_id`, then enter `FIX`. If required tests are missing or stale, treat this as `test_coverage_gap` and enter `FIX`. If reports are missing or invalid, treat this as `ACC-STOP-001` failure and enter `FIX`. If a required test cannot run because of local environment limits that cannot be fixed in the repo, emit explicit `ENV_BLOCKED` evidence or structured substitute diagnostics and enter `ENV_BLOCKED`; never report that test as `PASS`. |

### REVIEW

| Field | Definition |
| --- | --- |
| entry condition | Scoped machine tests passed and the task is not `task_blocked`. |
| actions | Run static design verification only: check code structure against `docs/01_prd.md`, `docs/02_arch.md`, `docs/03_ui_spec.md`, `docs/04_data_model.md`, `docs/05_api_contract.md` and `docs/06_dev_rules.md`; produce structured self-check findings for requirements fit, logic correctness, test sufficiency, architecture, maintainability, performance, security and compatibility; check schema/document diffs, dependency graph boundaries, internal field leaks and non-goal features. Treat API contract, data model and UI checks as static design/spec alignment only. Do not execute code or validate runtime outputs, API JSON responses, DB state, DOM snapshots, logs or generated reports. Do not treat review as a substitute for tests. |
| exit condition | Review finds no blocking issue and records all required self-check dimensions, or produces a structured list of required fixes. |
| failure handling | If review fails, enter `FIX`. If review reveals a task-local conflict that cannot be resolved from priority rules, mark task `task_blocked` and enter `TASK_BLOCKED`. If review reveals workflow metadata or document consistency cannot be interpreted deterministically, enter `WORKFLOW_BLOCKED`. |

### FIX

| Field | Definition |
| --- | --- |
| entry condition | `TEST` or `REVIEW` produced a failure. |
| actions | Before changing code, record root cause hypothesis, evidence reference from the structured test/review/optimization report, and change isolation boundary. Then apply the smallest fix that addresses the highest-priority blocking failure; increment task attempt count; avoid broad refactors; update tests or docs only when the source contract requires it. Repeat through `RE_TEST` until all blocking issues from the round are fixed or the task is blocked. |
| exit condition | A fix is ready for regression testing, all known blocking issues have been routed for verification, or retry limit is exceeded. |
| failure handling | If attempts exceed `task_retry_limit = 3`, mark task `task_blocked` and enter `TASK_BLOCKED`. If a fix creates a higher-priority failure, abandon that fix path and return to `PLAN`. |

### RE_TEST

| Field | Definition |
| --- | --- |
| entry condition | `FIX` completed within retry limit. |
| actions | First rerun the failed test stage; if it passes, rerun all affected earlier and later stages required by `docs/07_test_spec.md`; persist new structured reports. |
| exit condition | Regression scope passes and task can return to `REVIEW`, or failure persists and task returns to `FIX`. |
| failure handling | If the same failure persists, increment attempts and return to `FIX`. If a new failure appears, route the new failure by priority from `docs/07_test_spec.md#2.14`. |

### FIX_OPTIMIZE

| Field | Definition |
| --- | --- |
| entry condition | `REVIEW` produced no unresolvable blocker after scoped machine tests passed, or `RE_TEST` returned to `REVIEW` and review passed. |
| actions | Execute mandatory round step 5: confirm every blocking test and review finding from the round is fixed; apply necessary scoped optimization for code structure, module boundaries, maintainability, performance or security when supported by review evidence; if no optimization is needed, persist a no-op optimization rationale; if code, tests or docs changed, rerun the relevant scoped tests and persist structured reports; confirm no new regression issue before summary. |
| exit condition | Round acceptance criteria are met, no blocking finding remains, relevant `TEST` or `RE_TEST` reports are passing, optimization evidence exists, and the task can enter `SUMMARIZE`. |
| failure handling | If `FIX_OPTIMIZE` finds a blocking issue, route it by priority and enter `FIX`. If the issue cannot be resolved within task scope, enter `TASK_BLOCKED`. If workflow metadata or reports are inconsistent, enter `WORKFLOW_BLOCKED`. If the local environment cannot verify required optimization/regression checks, enter `ENV_BLOCKED`. |

### SUMMARIZE

| Field | Definition |
| --- | --- |
| entry condition | `FIX_OPTIMIZE` passed for a task that is not `task_blocked`. |
| actions | Persist `RoundSummaryReport` to `reports/tasks/<task_id>/summary.json` with round index, completed round count, completed work, corresponding PRD items, code modification scope, test execution results, review evidence, fix/optimize evidence, issues found and fixed, current system completion, remaining gaps and risks, and one unique explicit next-round goal. Then update only the MVP task summary fields in `tasks.md`: task status, `active_state: none`, evidence path, test report path, summary report path and acceptance mapping through `acceptance_gate`. |
| exit condition | The round summary report is parseable and task state is persisted as `passed`. |
| failure handling | If the summary report cannot be written or parsed, or `tasks.md` cannot be updated, enter `WORKFLOW_BLOCKED` with a persistence failure. Missing summary persistence must never advance to `ACCEPTANCE`. |

### ACCEPTANCE

| Field | Definition |
| --- | --- |
| entry condition | Acceptance entry is triggered: `tasks.md` is loaded, `tasks.count > 0`, every task has `status == passed`, no task is `pending`、`in_progress` or `task_blocked`, and no task has `active_state` in `FIX`, `RE_TEST` or `FIX_OPTIMIZE`. |
| actions | Create one immutable `tasks_snapshot = load_tasks("tasks.md")` and `tasks_hash_before = hash_file("tasks.md")` at entry. Run exactly one full gate command: `python3 scripts/run_harness.py --stage acceptance --report-dir reports`. Do not pass `--task-id`. Always evaluate all required gates in `docs/08_acceptance.md`: `ACC-STOP-001` to `ACC-STOP-010`, using only `tasks_snapshot` for task-derived evidence and existing non-stale full-stage reports under `reports/stages/<stage>.json` for product stages. This is where task completion, PRD coverage, task acceptance coverage, browser E2E evidence, local user acceptance, gate coverage, existing evidence, linked test reports, mandatory assertions and leak checks are validated. Gate coverage may use only tasks where `status == passed`, evidence exists, `test_report` exists and the linked reports are not stale under the context recovery policy. Any `task_blocked` task makes task completion fail and must not contribute to any gate coverage. Before `DONE`, recompute `tasks_hash_after = hash_file("tasks.md")` and require `tasks_hash_before == tasks_hash_after`. Use only structured evidence allowed by `docs/08_acceptance.md#3`. Never reuse previous task status or previous gate status as a substitute for full gate validation. |
| exit condition | Every gate has status `PASS`, `FAIL`, `UNKNOWN`, `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED`, and `STOP_ALLOWED` has been computed. |
| failure handling | If any gate is `FAIL` or `UNKNOWN`, enter `ITERATE` with the failed or unproven gate evidence. If a task-local unresolved blocker prevents a gate from being proven, enter `TASK_BLOCKED`. If workflow metadata, task records or report generation logic are inconsistent, enter `WORKFLOW_BLOCKED`. If the local environment cannot execute required verification, enter `ENV_BLOCKED`. Missing evidence is a failed gate unless the evidence generator itself is unavailable. |

### ITERATE

| Field | Definition |
| --- | --- |
| entry condition | `ACCEPTANCE` did not produce `STOP_ALLOWED = true`. |
| actions | Do only three things: extract failed acceptance gates and structured round-end findings, map each failed gate or finding to an existing task or create one new task, and order tasks by priority. If no actionable task results from that mapping, rebuild the MVP task queue from failed acceptance gates, missing test coverage, structured critical/security/blocking risk findings, PRD core-flow gaps and unverified contract fields, compare `rebuilt_tasks_hash` with `previous_tasks_hash`, then order it by priority. Do not pause tasks, resolve dependencies, schedule work, run tests or implement task lifecycle logic. Those concerns belong in `tasks.md` and the surrounding state transitions. |
| exit condition | New or updated tasks are available for `LOAD_TASKS`. |
| failure handling | If `rebuilt_tasks_hash == previous_tasks_hash`, classify loop type as `missing_task_mapping`, `unresolved_contract_gap`, `test_coverage_gap`, `unresolved_risk_gap` or `prd_core_flow_gap`, then enter `WORKFLOW_BLOCKED` for loop prevention. If no actionable task exists after the rebuild because a task-local decision is missing, enter `TASK_BLOCKED`. If no actionable task exists after the rebuild because workflow metadata is inconsistent, enter `WORKFLOW_BLOCKED`. If no actionable task exists after the rebuild because the local environment cannot run required verification, enter `ENV_BLOCKED`. |

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
| entry condition | `ACCEPTANCE` reads `STOP_ALLOWED == true` from `docs/08_acceptance.md`, every task in `tasks.md` has `status == passed`, every stop input is `PASS`, every required gate is mapped only to `passed` tasks with existing evidence and test report, and no `task_blocked` task exists. |
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
| `LOAD_TASKS` | `tasks.count > 0`, all tasks have `status == passed`, no task is `pending`、`in_progress` or `task_blocked`, and no task has `active_state` in `FIX`, `RE_TEST` or `FIX_OPTIMIZE` | `ACCEPTANCE` |
| `LOAD_TASKS` | No actionable task can be selected while any task is non-terminal | `WORKFLOW_BLOCKED` |
| `LOAD_TASKS` | Malformed task records cannot be normalized | `WORKFLOW_BLOCKED` |
| `PLAN` | Plan produced | `IMPLEMENT` |
| `PLAN` | Scope cannot be resolved | `TASK_BLOCKED` |
| `IMPLEMENT` | Scoped change complete | `TEST` |
| `IMPLEMENT` | Contract conflict found | `PLAN` |
| `TEST` | Scoped tests pass with mandatory assertions executed and no skipped required assertions | `REVIEW` |
| `TEST` | Test fails or report invalid | `FIX` |
| `REVIEW` | Review passes with all required self-check dimensions recorded | `FIX_OPTIMIZE` |
| `REVIEW` | Review fails | `FIX` |
| `REVIEW` | Unresolvable task-local conflict | `TASK_BLOCKED` |
| `REVIEW` | Unresolvable workflow/document conflict | `WORKFLOW_BLOCKED` |
| `FIX` | Fix complete and attempts <= 3 | `RE_TEST` |
| `FIX` | Attempts > 3 | `TASK_BLOCKED` |
| `RE_TEST` | Regression passes with mandatory assertions executed and no skipped required assertions | `REVIEW` |
| `RE_TEST` | Regression fails and attempts <= 3 | `FIX` |
| `RE_TEST` | Attempts > 3 | `TASK_BLOCKED` |
| `FIX_OPTIMIZE` | All blocking findings fixed, optimization evidence recorded, relevant tests verified, and no regression found | `SUMMARIZE` |
| `FIX_OPTIMIZE` | Blocking issue found | `FIX` |
| `FIX_OPTIMIZE` | Unresolvable task-local conflict | `TASK_BLOCKED` |
| `FIX_OPTIMIZE` | Workflow/report metadata blocker prevents verification | `WORKFLOW_BLOCKED` |
| `FIX_OPTIMIZE` | Environment blocker prevents verification | `ENV_BLOCKED` |
| `TASK_BLOCKED` | Task blocker unresolved | `TASK_BLOCKED` |
| `TASK_BLOCKED` | Explicit `resolve_task_blocker` completed with evidence | `LOAD_TASKS` |
| `WORKFLOW_BLOCKED` | Workflow blocker unresolved | `WORKFLOW_BLOCKED` |
| `WORKFLOW_BLOCKED` | Explicit `resolve_workflow_blocker` completed with evidence | `LOAD_TASKS` |
| `ENV_BLOCKED` | Environment unchanged | `ENV_BLOCKED` |
| `SUMMARIZE` | Round summary persisted and task persisted as `passed` | `LOAD_TASKS` |
| `ACCEPTANCE` | `STOP_ALLOWED == true`, `tasks_hash_before == tasks_hash_after`, same `tasks_snapshot` used for gate evaluation and DONE guard, every task in `tasks_snapshot` has `status == passed`, all stop inputs are `PASS`, all required gates map only to `passed` tasks with existing evidence and test report, no `task_blocked` task exists, and no required stage is `SKIPPED` | `DONE` |
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
- `TASK_BLOCKED` tasks do not automatically transition. They remain `TASK_BLOCKED` until an explicit `resolve_task_blocker` action provides evidence. They do not enter `REVIEW`, `FIX_OPTIMIZE`, `SUMMARIZE` or `ITERATE`, do not satisfy gate coverage and do not allow final delivery.
- `WORKFLOW_BLOCKED` may recover only after explicit workflow/document repair evidence.
- `ENV_BLOCKED` is terminal for the autonomous run unless the environment changes externally.

### 5.3 Report Policy

All test and acceptance results must produce machine-readable evidence:

- Test reports must follow `docs/07_test_spec.md#6`.
- Round summary reports must follow `workflows.md#RoundSummaryReport` and `schemas/round_summary_report.schema.json`.
- Command surface and report paths must follow `workflows.md#1.Overview`.
- Acceptance gate reports must map to `ACC-STOP-001` through `ACC-STOP-010`.
- Missing report means failure.
- Stale report means failure. A report is stale when its `data_hash`, fixture/mock/clock version, referenced files or source-document inputs no longer match the current worktree and source documents.
- Invalid schema means failure.
- `failed`, `flaky` or `skipped` required reports mean failure.
- Every core stop-verification stage declared in `docs/07_test_spec.md#2.13` must execute. Compatibility stages may produce useful evidence, but they do not reduce the need for PRD coverage or real browser E2E evidence.
- Full-regression materialization runs required product stages without `--task-id` and writes stage reports under `reports/stages/`. Task-scoped runs write only `reports/tasks/<task_id>/<stage>.json` and must not be used as substitutes for full-stage stop evidence.
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
- `FIX_OPTIMIZE` may modify only files listed in the current `plan_report.files`, and only when structured review or optimization evidence justifies the change. If no scoped optimization is needed, it must record a no-op optimization rationale instead of making cosmetic churn.
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
  required_dimensions:
    - requirements_fit
    - logic_correctness
    - test_sufficiency
    - architecture
    - maintainability
    - performance
    - security
    - compatibility
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

- `ITERATE` extracts failed acceptance gates from the latest acceptance result and structured round-end findings from the latest structured reports.
- `ITERATE` maps each failed gate or finding to an existing task or creates one new task when none exists.
- `ITERATE` orders tasks by priority: test failures, critical bugs, security risks, blocking risks, PRD core-flow gaps, acceptance gate failures, API contract failures, data model inconsistencies, UI mismatches and refactor tasks.
- If mapping creates no actionable task, `ITERATE` rebuilds `tasks.md` from failed acceptance gates, missing test coverage, structured critical/security/blocking risk findings, PRD core-flow gaps and unverified contract fields before classifying any blocked state.
- `ITERATE` must compute a deterministic content hash of rebuilt task records. Hash scope is only `task_id`, `acceptance_gate`, `status`, `priority` and `test_scope`; exclude timestamps, evidence path, test report path, attempt count and other metadata noise. If `rebuilt_tasks_hash == previous_tasks_hash`, enter `WORKFLOW_BLOCKED` to prevent silent rebuild loops.
- When rebuild hash does not change, `ITERATE` must record `loop_type` as one of: `missing_task_mapping`, `unresolved_contract_gap`, `test_coverage_gap`, `unresolved_risk_gap`, `prd_core_flow_gap`.

### 5.7 Round-End Decision Policy

After every completed task round, Codex must choose the next state or next task by the following branch order. A completed task round means `PLAN`, `IMPLEMENT`, `TEST`, `REVIEW`, `FIX_OPTIMIZE` and `SUMMARIZE` all produced their required evidence. If `TEST`, `RE_TEST`, `REVIEW` or `FIX_OPTIMIZE` finds a blocking issue, the workflow must resolve it through `FIX -> RE_TEST -> REVIEW -> FIX_OPTIMIZE` before the round may summarize.

```text
Complete current implementation round
├─ Does any required test fail or emit invalid evidence?
│  ├─ Yes -> stop new feature work, enter FIX/RE_TEST, then rerun the failed stage before broader regression
│  └─ No -> check review and risk findings
│
├─ Does any critical bug, security issue or blocking risk exist?
│  ├─ Yes -> make that finding the highest-priority actionable task for the next round
│  └─ No -> check PRD core-flow completion
│
├─ Is the PRD core flow complete?
│  ├─ No -> continue with the smallest unfinished PRD core-flow submodule
│  └─ Yes -> continue with boundary cases, non-core features and engineering quality
│
├─ Are all quality gates satisfied?
│  ├─ No -> iterate from the failed gate evidence
│  └─ Yes -> check final stop conditions
│
└─ Are all stop conditions satisfied?
   ├─ No -> enter the next round through LOAD_TASKS or ITERATE
   └─ Yes -> enter DONE and produce the final delivery summary
```

This round-end branch is an ordering rule, not a replacement for state transitions. `TEST` and `RE_TEST` failures still enter `FIX` immediately; `REVIEW` and `FIX_OPTIMIZE` findings still use `FIX`, `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED`; `ACCEPTANCE` remains the only state that may compute `STOP_ALLOWED`; and `DONE` remains allowed only through the strict stop conditions in section 6.

## 6. Stop Condition（停止条件）

`DONE` is the only successful stop state. `TASK_BLOCKED`、`WORKFLOW_BLOCKED` and `ENV_BLOCKED` may halt the current autonomous run, but they are non-success blocked states and must not be reported as product completion.

The workflow may enter `DONE` only when all conditions are true:

- `ACC-STOP-001` to `ACC-STOP-010` are all `PASS`.
- Terminal success condition is exactly `source: docs/08_acceptance.md`, `field: STOP_ALLOWED`, `expected: true`.
- Every task in `tasks.md` has `status == passed`; `pending`、`in_progress` and `task_blocked` all block `DONE`.
- Stop inputs `task_completion_status`、`prd_coverage_status`、`task_acceptance_coverage_status`、`browser_e2e_status` and `local_user_acceptance_status` are all `PASS`.
- `ACCEPTANCE` and `DONE` must use the same immutable `tasks_snapshot`; `tasks.md` must remain unchanged between acceptance evaluation and `DONE` transition.
- `tasks_hash_before == tasks_hash_after` is required before entering `DONE`.
- `docs/01_prd.md` acceptance coverage matrix is complete: every PRD acceptance statement is mapped to executed structured evidence.
- `tasks.md` acceptance coverage matrix is complete: every task acceptance criterion is mapped to executed structured evidence.
- Browser-visible E2E evidence exists for Home News Feed, 30-day HighScoreList, ArticleView and Sources page. API-only tests, string scans and static snapshots cannot satisfy this condition.
- The latest local deployment acceptance record exists at `reports/acceptance/local_user_acceptance.json` and has no failed user findings.
- `reports/acceptance/STOP_ALLOWED.json.round_count_policy.status == PASS`, proving either at least 10 valid completed rounds with linked summary/review/fix evidence or a valid early-DONE case where every stop condition is already satisfied.
- If the user reports any acceptance failure after `STOP_ALLOWED=true`, the previous stop decision is stale and the workflow must return to `ITERATE`.
- All required gates are covered only by tasks where `status == passed`, evidence exists and test report exists.
- `task_blocked` tasks must not contribute to any acceptance gate coverage.
- No task has `status == task_blocked` at `DONE`; blocked tasks must be resolved or removed from `tasks.md` only when they are genuinely out of scope.
- Each gate-covering task has an existing evidence file and linked test report.
- `tasks.md` is loaded and `tasks.count > 0`.
- No task is `pending` or `in_progress`.
- No task is `task_blocked`.
- No task has `active_state` in `FIX`, `RE_TEST` or `FIX_OPTIMIZE`.
- No required report is `failed`, `flaky` or `skipped`.
- No required stage is `SKIPPED`; if `stage` is in the required stages defined by `docs/07_test_spec.md#2.13`, `SKIPPED` is acceptance failure regardless of whether it came from `TEST` or `RE_TEST`.
- No required gate is `UNKNOWN`, `TASK_BLOCKED`, `WORKFLOW_BLOCKED` or `ENV_BLOCKED`.
- No task mapped to a required acceptance gate remains unresolved as `task_blocked`.
- No required gate is satisfied by `TASK_BLOCKED`, `WORKFLOW_BLOCKED`, `ENV_BLOCKED` or missing mandatory assertions.
- No required assertion is skipped in stop evidence.
- No mandatory assertion ID from `docs/07_test_spec.md#2.16` is missing, failed, flaky, skipped, duplicated with conflicting results, attached to the wrong stage or proven only by a task-scoped report.
- Behavior-only evidence without active assertions cannot be the sole evidence for a required acceptance gate.
- Synthetic evidence is forbidden for stop eligibility. Directory existence, placeholder files, scaffold modules and hardcoded sparse fixture smoke samples do not prove PRD completion.
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
    "test_failures",
    "critical_bugs",
    "security_risks",
    "blocking_risks",
    "prd_core_flow_gaps",
    "acceptance_gate_failures",
    "api_contract_failures",
    "data_model_violations",
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
                "summary_report": "none",
                "intentionally_out_of_scope": False,
                "blocker": "none",
            },
            forbid_inference=True,
        )
        normalize_missing_task_priority(
            tasks,
            rule=[
                "failed_test_stage_order",
                "structured_critical_bug_security_or_blocking_risk",
                "prd_core_flow_coverage_gap",
                "failed_acceptance_gate_mapping",
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
            and all_tasks_passed(tasks)
            and no_pending_or_in_progress(tasks)
            and no_task_blocked(tasks)
            and no_task_active_state_in(tasks, ["FIX", "RE_TEST", "FIX_OPTIMIZE"])
        ):
            state = "ACCEPTANCE"
        else:
            state = "WORKFLOW_BLOCKED"

    elif state == "PLAN":
        task.active_state = "PLAN"
        task.last_updated_state = "PLAN"
        plan = create_task_plan(
            task,
            prd_source="docs/01_prd.md",
            require_unimplemented_prd_audit=True,
            require_related_unfinished_features=True,
            require_prd_item_mapping=True,
            require_smallest_deliverable_submodule=True,
            require_round_acceptance_criteria=True,
        )
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
        apply_scoped_changes(
            plan_report,
            require_real_runnable_code=True,
            require_required_tests_or_docs=True,
            forbid_fake_implementation=True,
            forbid_placeholder_implementation=True,
            forbid_new_todo=True,
            preserve_module_boundaries=True,
        )
        state = "TEST"

    elif state == "TEST":
        task.active_state = "TEST"
        task.last_updated_state = "TEST"
        ensure_tests_added_or_updated_for_changed_logic(
            task,
            coverage=[
                "normal_flow",
                "invalid_input",
                "boundary_conditions",
                "regression_risk",
            ],
        )
        test_result = run_07_test_spec_stages(
            order=TEST_STAGE_ORDER,
            scope=task.test_scope,
            strict_mock=True,
        )
        if test_result.environment_blocked:
            emit_env_blocked_test_evidence_or_substitute_diagnostics(test_result)
            state = "ENV_BLOCKED"
            continue
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
            required_dimensions=[
                "requirements_fit",
                "logic_correctness",
                "test_sufficiency",
                "architecture",
                "maintainability",
                "performance",
                "security",
                "compatibility",
            ],
            forbidden=[
                "execute_code",
                "read_runtime_output",
                "infer_runtime_behavior",
                "influence_test_decision",
            ],
        )
        if review_result.passed:
            state = "FIX_OPTIMIZE"
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

    elif state == "FIX_OPTIMIZE":
        task.active_state = "FIX_OPTIMIZE"
        task.last_updated_state = "FIX_OPTIMIZE"
        plan_report = load_task_plan_report(task.plan_report)
        if not plan_report.parseable:
            state = "PLAN"
            continue
        optimize_result = run_fix_optimize_gate(
            task,
            allowed_files=plan_report.files,
            require_all_blockers_fixed=True,
            require_optimization_or_noop_rationale=True,
            require_no_regression=True,
        )
        persist_fix_optimize_evidence(optimize_result)
        if optimize_result.task_blocked:
            task.status = "task_blocked"
            task.active_state = "none"
            task.last_updated_state = "TASK_BLOCKED"
            task.blocker = optimize_result.blocker
            state = "TASK_BLOCKED"
        elif optimize_result.workflow_blocked:
            record_workflow_blocker(optimize_result.blocker)
            state = "WORKFLOW_BLOCKED"
        elif optimize_result.environment_blocked:
            record_environment_blocker(optimize_result.blocker)
            state = "ENV_BLOCKED"
        elif optimize_result.requires_fix:
            task.failure = optimize_result.failure
            state = "FIX"
        elif optimize_result.changed_files:
            retest_result = rerun_relevant_tests_after_optimization(optimize_result)
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
                state = "SUMMARIZE"
            else:
                task.failure = route_failure(retest_result.highest_priority_failure)
                state = "FIX"
        else:
            confirm_latest_test_or_retest_reports_pass(task)
            state = "SUMMARIZE"

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
        summary_report = persist_round_summary_report(
            task_id=task.id,
            round_index=derive_next_round_index(),
            completed_round_count=derive_completed_round_count_after_current_round(),
            completed_work=derive_completed_work(task),
            prd_items=derive_prd_items_from_plan(task.plan_report),
            changed_files=derive_changed_files(task),
            test_results=derive_test_results(task),
            review=derive_review_evidence(task, path=f"reports/tasks/{task.id}/review.json"),
            fix_optimize=derive_fix_optimize_evidence(
                task,
                path=f"reports/tasks/{task.id}/fix_optimize.json",
            ),
            issues_found_and_fixed=derive_issues_found_and_fixed(task),
            current_system_completion=derive_current_system_completion(),
            remaining_gaps_and_risks=derive_remaining_gaps_and_risks(),
            next_round_goal=derive_unique_next_round_goal(),
            round_end_decision=derive_round_end_decision(
                branch_order=[
                    "required_tests",
                    "critical_security_blocking_risks",
                    "prd_core_flow",
                    "quality_gates",
                    "stop_conditions",
                ],
                latest_test_results=derive_test_results(task),
                latest_review=derive_review_evidence(
                    task,
                    path=f"reports/tasks/{task.id}/review.json",
                ),
                remaining_gaps_and_risks=derive_remaining_gaps_and_risks(),
                next_round_goal=derive_unique_next_round_goal(),
            ),
            path=f"reports/tasks/{task.id}/summary.json",
        )
        if not summary_report.parseable:
            record_workflow_blocker("round_summary_report_invalid")
            state = "WORKFLOW_BLOCKED"
            continue
        task.status = "passed"
        task.active_state = "none"
        task.last_updated_state = "SUMMARIZE"
        task.evidence = latest_evidence_path(task)
        task.test_report = latest_test_report_path(task)
        task.summary_report = summary_report.path
        confirm_acceptance_gate_mapping(task.acceptance_gate)
        update_tasks_md(
            task,
            fields=[
                "status",
                "active_state",
                "last_updated_state",
                "evidence",
                "test_report",
                "summary_report",
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
            and all_tasks_passed(tasks_snapshot)
            and no_task_active_state_in(tasks_snapshot, ["FIX", "RE_TEST", "FIX_OPTIMIZE"])
            and prd_coverage_matrix_complete(
                source="docs/01_prd.md",
                evidence=acceptance.reports,
            )
            and task_acceptance_coverage_matrix_complete(
                source="tasks.md",
                report="reports/acceptance/task_acceptance_coverage.json",
                evidence=acceptance.reports,
            )
            and browser_e2e_evidence_passed(
                reports=acceptance.reports,
                required_surfaces=["home_news_feed", "high_score_list", "article_view", "sources_page"],
            )
            and local_deployment_acceptance_record_passed(
                report="reports/acceptance/local_user_acceptance.json",
            )
            and no_open_user_acceptance_failures()
            and required_gates_mapped_only_to_passed_tasks_with_evidence_and_reports(
                tasks_snapshot,
            )
            and task_blocked_tasks_do_not_contribute_to_gate_coverage(tasks_snapshot)
            and no_task_blocked(tasks_snapshot)
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
        round_end_findings = extract_structured_round_end_findings(
            categories=[
                "critical_bug",
                "security_risk",
                "blocking_risk",
                "prd_core_flow_gap",
            ],
        )
        user_acceptance_failures = extract_user_acceptance_failures(
            report="reports/acceptance/local_user_acceptance.json",
        )
        convert_user_failures_to_regression_tasks(user_acceptance_failures)
        map_failed_gates_to_existing_or_new_tasks(failed_gates)
        map_round_end_findings_to_existing_or_new_tasks(round_end_findings)
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
                structured_round_end_findings=round_end_findings,
                prd_core_flow_gaps=extract_prd_core_flow_gaps(),
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
                    structured_round_end_findings=round_end_findings,
                    prd_core_flow_gaps=extract_prd_core_flow_gaps(),
                    unverified_contract_fields=extract_unverified_contract_fields(),
                    allowed=[
                        "missing_task_mapping",
                        "unresolved_contract_gap",
                        "test_coverage_gap",
                        "unresolved_risk_gap",
                        "prd_core_flow_gap",
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
