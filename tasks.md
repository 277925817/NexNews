meta:
  version: tasks_mvp@v6
  mode: dag_execution
  purpose: "stable executable MVP product task system"
  architecture: "single FastAPI app + React/Vite SPA + SQLite"
  execution_loop: "plan -> implement -> test -> fix -> review"
  task_policy:
    definition_only: true
    runner_external: true
    no_task_run_verify_fix_dsl: true
    no_report_per_task: true
  reports:
    scope: "stage_level"
    format: "stage + result + failing_area"
    stages:
      - static_unit
      - pipeline_test
      - api_test
      - ui_test
      - integration_test
      - acceptance
  gates:
    - id: G1
      name: "pipeline correctness"
      maps_to: ["ACC-STOP-002", "ACC-STOP-003", "ACC-STOP-005", "ACC-STOP-007", "ACC-STOP-008"]
    - id: G2
      name: "api correctness"
      maps_to: ["ACC-STOP-004"]
    - id: G3
      name: "ui correctness"
      maps_to: ["ACC-STOP-006"]
    - id: G4
      name: "no leak and no forbidden fields"
      maps_to: ["ACC-STOP-001", "ACC-STOP-009", "ACC-STOP-010"]
  stop_condition: "G1, G2, G3, G4 pass and docs/08_acceptance.md STOP_ALLOWED = true"
  retry_policy:
    max_retry: 2
    fallback: "record failing_area + isolate owner task + retry"

