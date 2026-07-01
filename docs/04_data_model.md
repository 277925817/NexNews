# 04_data_model.md

## 1. Overview（概览）

本数据模型只服务 MVP 流程：

RSS 抓取 → 内容入库 → LLM 评分 → 内容筛选 → 内容翻译 → 前端展示

MVP 使用 SQLite 单库，保留 3 张核心表：

- `source`：RSS 新闻源。
- `news_item`：新闻主表，保存 RSS 原始数据、评分、内容和中文翻译。
- `processing_log`：最小处理记录。

MVP 遵守单真相原则：

- `pipeline_state` 是唯一流程状态。
- `is_selected` 是唯一业务过滤字段。
- UI 展示状态不入库，也不在本数据模型中分类。

技术流程：

`raw → scored → fetched`

`pipeline_state` only represents data acquisition stage, NOT business readiness or translation state.

翻译不进入 `pipeline_state`，翻译结果由字段事实和 API 投影规则共同判断：

- 已翻译：`title_zh`、`summary_zh`、`content_zh` 有值。
- 未翻译：中文字段为空。
- live 原文兜底：当 live LLM 不可用时，系统可暂存原文标题、RSS 摘要和原文内容作为展示候选事实；这不是翻译成功，API 必须将其投影为 `untranslated` 并禁止返回 `summary_zh` / `content_zh`。
- 翻译失败：列表查询使用 `has_translate_failed = 1`；最终处理事实以 `processing_log` 为准。

可展示数据事实：

- 不展示：`is_selected = 0`。
- 可展示：`is_selected = 1 AND (content_full IS NOT NULL OR content_raw IS NOT NULL)`。
- 已翻译：`title_zh`、`summary_zh`、`content_zh` 有值。
- 翻译失败缓存：`has_translate_failed = 1`。
- live 原文兜底可展示，但不得被 API/UI 视为 `translated`。

## 2. Core Tables（核心数据表）

### 2.1 NewsItem

Table name: `news_item`

Purpose: 新闻主表，承载 RSS 原始条目、LLM 评分、正文内容、中文翻译和前端展示所需字段。

| Field | Type | Key | Purpose |
| --- | --- | --- | --- |
| `id` | INTEGER | PK | 新闻唯一 ID。 |
| `source_id` | INTEGER | FK → `source.id` | 新闻所属 RSS 源。 |
| `rss_guid` | TEXT |  | RSS 条目 GUID；用于辅助识别 RSS 原始条目。 |
| `original_url` | TEXT |  | 新闻原文链接，用于抓取正文和详情页原文入口。 |
| `canonical_url` | TEXT | UNIQUE | 规范化后的原文链接，用于唯一去重。 |
| `discussion_url` | TEXT |  | 来源站内讨论链接，例如 Hacker News `item?id=...`；仅内部保存，当前 API/UI 不展示。 |
| `original_title` | TEXT |  | RSS 原始标题；未翻译或翻译失败时用于 UI 标题。 |
| `published_at` | TEXT | INDEX | RSS 信息源发布时间，用于主列表排序和 30 天榜单筛选。 |
| `score` | INTEGER | INDEX | LLM 最终 AI 价值评分，范围 `0-100`，用于筛选和榜单排序。 |
| `is_ai_news` | INTEGER | INDEX | 内部 AI 新闻判定，`1` 表示 LLM 判定为 AI 新闻；不得通过 API/UI 暴露。 |
| `ai_relevance_score` | INTEGER | INDEX | 内部 AI 相关性评分，范围 `0-100`；不得通过 API/UI 暴露。 |
| `pipeline_state` | TEXT | INDEX | 技术流程状态，只允许 `raw`、`scored`、`fetched`。 |
| `is_selected` | INTEGER |  | 是否通过 AI 价值筛选，`1` 表示 `is_ai_news = 1 AND ai_relevance_score >= 70 AND score >= 75`。 |
| `content_raw` | TEXT |  | RSS 摘要或 RSS 原始内容；用于评分，也作为正文抓取失败时的兜底内容。 |
| `content_full` | TEXT |  | 抓取到的新闻全文；抓取失败时可为空。 |
| `title_zh` | TEXT |  | 中文标题，仅翻译成功后写入。 |
| `summary_zh` | TEXT |  | 中文摘要；当前 UI 卡片需要该字段，翻译成功后写入。 |
| `content_zh` | TEXT |  | 中文正文，仅翻译成功后写入。 |
| `has_translate_failed` | INTEGER |  | 翻译失败展示缓存，`1` 表示最近一次翻译失败，默认 `0`。 |
| `created_at` | TEXT |  | 新闻首次入库时间。 |
| `updated_at` | TEXT |  | 新闻最近更新时间。 |

