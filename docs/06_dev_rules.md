# 06_dev_rules.md

## 0. Global Hard Constraints（全局硬规则）

- API 响应不得包含 `content_raw`、`content_full`、`deleted_at`、raw article body、fallback raw text、完整 prompt，否则数据泄漏检查必须失败；`GET /api/news/{id}` 在 `status = translated` 时返回 `content_zh` 是唯一允许的完整正文展示出口。
- 日志不得包含超过 `1024` 字符的正文类字段，否则必须在写日志前裁剪。
- `pipeline_state` 只能由 backend pipeline service 写入，否则 code review 必须拒绝该变更。
- `status` 只能由 API 层按 `05_api_contract.md` 投影，否则不得写入数据库。
- `has_translate_failed` 只能作为展示缓存，否则不得用于替代 `processing_log`。
- 前端不得读取数据库字段名，否则该代码必须移到 API 层。
- 新增 endpoint 必须先写入 `05_api_contract.md`，否则不得实现。
- 新增 UI 行为必须先写入 `03_ui_spec.md`，否则不得实现。
- 所有时间必须使用 ISO 8601 UTC 字符串，否则排序测试必须失败。
- 所有规则冲突必须按本文末尾 Rule Priority Order 处理，否则不得合并。
- If rules conflict within `06_dev_rules.md`, the more restrictive rule MUST win.

## 1. Code Style Rules（代码风格规范）

- 变量名必须使用完整英文含义，禁止 `tmp`、`data1`、`foo`，否则代码搜索无法定位数据来源。
- 函数名必须以动词开头，例如 `fetch_news_items`，否则调用点无法判断副作用。
- Python 文件名必须使用小写 snake_case，否则导入路径检查必须失败。
- React 组件文件必须使用 PascalCase，否则组件导入检查必须失败。
- API DTO 类型必须以 `Request`、`Response`、`Item` 结尾，否则接口输入输出会混用。
- 数据库模型命名必须与表名一一对应，否则 schema 映射测试必须失败。
- 禁止自造缩写，例如 `srcNm`、`pubAt`，否则字段语义会被误读。
- 允许固定缩写：`API`、`DTO`、`RSS`、`LLM`、`URL`、`HTML`、`JSON`、`UTC`。
- 同一字段在前端、后端、文档中必须同名，否则 DTO 映射测试必须失败。
- 单个函数超过 `60` 行必须拆分，否则 review 必须要求拆分。
- 单个文件超过 `300` 行必须拆分，否则后续修改必须先拆文件。
- 注释只能解释非显而易见的约束，否则必须删除。

## 2. Frontend Rules（React）

- Frontend allowed responsibilities: render API DTO、保存本地 UI state、捕获用户交互。
- Frontend forbidden responsibilities: 业务判断、pipeline 状态推导、数据库字段映射。
- 每个组件必须对应 `03_ui_spec.md` 的最终实现单元，否则必须删除。
- 页面组件只负责组合组件和加载数据，否则业务逻辑必须下沉到 API client 或 service。
- `NewsCard`、`ArticleView`、`HighScoreList` 只能消费 API DTO 字段，否则字段泄漏检查必须失败。
- 前端不得读取 `pipeline_state`、`is_selected`、`content_raw`、`content_full`。
- 前端只能根据 API `status` 渲染新闻状态，否则状态逻辑会重复实现。
- 全局状态只能保存跨页面必需数据，否则必须改成本地 state。
- 禁止用 `useEffect` 保存可由 props 计算出的状态，否则会产生双状态。
- API 调用必须集中在 API client 文件中，否则 endpoint 变更无法集中检查。
- UI 组件不得直接拼接 endpoint 字符串，否则接口修改必须全局搜索。
- 字段缺失必须按 `03_ui_spec.md` 不渲染，不得补默认文案。
- 列表渲染必须使用稳定 `id` 作为 key，否则刷新后 DOM 复用会错位。
- 表单提交中必须禁用重复点击，否则 RSS 源可能重复创建。
- `03_ui_spec.md#6.1` 的 Final Unit Rule 禁止拆业务 UI 子组件，但不禁止拆文件。为满足单文件 `300` 行限制，允许拆分 CSS 文件、API client、DTO/type 文件、test fixture、route/page 文件和不渲染 UI 的私有纯函数 helper。
- 禁止通过拆分 `NewsCardHeader`、`NewsCardMeta`、`ArticleHeader`、`BaseButton`、`BaseCard`、`components/ui` 或 `shared/ui` 等方式绕过 Final Unit Rule。

