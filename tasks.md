meta:
  version: tasks_mvp@v8
  mode: dag_execution
  purpose: "stable executable MVP product task system"
  architecture: "single FastAPI app + React/Vite SPA + SQLite"
  execution_loop: "plan -> implement -> test -> review -> fix_optimize -> summarize -> iterate"
  task_policy:
    definition_only: false
    runner_external: false
    no_task_run_verify_fix_dsl: true
    reports_required_for_gate_tasks: true
    task_state_fields: "workflow defaults from workflows.md#minimal-task-record-format"
    plan_report: "PLAN writes reports/tasks/<task_id>/plan.json and records the path in dag.nodes[*].plan_report"
    dag_node_record: "dag.nodes[*] is the canonical workflow task record; array source/acceptance_gate/test_scope fields are normalized by workflows.md"
  reports:
    scope: "task_and_stage_level"
    format: "docs/07_test_spec.md#6 TestReport"
    stages:
      - static
      - unit
      - contract
      - api
      - integration
      - replay
      - snapshot
      - e2e
      - acceptance
    product_stages:
      - static
      - unit
      - contract
      - api
      - integration
      - replay
      - snapshot
      - e2e
    gate_stage: "acceptance"
    acceptance_report_policy: "acceptance writes reports/acceptance/ACC-STOP-*.json, coverage reports, and STOP_ALLOWED.json; it is not a product-stage report and cannot replace reports/stages/<stage>.json evidence"
  gates:
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
  stop_condition: "every dag.nodes[*].status is passed, ACC-STOP-001 through ACC-STOP-010 are PASS, task_completion_status/prd_coverage_status/task_acceptance_coverage_status/browser_e2e_status/local_user_acceptance_status are PASS, and docs/08_acceptance.md STOP_ALLOWED = true"
  retry_policy:
    max_retry: 3
    fallback: "record failing_area + isolate owner task + retry"

