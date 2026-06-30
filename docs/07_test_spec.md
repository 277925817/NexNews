# 07_test_spec.md

## 1. 测试目标
- 验证 `RSS → ingest → score → filter → fetch → translate → API → UI` 主链路正确。
- 验证 LLM scoring 和 translation 在 mock 下可重复、可断言。
- 验证翻译字段只通过 `title_zh`、`summary_zh`、`content_zh` 映射到 API/UI。
- 验证 API contract 不破坏 `05_api_contract.md`。
- 验证 UI 只按 `NewsItem` / `NewsListItem` / `NewsDetailItem` 渲染。
- 验证 API/UI 不暴露 `pipeline_state`、`is_selected`、`content_raw`、`content_full`、`has_translate_failed`、`deleted_at`。

### 1.1 Pipeline Graph Awareness
- 系统按 DAG 测试，不只按线性链路测试。
- RSS ingest、scoring、fetch、translate、API projection 必须可单独测试。
- 任一 node 失败不得破坏其他 node 的 mock isolation。

### 1.2 Source Document Coverage Contract

`07_test_spec.md` 必须覆盖 `01_prd.md` 到 `06_dev_rules.md` 中所有会影响行为、接口、数据、UI、错误处理、日志和工程边界的可测试要求。

Coverage rule:

| Source document | Required test evidence |
| --- | --- |
| `01_prd.md` | RSS 源管理、定时/手动抓取、评分、过滤、全文抓取、翻译、首页、榜单、详情页、异常态的闭环验收测试。 |
| `02_arch.md` | 模块边界、核心数据流 `RSS → Crawl → Score → Filter → Fetch → Translate → UI`、FastAPI + React/Vite + SQLite 架构边界测试。 |
| `03_ui_spec.md` | `NewsItem` 渲染契约、字段禁止渲染规则、允许交互白名单、页面/组件/状态/视觉约束测试。 |
| `04_data_model.md` | `source`、`news_item`、`processing_log` schema、索引、约束、状态事实、禁止字段和持久化规则测试。 |
| `05_api_contract.md` | 全 endpoint 成功/失败行为、响应 envelope、DTO 字段白名单、状态投影、分页/排序/限流/非目标接口测试。 |
| `06_dev_rules.md` | 静态架构规则、代码风格、错误分类、日志裁剪、pipeline 写入边界、mock 隔离和测试确定性测试。 |

Conflict rule:

- 当 `01_prd.md` 与 `04_data_model.md`、`05_api_contract.md` 或 `06_dev_rules.md` 冲突时，测试必须按 `06_dev_rules.md` 的 Rule Priority Order 执行当前可执行契约，同时把被冲突影响的 PRD 验收项记录为 PRD coverage 或 document-consistency failure；测试不得静默遗漏、弱化或替换 PRD 核心需求。
- `status = ready | translated | translation_failed` 只作为 API/UI projection 测试，不作为数据库生命周期字段测试。
- 数据库流程状态只测试 `pipeline_state = raw | scored | fetched`。
- 测试不得要求实现 `news_task`、`rss_source`、`translation_status`、`content_source`、`title_domain_hash` 等已被 `04_data_model.md` 或 `05_api_contract.md` 排除的旧设计。
- `PATCH /api/sources/{id}` 和 Toggle RSS source frontend binding 按 `05_api_contract.md` 测试；如果 UI 实现选择不暴露可见 toggle，必须先更新 `05_api_contract.md` 或 `03_ui_spec.md` 消除冲突。

### 1.3 Isolation

```yaml
isolation: strict_mock
```

- 所有验收、集成、replay、LLM、RSS、HTML 和 UI 测试必须使用 fixture、mock 或 fixed clock。
- 测试断言不得访问真实 RSS、真实网页、真实 LLM、生产数据库、网络时间或当前系统时间。
- 测试框架可以使用真实时间实现进程调度、超时和耗时统计，但不得把真实时间作为业务断言输入。
- 任一测试无法证明其输入来自 fixture、mock 或 fixed clock 时，测试结果必须判定为 failed 或 blocked。

## 2. 测试分层

### 2.0 Static Compliance Test（静态合规）

- 技术栈边界：后端入口、路由和 DTO 必须匹配 FastAPI；前端入口、组件和构建配置必须匹配 React + Vite。
- Python 文件名必须为 snake_case；React 组件文件必须为 PascalCase。
- API DTO 类型必须以 `Request`、`Response`、`Item` 结尾。
- 禁止自造缩写和模糊变量名，例如 `tmp`、`data1`、`foo`、`srcNm`、`pubAt`。
- 单个函数超过 `60` 行、单个文件超过 `300` 行时必须失败。
- API 调用必须集中在 API client；UI 组件不得直接拼接 endpoint 字符串。
- Frontend 不得读取 `pipeline_state`、`is_selected`、`content_raw`、`content_full` 或任何数据库字段名。
- `pipeline_state` 只能由 backend pipeline service 写入；API handler 和 frontend 写入必须失败。
- API handler 不得直接返回 DB model；必须返回 DTO。
- SQL/data access 必须集中在 repository 或 database helper。
- 不得新增 `05_api_contract.md` 未记录 endpoint；不得新增 `03_ui_spec.md` 未记录 UI 行为或组件。

### 2.1 Unit Test（函数级）
- RSS parser：固定 RSS XML → 标准 item 列表。
- URL normalizer：链接变体 → 同一 canonical URL。
- Scoring parser：固定 LLM JSON → `0-100` score。
- Selection rule：默认 threshold `60` → selected / not selected。
- Translation mapper：固定翻译 JSON → 中文字段。
- API projector：内部对象 → `NewsListItem` / `NewsDetailItem`。
- API status projector：`title_zh`、`summary_zh`、`content_zh`、`has_translate_failed` → `ready | translated | translation_failed`。
- Error classifier：异常 → `network | parsing | llm | validation_llm_error | database | validation | timeout | unknown`，LLM schema validation failure → `validation_llm_error`。
- Log sanitizer：正文、prompt、token、密钥字段裁剪或移除。

### 2.2 Contract Test（API 契约）
- Contract Test = structure correctness（schema only），不验证 runtime behavior。
- 所有 API response 必须通过 JSON Schema 或 Pydantic schema 校验。
- Schema version 锁定为 `v1`，不得删除字段或改变字段类型。
- Response 必须使用 whitelist field validation，未定义字段出现即失败。
- API diff test 必须阻止 response shape 破坏。
- 所有成功响应必须使用 `{ "data": ... }` envelope；`204` 必须无 body。
- 所有错误响应必须使用 `{ "error": { "code": "...", "message": "..." } }`。
- API response 字段必须使用 `snake_case`，ID 必须以 string 返回，timestamp 必须为 ISO 8601 UTC。
- API response 必须拒绝 `pipeline_state`、`is_selected`、`content_raw`、`content_full`、`has_translate_failed`、`deleted_at`、旧字段名 `source_url`、完整 prompt 和内部 DB model 字段。
- DB schema contract 必须校验 `source`、`news_item`、`processing_log` 表、字段、约束和索引。
- Test report contract 必须通过 JSON Schema 校验。

### 2.3 API Test（HTTP 接口）
- API Test = runtime behavior correctness，覆盖 status code、pagination、concurrency、business logic。
- 成功响应必须符合 `{ "data": ... }`。
- 错误响应必须符合 `{ "error": { "code": "...", "message": "..." } }`。
- `204` 响应必须无 body。
- `GET /api/home` 必须返回 `latest_news` 和 `top_ranked_news`。
- `GET /api/news/{id}` 必须返回可展示 `NewsDetailItem`。
- `POST /api/refresh` 必须返回 `refreshed_at: string | null`。
- `GET /api/sources` 必须返回按 `created_at ASC` 排序的未删除 `SourceItem[]`。
- `POST /api/sources` 必须测试成功创建、缺少 name、空 name、缺少 rss_url、非法 URL、本地/私有地址、重复 URL。
- `PATCH /api/sources/{id}` 必须测试启用、停用、source 不存在、source 已删除、禁止关闭最后一个未删除且启用的 source。
- `DELETE /api/sources/{id}` 必须测试 `204` 无 body、source 不存在或已删除返回 `404`、历史新闻仍可见、删除后配置列表隐藏该 source。
- API response 不得出现非法内部字段。
- Non-goal APIs 必须不存在：user/login/search/category/comment/favorite/share/task progress/retry/admin/versioning。

### 2.4 Integration Test（RSS→LLM→DB→API→UI）
- 使用临时 SQLite。
- 使用 RSS fixture、LLM scoring mock、translation mock。
- 执行 refresh 后，通过 API 验证可展示新闻。
- 使用 API response mock 渲染 UI 关键组件。
- 不访问真实 RSS、真实网页、真实 LLM。
- 覆盖完整主链路：default sources → enabled sources → RSS parse → canonical dedupe → score → `is_selected` → fetch/fallback → translate → API projection → UI render。
- 覆盖部分失败：单个 RSS source 失败、单篇 fetch 失败、单篇 translate 失败都不得阻断其他 source/item。

### 2.5 Golden Snapshot Test
- 保存 `GET /api/home` JSON snapshot。
- 保存 `GET /api/news/{id}` JSON snapshot。
- 保存关键 React DOM snapshot。
- 保存 DB schema snapshot 和 public OpenAPI/schema snapshot。
- 每次 pipeline run 后比对 JSON / DOM diff。
- Snapshot diff 必须为空，或由同一 task 中的契约文档变更和结构化 snapshot approval evidence 共同批准；不得依赖人工口头判断、隐藏本地文件或未追踪 fixture。

### 2.6 Pipeline Replay Test
- RSS ingestion 必须可 replay。
- 输入 fixture + fixed seed → 输出必须完全一致。
- LLM scoring mock 和 translation mock 必须支持 deterministic seed mode。
- Replay test 不得依赖真实时间、网络或外部 API。