## 3. Backend Rules（FastAPI）

- 成功响应必须返回 `{ "data": ... }`，否则 API contract test 必须失败。
- 错误响应必须返回 `{ "error": { "code": "...", "message": "..." } }`。
- `204` 响应必须无 body，否则 HTTP contract test 必须失败。
- API handler 不得直接返回 DB model，否则内部字段会暴露。
- API handler 必须返回 DTO，否则 response shape 无法测试。
- 每个 endpoint 必须有独立函数，否则单接口测试无法隔离。
- Route 只做参数校验、调用 service、返回 DTO，否则业务逻辑会散在接口层。
- Service 函数必须接收明确参数，不得直接读取 request 对象。
- 数据访问必须集中在 repository 或 database helper 中，否则 SQL 会散落。
- 外部输入必须在 API 边界校验，否则脏数据会进入 pipeline。
- API 字段必须符合 `05_api_contract.md`，否则前端绑定测试必须失败。
- 禁止新增未记录 endpoint，否则实现绕过契约。

## 4. Data Pipeline Rules（数据处理规范）

- `pipeline_state` 只允许 `raw`、`scored`、`fetched`，否则数据库写入必须失败。
- `pipeline_state` transition independent of `is_selected`，否则流程状态会被业务判断污染。
- `is_selected` 必须由 configurable threshold 计算，默认值为 `60`。
- 写入 `score` 后必须立即计算 `is_selected`，否则 fetch 条件会漂移。
- 去重只能使用 `canonical_url`，否则同一新闻会重复入库。
- 翻译触发条件必须是 `pipeline_state = fetched` 且存在可用内容。
- 翻译成功必须写入 `title_zh`、`summary_zh`、`content_zh`。
- 翻译失败不得写入部分中文字段，否则 API status 投影会错误。
- Source 禁用只能停止未来抓取，不得删除历史新闻。
- Source 删除必须使用 soft tombstone：设置 `is_enabled = 0` 和 `deleted_at`，不得物理删除 source row 或历史新闻。
- API 不得更新 pipeline 字段，否则用户请求会绕过处理流程。

## 5. LLM Interaction Rules（LLM 调用规范）

- LLM 请求必须通过统一 client 入口，否则 mock 和限流无法集中处理。
- LLM scoring 输出必须按 JSON schema 校验，否则不得写入 `score`。
- LLM translation 输出必须按 JSON schema 校验，否则不得写入中文字段。
- If LLM output fails JSON schema validation, classify as `validation_llm_error`, not `llm_error`.
- 无效 LLM response 必须在 `processing_log` 中记录为 `llm` 或 `validation_llm_error` 错误，否则不得写入业务成功字段。
- LLM retry max = `2`，超过后必须写入失败 `processing_log`。
- If LLM scoring or translation fails after retry max, `pipeline_state` must remain unchanged; scoring failure must not advance the item, and translation failure must set `has_translate_failed = 1` without writing partial Chinese fields.
- Missing title or original URL is deterministic input validation and may assign `score = 0` without an LLM call; invalid, timeout, or schema-invalid LLM output must not be converted into a successful `score = 0`.
- LLM prompt 变更必须有测试 fixture 更新，否则输出契约无法验证。
- LLM prompt 不得包含完整 `content_full` 之外的敏感配置，否则日志和错误处理会泄漏。
- LLM mock 必须覆盖 scoring 和 translation，否则 pipeline 测试会依赖外部服务。

## 6. Error Handling Rules（错误处理规范）

