# 08_acceptance.md

## 0. Purpose

本文档定义 AI 新闻聚合系统 MVP 的 Codex Stop Gate。

`07_test_spec.md` 定义测试体系；本文档只回答一个问题：

> Codex 完成编程后，满足哪些验收条件才可以停止继续修改代码。

本文档只定义验收事实、通过条件、失败条件、证据模型、运行状态和停止判定。本文档不维护 Gate 到命令的执行映射。

## 1. Gate Configuration

```yaml
acceptance_gate:
  version: 08_acceptance@codex-stop-v5
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

  deterministic_inputs:
    fixture_set: mvp_acceptance_fixture@v1
    mock_set: mvp_mock@v1
    clock_source: fixed_clock_fixture@v1

  isolation: strict_mock

  test_report_contract:
    ref: 07_test_spec.md#6
    version: v1

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

  stop_rule: ALL_REQUIRED_GATES_PASSED
  continue_rule: ANY_REQUIRED_GATE_FAILED_OR_UNPROVEN

  runtime_state:
    gate_status_enum:
      - UNKNOWN
      - PASS
      - FAIL
      - BLOCKED
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

  boolean_eval_spec:
    engine: simple_boolean_interpreter_v1
    type: strict
    truth_table:
      PASS: true
      FAIL: false
      BLOCKED: false
      UNKNOWN: false

  final_decision:
    type: boolean_expression_ast
    evaluator: simple_boolean_interpreter_v1
    result: STOP_ALLOWED
    operands_source: runtime_state.gate_status
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
```

## 2. Isolation

```yaml
isolation: strict_mock
```

- Required Gates 必须基于 fixture、mock、fixed clock 或结构化测试报告完成判定。
- 验收证据不得依赖真实 RSS、真实网页、真实 LLM、生产数据库、网络时间或当前系统时间。
- 如果任一 Required Gate 只能通过 live dependency 得到证据，该 gate 必须判定为 failed 或 blocked。
- `strict_mock` 只约束验收输入来源，不定义执行命令、runner 或报告输出路径。

## 3. Evidence Model

验收只能读取可计算证据，不读取自由文本判断。

| Evidence | Required content |
| --- | --- |
| Gate report | `schema_ref`、`schema_version`、`test_id`、`stage`、`status`、`assertions`、`expected`、`actual`、`diff`、`trace_id`。 |
| Assertion record | Assertion id、assertion type、status、expected、actual、diff。 |
| API JSON evidence | Response envelope、DTO fields、status code、forbidden field scan result。 |
| DB state evidence | Table schema、state transition、dedupe、translation field facts。 |
| UI render evidence | Rendered DTO fields、loading/empty/error/not found state、forbidden DOM field scan result。 |
| Dependency evidence | Live RSS、live HTML、live LLM access count。 |
| Leak evidence | Forbidden field count、forbidden pattern count、allowlisted token field count。 |

Codex 不得把“看起来正常”“页面能打开”“日志没有明显错误”作为验收证据。

每个 Required Gate 必须产生结构化 Gate report。缺少 Gate report、Gate report 无法解析、Gate report 不符合 `07_test_spec.md#6` 或 Gate report 无法映射到 `ACC-STOP-001` 到 `ACC-STOP-010`，均判定为未通过。

## 4. Required Gates

### ACC-STOP-001 Test Report Gate

✔ Pass:

- 所有验收测试输出符合 `07_test_spec.md#6` 的 `TestReport`。
- 每个 required test report 的 `schema_ref = 07_test_spec.md#6`。
- 每个 required test report 的 `schema_version = v1`。
- 每个 required test report 的 `status = passed`。
- 每个 required test report 的 `assertions` 非空，且全部 assertion `status = passed`。

✘ Fail:

- 缺少 test report。
- 任一 report schema 不匹配。
- 任一 required report 为 `failed`、`flaky` 或 `skipped`。
- 任一 assertion 缺失 `expected`、`actual` 或 `diff`。

### ACC-STOP-002 Source Management Gate

✔ Pass:

- 空库首次启动写入 7 个默认 RSS source。
- `GET /api/sources` 返回 `SourceItem[]`，按 `created_at ASC` 排序。
- `POST /api/sources` 对合法公开 RSS URL 返回 `201`。
- 非法 URL、本地地址、私有地址返回 `400`。
- 重复 RSS URL 返回 `409`。
- 删除或禁用 source 后，历史 `news_item` 保留。
- 禁止关闭全部 source 的操作返回 `409`。

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
- `POST /api/refresh` 不返回 task、queue、worker、retry、progress 字段。
- 未记录 endpoint 返回 `404` 或 `405`。

✘ Fail:

- API response shape 与 `05_api_contract.md` 不一致。
- API handler 直接返回 DB model。
- API 暴露未记录 endpoint 或未记录字段。

### ACC-STOP-005 Data Integrity Gate

✔ Pass:

- SQLite schema 只依赖 MVP 核心表：`source`、`news_item`、`processing_log`。
- `news_item.pipeline_state` 只允许 `raw`、`scored`、`fetched`。
- `pipeline_state` transition 只允许 `raw -> scored -> fetched`。
- `is_selected` 由 score threshold 计算，默认 threshold 为 `60`。
- `canonical_url` 唯一约束阻止重复新闻。
- API `status` 只由 API 层投影，不写入数据库。
- 翻译完成事实只由 `title_zh`、`summary_zh`、`content_zh` 判断。

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
- `translated` detail page 渲染中文正文 `content_zh`。
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

- API JSON、UI DOM、logs、test reports 中禁止字段计数为 `0`。
- `acceptance_gate.leak_policy.forbidden_contextual_fields` 命中数为 `0`：
  - `full_llm_prompt`
  - `raw_pipeline_payload`
  - `raw_article_body`
- `acceptance_gate.leak_policy.forbidden_internal_fields` 命中数为 `0`：
  - `pipeline_state`
  - `is_selected`
  - `content_raw`
  - `content_full`
  - `has_translate_failed`
- `acceptance_gate.leak_policy.allowlist_fields.safe_tokens` 中的字段名不计为 token 泄漏。
- `acceptance_gate.leak_policy.forbidden_token_patterns` 命中数为 `0`。

✘ Fail:

- 任一 forbidden field 出现在 API/UI/log/report。
- 任一完整正文、完整 prompt、密钥、secret 或未在 allowlist 中的 token-like credential 泄漏。
- 日志中正文类字段超过 `1024` 字符。

### ACC-STOP-010 Change Consistency Gate

✔ Pass:

- 修改 API 行为时，同步更新 `05_api_contract.md`。
- 修改数据字段或状态事实时，同步更新 `04_data_model.md`。
- 修改 UI 行为或组件字段时，同步更新 `03_ui_spec.md`。
- 修改测试报告结构时，同步更新 `07_test_spec.md#6`。
- 未新增 MVP non-goal 能力，例如 user/login/search/category/comment/favorite/share/task progress/retry/admin/versioning。

✘ Fail:

- 代码行为与文档契约不一致。
- 新增 endpoint、UI 行为或数据字段未写入对应契约文档。
- 实现了 MVP 明确排除的功能。

## 5. Stop Decision

Machine-checkable decision is defined once in `acceptance_gate.final_decision`.

Evaluation rule:

- `acceptance_gate.boolean_eval_spec.engine` reads `acceptance_gate.runtime_state.gate_status`.
- Each operand `G1` to `G10` evaluates to `true` only when its value equals `PASS`.
- Any `UNKNOWN`、`FAIL` or `BLOCKED` operand evaluates to `false`.
- `STOP_ALLOWED` equals the result of `acceptance_gate.final_decision.expression`.

Codex 可以停止编程，当且仅当：

- ACC-STOP-001 到 ACC-STOP-010 全部通过。
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

Codex 可以停止但必须标记 blocked，当且仅当：

- 验收执行环境缺失，且无法在当前工作区修复。
- 依赖安装失败且无法在当前工作区修复。
- 缺失 fixture、mock 或可计算验收证据生成能力，且无法从现有文档和代码中补齐。

Blocked 状态不是验收通过。

## 6. Final Response Contract

Codex 最终回复必须包含：

- 修改了哪些文件。
- 每个 Required Gate 的最终状态。
- 每个 Required Gate 的结构化证据位置。
- Required Gates 是否全部通过。
- 未验证的 gate 及原因。
- 如存在失败，下一步修复入口。

Final response 不得声称完成，除非 Stop Decision 满足。