### 2.7 LLM Prompt Regression Test
- Prompt template 必须有 snapshot。
- Prompt 变更必须触发 test diff。
- Prompt diff 必须为空，或由同一 task 中的 LLM contract/fixture 变更和结构化 prompt approval evidence 共同批准。
- Mock LLM response 必须通过 schema validation。
- 固定 fixture 下 score distribution 必须稳定。

### 2.8 UI Test
- 使用 mock API response 渲染 UI。
- Click NewsCard → detail page。
- Click HighScoreList item → detail page。
- Home page fixture 必须包含足够数量的可展示新闻来证明产品体验；当 fixture 中最近 30 天合格新闻不少于 10 条时，HighScoreList 必须渲染 10 条。
- Home News Feed 不能只用 1-3 条 smoke sample 证明完成；验收 fixture 必须覆盖至少 10 条可展示新闻、`score=59` 排除项、30 天窗口外排除项和同分排序项。
- Browser/DOM E2E 必须点击 NewsCard 和 HighScoreList item 进入 ArticleView，并断言详情页请求 `GET /api/news/{id}`、展示匹配 ID 的中文详情、ready 等待态、translation_failed 失败态和 404 错误态。
- Browser/DOM E2E 必须进入 Sources page，并断言 `GET/POST/PATCH/DELETE /api/sources` 绑定、创建成功、非法 URL 错误、禁用最后一个启用 source 错误和删除视觉移除。
- Browser/DOM E2E 必须点击 Home `[刷新]`，断言调用 `POST /api/refresh`，完成后重新调用 `GET /api/home`，且不会出现 HTML 被当 JSON 解析的错误。
- NewsCard summary fixture 必须包含带 HTML-like 标签的中文摘要字符串，并断言浏览器 DOM 将其作为文本渲染，不生成对应元素节点。
- Loading state 必须在 fetch 期间出现。
- Error state 必须渲染固定 fallback view。
- `NewsItem.status` → UI 展示必须 deterministic。
- UI 不得从 `summary_zh` 或 `content_zh` 反推 status。
- Invalid state combination must throw error in dev mode。
- ScoreBadge 和 SourceMarker 必须不可点击。
- SourceMarker 或 NewsCard source color 必须按 source id/name 稳定映射，不同 source 在 fixture 中必须产生可区分颜色；颜色不得成为唯一信息载体。
- HighScoreList 不得拥有独立 API、独立刷新、独立滚动容器、tab、modal、drawer、dropdown 或 floating sidebar。
- NewsCard 不得在任何状态渲染 `content_zh`。
- `ready` / `translation_failed` UI 不得渲染 `summary_zh` 或 `content_zh`。
- 字段缺失或为空时必须不渲染该字段，不得用默认文案或其他字段替代。

### 2.9 Test Pyramid Strategy
- Static + Unit Test: 55%。
- Contract + API Test: 25%。
- Integration Test: 15%。
- Snapshot / Replay / E2E Test: 5%。
- Snapshot test 不得作为主要 correctness 判断依据。
- Integration test 必须保留最快失败反馈路径。

### 2.10 Flaky Test Control
- Unit test timeout default = `5s`。
- Integration test timeout default = `30s`。
- Retry 只允许 integration test 使用，max = `2`。
- Flaky test 必须标记 quarantine，不得阻塞确定性测试定位。
- Snapshot diff 不允许偶然通过。

### 2.11 Visual Regression Test
- NewsCard 和 ArticleView 必须生成 DOM snapshot。
- Home desktop layout 必须保持左 `News Feed`、右 `HighScoreList` 双列。
- NewsCard 最小高度、列表密度、骨架行尺寸、ArticleView 正文宽度必须符合 `03_ui_spec.md`。
- Hover 只能改变 border/background，不得出现 shadow、scale、lift 类效果。
- UI 不得出现未在 `03_ui_spec.md` 中列出的装饰性模块或组件。
- Layout diff 必须使用 pixel-level diff 或 structure diff。
- CSS class changes must trigger snapshot update approval。

### 2.12 End-to-End Deterministic Run
- 使用 clean database。
- 加载 RSS fixture。
- 执行 full pipeline。
- 断言 API output snapshot。
- 断言 UI snapshot。
- 输出必须 fully reproducible。

### 2.13 Test Execution Orchestration
- Test stages must run in deterministic order: `static → unit → contract → api → integration → replay → snapshot → e2e`。
- Required stages for acceptance are exactly: `static`, `unit`, `contract`, `api`, `integration`, `replay`, `snapshot`, `e2e`.
- `acceptance` is a harness gate-evaluation stage. It runs after required stages, consumes their reports, and emits ACC-STOP reports plus `STOP_ALLOWED.json`; it is not one of the required product verification stages.
- `acceptance` must run only as a full-stage command without `--task-id`; task-scoped acceptance is invalid and must emit structured failure evidence.
- Acceptance gate reports are written under `reports/acceptance/ACC-STOP-*.json`; acceptance must not be required to write `reports/stages/acceptance.json`, and any compatibility file at that path is diagnostic only.
- Stage commands, report paths and workflow loop strategy are defined by `workflows.md`.
- The historical stage list is supported for compatibility, but final stop eligibility depends on PRD coverage, browser-visible E2E evidence and latest local user acceptance. A shallow pass in each stage is not sufficient.
- Each stage must start from clean isolated state。
- Stage failure must stop downstream execution。
- No shared global state across stages。
- Any stage with behavior-only evidence and no mandatory assertion must still emit at least one `report_metadata` assertion in its `TestReport`; prose-only non-assertion evidence is not a valid report.

### 2.14 Assertion Hierarchy
- Failure priority: Static rule violation → Contract violation → Data model violation → Data leakage violation → Replay inconsistency → API behavior mismatch → Integration mismatch → Snapshot diff → UI visual regression。
- Higher priority failure overrides lower priority results。
- Only the highest severity failure is reported first。

### 2.15 Test Cost Control
- Static test max time: `5s`。
- Unit test max time: `5s`。
- API test max time: `15s`。
- Integration test max time: `30s`。
- Snapshot test max time: `10s`。
- Full E2E max time: `60s`。
- Timeout must fail with `timeout` category。

### 2.16 Mandatory Assertion Catalog

Harness stage reports must prove coverage through stable assertion IDs, not prose-only claims.

Assertion ID format:

```text
A-<stage>-<gate>-<slug>
```

Rules:

- `stage` MUST be one of `static`, `unit`, `contract`, `api`, `integration`, `replay`, `snapshot`, `e2e`, or `acceptance`.
- `gate` MUST be one `ACC-STOP-001` through `ACC-STOP-010`.
- `slug` MUST be lowercase ASCII words joined by `-`.
- Every mandatory catalog row below MUST appear in at least one full-stage or ACC-STOP report before `STOP_ALLOWED = true`.
- A mandatory assertion is covered only when an assertion with the exact `id` appears with `status = passed`, non-empty `expected`, non-empty `actual`, parseable `diff`, and correct `visibility`.
- Task-scoped reports may prove task progress but cannot replace full-stage mandatory assertion coverage.
- Task-scoped reports MUST execute only the mandatory assertion IDs that belong to the current task/stage scope; missing out-of-scope mandatory IDs fail only full-stage materialization or workflow acceptance.
- Each required `ACC-STOP-*` gate MUST have at least one mandatory assertion row in this catalog.
- Acceptance MUST fail `ACC-STOP-001` when any mandatory assertion ID is missing, skipped, flaky, failed, duplicated with conflicting results, or attached to the wrong stage.

Mandatory catalog:

