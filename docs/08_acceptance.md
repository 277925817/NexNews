# 08_acceptance.md

## 0. Purpose

本文档定义 AI 新闻聚合系统 MVP 的 Codex Stop Gate。

`07_test_spec.md` 定义测试体系；本文档只回答一个问题：

> Codex 完成编程后，满足哪些验收条件才可以停止继续修改代码。

本文档只定义验收事实、通过条件、失败条件、证据模型、运行状态和停止判定。命令、报告路径和循环策略由仓库根目录 `workflows.md` 统一维护。

## 1. Gate Configuration

```yaml
acceptance_gate:
  version: 08_acceptance@codex-stop-v6
  mode: codex_stop_gate
  layer: WHAT

  source_documents:
    api_contract: 05_api_contract.md
    data_model: 04_data_model.md
    dev_rules: 06_dev_rules.md
    ui_spec: 03_ui_spec.md
    test_spec: 07_test_spec.md
    prd: 01_prd.md
    architecture: 02_arch.md

  priority_order:
    - 05_api_contract.md
    - 04_data_model.md
    - 06_dev_rules.md
    - 03_ui_spec.md
    - 01_prd.md
    - 02_arch.md

  prd_preservation_policy:
    primary_requirement_source: 01_prd.md
    rule: higher-priority executable contracts may refine PRD details, but any clear PRD core requirement that is omitted, weakened or replaced must fail PRD coverage and block STOP_ALLOWED until the documents are repaired

  deterministic_inputs:
    fixture_set: mvp_acceptance_fixture@v1
    mock_set: mvp_mock@v1
    clock_source: fixed_clock_fixture@v1

  isolation: strict_mock

  test_report_contract:
    ref: 07_test_spec.md#6
    version: v2

  leak_policy:
    forbidden_contextual_fields:
      - full_llm_prompt
      - raw_pipeline_payload
      - raw_article_body
    forbidden_internal_fields:
      - pipeline_state
      - is_selected
      - content_raw
      - content_full
      - has_translate_failed
      - deleted_at
    allowlist_fields:
      safe_tokens:
        - next_cursor
        - page_token
        - csrf_token
    forbidden_token_patterns:
      - jwt
      - api_key
      - secret
      - password

  required_gates:
    - ACC-STOP-001
    - ACC-STOP-002
    - ACC-STOP-003
    - ACC-STOP-004
    - ACC-STOP-005
    - ACC-STOP-006
    - ACC-STOP-007
    - ACC-STOP-008
    - ACC-STOP-009
    - ACC-STOP-010

  stop_rule: ALL_REQUIRED_GATES_AND_STOP_INPUTS_PASSED
  continue_rule: ANY_REQUIRED_GATE_OR_STOP_INPUT_FAILED_OR_UNPROVEN

  runtime_state:
    gate_status_enum:
      - UNKNOWN
      - PASS
      - FAIL
      - TASK_BLOCKED
      - WORKFLOW_BLOCKED
      - ENV_BLOCKED
    initial_status: UNKNOWN
    gate_status:
      G1: UNKNOWN
      G2: UNKNOWN
      G3: UNKNOWN
      G4: UNKNOWN
      G5: UNKNOWN
      G6: UNKNOWN
      G7: UNKNOWN
      G8: UNKNOWN
      G9: UNKNOWN
      G10: UNKNOWN
    stop_inputs:
      task_completion_status: UNKNOWN
      prd_coverage_status: UNKNOWN
      task_acceptance_coverage_status: UNKNOWN
      browser_e2e_status: UNKNOWN
      local_user_acceptance_status: UNKNOWN

  boolean_eval_spec:
    engine: simple_boolean_interpreter_v1
    type: strict
    truth_table:
      PASS: true
      FAIL: false
      TASK_BLOCKED: false
      WORKFLOW_BLOCKED: false
      ENV_BLOCKED: false
      UNKNOWN: false

  final_decision:
    type: boolean_expression_ast
    evaluator: simple_boolean_interpreter_v1
    result: STOP_ALLOWED
    operands_source:
      gate_status: runtime_state.gate_status
      stop_inputs: runtime_state.stop_inputs
    pass_value: PASS
    expression:
      and:
        - G1
        - G2
        - G3
        - G4
        - G5
        - G6
        - G7
        - G8
        - G9
        - G10
        - TASKS
        - PRD
        - TASK_ACCEPTANCE
        - BROWSER_E2E
        - LOCAL_USER_ACCEPTANCE
    gate_mapping:
      G1: ACC-STOP-001
      G2: ACC-STOP-002
      G3: ACC-STOP-003
      G4: ACC-STOP-004
      G5: ACC-STOP-005
      G6: ACC-STOP-006
      G7: ACC-STOP-007
      G8: ACC-STOP-008
      G9: ACC-STOP-009
      G10: ACC-STOP-010
    stop_input_mapping:
      TASKS: task_completion_status
      PRD: prd_coverage_status
      TASK_ACCEPTANCE: task_acceptance_coverage_status
      BROWSER_E2E: browser_e2e_status
      LOCAL_USER_ACCEPTANCE: local_user_acceptance_status
```

