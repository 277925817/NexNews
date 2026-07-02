# 02_arch.md

## 1. Technology Stack（技术栈）

- Frontend: React + Vite
- Backend: Python FastAPI
- Database: SQLite
- Scheduler: In-process backend scheduler
- LLM: Structured JSON scoring and translation calls
- RSS parsing / scraping tools: RSS/XML parser, HTML content extractor

## 2. High-Level Architecture（整体架构）

The MVP runs as a single FastAPI application on one machine for local deployed use. The frontend is a React + Vite single-page app; in development it may run through Vite, but the long-running local service serves the Vite build output from FastAPI on `http://127.0.0.1:8010/` alongside `/api/*`. The backend owns RSS collection, LLM scoring, content fetching, LLM translation, scheduling, SQLite persistence, and local static SPA serving.

RSS sources provide the initial news entries. FastAPI reads RSS feeds, applies the live RSS 30-day freshness window before raw persistence, stores eligible raw entries in SQLite, sends title and summary data to the LLM for AI relevance and AI value scoring, marks only high-value AI items with `is_selected`, fetches content for selected items, translates fetched content, and exposes display-ready API/UI projections to the frontend.

Core data flow:

RSS → Crawl → Score → Filter → Fetch → Translate → UI

## 3. Core Modules（核心模块划分）

### 3.1 RSS Collector

### 3.2 News Scoring Service（LLM）

### 3.3 Content Fetcher

### 3.4 Translation Service（LLM）

### 3.5 Scheduler（定时任务）

### 3.6 API Service（FastAPI）

### 3.7 Frontend App

## 4. Module Interaction（模块交互）

1. RSS sources enter the system through the RSS Collector during scheduled crawling or manual refresh.
2. The RSS Collector parses enabled RSS feeds and, in live runtime, writes only items whose RSS `published_at` is within the last 30 days relative to refresh time as raw news records.
3. The News Scoring Service calls the LLM after a new raw item is available, using its title, summary, source, published time, and original link; live scoring applies the documented AI value rubric and score caps before selection.
4. Items set `is_selected = 1` only when `is_ai_news = true`, `ai_relevance_score >= 70`, and final `score > 80` (integer implementation `score >= 81`); this does not change `pipeline_state`.
5. The Content Fetcher runs only for selected items and stores either extracted article content or RSS summary fallback content.
6. When usable content exists, `pipeline_state` becomes `fetched`.
7. The Translation Service calls the LLM after the item is fetched, using the original title, summary, content, source, and score.
8. The API Service reads displayable items from SQLite for the frontend.
9. The Frontend App renders the news list, 30-day high-score list, source configuration page, and news reading page from backend data.

## 5. Data Flow（数据流）

RSS → raw → scored → fetched → API/UI status projection → UI

- RSS: Enabled RSS sources provide news entries; live runtime filters parsed entries by a 30-day RSS `published_at` window before persistence.
- raw: New freshness-eligible parsed entries are stored as unscored news.
- scored: The LLM returns `is_ai_news`, `ai_relevance_score`, and final `0-100` AI value `score` for each raw item. `score` reflects impact, originality/information gain, source evidence quality, specificity, and timeliness, with hard caps for non-AI, SEO/advertising, rumor/funding-only, and duplicate-summary content.
- fetched: Selected items receive extracted article content or RSS summary fallback content.
- API/UI status projection: The API derives `ready`, `translated`, or `translation_failed` from `title_zh`, `summary_zh`, `content_zh`, and `has_translate_failed`.
- UI: The frontend displays ready, translated, or translation-failed news according to API `status`, never database internals.

## 6. Project Map（目标目录）

The repository uses one backend app, one frontend app, deterministic fixtures, and local harness reports.

```text
backend/
  app/main.py              # FastAPI entrypoint
  app/api/                 # REST routes and DTO projection only
  app/services/            # Pipeline, scheduler, source, and refresh services
  app/repositories/        # SQLite data access and schema helpers
  app/clients/             # RSS, article, and LLM client interfaces/mocks
  app/core/                # config, clock, logging, errors
  tests/                   # backend unit, contract, API, integration tests
frontend/
  index.html
  src/main.tsx             # React/Vite entrypoint
  src/api/                 # API client and DTO types
  src/pages/               # Home, Article, Sources page units
  src/components/          # Final UI units listed in docs/03_ui_spec.md
  src/styles/              # CSS variables and page/component styles
  tests/                   # frontend integration and DOM snapshot tests
fixtures/
  rss/
  articles/
  llm/
  sources/
  clock/
schemas/
  test_report.schema.json
  stop_decision.schema.json
  task_plan_report.schema.json
  review_report.schema.json
  fix_optimize_report.schema.json
  round_summary_report.schema.json
  tasks.schema.json
  prd_coverage.schema.json
  task_acceptance_coverage.schema.json
  local_user_acceptance.schema.json
scripts/
  run_harness.py             # Thin workflow command adapter
  harness/                   # Harness executor, report, catalog, acceptance and observability internals
  harness_inspect.py         # Local diagnostic query CLI over structured harness evidence
reports/
  stages/
  tasks/
  acceptance/
  observability/
```

Existing legacy root files may remain only until their owning bootstrap task removes them from the active MVP runtime. New MVP runtime code must land in the target directories above.

## 7. Ownership And Import Boundaries（所有权和导入边界）

- `backend/app/main.py` wires FastAPI routes and startup hooks; it must not contain pipeline business logic.
- `backend/app/api/` validates request inputs, calls services, and returns DTOs from `docs/05_api_contract.md`; it must not return DB rows directly.
- `backend/app/services/` owns refresh orchestration, live RSS freshness filtering, and pipeline steps. Only pipeline services may write `news_item.pipeline_state` or compute `is_selected`.
- `backend/app/repositories/` owns SQL, SQLite schema creation, indexes, constraints, and seed helpers. Other backend modules access SQLite through repositories or database helpers.
- `backend/app/clients/` owns external-boundary interfaces and local fixture/mock clients. Tests and harness runs must bind fixture/mock clients, never live RSS, live webpages, or live LLM; the long-running local acceptance service must bind live RSS, live webpage fetch and live LLM clients through environment or `.env` configuration.
- `backend/app/core/clock` owns business time. Scheduler, ranking windows, timestamps, and tests must use injected clock values.
- `frontend/src/api/` is the only frontend layer allowed to know endpoint paths. UI pages and components consume API DTOs only.
- `frontend/src/pages/` may load data and compose final UI units, but must not derive pipeline state or map database fields.
- `frontend/src/components/` contains only the final units listed in `docs/03_ui_spec.md`; component sub-units such as `NewsCardHeader` are out of scope.
- `schemas/` defines machine-checkable report and task contracts consumed by the harness; schema changes must be reflected in `docs/07_test_spec.md` or `workflows.md`.
- `scripts/run_harness.py` owns only the workflow command adapter. Harness implementation modules under `scripts/harness/` may create internal observability artifacts for diagnosis, but those artifacts do not define new completion rules.
- `reports/stages/` contains product-stage evidence for `static` through `e2e` only. Acceptance gate evidence lives under `reports/acceptance/` and must not be treated as a product-stage substitute.
- `reports/observability/` contains local harness diagnostic events, metrics, traces and indexes derived from structured reports. It is not a product public API and must not expose `processing_log` through `/api/*`.