| Assertion id | Stage | Gate | Visibility | Required proof |
| --- | --- | --- | --- | --- |
| `A-static-ACC-STOP-010-architecture-boundaries` | static | ACC-STOP-010 | report_metadata | Project structure, import boundaries, non-goal files and documented contracts align. |
| `A-static-ACC-STOP-009-forbidden-public-fields` | static | ACC-STOP-009 | report_metadata | Static scan finds no forbidden internal fields in API/UI public surfaces. |
| `A-unit-ACC-STOP-003-rss-normalize-dedupe` | unit | ACC-STOP-003 | internal_evidence | RSS parser and canonical URL dedupe are deterministic. |
| `A-unit-ACC-STOP-007-llm-schema-validation` | unit | ACC-STOP-007 | internal_evidence | Scoring and translation mock responses pass schema validation and invalid outputs are rejected. |
| `A-unit-ACC-STOP-005-state-machine` | unit | ACC-STOP-005 | internal_evidence | `pipeline_state` transitions only `raw -> scored -> fetched`; `is_selected` does not mutate state. |
| `A-contract-ACC-STOP-004-api-shapes` | contract | ACC-STOP-004 | public_surface | Every documented endpoint response matches `05_api_contract.md` and unknown fields fail. |
| `A-contract-ACC-STOP-005-db-schema` | contract | ACC-STOP-005 | internal_evidence | SQLite application schema matches `04_data_model.md` tables, fields, constraints and indexes. |
| `A-api-ACC-STOP-002-source-management` | api | ACC-STOP-002 | public_surface | Source CRUD, soft delete, duplicate URL, private URL and last-enabled-source behavior pass. |
| `A-api-ACC-STOP-004-refresh-contract` | api | ACC-STOP-004 | public_surface | `POST /api/refresh` returns only `refreshed_at` and exposes no task/progress/run-summary fields. |
| `A-api-ACC-STOP-009-api-leak-scan` | api | ACC-STOP-009 | public_surface | API JSON leak scan has zero forbidden fields and zero sensitive content matches. |
| `A-integration-ACC-STOP-003-full-pipeline` | integration | ACC-STOP-003 | internal_evidence | Fixture pipeline produces displayable news through RSS, score, fetch, translate and API projection. |
| `A-integration-ACC-STOP-006-ui-render-contract` | integration | ACC-STOP-006 | public_surface | UI renders translated, ready, failed, loading, empty and not-found states from DTOs only. |
| `A-replay-ACC-STOP-008-deterministic-replay` | replay | ACC-STOP-008 | report_metadata | Two clean runs with same fixture/mock/clock produce matching hashes. |
| `A-snapshot-ACC-STOP-004-public-snapshots` | snapshot | ACC-STOP-004 | public_surface | API JSON, DOM, DB schema and public schema snapshots match or carry structured approval evidence. |
| `A-e2e-ACC-STOP-008-clean-run-isolation` | e2e | ACC-STOP-008 | report_metadata | E2E run uses only fixture/mock/fixed clock/temp SQLite and no live dependency. |
| `A-acceptance-ACC-STOP-001-mandatory-catalog-covered` | acceptance | ACC-STOP-001 | report_metadata | Acceptance confirms every mandatory assertion ID is present and passed in allowed reports. |
| `A-static-ACC-STOP-001-test-report-schema-contract` | static | ACC-STOP-001 | report_metadata | TestReport, StopDecisionReport, TaskPlanReport, ReviewReport, FixOptimizeReport and task DAG schemas are parseable and self-consistent. |
| `A-acceptance-ACC-STOP-001-stop-decision-schema` | acceptance | ACC-STOP-001 | report_metadata | `STOP_ALLOWED.json` conforms to `docs/08_acceptance.md#5.1` and contains only relative `generated_from_reports` paths. |
| `A-acceptance-ACC-STOP-001-no-task-scoped-substitution` | acceptance | ACC-STOP-001 | report_metadata | Acceptance rejects task-scoped gate evaluation and task-scoped reports as substitutes for full-stage stop evidence. |
| `A-static-ACC-STOP-001-round-evidence-report-schemas` | static | ACC-STOP-001 | report_metadata | ReviewReport, FixOptimizeReport and RoundSummaryReport schemas prevent counting rounds without review/fix evidence or summary-selected DONE. |
| `A-unit-ACC-STOP-001-round-count-policy-enforced` | unit | ACC-STOP-001 | report_metadata | Acceptance computes completed round count only from valid summary/review/fix evidence and rejects malformed round evidence. |
| `A-unit-ACC-STOP-001-coverage-schema-tightened` | unit | ACC-STOP-001 | report_metadata | PRD and task coverage reports cannot pass with uncovered items, prose-only evidence or coverage self-reference. |
| `A-unit-ACC-STOP-001-acceptance-evaluator-enforcement` | unit | ACC-STOP-001 | report_metadata | Acceptance evaluator rejects STOP_ALLOWED when round policy, task completion or required stop inputs are inconsistent. |
| `A-unit-ACC-STOP-001-local-user-acceptance-regression` | unit | ACC-STOP-001 | report_metadata | Failed local user acceptance findings keep STOP_ALLOWED false until converted into regression assertion evidence. |
| `A-api-ACC-STOP-002-default-source-seed` | api | ACC-STOP-002 | public_surface | Empty database initialization creates exactly the 7 documented default sources once. |
| `A-api-ACC-STOP-002-default-source-exact-list` | api | ACC-STOP-002 | public_surface | Empty database initialization creates a source URL set exactly equal to the 7 URLs listed in `docs/01_prd.md`. |
| `A-api-ACC-STOP-002-source-crud-errors` | api | ACC-STOP-002 | public_surface | Source create, update and delete APIs return documented success and structured error responses for invalid, duplicate, private, deleted and missing source cases. |
| `A-api-ACC-STOP-002-source-tombstone-history` | api | ACC-STOP-002 | public_surface | Source delete uses soft tombstone behavior, hides the source from configuration API and preserves historical news visibility. |
| `A-api-ACC-STOP-002-default-source-crud-parity` | api | ACC-STOP-002 | public_surface | Default seeded sources and user-created sources have identical enable, disable, delete, tombstone and no-auto-restore behavior. |
| `A-integration-ACC-STOP-002-source-ui-crud-parity` | integration | ACC-STOP-002 | public_surface | Sources UI renders identical controls and state transitions for default seeded and user-created sources. |
| `A-integration-ACC-STOP-003-scheduler-fixed-clock` | integration | ACC-STOP-003 | internal_evidence | Fixed clock cases trigger scheduled refresh at 09:00 and 18:00 and do not trigger at non-scheduled times. |
| `A-integration-ACC-STOP-003-threshold-selection` | integration | ACC-STOP-003 | internal_evidence | Threshold fixture proves score 60 is selected and score 59 is not API/UI visible. |
| `A-integration-ACC-STOP-003-dedupe-positive-distinct-items` | integration | ACC-STOP-003 | internal_evidence | Distinct high-score items with different canonical URLs or different domains remain separate fetch candidates. |
| `A-integration-ACC-STOP-003-fetch-fallback` | integration | ACC-STOP-003 | internal_evidence | Fetch success, extraction failure with RSS fallback and no-content failure produce the documented displayability outcomes. |
| `A-integration-ACC-STOP-003-fallback-summary-translation` | integration | ACC-STOP-003 | internal_evidence | A fetched item using RSS summary fallback produces non-empty Chinese summary and content through translation. |
| `A-integration-ACC-STOP-003-translation-failure-isolated` | integration | ACC-STOP-003 | internal_evidence | Translation failure does not write partial Chinese fields and does not block other items. |
| `A-api-ACC-STOP-004-home-detail-behavior` | api | ACC-STOP-004 | public_surface | Home and detail endpoints enforce sorting, 30-day ranking, translated detail fields and structured 404 behavior. |
| `A-api-ACC-STOP-004-non-goal-endpoints-absent` | api | ACC-STOP-004 | public_surface | User, login, search, category, comment, favorite, share, task progress, retry, admin and versioning endpoints are absent. |
| `A-static-ACC-STOP-005-pipeline-write-boundary` | static | ACC-STOP-005 | internal_evidence | Only backend pipeline services can write `pipeline_state` or compute `is_selected`. |
| `A-contract-ACC-STOP-005-forbidden-data-fields` | contract | ACC-STOP-005 | internal_evidence | DB schema excludes old/non-goal tables and fields including `translation_status`, `content_source`, `is_ready` and `display_mode`. |
| `A-unit-ACC-STOP-005-translation-facts` | unit | ACC-STOP-005 | internal_evidence | Translation success and failure facts derive only from Chinese fields, `has_translate_failed` cache and `processing_log`. |
| `A-integration-ACC-STOP-006-ui-forbidden-rendering` | integration | ACC-STOP-006 | public_surface | Ready and translation-failed UI states render no `summary_zh`, `content_zh`, raw English summary or raw English body. |
| `A-integration-ACC-STOP-006-ui-allowed-interactions` | integration | ACC-STOP-006 | public_surface | UI click behavior is limited to the whitelist in `docs/03_ui_spec.md#5.0`. |
| `A-e2e-ACC-STOP-006-home-news-density` | e2e | ACC-STOP-006 | public_surface | Browser-visible Home News Feed renders the PRD fixture news set, not a sparse smoke sample. |
| `A-e2e-ACC-STOP-006-high-score-list-browser` | e2e | ACC-STOP-006 | public_surface | Browser-visible HighScoreList renders up to 10 eligible 30-day items sorted by score and supports click-through to ArticleView. |
| `A-e2e-ACC-STOP-006-article-view-browser` | e2e | ACC-STOP-006 | public_surface | Browser-visible ArticleView loads `GET /api/news/{id}`, renders matching translated detail, ready waiting state, translation_failed state and structured 404 state without raw English body. |
| `A-e2e-ACC-STOP-006-article-original-link-button` | e2e | ACC-STOP-006 | public_surface | Browser-visible ArticleView renders a user-visible original URL link or button for translated details. |
| `A-e2e-ACC-STOP-006-no-direct-original-navigation` | e2e | ACC-STOP-006 | public_surface | Browser-visible NewsCard and HighScoreList clicks stay on internal ArticleView routes and do not navigate directly to the original site. |
| `A-e2e-ACC-STOP-006-sources-page-browser` | e2e | ACC-STOP-006 | public_surface | Browser-visible Sources page binds to `GET/POST/PATCH/DELETE /api/sources` and proves create, invalid URL, disable-all error and delete visual removal. |
| `A-e2e-ACC-STOP-006-refresh-action-browser` | e2e | ACC-STOP-006 | public_surface | Browser-visible refresh action calls `POST /api/refresh`, then reloads `GET /api/home`, with no HTML-as-JSON parse error. |
| `A-e2e-ACC-STOP-006-news-card-summary-text-only` | e2e | ACC-STOP-006 | public_surface | Browser-visible NewsCard summary renders HTML-like fixture text as text content and does not create raw HTML nodes. |
| `A-snapshot-ACC-STOP-006-layout-visual-contract` | snapshot | ACC-STOP-006 | public_surface | Home layout, NewsCard density, skeleton dimensions and ArticleView reading width match the UI spec. |
| `A-unit-ACC-STOP-007-llm-request-shapes` | unit | ACC-STOP-007 | internal_evidence | Scoring and translation requests contain exactly the documented structured JSON input fields. |
| `A-unit-ACC-STOP-007-llm-retry-failure-policy` | unit | ACC-STOP-007 | internal_evidence | Invalid, timeout and schema-invalid LLM outputs retry at most twice and do not write successful business fields. |
| `A-integration-ACC-STOP-008-live-dependency-blocked` | integration | ACC-STOP-008 | report_metadata | Harness blocks or fails any RSS, webpage, LLM, production DB or wall-clock dependency access during verification. |
| `A-replay-ACC-STOP-008-fixture-version-hash` | replay | ACC-STOP-008 | report_metadata | Replay reports include fixture version, mock version, fixed clock and stable data hashes for repeated runs. |
| `A-unit-ACC-STOP-009-log-sanitizer` | unit | ACC-STOP-009 | internal_evidence | Log sanitizer removes or truncates raw body, fallback text, prompt, secret and token-like values before persistence. |
| `A-integration-ACC-STOP-009-ui-dom-leak-scan` | integration | ACC-STOP-009 | public_surface | UI DOM leak scan finds zero forbidden internal fields and zero sensitive content matches. |
| `A-acceptance-ACC-STOP-009-report-leak-scan` | acceptance | ACC-STOP-009 | report_metadata | Acceptance scans public-surface reports for forbidden fields and internal-evidence reports for sensitive value leaks. |
| `A-acceptance-ACC-STOP-001-prd-coverage-complete` | acceptance | ACC-STOP-001 | report_metadata | Acceptance verifies every `docs/01_prd.md` acceptance statement is mapped to executed structured evidence. |
| `A-acceptance-ACC-STOP-001-task-acceptance-coverage-complete` | acceptance | ACC-STOP-001 | report_metadata | Acceptance verifies every `tasks.md` acceptance criterion is mapped to executed structured evidence. |
| `A-acceptance-ACC-STOP-001-task-completion-all-passed` | acceptance | ACC-STOP-001 | report_metadata | Acceptance verifies every `tasks.md` DAG node has `status = passed`; pending, in_progress and task_blocked nodes fail stop eligibility. |
| `A-acceptance-ACC-STOP-001-browser-e2e-evidence` | acceptance | ACC-STOP-001 | report_metadata | Acceptance verifies browser E2E stop input covers Home News Feed, HighScoreList, ArticleView and Sources page with structured passed evidence. |
| `A-acceptance-ACC-STOP-001-local-user-acceptance-passed` | acceptance | ACC-STOP-001 | report_metadata | Acceptance verifies latest local user acceptance report exists, matches schema and has no failed findings. |
| `A-static-ACC-STOP-010-contract-doc-sync` | static | ACC-STOP-010 | report_metadata | API, data, UI and report contract changes are reflected in their authoritative documents. |
| `A-static-ACC-STOP-010-non-goal-files-absent` | static | ACC-STOP-010 | report_metadata | Repository contains no active MVP implementation for documented non-goal capabilities. |

