# 07_test_spec.md

## 1. 测试目标
- 验证 `RSS → ingest → score → filter → fetch → translate → API → UI` 主链路正确。
- 验证 LLM scoring 和 translation 在 mock 下可重复、可断言。
- 验证翻译字段只通过 `title_zh`、`summary_zh`、`content_zh` 映射到 API/UI。
- 验证 API contract 不破坏 `05_api_contract.md`。
- 验证 UI 只按 `NewsItem` / `NewsListItem` / `NewsDetailItem` 渲染。
- 验证 API/UI 不暴露 `pipeline_state`、`is_selected`、`content_raw`、`content_full`、`has_translate_failed`。

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

- 当 `01_prd.md` 与 `04_data_model.md`、`05_api_contract.md` 或 `06_dev_rules.md` 冲突时，测试必须按 `06_dev_rules.md` 的 Rule Priority Order 执行。
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
- Error classifier：异常 → `network | parsing | llm | database | validation | unknown`，LLM schema validation failure → `validation_llm_error`。
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
- API response 必须拒绝 `pipeline_state`、`is_selected`、`content_raw`、`content_full`、`has_translate_failed`、完整 prompt 和内部 DB model 字段。
- DB schema contract 必须校验 `source`、`news_item`、`processing_log` 表、字段、约束和索引。
- Test report contract 必须通过 JSON Schema 校验。

### 2.3 API Test（HTTP 接口）
- API Test = runtime behavior correctness，覆盖 status code、pagination、concurrency、business logic。
- 成功响应必须符合 `{ "data": ... }`。
- 错误响应必须符合 `{ "error": { "code": "...", "message": "..." } }`。
- `204` 响应必须无 body。
- `GET /api/home` 必须返回 `latest_news` 和 `top_ranked_news`。
- `GET /api/news/{id}` 必须返回可展示 `NewsDetailItem`。
- `POST /api/refresh` 必须返回 `refreshed_at`。
- `GET /api/sources` 必须返回按 `created_at ASC` 排序的 `SourceItem[]`。
- `POST /api/sources` 必须测试成功创建、缺少 name、空 name、缺少 rss_url、非法 URL、本地/私有地址、重复 URL。
- `PATCH /api/sources/{id}` 必须测试启用、停用、source 不存在、禁止关闭全部 source。
- `DELETE /api/sources/{id}` 必须测试 `204` 无 body、source 不存在返回 `404`、历史新闻仍可见。
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
- Snapshot diff 必须为空或有显式批准。

### 2.6 Pipeline Replay Test
- RSS ingestion 必须可 replay。
- 输入 fixture + fixed seed → 输出必须完全一致。
- LLM scoring mock 和 translation mock 必须支持 deterministic seed mode。
- Replay test 不得依赖真实时间、网络或外部 API。

### 2.7 LLM Prompt Regression Test
- Prompt template 必须有 snapshot。
- Prompt 变更必须触发 test diff。
- Mock LLM response 必须通过 schema validation。
- 固定 fixture 下 score distribution 必须稳定。

### 2.8 UI Test
- 使用 mock API response 渲染 UI。
- Click NewsCard → detail page。
- Click HighScoreList item → detail page。
- Loading state 必须在 fetch 期间出现。
- Error state 必须渲染固定 fallback view。
- `NewsItem.status` → UI 展示必须 deterministic。
- UI 不得从 `summary_zh` 或 `content_zh` 反推 status。
- Invalid state combination must throw error in dev mode。
- ScoreBadge 和 SourceMarker 必须不可点击。
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
- Each stage must start from clean isolated state。
- Stage failure must stop downstream execution。
- No shared global state across stages。

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

## 3. 核心测试用例

### 3.1 RSS 层
- RSS 解析成功：给定 2 条 RSS item，输出 2 条标准新闻输入对象。
- RSS 重复去重：相同 canonical URL 只保留 1 条。
- URL canonicalization：`utm_*`、`fbclid` 等跟踪参数必须被移除。
- 不同 URL 但相同 `canonical_url` 不得重复入库。
- RSS 时间排序正确：`GET /api/home.data.latest_news` 按 `published_at DESC`。
- RSS 缺少 optional summary：parser 不 crash，后续评分仍可执行。
- RSS URL 无效：错误归类为 `parsing` 或 `network`，不得 silent fail。
- 默认 RSS source bootstrap：空库首次启动时写入默认源；已有 source 配置时不得重复写入。
- 预置 source 被删除/禁用后，不得在下一次启动或刷新时自动恢复。
- 只抓取 `is_enabled = 1` 的 source。
- 单个 source 抓取失败必须写入 `processing_log(stage=crawl, success=0)`，且其他 source 继续处理。