dag:
  nodes:
    - id: TASK-001
      name: "Repo runtime skeleton"
      layer: "L0: Bootstrap"
      type: ["setup"]
      status: "pending"
      source: ["docs/02_arch.md", "docs/06_dev_rules.md"]
      acceptance_gate: ["ACC-STOP-008", "ACC-STOP-010"]
      priority: "refactor_tasks"
      test_scope: ["static"]
      depends_on: []
      description: "Create only the minimal repository structure and runnable app shells for FastAPI backend and React/Vite frontend."
      inputs:
        - "FastAPI backend requirement."
        - "React/Vite frontend requirement."
      outputs:
        - "Backend entrypoint imports without side effects."
        - "Frontend entrypoint exists and can be loaded by Vite."
      acceptance_criteria:
        - "backend entrypoint exists."
        - "frontend entrypoint exists."
        - "static_unit stage result = pass for repo runtime skeleton."
      failure_criteria:
        - "FAIL if this task implements DB schema, fixtures, product pipeline, API behavior, or UI screens."

    - id: TASK-002A
      name: "DB schema constraints"
      layer: "Data Layer"
      type: ["data"]
      status: "pending"
      source: ["docs/04_data_model.md", "docs/06_dev_rules.md"]
      acceptance_gate: ["ACC-STOP-002", "ACC-STOP-005"]
      priority: "data_model_violations"
      test_scope: ["static", "unit"]
      depends_on: ["TASK-001"]
      description: "Create only the SQLite MVP schema, constraints, and indexes."
      inputs:
        - "SQLite table contract from docs/04_data_model.md."
      outputs:
        - "SQLite tables: source, news_item, processing_log."
        - "SQLite constraints and indexes required by the data model."
      acceptance_criteria:
        - "Application table set equals source, news_item, processing_log."
        - "source.rss_url and news_item.canonical_url are UNIQUE."
        - "news_item.pipeline_state accepts only raw, scored, fetched."
        - "processing_log enforces exactly one owner: source_id or news_item_id."
        - "static_unit stage result = pass for DB schema constraints."
      failure_criteria:
        - "FAIL if this task implements DB init hook, seed logic, fixtures, mocks, pipeline behavior, API behavior, or UI screens."
        - "FAIL if excluded tables or fields exist: rss_source, news_task, translation_status, content_source, title_domain_hash, is_ready, display_mode, category table."

    - id: TASK-002B
      name: "DB init hook and seed"
      layer: "Data Layer"
      type: ["data"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/04_data_model.md", "docs/06_dev_rules.md"]
      acceptance_gate: ["ACC-STOP-002", "ACC-STOP-005"]
      priority: "data_model_violations"
      test_scope: ["unit"]
      depends_on: ["TASK-002A"]
      description: "Create the SQLite init hook and idempotent default RSS source seed only."
      inputs:
        - "DB schema from TASK-002A."
        - "Default RSS source list from docs/01_prd.md."
      outputs:
        - "DB init hook can initialize an empty SQLite database."
        - "Exactly 7 default sources are seeded once."
      acceptance_criteria:
        - "Init hook creates the schema from TASK-002A in an empty SQLite database."
        - "Default source seed count is 7 on first init and unchanged on second init."
        - "Seed rows satisfy source table constraints."
        - "static_unit stage result = pass for DB init hook and seed."
      failure_criteria:
        - "FAIL if this task changes schema design, constraints, indexes, fixtures, mocks, pipeline behavior, API behavior, or UI screens."

    - id: TASK-003
      name: "Local config fixtures mocks"
      layer: "Data Layer"
      type: ["setup", "test"]
      status: "pending"
      source: ["docs/06_dev_rules.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-008"]
      priority: "test_failures"
      test_scope: ["static", "unit"]
      depends_on: ["TASK-001"]
      description: "Create local development config and fixture/mock inputs without adding product behavior."
      inputs:
        - "RSS, article HTML, LLM scoring, LLM translation, source, and fixed-clock fixture requirements."
      outputs:
        - "Local dev config points to SQLite and fixture/mock providers."
        - "Fixture RSS, article HTML, LLM scoring, LLM translation, source, and fixed clock data exist."
        - "Tests can run without live RSS, live webpage, live LLM, production DB, or current system time."
      acceptance_criteria:
        - "Fixture set includes RSS success/failure/duplicate cases."
        - "Mock set includes scoring valid/invalid/timeout cases."
        - "Mock set includes translation valid/invalid/timeout/partial cases."
        - "Fixed clock includes 09:00, 18:00, and non-trigger cases."
        - "static_unit stage result = pass for local config fixtures mocks."
      failure_criteria:
        - "FAIL if this task implements DB schema, pipeline business logic, API behavior, or UI screens."

    - id: TASK-004
      name: "RSS ingest"
      layer: "Pipeline Layer"
      type: ["backend", "data"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/04_data_model.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-003", "ACC-STOP-005", "ACC-STOP-008"]
      priority: "acceptance_gate_failures"
      test_scope: ["unit", "integration"]
      depends_on: ["TASK-002B", "TASK-003"]
      description: "Read enabled RSS sources from fixture-backed clients, parse items, normalize links, and store new raw news items."
      inputs:
        - "Enabled source records."
        - "RSS fixtures with success, malformed feed, duplicate link, and missing summary cases."
      outputs:
        - "New RSS items stored as news_item rows with pipeline_state = raw."
        - "canonical_url is populated for dedupe."
        - "processing_log(stage = crawl) records source success/failure."
      acceptance_criteria:
        - "Fixture with 2 RSS items produces 2 normalized input objects."
        - "Only is_enabled = 1 sources are ingested."
        - "Malformed/failing source writes processing_log success = 0 and does not block other sources."
        - "pipeline_test stage result = pass for ingest."
      failure_criteria:
        - "FAIL if ingest calls live RSS URLs or writes scored/fetched state."

    - id: TASK-005
      name: "Score news"
      layer: "Pipeline Layer"
      type: ["backend"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/04_data_model.md", "docs/06_dev_rules.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-003", "ACC-STOP-005", "ACC-STOP-007", "ACC-STOP-008"]
      priority: "acceptance_gate_failures"
      test_scope: ["unit", "integration"]
      depends_on: ["TASK-004"]
      description: "Score raw news with mock LLM JSON, validate score output, and transition raw items to scored."
      inputs:
        - "raw news_item rows."
        - "Mock scoring responses for valid, invalid JSON, timeout, missing title, and missing URL cases."
      outputs:
        - "Valid raw items receive score and pipeline_state = scored."
        - "Invalid scoring output does not advance as a successful score."
        - "processing_log(stage = score) records success/failure."
      acceptance_criteria:
        - "Scoring request contains title, summary, source, published_at, original_link."
        - "Valid score is numeric and within 0-100."
        - "Missing title or original_link scores 0."
        - "Invalid scoring JSON retries at most 2 times."
        - "pipeline_test stage result = pass for score."
      failure_criteria:
        - "FAIL if tests call live LLM or scoring writes fetched state."

    - id: TASK-006
      name: "Filter and dedupe"
      layer: "Pipeline Layer"
      type: ["backend", "data"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/04_data_model.md", "docs/06_dev_rules.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-003", "ACC-STOP-005"]
      priority: "acceptance_gate_failures"
      test_scope: ["unit", "integration"]
      depends_on: ["TASK-005"]
      description: "Apply score threshold filtering and canonical_url dedupe, producing the selected set for content fetch."
      inputs:
        - "scored news_item rows."
        - "Threshold config with default value 60."
      outputs:
        - "is_selected is computed from score immediately after scoring."
        - "Selected query returns score >= 60 items only."
        - "Duplicate canonical_url appears once."
      acceptance_criteria:
        - "score = 60 sets is_selected = 1."
        - "score = 59 sets is_selected = 0."
        - "is_selected does not change pipeline_state."
        - "Duplicate canonical_url count in news_item/displayable output <= 1."
        - "pipeline_test stage result = pass for filter."
      failure_criteria:
        - "FAIL if filter uses selected/ready/translated as database pipeline_state."

    - id: TASK-007
      name: "Fetch content"
      layer: "Pipeline Layer"
      type: ["backend", "data"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/04_data_model.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-003", "ACC-STOP-005", "ACC-STOP-008"]
      priority: "acceptance_gate_failures"
      test_scope: ["unit", "integration"]
      depends_on: ["TASK-006"]
      description: "Fetch article content for selected items using article HTML fixtures, with RSS content fallback."
      inputs:
        - "Selected scored news_item rows."
        - "Article HTML fixtures for success, extraction failure, network failure, and empty summary."
      outputs:
        - "Successful extraction writes content_full."
        - "Failed extraction keeps usable content_raw as fallback."
        - "Usable content moves pipeline_state to fetched."
      acceptance_criteria:
        - "Fetch success writes non-empty content_full and pipeline_state = fetched."
        - "Fetch failure with content_raw fallback still reaches fetched."
        - "Fetch failure with no content_raw is not displayable."
        - "processing_log(stage = fetch) records success/failure."
        - "pipeline_test stage result = pass for fetch."
      failure_criteria:
        - "FAIL if tests access live webpages or fetch unselected items."

    - id: TASK-008
      name: "Translate content"
      layer: "Pipeline Layer"
      type: ["backend"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/04_data_model.md", "docs/06_dev_rules.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-003", "ACC-STOP-007", "ACC-STOP-009"]
      priority: "acceptance_gate_failures"
      test_scope: ["unit", "integration"]
      depends_on: ["TASK-007"]
      description: "Translate fetched content with mock LLM JSON and persist Chinese fields or translation failure facts."
      inputs:
        - "fetched news_item rows with content_full or content_raw."
        - "Mock translation responses for valid, invalid JSON, timeout, and partial field cases."
      outputs:
        - "Translation success writes title_zh, summary_zh, content_zh."
        - "Translation failure writes no partial zh fields and sets has_translate_failed = 1."
        - "processing_log(stage = translate) records success/failure."
      acceptance_criteria:
        - "Translation request contains original_title, original_summary, original_content, source, score."
        - "Valid translation writes non-empty title_zh, summary_zh, content_zh."
        - "Invalid translation writes 0 zh fields."
        - "Translation does not mutate pipeline_state beyond fetched."
        - "pipeline_test stage result = pass for translate."
      failure_criteria:
        - "FAIL if category_zh is persisted/exposed or tests call live LLM."

    - id: TASK-009
      name: "Pipeline run record"
      layer: "Pipeline Layer"
      type: ["backend", "data"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/04_data_model.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-003", "ACC-STOP-005", "ACC-STOP-008"]
      priority: "acceptance_gate_failures"
      test_scope: ["integration"]
      depends_on: ["TASK-004", "TASK-005", "TASK-006", "TASK-007", "TASK-008"]
      description: "Record pipeline run metadata from pipeline step results; this task does not expose triggers, scheduler, API, or UI."
      inputs:
        - "Pipeline step outcomes from ingest, score, filter, fetch, and translate."
      outputs:
        - "Pipeline run summary facts are available from pipeline-owned records or logs."
      acceptance_criteria:
        - "Run summary includes started_at and finished_at."
        - "Run summary includes source_success_count and source_failure_count."
        - "Run summary includes rss_item_count, new_item_count, scored_item_count, selected_item_count, fetched_item_count, translated_item_count, and failure details."
        - "pipeline_test stage result = pass for pipeline run record."
      failure_criteria:
        - "FAIL if this task implements trigger scheduling, API response shaping, UI behavior, or duplicate pipeline business logic."

    - id: TASK-010
      name: "Refresh trigger signal"
      layer: "Trigger Layer"
      type: ["backend"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/02_arch.md", "docs/05_api_contract.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-003", "ACC-STOP-008"]
      priority: "acceptance_gate_failures"
      test_scope: ["integration"]
      depends_on: ["TASK-003"]
      description: "Emit manual and scheduled refresh signals only; it does not create persistent state, run summaries, or pipeline step logic."
      inputs:
        - "Fixed clock cases for 09:00, 18:00, and non-trigger time."
      outputs:
        - "Manual refresh signal."
        - "Scheduled refresh signal for 09:00 and 18:00 fixed-clock cases."
        - "Concurrent refresh signal rejection state."
      acceptance_criteria:
        - "Manual trigger emits exactly one refresh_requested signal."
        - "09:00 and 18:00 each emit one scheduled refresh signal."
        - "Non-trigger time emits zero scheduled refresh signals."
        - "Concurrent refresh signal does not emit a second refresh_requested signal."
        - "Trigger layer contains no RSS parsing, LLM scoring, filtering, fetching, translation, or run summary aggregation."
        - "Trigger layer MUST NOT produce ANY persistent state."
        - "pipeline_test stage result = pass for refresh trigger signal."
      failure_criteria:
        - "FAIL if trigger layer writes DB rows, processing logs, files, run records, summaries, scheduler state, external worker state, queue state, progress endpoint, live time assertions, or duplicate pipeline logic."

    - id: TASK-011
      name: "API home"
      layer: "API Layer"
      type: ["backend"]
      status: "pending"
      source: ["docs/04_data_model.md", "docs/05_api_contract.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-004", "ACC-STOP-009"]
      priority: "api_contract_failures"
      test_scope: ["contract", "api"]
      depends_on: ["TASK-008"]
      description: "Implement GET /api/home with latest news and 30-day high-score list."
      inputs:
        - "Displayable news rows."
        - "Fixed clock for 30-day window."
      outputs:
        - "HomeData response with latest_news and top_ranked_news."
      acceptance_criteria:
        - "GET /api/home returns 200 with top-level data."
        - "latest_news sorts by published_at DESC."
        - "top_ranked_news length <= 10 and sorts by score DESC, published_at DESC."
        - "Response contains no forbidden internal fields."
        - "api_test stage result = pass for home."
      failure_criteria:
        - "FAIL if API returns raw English body/summary or layout-column metadata."

    - id: TASK-012
      name: "API news detail"
      layer: "API Layer"
      type: ["backend"]
      status: "pending"
      source: ["docs/04_data_model.md", "docs/05_api_contract.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-004", "ACC-STOP-009"]
      priority: "api_contract_failures"
      test_scope: ["contract", "api"]
      depends_on: ["TASK-008"]
      description: "Implement GET /api/news/{id} with translated detail and safe non-translated states."
      inputs:
        - "Translated, ready, translation_failed, missing, and non-displayable item fixtures."
      outputs:
        - "NewsDetailItem response or structured 404."
      acceptance_criteria:
        - "Translated detail includes content_zh."
        - "ready and translation_failed details omit summary_zh and content_zh."
        - "Missing or non-displayable item returns 404 error envelope."
        - "Response contains no forbidden internal fields."
        - "api_test stage result = pass for news detail."
      failure_criteria:
        - "FAIL if non-translated detail returns raw body, null content_zh, or placeholder content."

    - id: TASK-013
      name: "API sources"
      layer: "API Layer"
      type: ["backend"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/05_api_contract.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-002", "ACC-STOP-004"]
      priority: "api_contract_failures"
      test_scope: ["contract", "api"]
      depends_on: ["TASK-002B", "TASK-003"]
      description: "Implement GET/POST/PATCH/DELETE /api/sources for RSS source management."
      inputs:
        - "Valid, duplicate, empty, invalid, local, private, disable-all, and missing-source cases."
      outputs:
        - "SourceItem list/create/update responses and 204 delete."
      acceptance_criteria:
        - "GET /api/sources returns SourceItem[] sorted by created_at ASC."
        - "POST valid public RSS URL returns 201."
        - "Invalid/local/private/duplicate source requests return stable errors and do not insert rows."
        - "PATCH rejects disabling all sources with 409."
        - "DELETE disables source, returns 204 with no body, and preserves historical news."
        - "api_test stage result = pass for sources."
      failure_criteria:
        - "FAIL if delete physically removes historical news_item rows."

    - id: TASK-014
      name: "API refresh"
      layer: "API Layer"
      type: ["backend"]
      status: "pending"
      source: ["docs/05_api_contract.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-004", "ACC-STOP-009"]
      priority: "api_contract_failures"
      test_scope: ["contract", "api"]
      depends_on: ["TASK-010"]
      description: "Implement POST /api/refresh as the API boundary for manual refresh signal."
      inputs:
        - "Refresh trigger signal."
        - "Concurrent refresh fixture case."
      outputs:
        - "Refresh response with refreshed_at only."
      acceptance_criteria:
        - "POST /api/refresh returns 200 with data.refreshed_at."
        - "Concurrent refresh does not emit a second refresh signal."
        - "Response exposes no task, queue, worker, retry, progress, run summary, processing logs, or internal fields."
        - "api_test stage result = pass for refresh."
      failure_criteria:
        - "FAIL if refresh endpoint exposes run summary, processing logs, progress endpoints, or pipeline internals."

    - id: TASK-015
      name: "UI home"
      layer: "UI Layer"
      type: ["frontend"]
      status: "pending"
      source: ["docs/03_ui_spec.md", "docs/05_api_contract.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-006", "ACC-STOP-009"]
      priority: "ui_failures"
      test_scope: ["integration"]
      depends_on: ["TASK-011", "TASK-014"]
      description: "Implement Home page news feed, high-score list, refresh button, loading, empty, and error states using mocked API client responses."
      inputs:
        - "HomeData mock responses for translated, ready, translation_failed, loading, empty, and error states."
      outputs:
        - "Home page, NewsCard, HighScoreList, status/score/source markers, refresh interaction."
      acceptance_criteria:
        - "Translated card shows Chinese title and summary_zh."
        - "ready and translation_failed cards show original_title/status and render 0 summary_zh/content_zh nodes."
        - "HighScoreList shows <= 10 items and no summaries."
        - "Refresh button disables as 刷新中 and reloads GET /api/home after refresh succeeds."
        - "ui_test stage result = pass for home."
      failure_criteria:
        - "FAIL if Home UI reads database/internal fields or adds unlisted interactions."

    - id: TASK-016
      name: "UI article"
      layer: "UI Layer"
      type: ["frontend"]
      status: "pending"
      source: ["docs/03_ui_spec.md", "docs/05_api_contract.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-006", "ACC-STOP-009"]
      priority: "ui_failures"
      test_scope: ["integration"]
      depends_on: ["TASK-012", "TASK-015"]
      description: "Implement ArticleView for translated reading, ready polling, translation_failed state, original link, and 404."
      inputs:
        - "NewsDetailItem mock responses for translated, ready, translation_failed, and 404."
      outputs:
        - "ArticleView route and safe render states."
      acceptance_criteria:
        - "Translated ArticleView renders title, original_title, source, published_at, score, and content_zh."
        - "ready ArticleView polls detail endpoint and renders no English body."
        - "translation_failed ArticleView renders failure state and original link, with 0 content_zh nodes."
        - "404 renders 新闻不存在或不可展示."
        - "ui_test stage result = pass for article."
      failure_criteria:
        - "FAIL if ArticleView directly jumps to original site instead of internal route."

    - id: TASK-017
      name: "UI sources"
      layer: "UI Layer"
      type: ["frontend"]
      status: "pending"
      source: ["docs/03_ui_spec.md", "docs/05_api_contract.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-002", "ACC-STOP-006"]
      priority: "ui_failures"
      test_scope: ["integration"]
      depends_on: ["TASK-013", "TASK-015"]
      description: "Implement RSS source configuration page using mocked source API responses."
      inputs:
        - "SourceItem list, create success, validation error, duplicate error, delete success, and delete 404 responses."
      outputs:
        - "Source page, SourceForm, and SourceRow."
      acceptance_criteria:
        - "Source list renders all sources."
        - "Empty form disables submit; invalid URL shows inline error."
        - "Create success clears inputs and reloads list."
        - "Delete success visually removes the row."
        - "ui_test stage result = pass for sources."
      failure_criteria:
        - "FAIL if UI exposes advanced settings, task progress, retry controls, or processing logs."

    - id: TASK-018
      name: "Integration pipeline only"
      layer: "Integration Layer"
      type: ["integration", "test"]
      status: "pending"
      source: ["docs/01_prd.md", "docs/02_arch.md", "docs/07_test_spec.md"]
      acceptance_gate: ["ACC-STOP-003", "ACC-STOP-005", "ACC-STOP-007", "ACC-STOP-008"]
      priority: "test_failures"
      test_scope: ["integration"]
      depends_on: ["TASK-008"]
      description: "Run the pipeline-only integration path directly with fixture data; verify DB facts only and do not call trigger layer, API routes, or render UI."
      inputs:
        - "Clean temporary SQLite database."
        - "RSS, article HTML, LLM, source, and fixed-clock fixtures."
      outputs:
        - "Pipeline creates scored, selected, fetched, translated, and translation_failed DB facts."
        - "Partial source/fetch/translation failures remain isolated in DB facts."
      acceptance_criteria:
        - "Full pipeline creates at least 1 displayable DB item."
        - "score = 60 item reaches fetched/translation path; score = 59 item does not."
        - "Duplicate canonical_url appears once in DB displayable query."
        - "processing_log contains DB facts for crawl, score, fetch, and translate success/failure."
        - "No live RSS, live webpage, live LLM, production DB, or current system time is used."
        - "integration_test stage result = pass for pipeline only."
      failure_criteria:
        - "FAIL if pipeline integration asserts API response shape, frontend DOM, trigger behavior, run summary correctness, or manual visual judgment."

    - id: TASK-019
      name: "Integration API only"
      layer: "Integration Layer"
      type: ["integration", "test"]
      status: "pending"
      source: ["docs/05_api_contract.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-004", "ACC-STOP-009"]
      priority: "test_failures"
      test_scope: ["integration"]
      depends_on: ["TASK-011", "TASK-012", "TASK-013", "TASK-014", "TASK-018"]
      description: "Run API integration against pipeline-produced fixture data; verify API responses only and do not render UI."
      inputs:
        - "Pipeline-produced temporary SQLite data from TASK-018 as API fixture input."
        - "API routes from TASK-011 through TASK-014."
      outputs:
        - "GET /api/home exposes displayable data."
        - "GET /api/news/{id} exposes translated detail and safe non-translated states."
        - "Source and refresh endpoints preserve contract behavior."
      acceptance_criteria:
        - "GET /api/home returns at least 1 latest_news item after pipeline integration."
        - "score = 60 item appears through API; score = 59 item does not."
        - "Duplicate canonical_url appears once through API."
        - "Detail API returns content_zh only for translated item."
        - "API JSON contains no forbidden internal fields."
        - "integration_test stage result = pass for API only."
      failure_criteria:
        - "FAIL if API integration asserts frontend DOM, pipeline internals, DB schema details, or manual visual judgment."

    - id: TASK-020
      name: "Integration UI only"
      layer: "Integration Layer"
      type: ["integration", "test"]
      status: "pending"
      source: ["docs/03_ui_spec.md", "docs/05_api_contract.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-006", "ACC-STOP-009"]
      priority: "test_failures"
      test_scope: ["integration"]
      depends_on: ["TASK-015", "TASK-016", "TASK-017", "TASK-019"]
      description: "Run UI integration against API fixture responses; verify rendered DOM only and do not re-run pipeline internals."
      inputs:
        - "API responses from TASK-019 or equivalent mocked API payloads."
        - "UI pages from TASK-015 through TASK-017."
      outputs:
        - "Home renders DOM from API news payloads."
        - "Article renders DOM for translated, ready, failed, and 404 payloads."
        - "Sources page renders DOM for source UI states."
      acceptance_criteria:
        - "Home renders at least 1 latest_news item from API payload."
        - "ready and translation_failed UI render no summary_zh/content_zh nodes."
        - "ArticleView renders content_zh only for translated detail."
        - "Source UI create/delete states work against API payloads."
        - "Rendered DOM contains no forbidden internal fields."
        - "integration_test stage result = pass for UI only."
      failure_criteria:
        - "FAIL if UI integration asserts DB state, API implementation internals, pipeline internals, or manual visual judgment."

    - id: TASK-021
      name: "MVP acceptance"
      layer: "Acceptance Layer"
      type: ["test"]
      status: "pending"
      source: ["workflows.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate:
        - "ACC-STOP-001"
        - "ACC-STOP-002"
        - "ACC-STOP-003"
        - "ACC-STOP-004"
        - "ACC-STOP-005"
        - "ACC-STOP-006"
        - "ACC-STOP-007"
        - "ACC-STOP-008"
        - "ACC-STOP-009"
        - "ACC-STOP-010"
      priority: "acceptance_gate_failures"
      test_scope: ["acceptance"]
      depends_on: ["TASK-009", "TASK-018", "TASK-019", "TASK-020"]
      description: "Evaluate four MVP gates from stage-level evidence and map them to docs/08_acceptance.md STOP_ALLOWED."
      inputs:
        - "Stage-level results: static_unit, pipeline_test, api_test, ui_test, integration_test."
        - "Gate mapping from meta.gates."
      outputs:
        - "G1-G4 pass/fail."
        - "ACC-STOP-001 through ACC-STOP-010 mapped pass/fail."
        - "STOP_ALLOWED value."
      acceptance_criteria:
        - "G1 pipeline correctness = pass."
        - "G2 api correctness = pass."
        - "G3 ui correctness = pass."
        - "G4 no leak and no forbidden fields = pass."
        - "STOP_ALLOWED = true."
      failure_criteria:
        - "FAIL if acceptance introduces new product or CI infrastructure tasks instead of reopening the owning failed task."