- 所有异常必须归类为 `network`、`parsing`、`llm`、`validation_llm_error`、`database`、`validation`、`timeout`、`unknown`。
- 禁止 silent fail，任何失败必须写入 `processing_log` 或应用日志。
- API validation error 必须返回结构化 error，否则前端无法稳定处理。
- RSS 解析失败必须归类为 `parsing`，否则源质量无法判断。
- 网络超时必须归类为 `network`，否则排障方向会错误。
- 未知异常必须转换为 `unknown` 并隐藏内部细节。
- 捕获异常后不得继续写入成功状态，否则数据会假成功。
- 禁止裸 `except` 后只返回默认值，否则真实错误会被吞掉。
- 错误 message 不得包含 raw article body、fallback raw text、prompt、token，否则敏感内容会进入响应。
- 同一错误分类必须使用固定 code，否则前端无法稳定处理。

## 7. Logging Rules（日志规范）

- 每个 pipeline step 必须写 `processing_log`，否则流程执行无法追踪。
- 日志必须包含对象 ID、stage、UTC timestamp，否则排障查询无法定位记录。
- 失败日志必须包含错误分类，否则无法统计失败来源。
- 成功日志不得包含超过 `1KB` 的字段，否则必须裁剪。
- 禁止打印 `content_raw`、`content_full`、raw article body、fallback raw text 或完整 prompt。
- In debug mode only, full prompt logging is allowed but must not persist to production storage.
- 日志可以记录 URL 和标题，但标题长度必须裁剪到 `300` 字符以内。
- 刷新开始和结束必须记录日志，否则手动刷新无法回放。
- 并发 refresh 被拒绝时必须记录日志，否则重复点击无法定位。
- 禁止使用 `print` 作为业务日志，否则日志格式无法统一。
- 日志必须使用同一 logger 入口，否则无法统一检索。

## 8. Change Management Rules（代码变更规范）

- 单次 commit 只能包含一个逻辑变更，否则 rollback 粒度不可控。
- 禁止同一 commit 混合重构和功能新增，否则行为 diff 无法隔离。
- 修改 API 必须同步更新 `05_api_contract.md`。
- 修改数据字段必须同步更新 `04_data_model.md`。
- 修改 UI 行为必须同步更新 `03_ui_spec.md`。
- 删除字段或 endpoint 前必须确认无现有调用，否则旧 UI 会崩溃。
- 新增依赖必须写明用途，否则不得合并。
- 禁止一次性修改多个无关模块，否则问题定位必须回退整个变更。
- 提交前必须运行可用测试，否则不得推送到 main。
- 禁止提交 `.env`、密钥、token 或本地配置。
- 禁止提交构建产物，除非项目文档明确要求。

## 9. Testing Rules（测试规范）

- 每个 API endpoint 必须有成功用例和错误用例，否则接口契约无法验证。
- `GET /api/home` 必须测试 `latest_news` 和 `top_ranked_news` 字段。
- `GET /api/news/{id}` 必须测试非 `translated` 不返回 `content_zh`。
- `POST /api/refresh` 必须测试重复调用不会启动并发刷新。
- `POST /api/sources` 必须测试重复 RSS URL 返回 `409`。
- `PATCH /api/sources/{id}` 必须测试禁止关闭最后一个 `deleted_at IS NULL AND is_enabled = 1` 的 source。
- 每个核心 pipeline step 必须可 mock，否则测试会依赖真实 RSS、网页和 LLM。
- MVP Critical Path Test 必须覆盖 `RSS → ingest → score → fetch → translate → API`。
- `pipeline_state` transitions must be tested as a strict finite state machine: `raw → scored → fetched`。
- Any invalid `pipeline_state` transition must fail tests.
- LLM scoring 和 translation 必须用 mock 响应测试。
- RSS parsing 必须用固定 fixture 测试，否则外部源变化会导致测试漂移。
- All tests must be deterministic and not depend on time, network, or external APIs.
- 数据库测试必须使用临时 SQLite，否则测试会污染开发数据。
- API 测试必须断言 response shape，否则字段漂移无法发现。
- 错误测试必须断言 error code，否则错误分类会退化成字符串比较。
- 禁止无测试合并到 main，否则无法证明变更可回滚。

## Rule Priority Order

1. `05_api_contract.md`
2. `04_data_model.md`
3. `06_dev_rules.md`
4. `03_ui_spec.md`
5. Implementation code

Document priority MUST override internal rule priority.
Within same document, more restrictive rule takes priority.