## 2. Isolation

```yaml
isolation: strict_mock
```

- Required Gates 必须基于 fixture、mock、fixed clock 或结构化测试报告完成判定。
- 验收证据不得依赖真实 RSS、真实网页、真实 LLM、生产数据库、网络时间或当前系统时间。
- 如果任一 Required Gate 只能通过 live dependency 得到证据，该 gate 必须判定为 `FAIL`、`TASK_BLOCKED`、`WORKFLOW_BLOCKED` 或 `ENV_BLOCKED`。
- `strict_mock` 只约束验收输入来源，不定义执行命令、runner 或报告输出路径。

## 3. Evidence Model

验收只能读取可计算证据，不读取自由文本判断。

| Evidence | Required content |
| --- | --- |
| Gate report | `schema_ref`、`schema_version`、`test_id`、`stage`、`status`、`assertions`、`expected`、`actual`、`diff`、`trace_id`、`referenced_files`、`data_hash`、`artifact_paths`。 |
| Stop decision report | `schema_ref`、`schema_version`、`STOP_ALLOWED`、`gate_status`、`passed_gates`、`failed_gates`、`blocked_gates`、`unknown_gates`、`stop_inputs`、`failed_stop_inputs`、`failure_reasons`、`unfinished_tasks`、`uncovered_prd_items`、`uncovered_task_acceptance_items`、`user_acceptance_failures`、`round_count_policy`、`generated_from_reports`。 |
| Assertion record | Assertion id、assertion type、visibility、status、expected、actual、diff。 |
| API JSON evidence | Response envelope、DTO fields、status code、forbidden field scan result。 |
| DB state evidence | Table schema、state transition、dedupe、translation field facts。 |
| UI render evidence | Rendered DTO fields、loading/empty/error/not found state、forbidden DOM field scan result。 |
| Dependency evidence | Live RSS、live HTML、live LLM access count。 |
| Leak evidence | Forbidden field count、forbidden pattern count、allowlisted token field count。 |
| PRD coverage evidence | Every acceptance statement from `docs/01_prd.md` mapped to stable PRD id、source line、task id、acceptance gate、assertion id、stage、report path and pass/fail status。 |
| Task acceptance coverage evidence | Every acceptance criterion from `tasks.md` mapped to task id、source line、assertion id、stage、report path and pass/fail status。 |
| Deployed browser smoke evidence | `http://127.0.0.1:8010/` real browser runtime result、HTTP status、API status、root mount count、NewsCard count、HighScoreList count、ArticleView result、Sources page result、refresh result、console/page errors and screenshot artifact。 |
| Local user acceptance evidence | Local URL、port、database、deployed browser smoke result、user acceptance findings, optional regression assertion id for each failed finding and current status。 |

Codex 不得把“看起来正常”“页面能打开”“日志没有明显错误”作为验收证据。

Codex 不得把 `STOP_ALLOWED=true` 当作不可撤销结论。用户在本地验收中报告失败后，最新 stop decision 立即失效；失败项必须转成 regression assertion、重新进入 workflow `ITERATE`，并在后续 acceptance 中重新证明。