### 3.1.1 Scheduler 与 Refresh
- Scheduler 使用 fixed clock 测试每天 `09:00` 和 `18:00` 各触发一次 crawl。
- Scheduler 不得依赖真实系统时间；测试必须注入 clock。
- `POST /api/refresh` 必须立即执行 crawl、score、filter、fetch、translate 和 API 可见性刷新。
- `POST /api/refresh` 必须幂等；重复 refresh 不得创建重复 `news_item`。
- 并发 refresh 被拒绝时不得启动第二个 pipeline run，必须返回 `200` 和 last known `refreshed_at`。
- Refresh start、finish 和 concurrent rejection 必须写入日志或 `processing_log`，且不得泄漏正文或 prompt。

### 3.2 LLM 评分
- Scoring request JSON 必须包含 `title`、`summary`、`source`、`published_at`、`original_link`。
- Scoring response JSON 必须通过 schema validation；`score` 必须为 `0-100` 数字。
- score 范围合法：小于 `0` 或大于 `100` 时拒绝写入。
- 高分过滤正确：score `80` 的新闻进入可展示链路。
- 低分过滤正确：score `30` 的新闻不出现在 `GET /api/home`。
- 标题或原文链接缺失时评分为 `0`，且不得进入 fetch。
- 摘要缺失时 scoring input 必须保留空字段，并测试扣分或 mock score rule。
- 写入 `score` 后必须立即计算 `is_selected`，默认 threshold 为 `60`。
- `is_selected = 1` 不得改变 `pipeline_state`；`pipeline_state` 只允许 `raw → scored → fetched`。
- JSON schema 错误：缺少 `score` 时归类为 `validation_llm_error`。
- retry 上限：连续失败超过 `2` 次后不继续推进该 item，并按 `06_dev_rules.md` 保持 `pipeline_state` 不被错误推进。

### 3.3 翻译层
- Translation trigger：仅当 `pipeline_state = fetched` 且 `content_full IS NOT NULL OR content_raw IS NOT NULL` 时触发。
- Translation request JSON 必须包含 `original_title`、`original_summary`、`original_content`、`source`、`score`。
- Translation response JSON 必须通过 schema validation；`title_zh`、`summary_zh`、`content_zh` 必须非空。
- 翻译输入优先使用 `content_full`；无全文时使用 `content_raw`。
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
- `GET /api/news/{id}` 对 `ready` / `translation_failed` 不返回 `summary_zh`、`content_zh`。
- `POST /api/refresh` 并发调用不触发第二次执行，仍返回 `200`。
- `POST /api/refresh` 无 request body，不返回 task ID、queue、worker、retry 或 progress 字段。
- `GET /api/sources` 返回所有 source，按 `created_at ASC` 排序。
- `POST /api/sources` 成功返回 `201`、`is_enabled = true`、`fetch_frequency = twice_daily`。
- `POST /api/sources` 空 name、空 rss_url、非法 URL、本地地址和私有地址返回结构化 `400`，且数据库不新增记录。
- `POST /api/sources` 重复 RSS URL 返回 `409`。
- `PATCH /api/sources/{id}` 成功返回更新后的 `SourceItem`。
- `PATCH /api/sources/{id}` source 不存在返回 `404`。
- `PATCH /api/sources/{id}` 禁止关闭全部源，返回 `409`。
- `DELETE /api/sources/{id}` 返回 `204` 且无 body。
- `DELETE /api/sources/{id}` source 不存在返回 `404`。
- `DELETE /api/sources/{id}` 以禁用实现，历史新闻仍通过 API 可见，未来 ingestion 停止。
- 所有 API response 必须通过非法字段黑名单检查。
- 所有 endpoint 必须覆盖成功用例和至少一个错误用例。
- 所有错误用例必须断言稳定 `error.code`。