dag:
  nodes:
    - id: TASK-000
      name: "Workflow executor contract repair"
      layer: "L0: Bootstrap"
      type: ["docs", "test"]
      status: "passed"
      source: ["workflows.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-008", "ACC-STOP-010"]
      priority: "acceptance_gate_failures"
      test_scope: ["static"]
      active_state: "none"
      last_updated_state: "SUMMARIZE"
      evidence: "reports/tasks/TASK-000/static.json"
      test_report: "reports/tasks/TASK-000/static.json"
      plan_report: "reports/tasks/TASK-000/plan.json"
      summary_report: "reports/tasks/TASK-000/summary.json"
      depends_on: []
      description: "Repair the document-level workflow executor contract and create the first runnable local command surface before product implementation begins."
      inputs:
        - "Workflow command surface from workflows.md."
        - "TestReport contract from docs/07_test_spec.md#6."
        - "Stop gate contract from docs/08_acceptance.md."
      outputs:
        - "scripts/run_harness.py accepts every workflow stage command."
        - "Stage and gate reports have deterministic structured failure output before product tests exist."
        - "schemas/test_report.schema.json, schemas/stop_decision.schema.json, schemas/task_plan_report.schema.json, schemas/review_report.schema.json, schemas/fix_optimize_report.schema.json, schemas/round_summary_report.schema.json, schemas/tasks.schema.json, schemas/prd_coverage.schema.json, schemas/task_acceptance_coverage.schema.json, and schemas/local_user_acceptance.schema.json exist."
        - "STOP_ALLOWED report has a documented stop-decision shape."
      acceptance_criteria:
        - "Every workflow stage command writes a machine-readable report to the documented report paths."
        - "TASK-000 static validation checks schema files are parseable and validates tasks.md plus generated stage reports against those schemas."
        - "Non-acceptance stages without implemented assertions fail with structured TestReport evidence, not missing files or free-form logs."
        - "Acceptance evaluates missing or failed stage reports as failed gates and writes STOP_ALLOWED = false."
        - "static stage result = pass for harness contract repair."
      failure_criteria:
        - "FAIL if this task implements product DB schema, pipeline behavior, API behavior, UI screens, external CI, or live dependency access."

    - id: TASK-001
      name: "Repo runtime skeleton"
      layer: "L0: Bootstrap"
      type: ["setup"]
      status: "pending"
      source: ["docs/02_arch.md", "docs/06_dev_rules.md"]
      acceptance_gate: ["ACC-STOP-008", "ACC-STOP-010"]
      priority: "refactor_tasks"
      test_scope: ["static"]
      depends_on: ["TASK-000"]
      description: "Replace legacy Flask/static-page traction with only the minimal repository structure and runnable app shells for FastAPI backend and React/Vite frontend."
      inputs:
        - "FastAPI backend requirement."
        - "React/Vite frontend requirement."
      outputs:
        - "Backend entrypoint imports without side effects."
        - "Frontend entrypoint exists and can be loaded by Vite."
        - "Legacy root static page and Flask dependency no longer define the MVP runtime."
      acceptance_criteria:
        - "backend entrypoint exists."
        - "frontend entrypoint exists."
        - "static stage result = pass for repo runtime skeleton."
      failure_criteria:
        - "FAIL if this task implements DB schema, fixtures, product pipeline, API behavior, or UI screens."
        - "FAIL if Flask or the legacy root index.html remains the active MVP runtime surface."

    - id: TASK-002A
      name: "DB schema constraints"
      layer: "Data Layer"
      type: ["data"]
      status: "pending"
      source: ["docs/04_data_model.md", "docs/06_dev_rules.md"]
      acceptance_gate: ["ACC-STOP-002", "ACC-STOP-005"]
      priority: "data_model_violations"
      test_scope: ["static", "unit"]
      depends_on: ["TASK-021"]
      description: "Create only the SQLite MVP schema, constraints, and indexes."
      inputs:
        - "SQLite table contract from docs/04_data_model.md."
      outputs:
        - "SQLite tables: source, news_item, processing_log."
        - "SQLite constraints and indexes required by the data model."
      acceptance_criteria:
        - "Application table set equals source, news_item, processing_log."
        - "source.rss_url and news_item.canonical_url are UNIQUE."
        - "source.deleted_at exists as nullable soft-delete tombstone."
        - "news_item.pipeline_state accepts only raw, scored, fetched."
        - "processing_log enforces exactly one owner: source_id or news_item_id."
        - "processing_log is a required core table; crawl rows require source_id and score/fetch/translate rows require news_item_id."
        - "static stage result = pass for DB schema constraints."
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
        - "Default source seed URL set exactly matches the 7 URLs listed in docs/01_prd.md."
        - "Seed rows satisfy source table constraints."
        - "static stage result = pass for DB init hook and seed."
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
        - "Harness stage commands consume fixture/mock inputs created by this task."
        - "Tests can run without live RSS, live webpage, live LLM, production DB, or current system time."
      acceptance_criteria:
        - "Fixture set includes RSS success/failure/duplicate cases."
        - "Mock set includes scoring valid/invalid/timeout cases."
        - "Mock set includes translation valid/invalid/timeout/partial cases."
        - "Fixed clock includes 09:00, 18:00, and non-trigger cases."
        - "Fixture and mock versions are present in harness reports."
        - "static stage result = pass for local config fixtures mocks."
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
        - "integration stage result = pass for ingest."
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
        - "Scoring response requires numeric score within 0-100 and non-empty reason."
        - "Missing title or original_link scores 0."
        - "Missing summary keeps the summary field present and applies the documented 20 point score penalty."
        - "Invalid scoring JSON retries at most 2 times."
        - "integration stage result = pass for score."
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
        - "Distinct high-score items with different canonical_url or different domains remain separate fetch candidates."
        - "integration stage result = pass for filter."
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
        - "integration stage result = pass for fetch."
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
        - "When content_full is unavailable but content_raw/RSS fallback is available, translation writes non-empty summary_zh and content_zh for the fallback item."
        - "Invalid translation writes 0 zh fields."
        - "Translation does not mutate pipeline_state beyond fetched."
        - "integration stage result = pass for translate."
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
        - "integration stage result = pass for pipeline run record."
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
      depends_on: ["TASK-009"]
      description: "Coordinate manual and scheduled refresh execution using fixed-clock triggers and concurrency guards; the refresh path runs the complete MVP pipeline through existing pipeline services."
      inputs:
        - "Fixed clock cases for 09:00, 18:00, and non-trigger time."
      outputs:
        - "Manual refresh execution request."
        - "Scheduled refresh execution for 09:00 and 18:00 fixed-clock cases."
        - "Concurrent refresh rejection state."
      acceptance_criteria:
        - "Manual trigger executes exactly one complete refresh flow."
        - "09:00 and 18:00 each execute one scheduled refresh flow under fixed clock."
        - "Non-trigger time executes zero scheduled refresh flows."
        - "Concurrent refresh does not start a second pipeline run."
        - "Trigger layer delegates RSS parsing, LLM scoring, filtering, fetching, translation, and run summary facts to pipeline services."
        - "Trigger layer writes no extra task/queue/progress state beyond required processing_log evidence."
        - "integration stage result = pass for refresh trigger signal."
      failure_criteria:
        - "FAIL if trigger layer implements duplicate pipeline logic, exposes task/queue/progress state, or uses live time assertions."

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
        - "latest_news proves the PRD fixture density and is not satisfied by a 1-3 item smoke sample."
        - "top_ranked_news length <= 10, contains only displayable news from the latest 30-day window, excludes older items, and sorts by score DESC, published_at DESC."
        - "Response contains no forbidden internal fields."
        - "api stage result = pass for home."
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
        - "api stage result = pass for news detail."
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
        - "Valid, duplicate, duplicate-deleted, empty, invalid, local, private, disable-all, missing-source, and deleted-source cases."
      outputs:
        - "SourceItem list/create/update responses and 204 delete."
      acceptance_criteria:
        - "GET /api/sources returns only non-deleted SourceItem[] sorted by created_at ASC."
        - "POST valid public RSS URL returns 201."
        - "Invalid/local/private/duplicate source requests return stable errors and do not insert rows, including duplicate URLs from deleted tombstones."
        - "Default seeded sources and user-created sources have identical GET/PATCH/DELETE behavior, including enable, disable, soft delete, duplicate tombstone rejection, and last-enabled-source protection."
        - "Default seeded sources that are deleted or disabled are not automatically restored by init, refresh, or list reload unless an explicit reset feature is later documented."
        - "PATCH rejects disabling the last non-deleted enabled source with 409 and returns 404 for deleted sources."
        - "DELETE soft-deletes source with is_enabled = 0 and deleted_at, returns 204 with no body, hides the source from GET /api/sources, and preserves historical news."
        - "api stage result = pass for sources."
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
      description: "Implement POST /api/refresh as the API boundary for the complete manual refresh flow."
      inputs:
        - "Complete refresh flow from TASK-010."
        - "Concurrent refresh fixture case."
      outputs:
        - "Refresh response with refreshed_at only, where refreshed_at may be string or null."
      acceptance_criteria:
        - "POST /api/refresh returns 200 with data.refreshed_at as string after completion or null for concurrent rejection before any successful refresh."
        - "Concurrent refresh does not start a second pipeline run."
        - "Response exposes no task, queue, worker, retry, progress, run summary, processing logs, or internal fields."
        - "api stage result = pass for refresh."
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
        - "Translated NewsCard summary renders as text content only; raw HTML tags from RSS or translated fixtures are escaped or absent from the DOM tree."
        - "ready and translation_failed cards show original_title/status and render 0 summary_zh/content_zh nodes."
        - "Home renders the PRD fixture news density and cannot pass with only 1-3 visible news cards when fixture data contains at least 10 displayable items."
        - "HighScoreList shows up to 10 latest-30-day eligible items sorted by score DESC, published_at DESC, excludes older items, and renders no summaries."
        - "HighScoreList item click opens the matching ArticleView route."
        - "Source colors or markers are stable and distinguish different sources in the fixture."
        - "Refresh button disables as 刷新中 and reloads GET /api/home after refresh succeeds."
        - "integration stage result = pass for home."
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
      description: "Implement ArticleView for translated reading, ready polling, translation_failed state, original_url link, and 404."
      inputs:
        - "NewsDetailItem mock responses for translated, ready, translation_failed, and 404."
      outputs:
        - "ArticleView route and safe render states."
      acceptance_criteria:
        - "Translated ArticleView renders title, original_title, source, published_at, score, and content_zh."
        - "Translated ArticleView renders an original_url link or button without using it as the card/list navigation target."
        - "ready ArticleView polls detail endpoint and renders no English body."
        - "translation_failed ArticleView renders failure state and original_url link, with 0 content_zh nodes."
        - "ArticleView navigation from NewsCard and HighScoreList stays on the internal route and never directly jumps to the original site."
        - "404 renders 新闻不存在或不可展示."
        - "integration stage result = pass for article."
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
        - "SourceItem list, create success, validation error, duplicate error, duplicate-deleted error, enable/disable success, disable-all error, delete success, and delete 404 responses."
      outputs:
        - "Source page, SourceForm, and SourceRow with enable/disable/delete controls."
      acceptance_criteria:
        - "Source list renders all non-deleted sources."
        - "Empty form disables submit; invalid URL shows inline error."
        - "Create success clears inputs and reloads list."
        - "Enable/disable success updates row state."
        - "Disabling the last enabled source shows structured API error."
        - "Delete success visually removes the row."
        - "Default seeded sources and user-created sources render the same enable, disable, and delete controls, and deleted or disabled seeded sources do not visually reappear after reload unless reset is explicitly documented."
        - "integration stage result = pass for sources."
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
        - "Pipeline creates scored/fetched pipeline_state facts, is_selected facts, Chinese translation field facts, and has_translate_failed failure facts."
        - "Partial source/fetch/translation failures remain isolated in DB facts."
      acceptance_criteria:
        - "Full pipeline creates at least 1 displayable DB item."
        - "score = 60 item reaches fetched/translation path; score = 59 item does not."
        - "Duplicate canonical_url appears once in DB displayable query."
        - "processing_log contains DB facts for crawl, score, fetch, and translate success/failure."
        - "No live RSS, live webpage, live LLM, production DB, or current system time is used."
        - "integration stage result = pass for pipeline only."
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
        - "GET /api/home returns the PRD fixture density after pipeline integration; a 1-3 item smoke sample is not sufficient when fixture data contains at least 10 displayable items."
        - "score = 60 item appears through API; score = 59 item does not."
        - "top_ranked_news returns 10 items when the latest-30-day fixture has at least 10 eligible items, excludes 30-day-window-outside items, and applies score DESC plus published_at DESC tie-break ordering."
        - "Duplicate canonical_url appears once through API."
        - "Detail API returns content_zh only for translated item."
        - "API JSON contains no forbidden internal fields."
        - "integration stage result = pass for API only."
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
        - "Home renders the PRD fixture news density from API payload and cannot pass with only 1-3 visible news cards when at least 10 displayable items are supplied."
        - "HighScoreList renders 10 latest-30-day eligible items when supplied, excludes older items, preserves score/published_at ordering, and click-through opens matching ArticleView."
        - "NewsCard summary DOM renders fixture strings containing HTML tags as safe text, not parsed markup."
        - "ready and translation_failed UI render no summary_zh/content_zh nodes."
        - "ArticleView renders content_zh only for translated detail."
        - "Source UI create/delete states work against API payloads."
        - "Rendered DOM contains no forbidden internal fields."
        - "integration stage result = pass for UI only."
      failure_criteria:
        - "FAIL if UI integration asserts DB state, API implementation internals, pipeline internals, or manual visual judgment."

    - id: TASK-021
      name: "Acceptance evaluator implementation"
      layer: "Acceptance Layer"
      type: ["test"]
      status: "pending"
      source: ["workflows.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-008", "ACC-STOP-010"]
      priority: "test_failures"
      test_scope: ["static", "unit"]
      depends_on: ["TASK-000", "TASK-003"]
      description: "Implement and test the local acceptance evaluator without making it the final stop gate task."
      inputs:
        - "Gate mapping from docs/08_acceptance.md."
        - "Workflow command and report paths from workflows.md."
        - "Schema files from schemas/."
      outputs:
        - "scripts/run_harness.py --stage acceptance --report-dir reports reads existing full-stage reports and emits ACC-STOP reports plus STOP_ALLOWED.json."
        - "Task-scoped acceptance command fails with structured TestReport evidence."
        - "Acceptance evaluator never creates, replaces, or skips required product stage reports."
      acceptance_criteria:
        - "Acceptance evaluator validates TestReport and StopDecisionReport schema files."
        - "Acceptance evaluator validates docs/07_test_spec.md#2.16 mandatory assertion catalog coverage from full-stage and ACC-STOP reports."
        - "Acceptance evaluator validates the traceability matrix mapping assertion_id -> gate -> owner task -> stage -> expected report path."
        - "Acceptance command without --task-id consumes reports/stages/static.json through reports/stages/e2e.json."
        - "Acceptance command with --task-id fails as an invalid task-scoped gate evaluation."
        - "Acceptance evaluator enforces live dependency scan, forbidden field scan, non-goal endpoint/UI scan, wrong-stage/conflicting mandatory assertion detection, and skipped-stage stop failure."
        - "Acceptance evaluator enforces every tasks.md dag node has status passed before task_completion_status can be PASS."
        - "Acceptance evaluator enforces PRD coverage, task acceptance coverage, browser E2E evidence, and local user acceptance as machine-checkable stop inputs."
        - "STOP_ALLOWED can become true only when workflow ACCEPTANCE runs full gate evaluation, all required gates are PASS, all tasks are passed, every task acceptance criterion has executed passing evidence, and all stop inputs are PASS."
      failure_criteria:
        - "FAIL if TASK-021 claims product behavior gate coverage for ACC-STOP-002 through ACC-STOP-007 or ACC-STOP-009."
        - "FAIL if TASK-021 accepts missing, skipped, flaky, wrong-stage, conflict-duplicated, or task-scoped-only mandatory assertion IDs."
        - "FAIL if TASK-021 runs product stages, synthesizes passing stage reports, or makes task-scoped acceptance valid."

    - id: TASK-022
      name: "Replay deterministic stage"
      layer: "Verification Layer"
      type: ["test"]
      status: "pending"
      source: ["workflows.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-008"]
      priority: "test_failures"
      test_scope: ["replay"]
      depends_on: ["TASK-018"]
      description: "Implement the replay stage owner that proves fixture, mock, seed, and fixed-clock pipeline outputs are deterministic across repeated runs."
      inputs:
        - "Pipeline-only integration path from TASK-018."
        - "Fixture, mock, seed, and fixed-clock versions."
      outputs:
        - "reports/tasks/TASK-022/replay.json with TestReport v2 evidence."
        - "Replay stage owner logic that TASK-025 can run later without --task-id to produce reports/stages/replay.json."
        - "Replay evidence contains matching data_hash values for repeated deterministic runs."
      acceptance_criteria:
        - "Replay runs the same fixture/mock/clock inputs at least twice from clean isolated state."
        - "Replay output hashes match exactly."
        - "Replay report contains referenced_files, data_hash, artifact_paths, and assertion visibility."
        - "replay stage result = pass for deterministic replay."
      failure_criteria:
        - "FAIL if replay uses real time, live RSS, live webpages, live LLM, production DB, or manual judgment."

    - id: TASK-023
      name: "Snapshot regression stage"
      layer: "Verification Layer"
      type: ["test"]
      status: "pending"
      source: ["workflows.md", "docs/03_ui_spec.md", "docs/05_api_contract.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-004", "ACC-STOP-006", "ACC-STOP-008", "ACC-STOP-009"]
      priority: "test_failures"
      test_scope: ["snapshot"]
      depends_on: ["TASK-019", "TASK-020"]
      description: "Implement the snapshot stage owner for API JSON, DB schema, public schema, and React DOM regression artifacts."
      inputs:
        - "API integration responses from TASK-019."
        - "UI integration render states from TASK-020."
        - "DB schema and public API/schema artifacts."
      outputs:
        - "reports/tasks/TASK-023/snapshot.json with TestReport v2 evidence."
        - "Snapshot stage owner logic that TASK-025 can run later without --task-id to produce reports/stages/snapshot.json."
        - "Snapshot artifacts bound to fixture/mock/data versions."
      acceptance_criteria:
        - "GET /api/home, GET /api/news/{id}, DB schema, public API/schema, and key React DOM snapshots are compared."
        - "Snapshot diffs are empty unless the task scope explicitly includes snapshot updates and matching contract documents changed."
        - "Snapshot report contains referenced_files, data_hash, artifact_paths, and assertion visibility."
        - "snapshot stage result = pass for regression snapshots."
      failure_criteria:
        - "FAIL if snapshot approval depends on manual judgment, hidden local files, live data, or untracked fixture changes."

    - id: TASK-024
      name: "E2E deterministic stage"
      layer: "Verification Layer"
      type: ["test"]
      status: "pending"
      source: ["workflows.md", "docs/01_prd.md", "docs/03_ui_spec.md", "docs/05_api_contract.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-003", "ACC-STOP-004", "ACC-STOP-006", "ACC-STOP-008", "ACC-STOP-009"]
      priority: "test_failures"
      test_scope: ["e2e"]
      depends_on: ["TASK-022", "TASK-023"]
      description: "Implement the deterministic end-to-end stage from clean SQLite database through refresh, API projection, and UI render."
      inputs:
        - "Clean temporary SQLite database."
        - "Fixture RSS, article HTML, LLM mocks, source fixtures, fixed clock, replay proof, and snapshots."
      outputs:
        - "reports/tasks/TASK-024/e2e.json with TestReport v2 evidence."
        - "E2E stage owner logic that TASK-025 can run later without --task-id to produce reports/stages/e2e.json."
        - "End-to-end evidence for full pipeline, API output, UI render, isolation, and leak scan."
      acceptance_criteria:
        - "E2E run loads fixtures, executes full pipeline, verifies API output, and verifies UI render from clean isolated state."
        - "E2E run uses a real browser or equivalent DOM-capable runner to verify homepage news feed, 30-day high-score list, news detail, and sources management."
        - "Browser E2E proves Home News Feed fixture density, HighScoreList 30-day ranking, NewsCard summary text-only rendering, NewsCard click-through, HighScoreList click-through, ArticleView translated/ready/translation_failed/404 states, ArticleView original_url button, no direct original-site navigation from cards or rank items, Sources create/disable/delete flows, default source CRUD parity, and refresh POST /api/refresh then GET /api/home behavior."
        - "E2E run emits no live dependency access and no forbidden public-surface fields."
        - "E2E report contains referenced_files, data_hash, artifact_paths, and assertion visibility."
        - "e2e stage result = pass for deterministic full run."
      failure_criteria:
        - "FAIL if e2e relies on current system time, live network, production DB, manual screenshots, or replaces replay/snapshot stage evidence."

    - id: TASK-025
      name: "Full stage report materialization"
      layer: "Verification Layer"
      type: ["test"]
      status: "pending"
      source: ["workflows.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-008", "ACC-STOP-010"]
      priority: "test_failures"
      test_scope: ["static", "unit"]
      depends_on: ["TASK-018", "TASK-019", "TASK-020", "TASK-021", "TASK-022", "TASK-023", "TASK-024"]
      description: "Implement the full-regression materialization path that runs every required product stage without --task-id before final workflow acceptance."
      inputs:
        - "Implemented stage owners for static, unit, contract, api, integration, replay, snapshot, and e2e."
        - "Workflow command surface from workflows.md."
      outputs:
        - "Full-stage commands write reports/stages/static.json, unit.json, contract.json, api.json, integration.json, replay.json, snapshot.json, and e2e.json."
        - "Task-scoped reports remain under reports/tasks/<task_id>/<stage>.json and are never copied into reports/stages/."
      acceptance_criteria:
        - "Full-regression materialization runs required stages in docs/07_test_spec.md#2.13 order without --task-id."
        - "Every full-stage report conforms to schemas/test_report.schema.json and docs/07_test_spec.md#6."
        - "Every mandatory assertion ID required for full-stage evidence is emitted by the owning stage report with the correct stage and visibility."
        - "Full-stage materialization preserves traceability matrix ownership and expected report paths for every mandatory assertion ID."
        - "Downstream stages are marked skipped after the first failed full-stage run and cannot satisfy STOP_ALLOWED."
        - "Full-stage materialization runs live dependency, forbidden field, non-goal endpoint/UI, wrong-stage/conflict, and skipped-stage stop-failure checks before final acceptance."
        - "Final acceptance consumes these existing stage-level reports and does not synthesize, replace, or skip replay, snapshot, or e2e evidence."
        - "Final acceptance fails STOP_ALLOWED if any task in tasks.md is not passed."
        - "Final acceptance consumes PRD coverage, task acceptance coverage, browser E2E, and local user acceptance evidence as required stop inputs."
      failure_criteria:
        - "FAIL if any task-scoped report is used as a substitute for reports/stages/<stage>.json."
        - "FAIL if full-regression materialization runs acceptance or writes ACC-STOP reports."

    - id: TASK-026A
      name: "Round evidence schema hardening"
      layer: "Acceptance Layer"
      type: ["test", "docs", "schema"]
      status: "pending"
      source: ["workflows.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-010"]
      priority: "acceptance_gate_failures"
      test_scope: ["static"]
      depends_on: ["TASK-021"]
      description: "Add explicit ReviewReport and FixOptimizeReport contracts and ensure RoundSummaryReport cannot count a completed round without parseable review and fix/optimize evidence."
      inputs:
        - "Round lifecycle rules from workflows.md."
        - "Report schema rules from docs/07_test_spec.md."
      outputs:
        - "schemas/review_report.schema.json and schemas/fix_optimize_report.schema.json exist and are validated by static harness checks."
        - "RoundSummaryReport schema requires round_index, completed_round_count, review, fix_optimize and round_end_decision."
        - "RoundSummaryReport schema forbids selected_next_state = DONE."
      acceptance_criteria:
        - "ReviewReport schema requires all eight review dimensions and forbids passed review reports with blocking findings."
        - "FixOptimizeReport schema requires resolved blocking findings, at least one retest report and no regression when status = passed."
        - "RoundSummaryReport schema rejects selected_next_state = DONE."
        - "static stage result = pass for round evidence schema hardening."
      failure_criteria:
        - "FAIL if completed rounds can be counted without parseable review and fix/optimize evidence."

    - id: TASK-026B
      name: "Stop decision and coverage schema hardening"
      layer: "Acceptance Layer"
      type: ["test", "docs", "schema"]
      status: "pending"
      source: ["docs/07_test_spec.md", "docs/08_acceptance.md", "schemas/stop_decision.schema.json"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-008", "ACC-STOP-010"]
      priority: "acceptance_gate_failures"
      test_scope: ["unit"]
      depends_on: ["TASK-026A"]
      description: "Make STOP_ALLOWED depend on valid round evidence and tighten PRD/task acceptance coverage schemas so prose-only or task-scoped-only evidence cannot pass."
      inputs:
        - "StopDecisionReport rules from docs/08_acceptance.md."
        - "Coverage report rules from docs/07_test_spec.md."
      outputs:
        - "round_count_policy includes round_evidence with summary, review and fix/optimize report paths."
        - "completed_round_count is derived from valid round_evidence entries."
        - "PRD and task acceptance coverage schemas reject passed reports with uncovered items or non-full-stage evidence paths."
      acceptance_criteria:
        - "STOP_ALLOWED schema requires round_count_policy.round_evidence."
        - "round_count_policy.status = PASS requires either 10 valid round evidence entries or a valid early_done_allowed case."
        - "PRD coverage schema rejects status = passed when uncovered_acceptance_items is non-empty."
        - "Task acceptance coverage schema rejects status = passed when uncovered_task_acceptance_items is non-empty."
        - "unit stage result = pass for stop decision and coverage schema hardening."
      failure_criteria:
        - "FAIL if STOP_ALLOWED can become true while round_count_policy.status is not PASS."
        - "FAIL if coverage can pass with prose-only evidence or coverage file self-reference."

    - id: TASK-026C
      name: "Acceptance evaluator enforcement"
      layer: "Acceptance Layer"
      type: ["test"]
      status: "pending"
      source: ["scripts/run_harness.py", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-008", "ACC-STOP-010"]
      priority: "acceptance_gate_failures"
      test_scope: ["unit"]
      depends_on: ["TASK-026B", "TASK-025"]
      description: "Teach the acceptance evaluator to compute valid completed rounds from parseable summary/review/fix evidence and to reject stale or failed local user acceptance."
      inputs:
        - "Report paths from workflows.md."
        - "Stop decision schema and local user acceptance schema."
      outputs:
        - "Acceptance evaluator validates round_evidence and computes completed_round_count from valid entries."
        - "Acceptance evaluator rejects early_done_allowed when unfinished work, failed gates, failed stop inputs or malformed round evidence exists."
        - "Acceptance evaluator preserves failed local user acceptance findings and keeps STOP_ALLOWED false."
      acceptance_criteria:
        - "Unit tests prove fewer than 10 valid rounds with unfinished work keeps round_count_policy = FAIL."
        - "Unit tests prove early_done_allowed is true only when all gates, stop inputs and round evidence preconditions are satisfied."
        - "Unit tests prove local user acceptance failed findings keep local_user_acceptance_status and STOP_ALLOWED failed."
        - "Task-scoped acceptance remains invalid and full acceptance with missing stop input writes STOP_ALLOWED = false."
        - "unit stage result = pass for acceptance evaluator enforcement."
      failure_criteria:
        - "FAIL if acceptance trusts RoundSummaryReport.completed_round_count without validating linked review and fix/optimize reports."

    - id: TASK-026
      name: "PRD, workflow stop-rule, and test-spec audit"
      layer: "Acceptance Layer"
      type: ["test", "docs", "review"]
      status: "pending"
      source: ["workflows.md", "tasks.md", "docs/01_prd.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
      acceptance_gate: ["ACC-STOP-001", "ACC-STOP-006", "ACC-STOP-008", "ACC-STOP-010"]
      priority: "acceptance_gate_failures"
      test_scope: ["static", "e2e"]
      depends_on: ["TASK-026A", "TASK-026B", "TASK-026C"]
      description: "Audit whether tasks.md covers every PRD requirement, whether workflows.md plus docs/08_acceptance.md can prevent premature long-running stop, and whether docs/07_test_spec.md has rigorous executable test plans for all PRD, acceptance, and task-level acceptance standards."
      inputs:
        - "Checklist-style acceptance statements from docs/01_prd.md."
        - "All DAG nodes, task acceptance criteria, and stop-condition metadata from tasks.md."
        - "Workflow DONE guard, LOAD_TASKS -> ACCEPTANCE guard, transition table, and pseudocode from workflows.md."
        - "Machine stop decision and StopDecisionReport rules from docs/08_acceptance.md."
        - "Mandatory assertion catalog from docs/07_test_spec.md#2.16."
        - "Browser E2E report from reports/stages/e2e.json."
        - "Local deployment URL, port, database, and user acceptance findings."
      outputs:
        - "reports/acceptance/prd_coverage.json conforming to schemas/prd_coverage.schema.json."
        - "reports/acceptance/task_acceptance_coverage.json conforming to schemas/task_acceptance_coverage.schema.json."
        - "A PRD-to-task coverage audit listing each PRD requirement, source line, mapped task_id, acceptance gate, and missing-task finding when coverage is absent."
        - "A task acceptance coverage audit listing each tasks.md acceptance criterion, task_id, criterion source line, mapped assertion ids, report paths, and pass/fail status."
        - "A stop-rule audit proving workflows.md, docs/08_acceptance.md, and tasks.md all require every DAG node to be passed plus all gates and stop inputs to pass before DONE."
        - "A round-lifecycle audit proving every completed round has plan, test, review, fix/optimize, and summary evidence, plus machine-readable round count policy evidence."
        - "A test-spec audit proving docs/07_test_spec.md has deterministic executable verification for every acceptance standard mentioned by docs/01_prd.md, docs/08_acceptance.md, and tasks.md."
        - "reports/acceptance/local_user_acceptance.json conforming to schemas/local_user_acceptance.schema.json."
        - "STOP_ALLOWED.json lists unfinished tasks, uncovered PRD items, failed stop inputs, and user acceptance failures."
      acceptance_criteria:
        - "PRD task coverage audit PASS only when every docs/01_prd.md requirement and acceptance item records source line, mapped task_id, acceptance gate, assertion ids, report paths, and pass/fail status."
        - "PRD task coverage audit FAILS if any PRD requirement is missing from tasks.md, is mapped only to acceptance_gate: none, or lacks executable evidence."
        - "Task acceptance coverage audit PASS only when every tasks.md dag.nodes[*].acceptance_criteria item records source line, mapped assertion ids, report paths, and executed pass/fail status."
        - "Task acceptance coverage audit FAILS if any task acceptance criterion is unmapped, mapped only to prose, mapped only to task-scoped evidence when full-stage evidence is required, unexecuted, failed, flaky, skipped, or missing a report path."
        - "Stop-rule audit PASS only when workflows.md, docs/08_acceptance.md, and tasks.md all agree that DONE requires all dag.nodes[*].status == passed, ACC-STOP-001 through ACC-STOP-010 PASS, task_completion_status PASS, prd_coverage_status PASS, task_acceptance_coverage_status PASS, browser_e2e_status PASS, local_user_acceptance_status PASS, and STOP_ALLOWED = true."
        - "Stop-rule audit FAILS if task_blocked, pending, in_progress, missing browser E2E, incomplete PRD coverage, incomplete task acceptance coverage, missing local user acceptance, or failed local user acceptance can reach DONE."
        - "Round-lifecycle audit PASS only when every passed task has a parseable RoundSummaryReport with round_index, completed_round_count, review evidence, fix_optimize evidence, and DONE before 10 rounds is allowed only by round_count_policy.early_done_allowed = true."
        - "Test-spec audit PASS only when docs/07_test_spec.md gives a deterministic, executable test method for every验收标准 mentioned in docs/01_prd.md, docs/08_acceptance.md, and tasks.md."
        - "Test-spec audit specifically covers homepage news density, 30-day high-score list, NewsCard summary HTML escaping, article detail, sources management, default source CRUD parity, refresh action, API envelope, leak checks, task completion, PRD coverage, task acceptance coverage, browser E2E, and local user acceptance regression."
        - "Mandatory assertion catalog includes task completion, PRD coverage, task acceptance coverage, round evidence schema enforcement, round count policy enforcement, coverage schema hardening, browser E2E stop input, local user acceptance, NewsCard summary text-only, exact default source list, default source parity, distinct dedupe positive case, fallback summary translation, ArticleView original link, no direct original-site navigation, ArticleView browser E2E, Sources page browser E2E, and refresh action browser E2E assertion IDs with traceability rows."
        - "Any PRD acceptance item without executed passing evidence appears in uncovered_acceptance_items and blocks STOP_ALLOWED."
        - "Any task acceptance criterion without executed passing evidence appears in uncovered_task_acceptance_items and blocks STOP_ALLOWED."
        - "Local user acceptance records local URL, port, database, checked surfaces, failed findings, and current status."
        - "Any failed local user acceptance finding blocks local_user_acceptance_status and STOP_ALLOWED."
        - "Browser-visible coverage includes homepage news feed, 30-day high-score list, article detail, sources page, and refresh action."
      failure_criteria:
        - "FAIL if any audit conclusion is prose-only and lacks source lines, task ids, assertion ids, report paths, and executable status."
        - "FAIL if PRD coverage is generated from prose only without executed structured evidence."
        - "FAIL if task acceptance coverage is generated from prose only without executed structured evidence."
        - "FAIL if local user acceptance omits failed findings reported by the user."
        - "FAIL if workflow, acceptance, or tasks stop rules allow task_blocked, pending, in_progress, missing browser E2E, missing PRD coverage, missing task acceptance coverage, or failed local user acceptance to reach DONE."
        - "FAIL if completed rounds can be counted without review/fix_optimize evidence, or if STOP_ALLOWED can be true before 10 rounds without round_count_policy proving all stop conditions passed."
        - "FAIL if browser E2E evidence is replaced by API-only tests, static string scans, screenshots without assertions, or manual visual judgment."