### 2.17 Mandatory Assertion Traceability Matrix

The matrix below is machine-checkable by the static harness. Each mandatory assertion ID from section 2.16 MUST appear exactly once here.

Rules:

- `Gate` MUST match the gate encoded in the assertion ID and the mandatory catalog row.
- `Owner task` MUST exist in `tasks.md` and include the same gate in its `acceptance_gate` list.
- `Stage` MUST match the stage encoded in the assertion ID and the mandatory catalog row.
- `Expected report path` MUST be `reports/stages/<stage>.json` for product stages and `reports/acceptance/<gate>.json` for acceptance-stage assertions.

| Assertion id | Gate | Owner task | Stage | Expected report path |
| --- | --- | --- | --- | --- |
| `A-static-ACC-STOP-010-architecture-boundaries` | ACC-STOP-010 | TASK-001 | static | reports/stages/static.json |
| `A-static-ACC-STOP-009-forbidden-public-fields` | ACC-STOP-009 | TASK-020 | static | reports/stages/static.json |
| `A-unit-ACC-STOP-003-rss-normalize-dedupe` | ACC-STOP-003 | TASK-004 | unit | reports/stages/unit.json |
| `A-unit-ACC-STOP-007-llm-schema-validation` | ACC-STOP-007 | TASK-008 | unit | reports/stages/unit.json |
| `A-unit-ACC-STOP-005-state-machine` | ACC-STOP-005 | TASK-002A | unit | reports/stages/unit.json |
| `A-contract-ACC-STOP-004-api-shapes` | ACC-STOP-004 | TASK-019 | contract | reports/stages/contract.json |
| `A-contract-ACC-STOP-005-db-schema` | ACC-STOP-005 | TASK-002A | contract | reports/stages/contract.json |
| `A-api-ACC-STOP-002-source-management` | ACC-STOP-002 | TASK-013 | api | reports/stages/api.json |
| `A-api-ACC-STOP-004-refresh-contract` | ACC-STOP-004 | TASK-014 | api | reports/stages/api.json |
| `A-api-ACC-STOP-009-api-leak-scan` | ACC-STOP-009 | TASK-019 | api | reports/stages/api.json |
| `A-integration-ACC-STOP-003-full-pipeline` | ACC-STOP-003 | TASK-018 | integration | reports/stages/integration.json |
| `A-integration-ACC-STOP-006-ui-render-contract` | ACC-STOP-006 | TASK-020 | integration | reports/stages/integration.json |
| `A-replay-ACC-STOP-008-deterministic-replay` | ACC-STOP-008 | TASK-022 | replay | reports/stages/replay.json |
| `A-snapshot-ACC-STOP-004-public-snapshots` | ACC-STOP-004 | TASK-023 | snapshot | reports/stages/snapshot.json |
| `A-e2e-ACC-STOP-008-clean-run-isolation` | ACC-STOP-008 | TASK-024 | e2e | reports/stages/e2e.json |
| `A-acceptance-ACC-STOP-001-mandatory-catalog-covered` | ACC-STOP-001 | TASK-021 | acceptance | reports/acceptance/ACC-STOP-001.json |
| `A-static-ACC-STOP-001-test-report-schema-contract` | ACC-STOP-001 | TASK-000 | static | reports/stages/static.json |
| `A-acceptance-ACC-STOP-001-stop-decision-schema` | ACC-STOP-001 | TASK-021 | acceptance | reports/acceptance/ACC-STOP-001.json |
| `A-acceptance-ACC-STOP-001-no-task-scoped-substitution` | ACC-STOP-001 | TASK-021 | acceptance | reports/acceptance/ACC-STOP-001.json |
| `A-static-ACC-STOP-001-round-evidence-report-schemas` | ACC-STOP-001 | TASK-026A | static | reports/stages/static.json |
| `A-unit-ACC-STOP-001-round-count-policy-enforced` | ACC-STOP-001 | TASK-026B | unit | reports/stages/unit.json |
| `A-unit-ACC-STOP-001-coverage-schema-tightened` | ACC-STOP-001 | TASK-026B | unit | reports/stages/unit.json |
| `A-unit-ACC-STOP-001-acceptance-evaluator-enforcement` | ACC-STOP-001 | TASK-026C | unit | reports/stages/unit.json |
| `A-unit-ACC-STOP-001-local-user-acceptance-regression` | ACC-STOP-001 | TASK-026C | unit | reports/stages/unit.json |
| `A-api-ACC-STOP-002-default-source-seed` | ACC-STOP-002 | TASK-002B | api | reports/stages/api.json |
| `A-api-ACC-STOP-002-default-source-exact-list` | ACC-STOP-002 | TASK-002B | api | reports/stages/api.json |
| `A-api-ACC-STOP-002-source-crud-errors` | ACC-STOP-002 | TASK-013 | api | reports/stages/api.json |
| `A-api-ACC-STOP-002-source-tombstone-history` | ACC-STOP-002 | TASK-013 | api | reports/stages/api.json |
| `A-api-ACC-STOP-002-default-source-crud-parity` | ACC-STOP-002 | TASK-013 | api | reports/stages/api.json |
| `A-integration-ACC-STOP-002-source-ui-crud-parity` | ACC-STOP-002 | TASK-017 | integration | reports/stages/integration.json |
| `A-integration-ACC-STOP-003-scheduler-fixed-clock` | ACC-STOP-003 | TASK-010 | integration | reports/stages/integration.json |
| `A-integration-ACC-STOP-003-threshold-selection` | ACC-STOP-003 | TASK-006 | integration | reports/stages/integration.json |
| `A-integration-ACC-STOP-003-dedupe-positive-distinct-items` | ACC-STOP-003 | TASK-006 | integration | reports/stages/integration.json |
| `A-integration-ACC-STOP-003-fetch-fallback` | ACC-STOP-003 | TASK-007 | integration | reports/stages/integration.json |
| `A-integration-ACC-STOP-003-fallback-summary-translation` | ACC-STOP-003 | TASK-008 | integration | reports/stages/integration.json |
| `A-integration-ACC-STOP-003-translation-failure-isolated` | ACC-STOP-003 | TASK-008 | integration | reports/stages/integration.json |
| `A-api-ACC-STOP-004-home-detail-behavior` | ACC-STOP-004 | TASK-019 | api | reports/stages/api.json |
| `A-api-ACC-STOP-004-non-goal-endpoints-absent` | ACC-STOP-004 | TASK-019 | api | reports/stages/api.json |
| `A-static-ACC-STOP-005-pipeline-write-boundary` | ACC-STOP-005 | TASK-002A | static | reports/stages/static.json |
| `A-contract-ACC-STOP-005-forbidden-data-fields` | ACC-STOP-005 | TASK-002A | contract | reports/stages/contract.json |
| `A-unit-ACC-STOP-005-translation-facts` | ACC-STOP-005 | TASK-018 | unit | reports/stages/unit.json |
| `A-integration-ACC-STOP-006-ui-forbidden-rendering` | ACC-STOP-006 | TASK-020 | integration | reports/stages/integration.json |
| `A-integration-ACC-STOP-006-ui-allowed-interactions` | ACC-STOP-006 | TASK-020 | integration | reports/stages/integration.json |
| `A-e2e-ACC-STOP-006-home-news-density` | ACC-STOP-006 | TASK-024 | e2e | reports/stages/e2e.json |
| `A-e2e-ACC-STOP-006-high-score-list-browser` | ACC-STOP-006 | TASK-024 | e2e | reports/stages/e2e.json |
| `A-e2e-ACC-STOP-006-article-view-browser` | ACC-STOP-006 | TASK-024 | e2e | reports/stages/e2e.json |
| `A-e2e-ACC-STOP-006-article-original-link-button` | ACC-STOP-006 | TASK-024 | e2e | reports/stages/e2e.json |
| `A-e2e-ACC-STOP-006-no-direct-original-navigation` | ACC-STOP-006 | TASK-024 | e2e | reports/stages/e2e.json |
| `A-e2e-ACC-STOP-006-sources-page-browser` | ACC-STOP-006 | TASK-024 | e2e | reports/stages/e2e.json |
| `A-e2e-ACC-STOP-006-refresh-action-browser` | ACC-STOP-006 | TASK-024 | e2e | reports/stages/e2e.json |
| `A-e2e-ACC-STOP-006-news-card-summary-text-only` | ACC-STOP-006 | TASK-024 | e2e | reports/stages/e2e.json |
| `A-snapshot-ACC-STOP-006-layout-visual-contract` | ACC-STOP-006 | TASK-023 | snapshot | reports/stages/snapshot.json |
| `A-unit-ACC-STOP-007-llm-request-shapes` | ACC-STOP-007 | TASK-005 | unit | reports/stages/unit.json |
| `A-unit-ACC-STOP-007-llm-retry-failure-policy` | ACC-STOP-007 | TASK-005 | unit | reports/stages/unit.json |
| `A-integration-ACC-STOP-008-live-dependency-blocked` | ACC-STOP-008 | TASK-003 | integration | reports/stages/integration.json |
| `A-replay-ACC-STOP-008-fixture-version-hash` | ACC-STOP-008 | TASK-022 | replay | reports/stages/replay.json |
| `A-unit-ACC-STOP-009-log-sanitizer` | ACC-STOP-009 | TASK-008 | unit | reports/stages/unit.json |
| `A-integration-ACC-STOP-009-ui-dom-leak-scan` | ACC-STOP-009 | TASK-020 | integration | reports/stages/integration.json |
| `A-acceptance-ACC-STOP-009-report-leak-scan` | ACC-STOP-009 | TASK-023 | acceptance | reports/acceptance/ACC-STOP-009.json |
| `A-acceptance-ACC-STOP-001-prd-coverage-complete` | ACC-STOP-001 | TASK-026 | acceptance | reports/acceptance/ACC-STOP-001.json |
| `A-acceptance-ACC-STOP-001-task-acceptance-coverage-complete` | ACC-STOP-001 | TASK-026 | acceptance | reports/acceptance/ACC-STOP-001.json |
| `A-acceptance-ACC-STOP-001-task-completion-all-passed` | ACC-STOP-001 | TASK-021 | acceptance | reports/acceptance/ACC-STOP-001.json |
| `A-acceptance-ACC-STOP-001-browser-e2e-evidence` | ACC-STOP-001 | TASK-026 | acceptance | reports/acceptance/ACC-STOP-001.json |
| `A-acceptance-ACC-STOP-001-local-user-acceptance-passed` | ACC-STOP-001 | TASK-026 | acceptance | reports/acceptance/ACC-STOP-001.json |
| `A-static-ACC-STOP-010-contract-doc-sync` | ACC-STOP-010 | TASK-025 | static | reports/stages/static.json |
| `A-static-ACC-STOP-010-non-goal-files-absent` | ACC-STOP-010 | TASK-001 | static | reports/stages/static.json |