Time format:

- All timestamps use ISO 8601 UTC string.

Allowed `pipeline_state` values:

- `raw`：RSS 条目已入库，尚未评分。
- `scored`：已完成 LLM 评分。
- `fetched`：已获得 `content_full` 或可用的 `content_raw`。

State constraints:

- `pipeline_state = 'scored'` ⇒ `score IS NOT NULL`。

Business filter:

- `is_selected = 1`：`is_ai_news = 1 AND ai_relevance_score >= 70 AND score >= 75`，需要抓取正文并可进入展示候选。
- `is_selected = 0`：非 AI、AI 相关性不足或最终 AI 价值分不足，不抓取全文，不进入前端展示。
- `is_selected` 在 `pipeline_state = scored` 后立即计算。
- `pipeline_state` transition is independent of `is_selected`。
- `is_selected` 不改变 `pipeline_state`，不是状态节点。

Content rules:

- `content_raw` 保存 RSS 摘要或 RSS 原始内容，不再单独保存 `rss_summary`。
- `content_full` 只保存抓取到的全文。
- 翻译输入优先使用 `content_full`；没有全文时使用 `content_raw`。
- 不保存 `content_source`；是否使用兜底内容由 `content_full` 是否为空判断。
- 不保存 `title_domain_hash`；MVP 只使用 `canonical_url` 去重。
- `original_url` 保存文章 URL；`discussion_url` 保存 RSS `<comments>` 或等价评论页 URL，不参与正文抓取、去重或 API 展示。

Translation rules:

- Translation is triggered when `pipeline_state = 'fetched' AND (content_full IS NOT NULL OR content_raw IS NOT NULL)`。
- `processing_log` is the source of truth for translation status.
- `has_translate_failed` is a derived cache field from `processing_log`。
- 翻译成功时写入 `title_zh`、`summary_zh`、`content_zh`，并设置 `has_translate_failed = 0`。
- live LLM 禁用或不可用时，原文兜底写入不得代表翻译成功；`processing_log(stage = translate)` 必须记录为兜底原因而非成功翻译，API projection 必须依据原文兜底匹配规则返回 `untranslated`。
- 翻译失败时不写入中文字段，并设置 `has_translate_failed = 1`。
- 翻译失败原因只写入 `processing_log`。
- 不保存 `translation_status`。

UI projection:

- UI `title` 在中文字段存在时来自 `title_zh`。
- UI `title` 在中文字段不存在时来自 `original_title`。
- UI `summary_zh` 来自 `summary_zh`。
- UI `content_zh` 来自 `content_zh`。
- UI/API `original_url` 来自 `news_item.original_url`。
- `discussion_url` 是内部字段，当前不进入 UI/API DTO。
- API 必须先按 `docs/05_api_contract.md#3.1` 推导 `NewsStatus`；只有 `status = translated` 时才允许把 `summary_zh` / `content_zh` 暴露给 UI。

### 2.2 Source

Table name: `source`

Purpose: 保存默认和用户新增的 RSS 新闻源。