### 3.5 UI 层
- NewsCard 正确渲染标题、来源、时间、评分、状态。
- HighScoreList 使用与 News Feed 相同的 `NewsListItem` shape。
- ArticleView 在 `translated` 时渲染 `content_zh`。
- ArticleView 在 `ready` / `translation_failed` 时不渲染 `summary_zh`、`content_zh`。
- 空字段不 crash，不自动补默认文案，不用其他字段替代。
- NewsCard 点击和 Title 点击必须进入同一个 ArticleView。
- HighScoreList item 点击必须使用同一个 news `id` 进入 ArticleView。
- ScoreBadge 不得触发排序、筛选或跳转。
- SourceMarker 不得跳转来源站点。
- TopBar 只提供 NexNews 返回主页、刷新、信源入口。
- Refresh 默认文案为 `刷新`，加载中禁用且文案为 `刷新中`，完成后重新加载新闻列表。
- 新闻列表加载中必须渲染与 NewsCard 尺寸一致的紧凑 skeleton。
- 空列表渲染 `暂无可展示新闻`。
- 新闻加载失败渲染 `新闻加载失败`。
- ArticleView 404 / 不可用状态渲染 `新闻不存在或不可展示` 和返回按钮。
- SourceForm 空字段时新增按钮禁用；非法 URL 显示行内校验；新增中按钮禁用；新增成功后清空输入并刷新列表。
- Source toggle frontend binding 必须调用 `PATCH /api/sources/{id}`，并正确展示 `404`、`409` 错误状态。
- RSS 配置页不得出现高级设置、分类、未记录的 UI 行为或额外组件。

### 3.6 数据模型与持久化层
- SQLite application schema 必须只保留 MVP 核心表：`source`、`news_item`、`processing_log`；SQLite 内部表不计入应用表集合。
- `source.rss_url` 必须唯一；`source.is_enabled` 必须可索引。
- `news_item.canonical_url` 必须唯一。
- `news_item.pipeline_state` 只允许 `raw`、`scored`、`fetched`。
- `pipeline_state = scored` 必须满足 `score IS NOT NULL`。
- `is_selected` 必须由 threshold 计算，不得作为 pipeline 状态。
- `content_raw` 保存 RSS 摘要或原始内容；`content_full` 只保存抓取全文。
- 不得保存 `content_source`、`title_domain_hash`、`translation_status`、`is_ready`、`display_mode`、独立任务队列表或多语言表。
- `processing_log` 必须满足 `source_id` 与 `news_item_id` 恰好一个非空。
- `processing_log.stage` 只允许 `crawl`、`score`、`fetch`、`translate`。
- `processing_log` 不驱动任务调度；它只记录处理结果。
- 删除或禁用 source 后，历史 `news_item` 必须保留。
- 所有 DB timestamp 必须为 ISO 8601 UTC string。
- 必须验证 `news_item.source_id`、`news_item.pipeline_state`、`news_item.published_at`、`news_item.score`、`processing_log(source_id, stage)`、`processing_log(news_item_id, stage, success)`、`processing_log.created_at` 索引存在。

### 3.7 内容抓取层
- 原文页面可访问且能抽取正文时，必须写入 `content_full`。
- 原文页面不可访问或正文抽取失败时，必须使用 `content_raw` 兜底。
- `content_full` 和 `content_raw` 都不可用时，不得进入可展示 API 查询结果，也不得触发翻译。
- 内容降级优先级必须为 `content_full` → `content_raw` → 不可展示。
- 抓取成功或兜底成功后，`pipeline_state` 必须更新为 `fetched`。

### 3.8 错误处理、日志与可观测性
- 所有异常必须归类为 `network`、`parsing`、`llm`、`database`、`validation`、`unknown`。
- LLM schema validation failure 必须归类为 `validation_llm_error`。
- 禁止 silent fail；失败必须写入 `processing_log` 或应用日志。
- RSS 解析失败必须归类为 `parsing`。
- 网络超时必须归类为 `network`。
- 未知异常必须转换为 `unknown`，不得暴露内部细节。
- 捕获异常后不得继续写入成功状态。
- 错误 message 不得包含完整正文、prompt、token 或密钥。
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
- 数据泄漏：API response 中不得出现 `content_raw`、`content_full`、完整 prompt。
- 数据泄漏：日志、测试报告、错误响应不得出现完整正文、完整 prompt、密钥、token。
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
  version: v1