## 3. 核心测试用例

### 3.1 RSS 层
- RSS 解析成功：给定 2 条 RSS item，输出 2 条标准新闻输入对象。
- RSS 重复去重：相同 canonical URL 只保留 1 条。
- URL canonicalization：`utm_*`、`fbclid` 等跟踪参数必须被移除。
- 不同 URL 但相同 `canonical_url` 不得重复入库。
- 不同 `canonical_url` 或不同域名的高分新闻必须保持为不同待抓取候选，不得被标题相似度或过宽去重规则合并。
- RSS 时间排序正确：`GET /api/home.data.latest_news` 按 `published_at DESC`。
- RSS 缺少 optional summary：parser 不 crash，后续评分仍可执行。
- RSS URL 无效：错误归类为 `parsing` 或 `network`，不得 silent fail。
- 默认 RSS source bootstrap：空库首次启动时写入默认源；已有 source 配置时不得重复写入。
- 默认 RSS source bootstrap 必须断言 source URL 集合精确等于 `docs/01_prd.md` 列出的 7 个 URL，不得只断言数量。
- 预置 source 被删除/禁用后，不得在下一次启动或刷新时自动恢复。
- 预置 source 与用户新增 source 在 API 中必须拥有相同启用、停用、删除、重复 tombstone 校验和最后启用 source 保护行为。
- 只抓取 `is_enabled = 1` 的 source。
- 单个 source 抓取失败必须写入 `processing_log(stage=crawl, success=0)`，且其他 source 继续处理。

### 3.1.1 Scheduler 与 Refresh
- Scheduler 使用 fixed clock 测试每天 `09:00` 和 `18:00` 各触发一次 crawl。
- Scheduler 不得依赖真实系统时间；测试必须注入 clock。
- `POST /api/refresh` 必须立即执行 crawl、score、filter、fetch、translate 和 API 可见性刷新。
- `POST /api/refresh` 必须幂等；重复 refresh 不得创建重复 `news_item`。
- 并发 refresh 被拒绝时不得启动第二个 pipeline run，必须返回 `200` 和 last successful `refreshed_at`；如果尚无成功 refresh，必须返回 `refreshed_at: null`。
- Refresh start、finish 和 concurrent rejection 必须写入日志或 `processing_log`，且不得泄漏正文或 prompt。

### 3.2 LLM 评分
- Scoring request JSON 必须包含 `title`、`summary`、`source`、`published_at`、`original_link`。
- Scoring response JSON 必须通过 schema validation；`score` 必须为 `0-100` 数字。
- Scoring response JSON 必须包含非空 `reason`；缺失或空 `reason` 必须归类为 `validation_llm_error`。
- score 范围合法：小于 `0` 或大于 `100` 时拒绝写入。
- 高分过滤正确：score `80` 的新闻进入可展示链路。
- 低分过滤正确：score `30` 的新闻不出现在 `GET /api/home`。
- 标题或原文链接缺失时评分为 `0`，且不得进入 fetch。
- 摘要缺失时 scoring input 必须保留空字段，并断言最终 score 比同等完整摘要基准扣 `20` 分。
- 写入 `score` 后必须立即计算 `is_selected`，默认 threshold 为 `60`。
- `is_selected = 1` 不得改变 `pipeline_state`；`pipeline_state` 只允许 `raw → scored → fetched`。
- JSON schema 错误：缺少 `score` 时归类为 `validation_llm_error`。
- retry 上限：连续失败超过 `2` 次后不继续推进该 item，并按 `06_dev_rules.md` 保持 `pipeline_state` 不被错误推进。

### 3.3 翻译层
- Translation trigger：仅当 `pipeline_state = fetched` 且 `content_full IS NOT NULL OR content_raw IS NOT NULL` 时触发。
- Translation request JSON 必须包含 `original_title`、`original_summary`、`original_content`、`source`、`score`。
- Translation response JSON 必须通过 schema validation；`title_zh`、`summary_zh`、`content_zh` 必须非空。
- 翻译输入优先使用 `content_full`；无全文时使用 `content_raw`。
- 当 `content_full` 不可用且 `content_raw` 来自 RSS 摘要兜底时，翻译必须基于同一 fallback item 生成非空 `summary_zh` 和 `content_zh`。
- 翻译成功：`title_zh` 映射到 API `title`。
- 中文摘要：`summary_zh` 只在 `translated` 时返回。
- 中文正文：`content_zh` 只在详情接口且 `translated` 时返回。
- 翻译失败：失败 item 不返回 `summary_zh`、`content_zh`。
- 翻译失败：不得写入部分中文字段，必须设置 `has_translate_failed = 1`，失败原因写入 `processing_log(stage=translate, success=0)`。
- 翻译成功：必须设置 `has_translate_failed = 0`。
- 部分翻译：只有 `title_zh` 或只有 `summary_zh` 时不得返回 `translated`。
- API status priority：完整中文字段优先投影为 `translated`；否则 `has_translate_failed = 1` 投影为 `translation_failed`；否则投影为 `ready`。

### 3.4 API 层
- `GET /api/home` 返回 `latest_news` 和 `top_ranked_news`。
- `GET /api/home` 的 `latest_news` 只返回可展示新闻，按 `published_at DESC` 排序。
- `GET /api/home` 的 `limit` 默认 `50`，最大 `100`；只作用于 `latest_news`。
- `GET /api/home` 的 `top_ranked_news` 按 `score DESC, published_at DESC`。
- `GET /api/home` 的 `top_ranked_news` 只包含最近 30 天可展示新闻，最多 10 条，不使用 cursor pagination。
- `GET /api/home` 的 `next_cursor` 可选；出现时必须为 string。
- `GET /api/home` 不得返回 layout column 描述。
- `GET /api/news/{id}` 对不存在 ID 返回结构化 `404`。
- `GET /api/news/{id}` 对不可展示 item 返回结构化 `404`。
- `GET /api/news/{id}` 对 `translated` item 必须返回非空 `summary_zh` 和 `content_zh`。
- `GET /api/news/{id}` 对 `ready` / `translation_failed` 不返回 `summary_zh`、`content_zh`。
- `POST /api/refresh` 并发调用不触发第二次执行，仍返回 `200`。
- `POST /api/refresh` 无 request body，不返回 task ID、queue、worker、retry 或 progress 字段。
- `GET /api/sources` 返回 `deleted_at IS NULL` 的 source，按 `created_at ASC` 排序。
- `POST /api/sources` 成功返回 `201`、`is_enabled = true`、`fetch_frequency = twice_daily`。
- `POST /api/sources` 空 name、空 rss_url、非法 URL、本地地址和私有地址返回结构化 `400`，且数据库不新增记录。
- `POST /api/sources` 重复 RSS URL 返回 `409`，包括已删除 source tombstone 上的同一 URL。
- `POST/PATCH/DELETE /api/sources` 必须分别使用默认预置 source 和用户新增 source 执行同一组 CRUD 行为断言，结果除对象 ID 和创建时间外必须一致。
- `PATCH /api/sources/{id}` 成功返回更新后的 `SourceItem`。
- `PATCH /api/sources/{id}` source 不存在返回 `404`。
- `PATCH /api/sources/{id}` source 已删除返回 `404`。
- `PATCH /api/sources/{id}` 禁止关闭最后一个 `deleted_at IS NULL AND is_enabled = 1` 的 source，返回 `409`。
- `DELETE /api/sources/{id}` 在不会关闭最后一个 `deleted_at IS NULL AND is_enabled = 1` 的 source 时返回 `204` 且无 body。
- `DELETE /api/sources/{id}` 如果会关闭最后一个 `deleted_at IS NULL AND is_enabled = 1` 的 source，返回 `409` 且不更新数据库。
- `DELETE /api/sources/{id}` source 不存在或已删除返回 `404`。
- `DELETE /api/sources/{id}` 以 soft tombstone 实现，设置 `is_enabled = 0` 和 `deleted_at`，历史新闻仍通过 API 可见，未来 ingestion 停止。
- 所有 API response 必须通过非法字段黑名单检查。
- 所有 endpoint 必须覆盖成功用例和至少一个错误用例。
- 所有错误用例必须断言稳定 `error.code`。