每个 Required Gate 必须产生结构化 Gate report。缺少 Gate report、Gate report 无法解析、Gate report 不符合 `07_test_spec.md#6` 或 Gate report 无法映射到 `ACC-STOP-001` 到 `ACC-STOP-010`，均判定为未通过。

`reports/acceptance/STOP_ALLOWED.json` 必须产生结构化 Stop decision report，且必须符合本文 `#5.1 StopDecisionReport`。它不是 `TestReport`，不得替代任何 ACC-STOP gate report。

## 4. Required Gates

### ACC-STOP-001 Test Report Gate

✔ Pass:

- 所有验收测试输出符合 `07_test_spec.md#6` 的 `TestReport`。
- 每个 required test report 的 `schema_ref = 07_test_spec.md#6`。
- 每个 required test report 的 `schema_version = v2`。
- 每个 required test report 包含 `referenced_files`、`data_hash` 和 `artifact_paths`。
- 每个 assertion 包含 `visibility`，且值为 `public_surface`、`internal_evidence` 或 `report_metadata`。
- 每个 required test report 的 `status = passed`。
- 每个 required test report 的 `assertions` 非空，且全部 assertion `status = passed`。
- `docs/07_test_spec.md#2.16` Mandatory Assertion Catalog 中的每个 assertion id 都在允许的 full-stage 或 ACC-STOP report 中出现且 `status = passed`。
- Mandatory assertion id 不得缺失、重复产生冲突结果、挂在错误 stage、被 task-scoped report 代替或以 `flaky` / `skipped` 计入通过。
- `tasks.md` 中每个 DAG node 的 `status` 都是 `passed`。
- `docs/01_prd.md` 中每个验收标准都有 coverage matrix 记录，且每条记录指向已执行、已通过的结构化证据。
- `tasks.md` 中每个 `acceptance_criteria` item 都有 task acceptance coverage matrix 记录，且每条记录指向已执行、已通过的结构化证据。
- 真实浏览器或等价 DOM runner E2E 证据覆盖主页新闻流、30 天高分榜单、新闻详情页和信源管理页。
- 最新本地用户验收记录存在，且没有 failed finding。

✘ Fail:

- 缺少 test report。
- 任一 report schema 不匹配。
- 任一 required report 为 `failed`、`flaky` 或 `skipped`。
- 任一 assertion 缺失 `expected`、`actual` 或 `diff`。
- 任一 mandatory assertion id 缺失、失败、跳过、flaky、重复冲突、stage 不匹配或只存在于 task-scoped report。
- `tasks.md` 中任一 DAG node 不是 `passed`，包括 `pending`、`in_progress` 或 `task_blocked`。
- 任一 PRD 验收标准没有映射到结构化证据。
- 任一 task acceptance criterion 没有映射到已执行、已通过的结构化证据。
- 浏览器 E2E 证据缺失、只用 API 测试代替、或未覆盖主页新闻流、30 天高分榜单、新闻详情页和信源管理页。
- 任一本地用户验收 finding 为 failed。

### ACC-STOP-002 Source Management Gate

✔ Pass:

- 空库首次启动写入 7 个默认 RSS source。
- `GET /api/sources` 返回 `deleted_at IS NULL` 的 `SourceItem[]`，按 `created_at ASC` 排序。
- `POST /api/sources` 对合法公开 RSS URL 返回 `201`。
- 非法 URL、本地地址、私有地址返回 `400`。
- 重复 RSS URL 返回 `409`。
- 删除或禁用 source 后，历史 `news_item` 保留。
- 删除 source 设置 `is_enabled = 0` 和 `deleted_at`，且删除后的 source 不再出现在 `GET /api/sources`。
- 会关闭最后一个 `deleted_at IS NULL AND is_enabled = 1` source 的操作返回 `409`。

✘ Fail:

- 默认 source 缺失或重复写入。
- Source API 返回未在 `05_api_contract.md` 记录的字段。
- 删除 source 导致历史新闻被删除。

### ACC-STOP-003 Pipeline Functional Gate

