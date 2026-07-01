# 05_api_contract.md

## 1. Overview（概览）

本接口契约只服务 AI 新闻聚合系统 MVP。

Frontend 使用 React + Vite，通过 FastAPI REST API 读取和更新数据。

API 只暴露 UI 必需能力：

- 获取首页数据。
- 获取新闻详情。
- 手动刷新 RSS。
- 添加 / 删除 / 启用 / 停用 RSS 源。

API 不暴露数据库内部字段，不暴露 `pipeline_state`、`is_selected`、`content_raw`、`content_full`、`has_translate_failed`、`discussion_url`、`deleted_at`。

## 2. API Conventions（接口约定）

Base path:

```text
/api
```

Response format:

```json
{
  "data": {}
}
```

List response format:

```json
{
  "data": []
}
```

`204` responses return no body. All other successful JSON responses use the `data` envelope.

Error response format:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable error"
  }
}
```

Field naming:

- API response fields use the same `snake_case` names as `03_ui_spec.md`.
- All timestamps use ISO 8601 UTC string.
- IDs are returned as strings even if SQLite stores them as integers.

HTTP status codes:

| Status | Meaning |
| --- | --- |
| `200` | Request succeeded. |
| `201` | Resource created. |
| `204` | Resource deleted or disabled. |
| `400` | Invalid request. |
| `404` | Resource not found. |
| `409` | Duplicate resource conflict. |
| `500` | Internal server error. |

API stability rules:

- Existing response fields must not be removed.
- Existing response field types must not be changed.
- New response fields must be optional unless a new endpoint is introduced.
- Internal database fields must not be exposed through API responses.
- Endpoint response shape must only vary through fields documented in this contract.

## 3. Shared Types（共享类型）

### 3.1 NewsStatus

```ts
type NewsStatus = "ready" | "translated" | "untranslated" | "translation_failed";
```

Status mapping:

- `translated`: `title_zh`、`summary_zh`、`content_zh` all exist and represent Chinese translated content.
- `untranslated`: live RSS fallback content exists, but it is the original untranslated title/summary/content because live LLM translation is disabled or unavailable.
- `translation_failed`: not `translated` and `has_translate_failed = 1`.
- `ready`: not `translated` and not `translation_failed`.

Status is an API/UI projection. It is not stored as a database column.

Status derivation priority:

1. If all translated fields exist but exactly match the live original-title/RSS-summary fallback, return `untranslated`.
2. Else if all translated fields exist, return `translated`.
3. Else if `has_translate_failed = 1`, return `translation_failed`.
4. Otherwise return `ready`.

Partial translated fields must not change `status` by themselves and must not be returned by the API.

Status consistency rules:

- `status = "translated"` requires `title_zh`、`summary_zh`、`content_zh` to exist.
- `status = "translated"` must never be returned if any translated field is missing.
- `status = "untranslated"` must not include `summary_zh` or `content_zh` in API responses; UI labels it as `未翻译`.
- `status = "ready"` must not include `summary_zh` or `content_zh`.
- `status = "translation_failed"` must not include `summary_zh` or `content_zh`.

### 3.2 NewsItem

```ts
type NewsItem = {
  id: string;
  /**
   * Computed display field:
   * - translated -> title_zh
   * - fallback -> original_title
   */
  title: string;
  original_title: string;
  source_name: string;
  original_url: string;
  published_at: string;
  score: number;
  status: NewsStatus;
};
```

`NewsItem` is the base type. List and detail responses must use the more specific types below.

`original_url` rules:

- `original_url` MUST be the public HTTP(S) article URL read from the RSS item link.
- `original_url` MUST NOT be synthesized by the API, replaced by `canonical_url`, or rewritten to a local fixture path.
- For sources such as Hacker News, RSS comment/discussion URLs MUST be stored separately as internal data and MUST NOT replace `original_url`.
- `discussion_url` is internal for this MVP iteration and MUST NOT be returned by News list/detail API responses until `03_ui_spec.md` introduces a UI behavior that consumes it.
- Product-facing local acceptance fixtures MUST NOT use reserved placeholder hosts such as `example.com`, `example.org`, `example.net`, `.test`, or `.invalid` for displayable news `original_url`.
- Canonicalization is only an internal dedupe operation; API responses keep the RSS item link as `original_url`.

### 3.3 NewsListItem

```ts
type NewsListItem = NewsItem & {
  summary_zh?: string;
};
```

`summary_zh` is returned only when `status = "translated"`.

### 3.4 NewsDetailItem

```ts
type NewsDetailItem = NewsItem & {
  summary_zh?: string;
  content_zh?: string;
};
```

`summary_zh` and `content_zh` are optional in the structural TypeScript shape only because they are omitted for non-translated states. A translated detail response must include both fields as non-empty strings.

Detail field rules:

- If `status = "translated"`:
  - `summary_zh` is required.
  - `content_zh` is required.
- If `status != "translated"`:
  - `summary_zh` MUST NOT exist.
  - `content_zh` MUST NOT exist.
- API enforcement rule:
  - When `status = "translated"`, `summary_zh` and `content_zh` MUST be non-empty strings.
  - When `status != "translated"`, `summary_zh` and `content_zh` MUST be omitted.
  - They MUST NOT appear in the JSON response as `null`, empty string, or placeholder value.
  - For translated local acceptance fixtures, `summary_zh` and `content_zh` MUST be article-specific Chinese content, not generic fixture/mock/placeholder text.

Field mapping:

| API field | Source |
| --- | --- |
| `id` | `news_item.id` as string |
| `title` | `title_zh` when translated; otherwise `original_title` |
| `original_title` | `news_item.original_title` |
| `summary_zh` | `news_item.summary_zh`, only when `status = translated` |
| `content_zh` | `news_item.content_zh`, only on detail response and only when `status = translated` |
| `source_name` | `source.name` |
| `original_url` | `news_item.original_url` |
| `discussion_url` | Internal only; not returned in current News API responses |
| `published_at` | `news_item.published_at` |
| `score` | `news_item.score` |
| `status` | derived API/UI status |

Display field language rules:

- `title`: Chinese when translated title exists; otherwise fallback to `original_title`.
- `original_title`: original source title and may be non-Chinese.
- `summary_zh`: required for translated detail responses, returned only for translated list/detail responses, must be Chinese, and must never contain raw RSS summary.
- `content_zh`: only returned in translated detail responses, must be Chinese, and must never contain raw article content.
- API must never return `content_raw` or `content_full`.

Allowed API content fields:

- `original_title` as metadata only.
- `title_zh` for translated display title.
- `summary_zh` for translated summary.
- `content_zh` for translated detail content.

Forbidden API content exposure:

- API layer must never expose raw or unprocessed content.
- Raw ingestion sources must be sanitized before persistence.
- Raw RSS content, raw scraped HTML, raw extracted article text, and fallback raw text must not appear in API responses.

Do not return `summary_zh` or `content_zh` for `ready`, `untranslated`, or `translation_failed` items.

### 3.5 FetchFrequency

```ts
type FetchFrequency = "manual" | "hourly" | "twice_daily" | "daily";
```

MVP creates new RSS sources with `fetch_frequency = "twice_daily"`.

### 3.6 SourceItem

```ts
type SourceItem = {
  id: string;
  name: string;
  rss_url: string;
  is_enabled: boolean;
  fetch_frequency: FetchFrequency;
  created_at: string;
};
```

`deleted_at` is an internal source tombstone field and MUST NOT be returned by Source APIs.

### 3.7 RefreshResponse

```ts
type RefreshResponse = {
  refreshed_at: string | null;
};
```

`refreshed_at` is the completed refresh timestamp. It is `null` only when a concurrent refresh is rejected before any successful refresh has completed.

### 3.8 HomeData

```ts
type HomeData = {
  latest_news: NewsListItem[];
  top_ranked_news: NewsListItem[];
  next_cursor?: string;
};
```

`latest_news` and `top_ranked_news` are semantic data groups. API responses must not describe UI layout columns.

## 4. Endpoints（接口列表）

### 4.1 GET `/api/home`

Purpose: 获取首页新闻数据。

Query:

| Name | Type | Required | Rule |
| --- | --- | --- | --- |
| `cursor` | string | No | Opaque cursor returned by the previous `next_cursor`; applies only to `latest_news`. |
| `limit` | number | No | Default `50`, max `100`; applies to `latest_news`. |

Data rule:

- `latest_news` returns displayable news sorted by `published_at DESC`; fixture/mock acceptance items are `translated`, while live RSS fallback items may be `untranslated`.
- Only `latest_news` is cursor paginated in MVP.
- `limit` controls the `latest_news` page size; default is `50`, minimum effective value is `1`, max is `100`.
- When another `latest_news` page exists, response MUST include `next_cursor`.
- When no further `latest_news` page exists, response MUST omit `next_cursor`.
- A request using a returned `cursor` MUST return the next page after the previous page and MUST NOT repeat items from earlier pages for the same sort order.
- `top_ranked_news` returns displayable news from the last 30 days; fixture/mock acceptance items are `translated`, while live RSS fallback items may be `untranslated`.
- `top_ranked_news` sorts by `score DESC, published_at DESC`.
- `top_ranked_news` returns at most 10 items.
- `top_ranked_news` is a fixed-size window query and does not use cursor pagination.
- Both lists share `NewsListItem` shape and are independent semantic groups.
- Response type is `HomeData`.
- Do not return raw English summary or raw English content as `summary_zh` / `content_zh`.
- Every fixture/mock item in `latest_news` and `top_ranked_news` MUST have `status = "translated"` and include non-empty `summary_zh`; live fallback items MUST have `status = "untranslated"` and omit `summary_zh` / `content_zh`.
- `ready` and `translation_failed` remain valid detail statuses for direct/stale routes and regression tests, but they MUST NOT appear in the primary Home lists.

Response:

```json
{
  "data": {
    "latest_news": [
      {
        "id": "1",
        "title": "AI startup raises new funding",
        "original_title": "AI startup raises new funding",
        "source_name": "TechCrunch",
        "original_url": "https://openai.com/index/introducing-life-sci-bench/",
        "published_at": "2026-06-28T08:00:00Z",
        "score": 82,
        "summary_zh": "这是一条中文摘要。",
        "status": "translated"
      }
    ],
    "top_ranked_news": [
      {
        "id": "2",
        "title": "OpenAI 发布 LifeSciBench 生命科学基准",
        "original_title": "Introducing LifeSciBench",
        "source_name": "OpenAI Blog",
        "original_url": "https://www.anthropic.com/news/claude-sonnet-5",
        "published_at": "2026-06-28T07:00:00Z",
        "score": 96,
        "status": "translated"
      }
    ],
    "next_cursor": "2026-06-28T08:00:00Z"
  }
}
```

### 4.2 GET `/api/news/{id}`

Purpose: 获取新闻详情页 ArticleView。

Path:

| Name | Type | Required | Rule |
| --- | --- | --- | --- |
| `id` | string | Yes | News ID |

Data rule:

- Return one displayable `NewsDetailItem`.
- Include `content_zh` only when `status = translated`.
- Do not return raw English body content.
- Return `404` if the item does not exist or is not displayable.

Response:

```json
{
  "data": {
    "id": "2",
    "title": "OpenAI 发布 LifeSciBench 生命科学基准",
    "original_title": "Introducing LifeSciBench",
    "summary_zh": "这是一条中文摘要。",
    "content_zh": "这是一篇中文正文。",
    "source_name": "OpenAI Blog",
    "original_url": "https://openai.com/index/introducing-life-sci-bench/",
    "published_at": "2026-06-28T07:00:00Z",
    "score": 96,
    "status": "translated"
  }
}
```

### 4.3 POST `/api/refresh`

Purpose: 手动刷新 RSS，执行 MVP 主流程。

Request body:

None.

Processing rule:

- Runs in the FastAPI backend process.
- Executes RSS crawl, scoring, filtering, fetching, and translation.
- In live network mode, RSS crawl applies a 30-day freshness window before raw persistence: items with RSS `published_at` older than `refresh now - 30 days` are ignored and never reach scoring, fetching, or translation.
- Fixture/mock refresh may retain older records only for deterministic ranking-window and exclusion tests.
- Refresh is idempotent.
- If refresh is already running, API must not trigger a second concurrent refresh.
- If refresh is already running, API must return `200`.
- If refresh is already running, response must contain the last successful `refreshed_at`.
- If refresh is already running and no successful refresh has completed yet, response must contain `refreshed_at: null`.
- Does not create a task ID.
- Does not expose queue, worker, retry, or progress APIs.

Response type: `RefreshResponse`.

Response:

```json
{
  "data": {
    "refreshed_at": "2026-06-28T09:00:00Z"
  }
}
```

### 4.4 GET `/api/sources`

Purpose: 获取 RSS 信息源配置列表。

Query:

None.

Data rule:

- Return only RSS sources where `deleted_at IS NULL`.
- Sort by `created_at ASC`.
- Return `SourceItem[]`.

Response:

```json
{
  "data": [
    {
      "id": "1",
      "name": "TechCrunch AI",
      "rss_url": "https://example.com/rss.xml",
      "is_enabled": true,
      "fetch_frequency": "twice_daily",
      "created_at": "2026-06-28T06:00:00Z"
    }
  ]
}
```

### 4.5 POST `/api/sources`

Purpose: 新增 RSS 信息源。

Request:

```ts
type CreateSourceRequest = {
  name: string;
  rss_url: string;
};
```

Validation:

- `name` is required and must not be empty.
- `rss_url` is required and must be a valid URL.
- Duplicate `rss_url` returns `409`, including a URL that exists on a deleted source tombstone.
- New source uses `is_enabled = true`.
- New source uses `fetch_frequency = "twice_daily"`.

Response:

Status: `201`

Response type: `SourceItem`.

```json
{
  "data": {
    "id": "3",
    "name": "Example AI Feed",
    "rss_url": "https://example.com/rss.xml",
    "is_enabled": true,
    "fetch_frequency": "twice_daily",
    "created_at": "2026-06-28T10:00:00Z"
  }
}
```

### 4.6 PATCH `/api/sources/{id}`

Purpose: 启用或停用 RSS 信息源。

Path:

| Name | Type | Required | Rule |
| --- | --- | --- | --- |
| `id` | string | Yes | Source ID |

Request:

```ts
type UpdateSourceRequest = {
  is_enabled: boolean;
};
```

Validation:

- `is_enabled` is required.
- Return `404` if source does not exist or has `deleted_at IS NOT NULL`.
- Return `409` if the update would result in invalid source configuration.
- MVP invalid configuration includes leaving zero sources where `deleted_at IS NULL AND is_enabled = 1`.

Response:

Response type: `SourceItem`.

```json
{
  "data": {
    "id": "1",
    "name": "TechCrunch AI",
    "rss_url": "https://example.com/rss.xml",
    "is_enabled": false,
    "fetch_frequency": "twice_daily",
    "created_at": "2026-06-28T06:00:00Z"
  }
}
```

### 4.7 DELETE `/api/sources/{id}`

Purpose: 删除 RSS 信息源。

Path:

| Name | Type | Required | Rule |
| --- | --- | --- | --- |
| `id` | string | Yes | Source ID |

Behavior:

- MVP delete is implemented as source tombstone: set `is_enabled = 0` and `deleted_at` to the business UTC timestamp from the injected clock.
- Return `409` and do not update the row if deleting the source would leave zero sources where `deleted_at IS NULL AND is_enabled = 1`.
- Disabling a source does not affect existing news items.
- Historical data remains visible in all APIs.
- Only future ingestion is stopped.
- Return `404` if source does not exist or already has `deleted_at IS NOT NULL`.
- Deleted sources are omitted from `GET /api/sources`.

Response:

Status: `204`

No response body.

## 5. Frontend Binding（前端绑定）

| UI action | API |
| --- | --- |
| Home page loads News Feed | `GET /api/home` → `data.latest_news` |
| Home page loads HighScoreList | `GET /api/home` → `data.top_ranked_news` |
| Click NewsCard / Title / HighScoreList item | `GET /api/news/{id}` |
| Click Refresh | `POST /api/refresh` |
| Open RSS source page | `GET /api/sources` |
| Add RSS source | `POST /api/sources` |
| Toggle RSS source | `PATCH /api/sources/{id}` |
| Delete RSS source | `DELETE /api/sources/{id}` |

## 6. Non-Goals（非目标）

MVP API 不设计以下接口：

- User / login / permission APIs.
- Search APIs.
- Category APIs.
- Comment / favorite / share APIs.
- Processing log APIs.
- Task status / progress APIs.
- Retry APIs.
- Admin APIs.
- API versioning.