### 3.5 UI 层
- NewsCard 正确渲染标题、来源、时间、评分、状态。
- NewsCard translated summary 必须通过 text node 渲染；当 fixture 的 `summary_zh` 包含 `<b>`、`<script>` 或类似 HTML-like 文本时，DOM 不得生成对应标签节点。
- HighScoreList 使用与 News Feed 相同的 `NewsListItem` shape。
- HighScoreList 必须只渲染最近 30 天合格新闻；当 fixture 中合格新闻不少于 10 条时渲染 10 条，并按 `score DESC, published_at DESC` 排序。
- ArticleView 在 `translated` 时渲染 `summary_zh` 和 `content_zh`。
- ArticleView 在 `ready` / `translation_failed` 时不渲染 `summary_zh`、`content_zh`。
- ArticleView 在 translated detail 中必须渲染原文链接按钮或链接，且该链接不得替代 NewsCard 或 HighScoreList 的站内导航。
- NewsCard 标题点击和 HighScoreList 标题点击必须停留在站内 ArticleView route，不得直接打开原文站点。
- 空字段不 crash，不自动补默认文案，不用其他字段替代。
- NewsCard 点击和 Title 点击必须进入同一个 ArticleView。
- HighScoreList item 点击必须使用同一个 news `id` 进入 ArticleView。
- ScoreBadge 不得触发排序、筛选或跳转。
- SourceMarker 不得跳转来源站点。
- SourceMarker 或卡片来源色必须稳定且可区分，测试不得只验证元素存在。
- TopBar 只提供 NexNews 返回主页、刷新、信源入口。
- Refresh 默认文案为 `刷新`，加载中禁用且文案为 `刷新中`，完成后重新加载新闻列表。
- 新闻列表加载中必须渲染与 NewsCard 尺寸一致的紧凑 skeleton。
- 空列表渲染 `暂无可展示新闻`。
- 新闻加载失败渲染 `新闻加载失败`。
- ArticleView 404 / 不可用状态渲染 `新闻不存在或不可展示` 和返回按钮。
- SourceForm 空字段时新增按钮禁用；非法 URL 显示行内校验；新增中按钮禁用；新增成功后清空输入并刷新列表。
- Source toggle frontend binding 必须调用 `PATCH /api/sources/{id}`，并正确展示 `404`、`409` 错误状态。
- 默认预置 source 和用户新增 source 在 RSS 配置页必须显示相同启用、停用、删除控件；被删除或停用的预置 source 重新加载后不得自动恢复显示。
- RSS 配置页不得出现高级设置、分类、未记录的 UI 行为或额外组件。

### 3.6 数据模型与持久化层
- SQLite application schema 必须只保留 MVP 核心表：`source`、`news_item`、`processing_log`；SQLite 内部表不计入应用表集合。
- `source.rss_url` 必须唯一；`source.is_enabled` 必须可索引。
- `source.deleted_at` 必须存在，用作 source soft tombstone；未删除时为空。
- `news_item.canonical_url` 必须唯一。
- `news_item.pipeline_state` 只允许 `raw`、`scored`、`fetched`。
- `pipeline_state = scored` 必须满足 `score IS NOT NULL`。
- `is_selected` 必须由 threshold 计算，不得作为 pipeline 状态。
- `content_raw` 保存 RSS 摘要或原始内容；`content_full` 只保存抓取全文。
- 不得保存 `content_source`、`title_domain_hash`、`translation_status`、`is_ready`、`display_mode`、独立任务队列表或多语言表。
- `processing_log` 是必需核心表，必须满足 `source_id` 与 `news_item_id` 恰好一个非空。
- `processing_log.stage` 只允许 `crawl`、`score`、`fetch`、`translate`。
- `processing_log(stage = crawl)` 必须关联 `source_id`；`score`、`fetch`、`translate` 必须关联 `news_item_id`。
- `processing_log` 不驱动任务调度；它只记录处理结果。
- 删除或禁用 source 后，历史 `news_item` 必须保留；删除 source 后 `GET /api/sources` 不再返回该 source。
- 所有 DB timestamp 必须为 ISO 8601 UTC string。
- 必须验证 `news_item.source_id`、`news_item.pipeline_state`、`news_item.published_at`、`news_item.score`、`processing_log(source_id, stage)`、`processing_log(news_item_id, stage, success)`、`processing_log.trace_id`、`processing_log.created_at` 索引存在。

### 3.7 内容抓取层
- 原文页面可访问且能抽取正文时，必须写入 `content_full`。
- 原文页面不可访问或正文抽取失败时，必须使用 `content_raw` 兜底。
- `content_full` 和 `content_raw` 都不可用时，不得进入可展示 API 查询结果，也不得触发翻译。
- 内容降级优先级必须为 `content_full` → `content_raw` → 不可展示。
- 抓取成功或兜底成功后，`pipeline_state` 必须更新为 `fetched`。

### 3.8 错误处理、日志与可观测性
- 所有异常必须归类为 `network`、`parsing`、`llm`、`validation_llm_error`、`database`、`validation`、`timeout`、`unknown`。
- LLM schema validation failure 必须归类为 `validation_llm_error`。
- 禁止 silent fail；失败必须写入 `processing_log` 或应用日志。
- RSS 解析失败必须归类为 `parsing`。
- 网络超时必须归类为 `network`。
- 未知异常必须转换为 `unknown`，不得暴露内部细节。
- 捕获异常后不得继续写入成功状态。
- 错误 message 不得包含 raw article body、fallback raw text、prompt、token 或密钥。
- 日志标题字段必须裁剪到 `300` 字符以内；正文类字段必须裁剪到 `1024` 字符以内。
- 业务日志不得使用 `print`。
- 所有 pipeline step 必须产生包含对象 ID、stage、UTC timestamp、trace_id 的日志或 `processing_log`。

### 3.9 架构与非目标接口
- API route 只做参数校验、调用 service、返回 DTO。
- Service 函数必须接收明确参数，不得直接读取 request 对象。
- Frontend 只负责 render API DTO、本地 UI state 和用户交互。
- Frontend 不得执行业务判断、pipeline 状态推导或数据库字段映射。
- 页面组件只组合组件和加载数据；业务逻辑必须在 API client 或 service 中。
- MVP 不得暴露 User/login、Search、Category、Comment、Favorite、Share、Processing log、Task status/progress、Retry、Admin、API versioning endpoint。

## 4. Mock 与测试数据策略

### 4.1 RSS Mock
```json
{
  "source_name": "Mock AI Feed",
  "rss_url": "https://example.com/rss.xml",
  "items": [
    {
      "guid": "mock-1",
      "title": "Mock AI News",
      "link": "https://example.com/news/1",
      "published_at": "2026-06-28T08:00:00Z",
      "summary": "Mock RSS summary"
    }
  ]
}
```

### 4.2 LLM Scoring Mock
```json
{
  "score": 82,
  "reason": "High signal AI product news"
}
```
- scoring mock 必须固定输出。
- invalid scoring mock 必须覆盖 missing field、wrong type、out of range。
- scoring request fixture 必须覆盖 missing title、missing original_link、missing summary。

### 4.3 Translation Mock
```json
{
  "title_zh": "模拟 AI 新闻",
  "summary_zh": "模拟中文摘要",
  "content_zh": "模拟中文正文",
  "category_zh": "产品"
}
```
- translation mock 必须固定输出。
- failure mock 必须覆盖 timeout、invalid JSON、partial fields。
- `category_zh` 可用于 LLM contract validation，但不得要求 API 或数据库暴露中文分类字段。

### 4.4 Source Mock
```json
{
  "name": "Mock AI Feed",
  "rss_url": "https://example.com/rss.xml",
  "is_enabled": true,
  "fetch_frequency": "twice_daily",
  "created_at": "2026-06-28T06:00:00Z"
}
```
- source fixture 必须覆盖默认源、用户新增源、禁用源、重复 URL、非法 URL、本地地址、私有地址。

### 4.5 Article HTML Mock
```html
<html>
  <body>
    <nav>Navigation</nav>
    <article>
      <h1>Mock Article</h1>
      <p>Useful article paragraph.</p>
    </article>
  </body>
</html>
```
- article fixture 必须覆盖正文抽取成功、正文抽取失败、网络失败、空 RSS summary。

### 4.6 Clock Mock
```json
{
  "now": "2026-06-28T09:00:00Z",
  "timezone": "UTC"
}
```
- clock fixture 必须覆盖 scheduler `09:00`、`18:00`、非触发时间、最近 30 天榜单窗口边界。

### 4.7 外部依赖规则
- 禁止测试访问真实 RSS URL。
- 禁止测试访问真实网页正文。
- 禁止测试调用真实 LLM API。
- 禁止测试依赖当前系统时间；必须注入 fixed clock。
- 所有 mock 必须支持 fixed seed。
- Snapshot fixture 必须提交到测试目录。