✔ Pass:

Correctness:

- 固定 fixture 下完整执行 `RSS -> ingest -> score -> filter -> fetch -> translate -> API -> UI`。
- RSS parser 输出标准新闻输入对象。
- canonical URL 去重后，同一新闻只展示一次。
- fetch 成功写入可翻译内容；fetch 失败时使用 RSS 内容兜底。
- translation 成功只写入 `title_zh`、`summary_zh`、`content_zh`。
- translation 失败不写入部分中文字段。

Policy validation:

- mock scoring 输出稳定 `0-100` 分数。
- score threshold 从配置读取，默认值为 `60`。
- threshold fixture 中 `score = 60` 的 item 进入 fetch/translate/API 可见链路。
- threshold fixture 中 `score = 59` 的 item 不出现在 `GET /api/home`。

✘ Fail:

- 主链路无法从 fixture 产出可展示新闻。
- 低分新闻进入 API/UI。
- 重复新闻重复展示。
- 翻译失败产生部分中文字段。

### ACC-STOP-004 API Contract Gate

✔ Pass:

- 所有 endpoint 与 `05_api_contract.md` 完全一致。
- 成功响应使用 `{ "data": ... }` envelope。
- 错误响应使用 `{ "error": { "code": "...", "message": "..." } }`。
- `204` response 无 body。
- `GET /api/home` 返回 `HomeData`，包含 `latest_news` 和 `top_ranked_news`。
- `GET /api/news/{id}` 只返回可展示 `NewsDetailItem`；不可展示或不存在返回 `404`。
- `GET /api/news/{id}` 对 `translated` item 返回非空 `summary_zh` 和 `content_zh`。
- `POST /api/refresh` 不返回 task、queue、worker、retry、progress 字段。
- 未记录 endpoint 返回 `404` 或 `405`。

✘ Fail:

- API response shape 与 `05_api_contract.md` 不一致。
- API handler 直接返回 DB model。
- API 暴露未记录 endpoint 或未记录字段。

### ACC-STOP-005 Data Integrity Gate

✔ Pass:

- SQLite schema 只依赖 MVP 核心表：`source`、`news_item`、`processing_log`。
- `source.deleted_at` 存在且只作为 source soft tombstone。
- `processing_log` 是必需核心表。
- `news_item.pipeline_state` 只允许 `raw`、`scored`、`fetched`。
- `pipeline_state` transition 只允许 `raw -> scored -> fetched`。
- `is_selected` 由 score threshold 计算，默认 threshold 为 `60`。
- `canonical_url` 唯一约束阻止重复新闻。
- API `status` 只由 API 层投影，不写入数据库。
- 翻译完成事实只由 `title_zh`、`summary_zh`、`content_zh` 判断。
- 删除 source 不物理删除 source row 或历史 `news_item`，但 Source API 不返回已删除 source。

✘ Fail:

- 出现未记录表或旧设计字段，例如 `translation_status`、`content_source`、`is_ready`、`display_mode`。
- 非 pipeline service 写入 `pipeline_state`。
- API/UI 直接依赖数据库内部字段。

### ACC-STOP-006 UI Compliance Gate

✔ Pass:

- UI 只消费 `NewsItem`、`NewsListItem`、`NewsDetailItem`。
- `NewsCard` 不渲染 `content_zh`。
- `ready` 状态不渲染 `summary_zh` 或 `content_zh`。
- `translation_failed` 状态不渲染 `summary_zh` 或 `content_zh`。
- `translated` detail page 渲染中文摘要 `summary_zh` 和中文正文 `content_zh`。
- Loading、empty、error、not found state 可渲染。
- 点击 NewsCard、标题、高分榜 item 进入 ArticleView。

✘ Fail:

- UI 读取 `pipeline_state`、`is_selected`、`content_raw`、`content_full`、`has_translate_failed`。
- UI 用缺失字段生成默认文案或自动猜测字段含义。
- UI 展示 raw English summary/body。

### ACC-STOP-007 LLM Determinism Gate

✔ Pass:

- Scoring 使用 mock LLM，可重复输出相同 score。
- Translation 使用 mock LLM，可重复输出相同 `*_zh` 字段。
- Scoring request JSON 包含 `title`、`summary`、`source`、`published_at`、`original_link`。
- Translation request JSON 包含 `original_title`、`original_summary`、`original_content`、`source`、`score`。
- 无效 LLM JSON 不写入 score 或中文字段。

✘ Fail:

- 验收测试访问真实 LLM。
- mock 输出不稳定。
- LLM schema validation failure 后仍写入业务字段。

### ACC-STOP-008 Isolation And Determinism Gate

✔ Pass:

- 验收测试使用 `mvp_acceptance_fixture@v1`。
- 验收测试使用 `mvp_mock@v1`。
- 验收断言使用 `fixed_clock_fixture@v1`。
- RSS、HTML、LLM 都使用 mock 或 fixture。
- 测试数据库使用临时 SQLite。
- Replay test 在相同 fixture、mock、clock 下输出一致。

✘ Fail:

- 验收测试依赖真实 RSS、真实网页或真实 LLM。
- 验收断言读取真实系统时间、网络时间或当前日期。
- 测试污染开发数据库。

### ACC-STOP-009 Leak Gate

✔ Pass:

- API JSON、UI DOM、logs 中禁止字段计数为 `0`。
- Test reports 中 `visibility = public_surface` 的 assertion 禁止字段计数为 `0`。
- Test reports 中 `visibility = internal_evidence` 的 DB/schema/state assertion 可以出现内部字段名以证明数据事实，但不得包含 raw article body、fallback raw text、完整 prompt、密钥、secret、token 或超过 `1024` 字符的正文片段。
- `acceptance_gate.leak_policy.forbidden_contextual_fields` 命中数为 `0`：
  - `full_llm_prompt`
  - `raw_pipeline_payload`
  - `raw_article_body`
- API JSON、UI DOM、logs 和 `visibility = public_surface` report assertion 中，`acceptance_gate.leak_policy.forbidden_internal_fields` 命中数为 `0`：
  - `pipeline_state`
  - `is_selected`
  - `content_raw`
  - `content_full`
  - `has_translate_failed`
  - `deleted_at`
- `acceptance_gate.leak_policy.allowlist_fields.safe_tokens` 中的字段名不计为 token 泄漏。
- `acceptance_gate.leak_policy.forbidden_token_patterns` 命中数为 `0`。

✘ Fail:

- 任一 forbidden field 出现在 API/UI/log 或 `visibility = public_surface` 的 report assertion。
- `visibility = internal_evidence` 的 report assertion 包含内部字段的完整业务值、raw article body、fallback raw text、完整 prompt、密钥、secret 或 token-like credential。
- 任一 raw article body、fallback raw text、完整 prompt、密钥、secret 或未在 allowlist 中的 token-like credential 泄漏。
- 日志中正文类字段超过 `1024` 字符。

### ACC-STOP-010 Change Consistency Gate

✔ Pass:

- 修改 API 行为时，同步更新 `05_api_contract.md`。
- 修改数据字段或状态事实时，同步更新 `04_data_model.md`。
- 修改 UI 行为或组件字段时，同步更新 `03_ui_spec.md`。
- 修改测试报告结构时，同步更新 `07_test_spec.md#6`。
- 修改 source document、task record、schema、fixture、mock 或 referenced file 后，相关旧报告必须通过 current `data_hash` 重新证明；过期报告不得继续作为 gate 通过证据。
- 未新增 MVP non-goal 能力，例如 user/login/search/category/comment/favorite/share/task progress/retry/admin/versioning。

✘ Fail:

- 代码行为与文档契约不一致。
- 新增 endpoint、UI 行为或数据字段未写入对应契约文档。
- 使用 stale report、旧 task snapshot 或旧 source-document hash 作为通过证据。
- 实现了 MVP 明确排除的功能。

## 5. Stop Decision

Machine-checkable decision is defined once in `acceptance_gate.final_decision`.

Evaluation rule:

- `acceptance_gate.boolean_eval_spec.engine` reads both `acceptance_gate.runtime_state.gate_status` and `acceptance_gate.runtime_state.stop_inputs`.
- Each operand `G1` to `G10` evaluates to `true` only when its value equals `PASS`.
- Stop input operands `TASKS`、`PRD`、`TASK_ACCEPTANCE`、`BROWSER_E2E` and `LOCAL_USER_ACCEPTANCE` evaluate to `true` only when their mapped status equals `PASS`.
- Any `UNKNOWN`、`FAIL`、`TASK_BLOCKED`、`WORKFLOW_BLOCKED` or `ENV_BLOCKED` gate or stop-input operand evaluates to `false`.
- `STOP_ALLOWED` equals the result of `acceptance_gate.final_decision.expression`.
- `task_completion_status = PASS` requires every `tasks.md.dag.nodes[*].status == passed`; `pending`、`in_progress` or `task_blocked` all produce `FAIL`.
- `prd_coverage_status = PASS` requires `reports/acceptance/prd_coverage.json` to exist, match `schemas/prd_coverage.schema.json`, use `schema_ref = 07_test_spec.md#6.3.1`, report `status = passed`, map every PRD acceptance item to stable PRD id、source line、passed task id、acceptance gate、assertion id and report path, and contain no uncovered PRD acceptance item.
- `task_acceptance_coverage_status = PASS` requires `reports/acceptance/task_acceptance_coverage.json` to exist, match `schemas/task_acceptance_coverage.schema.json`, report `status = passed`, and contain no uncovered task acceptance item.
- `browser_e2e_status = PASS` requires structured browser or DOM-capable E2E evidence for homepage news feed, 30-day high-score list, news detail, and sources management. API-only evidence cannot satisfy this input.
- `local_user_acceptance_status = PASS` requires `reports/acceptance/deployed_browser_smoke.json` to exist, target `http://127.0.0.1:8010/`, prove the deployed app mounts in a real browser with zero console/page errors and visible Home/HighScore/Article/Sources/Refresh surfaces, and requires `reports/acceptance/local_user_acceptance.json` to exist, match `schemas/local_user_acceptance.schema.json`, report `status = passed`, and contain no failed findings.

### 5.1 StopDecisionReport

`reports/acceptance/STOP_ALLOWED.json` must use this shape:

The machine-checkable JSON Schema for this report lives at `schemas/stop_decision.schema.json`. The prose rules in this section remain authoritative when schema and prose conflict, and schema changes must update this section in the same task.