| Field | Type | Key | Purpose |
| --- | --- | --- | --- |
| `id` | INTEGER | PK | RSS 源唯一 ID。 |
| `name` | TEXT |  | RSS 源显示名称。 |
| `rss_url` | TEXT | UNIQUE | RSS 订阅地址。 |
| `is_enabled` | INTEGER | INDEX | 是否启用，`1` 表示启用，`0` 表示停用。 |
| `deleted_at` | TEXT |  | 软删除 tombstone 时间；未删除时为空。 |
| `fetch_frequency` | TEXT |  | 抓取频率；MVP 固定为 `twice_daily`。 |
| `created_at` | TEXT |  | RSS 源创建时间。 |

Notes:

- 默认 RSS 源和用户新增 RSS 源使用同一张表。
- 删除 Source 是软删除：设置 `is_enabled = 0` 和 `deleted_at`，不物理删除 source row。
- `GET /api/sources` 只返回 `deleted_at IS NULL` 的 source。
- 已删除 source 不参与未来抓取、启用 / 停用操作或“至少一个启用源”判断。
- 删除 Source 后，历史 `news_item` 可继续保留。

### 2.3 ProcessingLog

Table name: `processing_log`

Purpose: 记录 source 或 news_item 在 crawl / score / fetch / translate 阶段的最小处理结果。

| Field | Type | Key | Purpose |
| --- | --- | --- | --- |
| `id` | INTEGER | PK | 日志唯一 ID。 |
| `source_id` | INTEGER | FK → `source.id`, COMPOSITE INDEX | crawl 阶段关联的 RSS 源；其他阶段为空。 |
| `news_item_id` | INTEGER | FK → `news_item.id`, COMPOSITE INDEX | score / fetch / translate 阶段关联的新闻；crawl 阶段可为空。 |
| `stage` | TEXT | COMPOSITE INDEX | 处理阶段，只允许 `crawl`、`score`、`fetch`、`translate`。 |
| `success` | INTEGER | COMPOSITE INDEX | 是否成功，`1` 表示成功，`0` 表示失败。 |
| `error` | TEXT |  | 失败原因；成功时为空。 |
| `trace_id` | TEXT | INDEX | 单次刷新或处理链路的追踪 ID，用于连接测试报告、日志和处理记录。 |
| `created_at` | TEXT | INDEX | 日志创建时间。 |

Rules:

- Exactly one of (`source_id`, `news_item_id`) must be NOT NULL。
- SQL constraint: `(source_id IS NOT NULL AND news_item_id IS NULL) OR (source_id IS NULL AND news_item_id IS NOT NULL)`。
- If `stage = 'crawl'`, `source_id` MUST be NOT NULL。
- If `stage IN ('score', 'fetch', 'translate')`, `news_item_id` MUST be NOT NULL。
- `processing_log` 不驱动任务调度。
- 不记录 `retry_count`。
- 不记录独立 `status` 字段；`success` 已表达处理结果。
- `trace_id` must be present for every processing log row.
- All timestamps use ISO 8601 UTC string.

## 3. Data Relationships（数据关系）

关系：

- `source 1 → N news_item`
- `source 1 → N processing_log`
- `news_item 1 → N processing_log`

主流程：

1. `source` 提供 `deleted_at IS NULL AND is_enabled = 1` 的 RSS URL。
2. RSS 条目写入 `news_item`，初始 `pipeline_state = raw`。
3. LLM 评分后写入 `is_ai_news`、`ai_relevance_score`、`score`，`pipeline_state` 更新为 `scored`。
4. 如果 `is_ai_news = 1 AND ai_relevance_score >= 70 AND score >= 75`，写入 `is_selected = 1`。
5. `is_selected` 不改变 `pipeline_state`；它只决定是否继续抓取正文。
6. `is_selected = 1` 的新闻执行正文抓取。
7. 抓取成功时写入 `content_full`；抓取失败但 RSS 内容可用时保留 `content_raw` 作为兜底。
8. 有可用内容后，`pipeline_state` 更新为 `fetched`。
9. 当 `pipeline_state = 'fetched' AND (content_full IS NOT NULL OR content_raw IS NOT NULL)` 时触发翻译。
10. 翻译成功后写入 `title_zh`、`summary_zh`、`content_zh`，并设置 `has_translate_failed = 0`。
11. 翻译失败时设置 `has_translate_failed = 1`，并在 `processing_log` 写入失败记录。
12. 非翻译阶段失败只写入 `processing_log`，不修改 `has_translate_failed`。