### 4.8 Test Data Versioning
- 所有 fixtures 必须带 version。
- Snapshot 必须绑定 data version。
- Test failure 必须记录 data hash。
- Fixture 更新必须说明影响的 snapshot。

## 5. 非功能测试
- 性能：100 条 RSS item 的 parse + dedupe + mock scoring 在本地 SQLite 下必须在 5 秒内完成。
- 稳定性：LLM 连续失败后 item 不得假成功进入 translated UI。
- 数据一致性：API 返回字段必须与 UI 渲染字段一致。
- 数据泄漏：API response 中不得出现 `content_raw`、`content_full`、`deleted_at`、完整 prompt。
- 数据泄漏：日志、测试报告、错误响应不得出现 raw article body、fallback raw text、完整 prompt、密钥、token。
- 幂等性：重复 refresh 不得创建重复新闻。
- 可维护性：静态合规测试必须阻止未记录 endpoint、未记录 UI 行为、未记录组件和跨层字段泄漏。
- 安全性：RSS URL validation 必须拒绝本地地址、私有地址、非 `http/https` URL。

### 5.1 Observability Test
- 每个 pipeline step 必须产生日志。
- 每条 pipeline log 必须包含 trace_id。
- 日志不得包含 `content_raw`、`content_full`、完整 prompt。
- LLM failure log 必须包含错误分类。
- 每个 `processing_log` 必须包含 stage、success、created_at 和恰好一个关联对象 ID。
- Refresh start / finish / concurrent rejection 必须可通过日志或报告追踪。

### 5.2 Test Failure Traceability
- Each failure must include pipeline stage: `static` / `RSS` / `source` / `scheduler` / `score` / `fetch` / `translate` / `DB` / `API` / `UI`。
- Each failure must include `trace_id`。
- Each failure must include fixture version。
- Each failure must include mock version。
- Each failure must include expected vs actual diff。
- Each failure must include node-level failure isolation report。
- Each failure must include `failure_type` and `error_category` when applicable。

## 6. Test Report Contract（测试结果契约）

```yaml
test_report_contract:
  ref: 07_test_spec.md#6
  version: v2
```

All test executions MUST output machine-readable structured reports. The report is the only supported interface for CI parsing, AI automatic repair, failure routing, and traceability consumption.

The machine-checkable JSON Schema for this contract lives at `schemas/test_report.schema.json`. The prose contract in this section remains authoritative when schema and prose conflict, and schema changes must update this section in the same task.

Each test case or stage-level result MUST emit one `TestReport` object:

```json
{
  "schema_ref": "07_test_spec.md#6",
  "schema_version": "v2",
  "test_id": "...",
  "stage": "static | unit | contract | api | integration | replay | snapshot | e2e | acceptance",
  "status": "passed | failed | flaky | skipped",
  "failure_type": "api | scheduler | integration | contract | data_model | ui | observability | leak | null",
  "error_category": "network | parsing | llm | validation_llm_error | database | validation | timeout | unknown | null",
  "trace_id": "...",
  "fixture_set": "...",
  "mock_set": "...",
  "clock_source": "...",
  "fixture_version": "...",
  "mock_version": "...",
  "commands": ["python3 scripts/run_harness.py --stage unit --report-dir reports"],
  "case_count": 12,
  "passed_count": 12,
  "failed_count": 0,
  "skipped_count": 0,
  "pass_rate": 1.0,
  "failure_reasons": [],
  "repair_status": "not_required | unresolved | fixed | blocked",
  "regression_detected": false,
  "referenced_files": ["path/to/file"],
  "data_hash": "sha256:...",
  "artifact_paths": ["reports/path.json"],
  "assertions": [
    {
      "id": "...",
      "type": "api_response | db_state | side_effect | pipeline_output | llm_io | ui_render | report_schema | log_record | isolation",
      "visibility": "public_surface | internal_evidence | report_metadata",
      "status": "passed | failed | flaky | skipped",
      "expected": {},
      "actual": {},
      "diff": {},
      "leak_detection": {
        "method": "structured_field_scan",
        "target": "api_json | ui_dom | logs | test_report | null",
        "forbidden_field_count": 0,
        "sensitive_content_count": 0,
        "matched_paths": []
      }
    }
  ],
  "expected": {},
  "actual": {},
  "diff": {},
  "node": "harness | acceptance | static | source | RSS | scheduler | score | filter | fetch | translate | DB | API | UI",
  "timestamp": "ISO8601"
}
```

Field rules:

- `schema_ref` MUST equal `07_test_spec.md#6`.
- `schema_version` MUST equal `v2`.
- `test_id` MUST be stable across runs and unique within the test suite.
- `stage` MUST match the orchestration stages defined in section 2.13.
- `stage = acceptance` is allowed only for gate-evaluation reports written under `reports/acceptance/ACC-STOP-*.json`.
- `status` MUST use only the documented values; failed retry results MUST be reported as `flaky` only when a retry passes.
- `failure_type` MUST use this closed enum for `failed` and `flaky` reports: `api`、`scheduler`、`integration`、`contract`、`data_model`、`ui`、`observability`、`leak`.
- `failure_type` MUST be `null` for `passed` and `skipped` reports.
- `failure_type` MUST NOT use nested names, dotted names, stage names, timeout names, or custom extension values.
- `error_category` MUST use the categories from `06_dev_rules.md` when the result comes from an exception or validation failure; otherwise it MAY be `null`.
- `trace_id` MUST connect the report to pipeline logs and failure details.
- `fixture_set` and `mock_set` MUST be present for all acceptance tests.
- `fixture_set` and `mock_set` MUST match the release gate policy for `ACC-STOP-*` reports.
- `clock_source` MUST be present for all acceptance tests.
- `clock_source` MUST match the release gate policy for `ACC-STOP-*` reports.
- `ACC-STOP-*` tests MUST use `fixed_clock_fixture@v1` as the only time source.
- `ACC-STOP-*` tests MUST NOT read wall clock time, system time, current process time, or network time as an assertion input.
- `fixture_version` and `mock_version` MUST be present for all deterministic tests.
- `commands` MUST list the exact command or commands that produced the report.
- `case_count` MUST equal the total number of machine-checkable cases or assertions counted for the report.
- `passed_count`、`failed_count` and `skipped_count` MUST describe the case outcomes included in `case_count`.
- `pass_rate` MUST be a number from `0` to `1` and equal `passed_count / case_count` when `case_count > 0`.
- `failure_reasons` MUST list stable machine-readable failure reasons or failed assertion ids; it MUST be empty when there is no failure.
- `repair_status` MUST be `not_required`、`unresolved`、`fixed` or `blocked`.
- `regression_detected` MUST be `true` when the run found a regression in previously completed behavior, and `false` otherwise.
- `referenced_files` MUST be present and MUST be an array of repository-relative paths involved in the assertion, failure, or owning harness logic.
- For `failed` and `flaky` reports, `referenced_files` MUST be non-empty so `workflows.md#5.4` can compute a deterministic fix boundary.
- `data_hash` MUST be present and MUST be a stable `sha256:` hash of the deterministic fixture/mock/report input facts, referenced files, source documents, schema files and task records used by the report.
- A report whose `data_hash` no longer matches the current referenced files, source documents, schema files, fixture/mock/clock versions or task records is stale. Stale reports MUST NOT be counted as `passed` evidence for acceptance or `DONE`.
- `artifact_paths` MUST be present and MUST be an array of repository-relative or report-directory-relative evidence artifacts written by the run.
- `assertions` MUST be present and non-empty for every `TestReport`. A behavior-only stage with no mandatory assertion must represent its behavior evidence as a `report_metadata` assertion.
- Each assertion MUST include `id`、`type`、`status`、`expected`、`actual` and `diff`.
- Assertion `type` MUST use the closed enum in section 6.2.
- Each assertion MUST include `visibility`.
- Assertion `visibility` MUST be one of `public_surface`、`internal_evidence` or `report_metadata`.
- `public_surface` means API JSON, UI DOM, user-visible error response, or user-visible logs and MUST use strict forbidden-field scanning.
- `internal_evidence` means DB/schema/state evidence needed for acceptance; it MAY name internal fields such as `pipeline_state` or `is_selected`, but MUST NOT include raw article bodies, full prompts, secrets, tokens, or正文片段超过 `1024` 字符.
- `report_metadata` means harness/report bookkeeping fields and MUST NOT include product payload bodies or prompts.
- `ACC-STOP-*` report `status` MUST be `passed` only when every assertion has `status = passed`.
- Leak assertions MUST include `leak_detection`.
- `leak_detection.method` MUST equal `structured_field_scan`.
- `leak_detection.target` MUST be one of `api_json`、`ui_dom`、`logs`、`test_report`.
- `leak_detection.matched_paths` MUST be an array.
- `expected`, `actual`, and `diff` MUST be valid JSON objects; empty objects are allowed only when no assertion diff exists.
- `node` MUST identify the isolated pipeline node most responsible for the result.
- `timestamp` MUST be an ISO 8601 UTC string.
- `ACC-STOP-*` report `timestamp` MUST be derived from `clock_source`.

Output rules:

- CI MUST persist the full report collection as JSON.
- Human-readable logs MAY be generated from the structured report, but MUST NOT be the source of truth.
- Report fields MUST NOT contain完整 prompt、密钥、token、secret 或超过 `1024` 字符的正文片段。
- Report fields with assertion `visibility = public_surface` MUST NOT contain `pipeline_state`、`is_selected`、`content_raw`、`content_full`、`has_translate_failed` or `deleted_at`.
- Report fields with assertion `visibility = internal_evidence` MAY contain internal field names required to prove DB/schema/state facts, but MUST NOT contain raw field values that are full article bodies, full prompts, secrets, or token-like credentials.
- Failure routing MUST use `stage`、`failure_type`、`error_category`、`node` and `trace_id`, not free-form error text.
- AI automatic repair MUST consume this report contract before reading raw logs.