```json
{
  "schema_ref": "08_acceptance.md#5.1",
  "schema_version": "v1",
  "STOP_ALLOWED": false,
  "gate_status": {
    "ACC-STOP-001": "FAIL",
    "ACC-STOP-002": "UNKNOWN",
    "ACC-STOP-003": "UNKNOWN",
    "ACC-STOP-004": "UNKNOWN",
    "ACC-STOP-005": "UNKNOWN",
    "ACC-STOP-006": "UNKNOWN",
    "ACC-STOP-007": "UNKNOWN",
    "ACC-STOP-008": "UNKNOWN",
    "ACC-STOP-009": "UNKNOWN",
    "ACC-STOP-010": "UNKNOWN"
  },
  "passed_gates": [],
  "failed_gates": ["ACC-STOP-001"],
  "blocked_gates": [],
  "unknown_gates": [
    "ACC-STOP-002",
    "ACC-STOP-003",
    "ACC-STOP-004",
    "ACC-STOP-005",
    "ACC-STOP-006",
    "ACC-STOP-007",
    "ACC-STOP-008",
    "ACC-STOP-009",
    "ACC-STOP-010"
  ],
  "stop_inputs": {
    "task_completion_status": "FAIL",
    "prd_coverage_status": "FAIL",
    "task_acceptance_coverage_status": "FAIL",
    "browser_e2e_status": "FAIL",
    "local_user_acceptance_status": "FAIL"
  },
  "failed_stop_inputs": [
    "task_completion_status",
    "prd_coverage_status",
    "task_acceptance_coverage_status",
    "browser_e2e_status",
    "local_user_acceptance_status"
  ],
  "failure_reasons": {
    "task_completion_status": ["task_completion:unfinished_count=1"],
    "prd_coverage_status": ["prd_coverage:missing_report"],
    "task_acceptance_coverage_status": ["task_acceptance_coverage:missing_report"],
    "browser_e2e_status": ["browser_e2e:missing_stage_report"],
    "local_user_acceptance_status": ["local_user_acceptance:missing_report"]
  },
  "unfinished_tasks": [
    {
      "id": "TASK-026",
      "status": "pending"
    }
  ],
  "uncovered_prd_items": [],
  "uncovered_task_acceptance_items": [],
  "user_acceptance_failures": [],
  "round_count_policy": {
    "status": "FAIL",
    "completed_round_count": 1,
    "minimum_recommended_rounds": 10,
    "unfinished_work_exists": true,
    "early_done_allowed": false,
    "summary_reports": [
      "reports/tasks/TASK-000/summary.json"
    ],
    "round_evidence": [
      {
        "task_id": "TASK-000",
        "summary_report": "reports/tasks/TASK-000/summary.json",
        "review_report": "reports/tasks/TASK-000/review.json",
        "fix_optimize_report": "reports/tasks/TASK-000/fix_optimize.json",
        "round_index": 1,
        "valid": false,
        "failure_reasons": [
          "review:missing"
        ]
      }
    ],
    "failure_reasons": [
      "round_count:unfinished_work_exists"
    ]
  },
  "generated_from_reports": [
    "reports/acceptance/ACC-STOP-001.json",
    "reports/acceptance/ACC-STOP-002.json",
    "reports/acceptance/ACC-STOP-003.json",
    "reports/acceptance/ACC-STOP-004.json",
    "reports/acceptance/ACC-STOP-005.json",
    "reports/acceptance/ACC-STOP-006.json",
    "reports/acceptance/ACC-STOP-007.json",
    "reports/acceptance/ACC-STOP-008.json",
    "reports/acceptance/ACC-STOP-009.json",
    "reports/acceptance/ACC-STOP-010.json"
  ],
  "timestamp": "2026-06-28T09:00:00Z"
}
```

Rules:

- `schema_ref` MUST equal `08_acceptance.md#5.1`.
- `schema_version` MUST equal `v1`.
- `STOP_ALLOWED` MUST be `true` only when every required gate status is `PASS` and every stop input status is `PASS`.
- `STOP_ALLOWED` MUST be `false` when any task is not `passed`, PRD coverage is incomplete, task acceptance coverage is incomplete, browser E2E evidence is missing, or latest local user acceptance has failed findings.
- When `STOP_ALLOWED = true`, `failure_reasons`、`failed_gates`、`blocked_gates`、`unknown_gates`、`failed_stop_inputs`、`unfinished_tasks`、`uncovered_prd_items`、`uncovered_task_acceptance_items` and `user_acceptance_failures` MUST all be empty.
- `gate_status` values MUST use `PASS`、`FAIL`、`UNKNOWN`、`TASK_BLOCKED`、`WORKFLOW_BLOCKED` or `ENV_BLOCKED`.
- `stop_inputs` MUST contain exactly `task_completion_status`、`prd_coverage_status`、`task_acceptance_coverage_status`、`browser_e2e_status` and `local_user_acceptance_status`.
- `failed_stop_inputs` MUST list every stop input whose status is not `PASS`.
- `failure_reasons` MUST include machine-readable reasons for every failed stop input and every stop-decision schema failure.
- `unfinished_tasks` MUST list every `tasks.md` node whose status is not `passed`.
- `uncovered_prd_items` MUST list every PRD acceptance item not mapped to executed passing evidence.
- `uncovered_task_acceptance_items` MUST list every task acceptance criterion not mapped to executed passing evidence.
- `user_acceptance_failures` MUST list every failed local user acceptance finding.
- `round_count_policy` MUST list `completed_round_count`、`minimum_recommended_rounds`、`unfinished_work_exists`、`early_done_allowed`、`summary_reports`、`round_evidence`、`failure_reasons` and `status`.
- `round_evidence` MUST contain one entry for each candidate completed round and list `task_id`、`summary_report`、`review_report`、`fix_optimize_report`、`round_index`、`valid` and any machine-readable failure reasons.
- `completed_round_count` MUST be derived from `round_evidence[*].valid == true`, not copied from a task summary's self-reported count.
- `round_count_policy.status = PASS` requires either at least 10 valid round evidence entries or `early_done_allowed = true`, and `round_count_policy.failure_reasons` MUST be empty.
- `round_count_policy.early_done_allowed = true` is permitted only when `completed_round_count < 10`, no unfinished work exists, no malformed round evidence exists, every required gate is `PASS`, every stop input is `PASS`, PRD coverage is complete, task acceptance coverage is complete, browser E2E has passed, and latest local user acceptance has no failed findings.
- `STOP_ALLOWED = true` requires `round_count_policy.status = PASS`.
- `timestamp` MUST be derived from `fixed_clock_fixture@v1` for acceptance runs.
- `generated_from_reports` MUST contain exactly one stable relative path for each `ACC-STOP-001` through `ACC-STOP-010` report, never absolute paths. Paths MAY be repo-relative (`reports/acceptance/ACC-STOP-001.json`) or report-dir-relative (`acceptance/ACC-STOP-001.json`) when `--report-dir` points outside the repository default.
- `STOP_ALLOWED = true` MUST be produced only by the workflow `ACCEPTANCE` state running `python3 scripts/run_harness.py --stage acceptance --report-dir reports` without `--task-id`.
- A previous `STOP_ALLOWED = true` MUST be considered stale when a later local user acceptance finding fails.
- A failed local user acceptance finding MUST be preserved in `reports/acceptance/local_user_acceptance.json`, force workflow `ITERATE`, and be mapped to a regression assertion before a later acceptance run may restore `local_user_acceptance_status = PASS`.