```

All test executions MUST output machine-readable structured reports. The report is the only supported interface for CI parsing, AI automatic repair, failure routing, and traceability consumption.

Each test case or stage-level result MUST emit one `TestReport` object:

```json
{
  "schema_ref": "07_test_spec.md#6",
  "schema_version": "v1",
  "test_id": "...",
  "stage": "static | unit | contract | api | integration | replay | snapshot | e2e",
  "status": "passed | failed | flaky | skipped",
  "failure_type": "api | scheduler | integration | contract | data_model | ui | observability | leak | null",
  "error_category": "network | parsing | llm | validation_llm_error | database | validation | unknown | null",
  "trace_id": "...",
  "fixture_set": "...",
  "mock_set": "...",
  "clock_source": "...",
  "fixture_version": "...",
  "mock_version": "...",
  "assertions": [
    {
      "id": "...",
      "type": "api_response | db_state | side_effect | pipeline_output | llm_io | ui_render | report_schema | log_record | isolation",
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
  "node": "static | source | RSS | scheduler | score | filter | fetch | translate | DB | API | UI",
  "timestamp": "ISO8601"
}
```

Field rules:

- `schema_ref` MUST equal `07_test_spec.md#6`.
- `schema_version` MUST equal `v1`.
- `test_id` MUST be stable across runs and unique within the test suite.
- `stage` MUST match the orchestration stages defined in section 2.13.
- `status` MUST use only the documented values; failed retry results MUST be reported as `flaky` only when a retry passes.
- `failure_type` MUST use this closed enum for `failed` and `flaky` reports: `api`、`scheduler`、`integration`、`contract`、`data_model`、`ui`、`observability`、`leak`.
- `failure_type` MUST be `null` for `passed` and `skipped` reports.
- `failure_type` MUST NOT use nested names, dotted names, stage names, timeout names, or custom extension values.
- `error_category` MUST use the categories from `06_dev_rules.md` when the result comes from an exception or validation failure; otherwise it MAY be `null`.
- `trace_id` MUST connect the report to pipeline logs and failure details.
- `fixture_set` and `mock_set` MUST be present for all acceptance tests.
- `fixture_set` and `mock_set` MUST match the release gate policy for `ACC-MVP-*` reports.
- `clock_source` MUST be present for all acceptance tests.
- `clock_source` MUST match the release gate policy for `ACC-MVP-*` reports.
- `ACC-MVP-*` tests MUST use `fixed_clock_fixture@v1` as the only time source.
- `ACC-MVP-*` tests MUST NOT read wall clock time, system time, current process time, or network time as an assertion input.
- `fixture_version` and `mock_version` MUST be present for all deterministic tests.
- `assertions` MUST be present and non-empty for all `ACC-MVP-*` reports.
- Each assertion MUST include `id`、`type`、`status`、`expected`、`actual` and `diff`.
- Assertion `type` MUST use the closed enum in section 6.2.
- `ACC-MVP-*` report `status` MUST be `passed` only when every assertion has `status = passed`.
- Leak assertions MUST include `leak_detection`.
- `leak_detection.method` MUST equal `structured_field_scan`.
- `leak_detection.target` MUST be one of `api_json`、`ui_dom`、`logs`、`test_report`.
- `leak_detection.matched_paths` MUST be an array.
- `expected`, `actual`, and `diff` MUST be valid JSON objects; empty objects are allowed only when no assertion diff exists.
- `node` MUST identify the isolated pipeline node most responsible for the result.
- `timestamp` MUST be an ISO 8601 UTC string.
- `ACC-MVP-*` report `timestamp` MUST be derived from `clock_source`.

Output rules:

- CI MUST persist the full report collection as JSON.
- Human-readable logs MAY be generated from the structured report, but MUST NOT be the source of truth.
- Report fields MUST NOT contain `content_raw`、`content_full`、完整 prompt、密钥或超过 `1024` 字符的正文片段。
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

## 7. 验收标准
- Static compliance tests pass。
- 所有 API tests pass。
- Contract tests 100% pass。
- DB schema contract tests 100% pass。
- 核心链路 integration test 100% pass。
- Golden snapshot diff 必须为空或有显式批准。
- Pipeline replay test 输出必须完全一致。
- LLM prompt snapshot diff 必须有显式批准。
- Test pyramid ratio 不得被 snapshot / integration test 反向压倒。
- Flaky quarantine 必须为空或有明确 owner。
- Test report collection 必须符合 `Test Report Contract`。
- Test report collection 必须覆盖 `static`、`unit`、`contract`、`api`、`integration`、`replay`、`snapshot`、`e2e` stage。
- Test failure 必须输出 fixture version 和 data hash。
- Test execution 必须按 orchestration order 执行。
- Test failure 必须先报告最高优先级 failure。
- Timeout failure 必须使用 `timeout` category。
- Test failure 必须包含 `trace_id`、fixture version、mock version、expected vs actual diff。
- 所有 `01_prd.md` 到 `06_dev_rules.md` 的可测试要求必须能映射到本文件的测试层、核心用例或验收标准。
- End-to-end deterministic run 必须 pass。
- UI tests 无 crash。
- `GET /api/home` 和 `GET /api/news/{id}` 不暴露非法字段。
- `translated` item 必须有中文正文详情。
- `ready` / `translation_failed` item 必须省略中文摘要和中文正文。
- LLM mock tests 和 RSS fixture tests 不访问外部网络。
- 重复 RSS item 不产生重复展示新闻。
- 无测试失败时才允许进入 coding 完成状态。