### 6.1 Failure Type Schema

```yaml
failure_types:
  - api
  - scheduler
  - integration
  - contract
  - data_model
  - ui
  - observability
  - leak
failure_type_policy:
  schema: CLOSED_ENUM
  hierarchy: FLAT
  extension: FORBIDDEN
  applies_to_statuses:
    - failed
    - flaky
  passed_report_failure_type: null
```

### 6.2 Assertion Type Schema

```yaml
assertion_types:
  - api_response
  - db_state
  - side_effect
  - pipeline_output
  - llm_io
  - ui_render
  - report_schema
  - log_record
  - isolation
assertion_policy:
  schema: CLOSED_ENUM
  aggregation: ALL_ASSERTIONS_PASSED
  extension: FORBIDDEN
```

### 6.3 Assertion Visibility Schema

```yaml
assertion_visibility:
  - public_surface
  - internal_evidence
  - report_metadata
visibility_policy:
  schema: CLOSED_ENUM
  public_surface: "strict forbidden-field scan applies"
  internal_evidence: "internal DB/schema/state field names allowed, sensitive values forbidden"
  report_metadata: "harness bookkeeping only, product payload bodies forbidden"
```

### 6.3.1 PRD Coverage Report

`reports/acceptance/prd_coverage.json` records PRD acceptance coverage. It is a stop-input artifact, not a substitute for full-stage reports.

The machine-checkable JSON Schema for this report lives at `schemas/prd_coverage.schema.json`. The prose contract in this section remains authoritative when schema and prose conflict, and schema changes must update this section in the same task.

```json
{
  "schema_ref": "07_test_spec.md#6.3.1",
  "schema_version": "v1",
  "status": "failed",
  "source": {
    "path": "docs/01_prd.md",
    "version": "prd_mvp@v1"
  },
  "coverage_items": [
    {
      "id": "PRD-5.1-AC-001",
      "source_path": "docs/01_prd.md",
      "source_line": 328,
      "acceptance_text": "主页面能展示新闻卡片列表。",
      "task_ids": ["TASK-015", "TASK-024"],
      "acceptance_gate": ["ACC-STOP-006"],
      "assertion_ids": [
        "A-e2e-ACC-STOP-006-home-news-density"
      ],
      "report_paths": ["reports/stages/e2e.json"],
      "status": "passed"
    }
  ],
  "uncovered_acceptance_items": [],
  "timestamp": "2026-06-28T09:00:00Z"
}
```

Rules:

- `schema_ref` MUST equal `07_test_spec.md#6.3.1`.
- `schema_version` MUST equal `v1`.
- Every checklist-style acceptance bullet under `docs/01_prd.md` MUST receive a stable id with format `PRD-<feature>.<flow>-AC-<nnn>`, for example `PRD-6.1-AC-003`.
- `status = passed` requires every checklist-style acceptance bullet under `docs/01_prd.md` to appear in `coverage_items` with `status = passed`.
- Every `coverage_items[*]` record MUST include PRD id, source path, source line, acceptance text, mapped task ids, acceptance gates, assertion ids, report paths and execution status.
- `task_ids` MUST reference tasks that exist in `tasks.md`, have `status = passed` at final acceptance time, and declare at least one matching `acceptance_gate`.
- `assertion_ids` MUST reference assertion ids that exist in a structured task, full-stage or ACC-STOP report. When the PRD item contributes to final stop eligibility, at least one referenced report MUST be a full-stage or ACC-STOP report, not only a task-scoped report.
- `report_paths` MUST be stable relative paths and MUST point to existing structured evidence when status is `passed`.
- `uncovered_acceptance_items` MUST list every PRD acceptance item that is unmapped, unexecuted, failed, flaky, skipped, mapped only to prose, mapped only to task-scoped evidence when full-stage evidence is required, or missing report paths.
- `prd_coverage_status = PASS` requires this report to exist, match `schemas/prd_coverage.schema.json`, report `status = passed`, and contain no uncovered PRD acceptance item.

### 6.4 Task Acceptance Coverage Report

`reports/acceptance/task_acceptance_coverage.json` records task-level acceptance coverage. It is a stop-input artifact, not a substitute for full-stage reports.

The machine-checkable JSON Schema for this report lives at `schemas/task_acceptance_coverage.schema.json`. The prose contract in this section remains authoritative when schema and prose conflict, and schema changes must update this section in the same task.

```json
{
  "schema_ref": "07_test_spec.md#6.4",
  "schema_version": "v1",
  "status": "failed",
  "source": {
    "path": "tasks.md",
    "version": "tasks_mvp@v8"
  },
  "coverage_items": [
    {
      "id": "TASK-015:AC-001",
      "task_id": "TASK-015",
      "source_path": "tasks.md",
      "source_line": 483,
      "acceptance_text": "Translated card shows Chinese title and summary_zh.",
      "acceptance_gate": ["ACC-STOP-006"],
      "test_scope": ["integration"],
      "assertion_ids": ["A-integration-ACC-STOP-006-ui-render-contract"],
      "report_paths": ["reports/stages/integration.json"],
      "status": "passed"
    }
  ],
  "uncovered_task_acceptance_items": [],
  "timestamp": "2026-06-28T09:00:00Z"
}
```

Rules:

- `schema_ref` MUST equal `07_test_spec.md#6.4`.
- `schema_version` MUST equal `v1`.
- `status = passed` requires every `tasks.md.dag.nodes[*].acceptance_criteria[*]` item to appear in `coverage_items` with `status = passed`.
- Every `coverage_items[*]` record MUST include task id, source path, source line, acceptance text, declared acceptance gates, declared test scopes, assertion ids, report paths and execution status.
- `assertion_ids` MUST reference assertion ids that exist in a structured task or full-stage report. When the criterion contributes to final stop eligibility, at least one referenced report MUST be a full-stage or ACC-STOP report, not only a task-scoped report.
- `report_paths` MUST be stable relative paths and MUST point to existing structured evidence when status is `passed`.
- `uncovered_task_acceptance_items` MUST list every criterion that is unmapped, unexecuted, failed, flaky, skipped, mapped only to prose, or missing report paths.
- `task_acceptance_coverage_status = PASS` requires this report to exist, match `schemas/task_acceptance_coverage.schema.json`, report `status = passed`, and contain no uncovered task acceptance item.

### 6.5 Review And Fix/Optimize Reports

`reports/tasks/<task_id>/review.json` records the mandatory static review step. It MUST match `schemas/review_report.schema.json`, use `schema_ref = workflows.md#ReviewReport`, include all eight review dimensions, and report `status = passed` only when every dimension is `passed` and `blocking_findings` is empty.

`reports/tasks/<task_id>/fix_optimize.json` records the mandatory fix/optimize step. It MUST match `schemas/fix_optimize_report.schema.json`, use `schema_ref = workflows.md#FixOptimizeReport`, and report `status = passed` only when blocking findings are resolved, at least one relevant retest report is referenced, and `regression_detected = false`.

Round counting MUST validate these two reports through schema and status checks. A `RoundSummaryReport` that merely embeds review/fix prose or paths without parseable reports is not a completed round.

## 7. 验收标准
- Static compliance tests pass。
- 所有 API tests pass。
- Contract tests 100% pass。
- DB schema contract tests 100% pass。
- 核心链路 integration test 100% pass。
- Golden snapshot diff 必须为空，或由结构化 snapshot approval evidence 证明与同一 task 的契约文档变更一致。
- Pipeline replay test 输出必须完全一致。
- LLM prompt snapshot diff 必须为空，或由结构化 prompt approval evidence 证明与同一 task 的 LLM contract/fixture 变更一致。
- Test pyramid ratio 不得被 snapshot / integration test 反向压倒。
- Flaky quarantine 必须为空或有明确 owner。
- Test report collection 必须符合 `Test Report Contract`。
- Test report collection 必须覆盖 `static`、`unit`、`contract`、`api`、`integration`、`replay`、`snapshot`、`e2e` stage。
- Task acceptance coverage report 必须覆盖 `tasks.md` 中每条 acceptance criterion，且不得以 prose-only 或 task-scoped-only evidence 满足最终 stop eligibility。
- ReviewReport 和 FixOptimizeReport 必须分别符合 `schemas/review_report.schema.json` 与 `schemas/fix_optimize_report.schema.json`；缺失、schema 不匹配、review 维度不全、fix 未复测或存在回归时，该轮不得计入 completed round。
- Round summary report 必须包含 `round_index`、`completed_round_count`、`review`、`fix_optimize` 和 `round_end_decision`，并指向本轮 review、fix/optimize 与轮末分支决策的结构化证据；`round_end_decision.selected_next_state` 不得为 `DONE`。
- Acceptance stop decision 必须包含可计算 `round_count_policy`，用来证明未完成工作存在时持续迭代，或证明 10 轮前提前 DONE 只发生在全部停止条件已通过时。
- Test failure 必须输出 fixture version 和 data hash。
- Test execution 必须按 orchestration order 执行。
- Test execution command surface and report paths must match `workflows.md`.
- Test failure 必须先报告最高优先级 failure。
- Timeout failure 必须使用 `timeout` category。
- Test failure 必须包含 `trace_id`、fixture version、mock version、expected vs actual diff。
- 所有 `01_prd.md` 到 `06_dev_rules.md` 的可测试要求必须能映射到本文件的测试层、核心用例或验收标准。
- End-to-end deterministic run 必须 pass。
- UI tests 无 crash。
- `GET /api/home` 和 `GET /api/news/{id}` 不暴露非法字段。
- `translated` detail item 必须有中文摘要和中文正文详情。
- `ready` / `translation_failed` item 必须省略中文摘要和中文正文。
- LLM mock tests 和 RSS fixture tests 不访问外部网络。
- 重复 RSS item 不产生重复展示新闻。
- 无测试失败时才允许进入 coding 完成状态。