Codex 可以成功停止编程并进入 `DONE`，当且仅当：

- ACC-STOP-001 到 ACC-STOP-010 全部通过。
- PRD coverage matrix 覆盖 `docs/01_prd.md` 的全部验收标准。
- Task acceptance coverage matrix 覆盖 `tasks.md` 的全部 task acceptance criteria。
- 真实浏览器或等价 DOM runner 已验证主页新闻流、30 天高分榜单、详情页和信源页。
- 最新本地用户验收记录无失败项。
- 最新部署浏览器 smoke 记录无失败项，且证明 `http://127.0.0.1:8010/` 不是空白页或运行时崩溃页。
- Round count policy 证明已经完成至少 10 轮，或在 10 轮前已满足全部停止条件并允许提前 DONE。
- 没有 `failed`、`flaky`、`skipped` required report。
- 没有 API/UI/log/report 数据泄漏。
- 没有未解释的验收证据缺口。
- `STOP_ALLOWED = true`。

Codex 必须继续编程，当任一条件成立：

- 任一 Required Gate 失败。
- 任一 Required Gate 未执行。
- 任一 Required Gate 无结构化证据。
- 代码与 `03`、`04`、`05`、`06`、`07` 任一契约冲突。
- Codex 只能给出人工判断，不能给出可计算证据。

Codex 可以停止当前自主运行但不得进入 `DONE`，并且必须标记 `TASK_BLOCKED`、`WORKFLOW_BLOCKED` 或 `ENV_BLOCKED`，当且仅当：

- 验收执行环境缺失，且无法在当前工作区修复。
- 依赖安装失败且无法在当前工作区修复。
- 缺失 fixture、mock 或可计算验收证据生成能力，且无法从现有文档和代码中补齐。

Blocked 状态不是验收通过，不得写入 `STOP_ALLOWED = true`，也不得在最终回复中声称项目完成。

## 6. Final Response Contract

Codex 最终回复必须包含：

- 修改了哪些文件。
- 每个 Required Gate 的最终状态。
- 每个 Required Gate 的结构化证据位置。
- Required Gates 是否全部通过。
- 未验证的 gate 及原因。
- 如存在失败，下一步修复入口。

Final response 不得声称完成，除非 Stop Decision 满足。