前端展示关系：

- 主列表读取 `is_selected = 1 AND (content_full IS NOT NULL OR content_raw IS NOT NULL)` 的新闻。
- 主列表按 `published_at DESC` 排序。
- 30 天高分榜读取最近 30 天内可展示新闻。
- 30 天高分榜按 `score DESC, published_at DESC` 排序。
- Live RSS ingest 在写入 `news_item` 前按本次 refresh/crawl 的 `now` 计算 30 天窗口，只写入 RSS `published_at` 位于该窗口内的条目；fixture ingest 可以保留窗口外历史样本，用于验证榜单排除逻辑。

数据事实：

- 可展示事实：`is_selected = 1 AND (content_full IS NOT NULL OR content_raw IS NOT NULL)`。
- 已翻译事实：`title_zh`、`summary_zh`、`content_zh` 有值。
- 翻译状态最终事实来源：`processing_log`。
- 翻译失败缓存字段：`has_translate_failed = 1`。

## 4. Index & Performance Notes（索引与性能）

必须索引：

- `source.rss_url UNIQUE`：避免重复添加 RSS 源。
- `source.is_enabled`：快速读取启用 RSS 源。
- `news_item.source_id`：按 RSS 源查询历史新闻。
- `news_item.canonical_url UNIQUE`：避免同一原文链接重复入库。
- `news_item.pipeline_state`：快速筛选流程阶段。
- `news_item.published_at`：支持主列表按发布时间排序。
- `news_item.score`：支持高分榜排序。
- `news_item.is_ai_news`：支持 AI 价值筛选排查。
- `news_item.ai_relevance_score`：支持 AI 相关性筛选排查。
- `processing_log(source_id, stage)`：查询 source 维度的 crawl 处理结果。
- `processing_log(news_item_id, stage, success)`：查询单条新闻的 pipeline 步骤和失败结果。
- `processing_log.trace_id`：连接 pipeline、测试报告和日志证据。
- `processing_log.created_at`：按时间查看处理日志。

不建索引：

- `news_item.is_selected`：MVP 数据量小，布尔索引收益低。
- `news_item.has_translate_failed`：仅用于展示状态，不作为筛选入口。
- `processing_log.source_id`：不单独索引，使用 `processing_log(source_id, stage)` 组合索引。
- `processing_log.news_item_id`：不单独索引，使用 `processing_log(news_item_id, stage, success)` 组合索引。
- `processing_log.stage`：不单独索引，使用组合索引。
- `processing_log.success`：不单独索引，使用 `processing_log(news_item_id, stage, success)` 组合索引。

UI 排序字段：

- 主新闻列表：`published_at DESC`
- 30 天高分榜：`score DESC, published_at DESC`

## 5. MVP Simplification Notes（MVP简化说明）

- 不设计用户表。
- 不设计评论、收藏、分享、推荐数据。
- 不设计分类表；中文分类不进入 MVP 数据模型。
- 不设计独立任务队列表；处理结果只写入 `processing_log`。
- 不设计全文搜索索引。
- 不设计多语言表；中文翻译字段直接存放在 `news_item`。
- 不设计 `is_ready` 字段；可展示条件由查询计算。
- 不设计 `display_mode` 字段；UI 状态由 `03_ui_spec.md` 定义。
- 不设计 `translation_status` 字段；翻译成功由中文字段是否存在判断，失败展示使用 `has_translate_failed` 缓存，最终事实以 `processing_log` 为准。
- 不设计 `content_source` 字段；由 `content_full` 是否为空判断内容来源。
- 不设计 `title_domain_hash` 字段；MVP 只用 `canonical_url` 去重。
- 不拆分正文内容表；原文内容和中文内容都保存在 `news_item`。
- 不把来源讨论页和原文页混用；讨论页只保存在 `discussion_url`，原文抓取和详情页入口使用 `original_url`。
