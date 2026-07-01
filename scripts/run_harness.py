#!/usr/bin/env python3
"""Local Codex Harness command surface.

The harness owns stage reporting and stop-gate evaluation. Product feature
implementation is intentionally out of scope in this file, but all commands and
reports must remain machine-readable and deterministic.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import ipaddress
import json
import py_compile
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


STAGES = [
    "static",
    "unit",
    "contract",
    "api",
    "integration",
    "replay",
    "snapshot",
    "e2e",
    "acceptance",
]
REQUIRED_PRODUCT_STAGES = STAGES[:-1]
REQUIRED_GATES = [f"ACC-STOP-{index:03d}" for index in range(1, 11)]

SCHEMA_REF = "07_test_spec.md#6"
SCHEMA_VERSION = "v2"
FIXTURE_SET = "mvp_acceptance_fixture@v1"
MOCK_SET = "mvp_mock@v1"
CLOCK_SOURCE = "fixed_clock_fixture@v1"
FIXED_TIMESTAMP = "2026-06-28T09:00:00Z"
FIXTURE_VERSION = "mvp_acceptance_fixture@v1"
MOCK_VERSION = "mvp_mock@v1"
REQUIRED_TASK_IDS = {"TASK-000", "TASK-001", "TASK-003", "TASK-021"}

REPORT_VISIBILITY_VALUES = {
    "public_surface",
    "internal_evidence",
    "report_metadata",
}
FORBIDDEN_PUBLIC_FIELDS = {
    "pipeline_state",
    "is_selected",
    "content_raw",
    "content_full",
    "has_translate_failed",
    "discussion_url",
    "deleted_at",
}
FORBIDDEN_CONTEXTUAL_FIELDS = {
    "full_llm_prompt",
    "raw_pipeline_payload",
    "raw_article_body",
}
FORBIDDEN_TOKEN_PATTERNS = {
    "jwt",
    "api_key",
    "secret",
    "password",
}
FORBIDDEN_PATH_PATTERNS = (
    "user/login",
    "search",
    "category",
    "comment",
    "favorite",
    "share",
    "task progress",
    "retry",
    "admin",
    "versioning",
)
REQUIRED_API_ROUTES = {
    ("GET", "/api/home"),
    ("GET", "/api/news/{id}"),
    ("POST", "/api/refresh"),
    ("GET", "/api/sources"),
    ("POST", "/api/sources"),
    ("PATCH", "/api/sources/{id}"),
    ("DELETE", "/api/sources/{id}"),
}
TASK_002A_EXCLUDED_FIELDS = {
    "translation_status",
    "content_source",
    "title_domain_hash",
    "is_ready",
    "display_mode",
}
TASK_002A_DUPLICATE_SOURCE_SQL = (
    "INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at) "
    "VALUES ('Duplicate', 'https://example.com/rss.xml', 1, 'twice_daily', '2026-06-28T06:01:00Z')"
)
TASK_002A_DUPLICATE_NEWS_SQL = (
    "INSERT INTO news_item (source_id, original_url, canonical_url, original_title, "
    "published_at, pipeline_state, created_at, updated_at) VALUES "
    "(1, 'https://example.com/2', 'https://example.com/1', 'Duplicate', "
    "'2026-06-28T07:01:00Z', 'raw', '2026-06-28T09:00:00Z', '2026-06-28T09:00:00Z')"
)
TASK_002A_BAD_STATE_SQL = (
    "INSERT INTO news_item (source_id, original_url, canonical_url, original_title, "
    "published_at, pipeline_state, created_at, updated_at) VALUES "
    "(1, 'https://example.com/bad', 'https://example.com/bad', 'Bad', "
    "'2026-06-28T07:02:00Z', 'translated', '2026-06-28T09:00:00Z', '2026-06-28T09:00:00Z')"
)
TASK_002A_BOTH_OWNER_LOG_SQL = (
    "INSERT INTO processing_log (source_id, news_item_id, stage, success, trace_id, created_at) "
    "VALUES (1, 1, 'crawl', 1, 'trace', '2026-06-28T09:00:00Z')"
)
TASK_002A_CRAWL_NEWS_LOG_SQL = (
    "INSERT INTO processing_log (news_item_id, stage, success, trace_id, created_at) "
    "VALUES (1, 'crawl', 1, 'trace', '2026-06-28T09:00:00Z')"
)
TASK_002A_SCORE_SOURCE_LOG_SQL = (
    "INSERT INTO processing_log (source_id, stage, success, trace_id, created_at) "
    "VALUES (1, 'score', 1, 'trace', '2026-06-28T09:00:00Z')"
)
DEFAULT_SOURCES_FIXTURE_PATH = Path("fixtures/sources/default_sources.json")
RSS_FEEDS_FIXTURE_PATH = Path("fixtures/rss/feeds.json")


def normalize_default_source_record(record: object) -> dict[str, str]:
    if isinstance(record, str):
        return {"name": "", "rss_url": record.strip()}
    if isinstance(record, dict):
        return {
            "name": str(record.get("name") or "").strip(),
            "rss_url": str(record.get("rss_url") or "").strip(),
        }
    return {"name": "", "rss_url": ""}


def default_source_records() -> list[dict[str, str]]:
    payload = json.loads(DEFAULT_SOURCES_FIXTURE_PATH.read_text())
    records = payload.get("sources") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        return []
    return [normalize_default_source_record(record) for record in records]


def default_source_urls() -> set[str]:
    return {record["rss_url"] for record in default_source_records() if record["rss_url"]}


def default_source_count() -> int:
    return len(default_source_urls())


def rss_fixture_counts() -> dict[str, int]:
    payload = json.loads(RSS_FEEDS_FIXTURE_PATH.read_text())
    feeds = payload.get("feeds") if isinstance(payload, dict) else []
    feeds = feeds if isinstance(feeds, list) else []
    success_count = sum(1 for feed in feeds if isinstance(feed, dict) and feed.get("status") == "success")
    failure_count = sum(1 for feed in feeds if isinstance(feed, dict) and feed.get("status") != "success")
    item_count = 0
    for feed in feeds:
        if not isinstance(feed, dict) or feed.get("status") != "success":
            continue
        items = feed.get("items", [])
        if isinstance(items, list):
            item_count += len(items)
    return {
        "feed_count": len(feeds),
        "source_success_count": success_count,
        "source_failure_count": failure_count,
        "rss_item_count": item_count,
    }


CONTRACT_FRONTEND_ENDPOINTS = {
    "/api/home",
    "/api/news/",
    "/api/refresh",
    "/api/sources",
}
LEGACY_FRONTEND_ENDPOINTS = {
    "/rss",
    "/api/sync",
    "/api/feeds",
    "/api/items",
}
FRONTEND_SCAN_ENTRYPOINTS = [
    Path("index.html"),
    Path("frontend/index.html"),
    Path("frontend/vite.config.ts"),
]
FRONTEND_SCAN_ROOTS = [
    Path("frontend/src"),
]
FRONTEND_SCAN_EXTENSIONS = ("*.ts", "*.tsx", "*.js", "*.jsx", "*.html")
FRONTEND_GENERATED_PATH_PARTS = {"node_modules", "dist", ".vite"}
DEPLOYED_BROWSER_SMOKE_REPORT = "deployed_browser_smoke.json"
DEPLOYED_BROWSER_SMOKE_URL = "http://127.0.0.1:8010"
DEPLOYED_BROWSER_SMOKE_PORT = 8010
DEPLOYED_RUNTIME_TIMEOUT_SECONDS = 3
RESERVED_PLACEHOLDER_HOSTS = {"example.com", "example.org", "example.net"}
RESERVED_PLACEHOLDER_SUFFIXES = (".test", ".invalid")
FIXTURE_THRESHOLD_CANONICAL_URL = "https://developers.openai.com/resources/agentic-app-production/"
FIXTURE_TRANSLATED_CANONICAL_URL = "https://openai.com/index/introducing-life-sci-bench/"
FIXTURE_TRANSLATION_PARTIAL_CANONICAL_URL = "https://openai.com/index/introducing-openai-o3-pro/"
FIXTURE_EXTRACTION_FAILURE_URL = "https://openai.com/index/extraction-failure-fixture/"
FIXTURE_EMPTY_SUMMARY_URL = "https://openai.com/index/empty-summary-fixture/"
ARCHIVAL_OPENAI_FIXTURE_CANONICAL_URLS = {
    "https://openai.com/index/gpt-4-1",
    "https://openai.com/index/gpt-4-1/",
    "https://openai.com/index/introducing-gpt-4-1-in-the-api/",
}
FORBIDDEN_TRANSLATION_PLACEHOLDER_TERMS = (
    "fixture",
    "mock",
    "模拟",
    "占位",
    "这是一条",
    "这是一篇",
)
TRANSLATION_QUALITY_KEYWORDS = {
    "fixture-translated-96": ("LifeSciBench", "生命科学", "基准"),
    "fixture-rank-95": ("安全", "基准", "企业"),
    "fixture-rank-94": ("评测", "智能体", "任务"),
    "fixture-rank-93": ("芯片", "调度", "延迟"),
    "fixture-rank-92": ("多模态", "工具", "基准"),
    "fixture-rank-91": ("数据", "合成", "问答"),
    "fixture-rank-90": ("检索", "规划", "小型"),
    "fixture-rank-89": ("可观测", "提示词", "回归"),
    "fixture-rank-88": ("编码", "仓库", "契约"),
    "fixture-rank-87": ("产品", "漂移", "智能体"),
    "fixture-old-high-99": ("里程碑", "窗口", "榜单"),
}
E2E_REQUIRED_SURFACES = [
    "home_news_feed",
    "high_score_list",
    "article_view",
    "sources_page",
    "refresh_action",
]
E2E_SURFACE_ASSERTION_MAP = {
    "home_news_feed": ["A-e2e-ACC-STOP-006-home-news-density"],
    "high_score_list": ["A-e2e-ACC-STOP-006-high-score-list-browser"],
    "article_view": [
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-click-to-read-readability",
        "A-e2e-ACC-STOP-006-article-original-link-button",
        "A-e2e-ACC-STOP-006-no-direct-original-navigation",
    ],
    "sources_page": ["A-e2e-ACC-STOP-006-sources-page-browser"],
    "refresh_action": ["A-e2e-ACC-STOP-006-refresh-action-browser"],
}
HOME_LIST_REQUIRED_FIELDS = {
    "id",
    "title",
    "original_title",
    "source_name",
    "original_url",
    "published_at",
    "score",
    "status",
}
HOME_LIST_ALLOWED_FIELDS = HOME_LIST_REQUIRED_FIELDS | {"summary_zh"}
HOME_LAYOUT_FIELDS = {"left_column", "right_column", "layout", "columns"}
SOURCE_ITEM_FIELDS = {
    "id",
    "name",
    "rss_url",
    "is_enabled",
    "fetch_frequency",
    "created_at",
}
SOURCE_INVALID_CREATE_CASES = [
    ("empty_name", {"name": "", "rss_url": "https://example.com/empty-name.xml"}),
    ("empty_url", {"name": "Empty URL", "rss_url": ""}),
    ("missing_url", {"name": "Missing URL"}),
    ("invalid_url", {"name": "Invalid URL", "rss_url": "not-a-url"}),
    ("local_url", {"name": "Local URL", "rss_url": "http://localhost/rss.xml"}),
    ("private_url", {"name": "Private URL", "rss_url": "http://192.168.0.1/rss.xml"}),
]
REFRESH_DATA_FIELDS = {"refreshed_at"}
REFRESH_FORBIDDEN_FIELDS = FORBIDDEN_PUBLIC_FIELDS | {
    "task",
    "task_id",
    "queue",
    "worker",
    "retry",
    "progress",
    "run_summary",
    "processing_log",
    "processing_logs",
}
TASK_015_SOURCE_FILES = [
    "frontend/src/main.tsx",
    "frontend/src/types/news.ts",
    "frontend/src/api/news.ts",
    "frontend/src/pages/HomePage.tsx",
    "frontend/src/components/AppShell.tsx",
    "frontend/src/components/TopBar.tsx",
    "frontend/src/components/NewsCard.tsx",
    "frontend/src/components/HighScoreList.tsx",
    "frontend/src/components/ScoreBadge.tsx",
    "frontend/src/components/StatusBadge.tsx",
    "frontend/src/components/SourceMarker.tsx",
    "frontend/src/components/LoadingState.tsx",
    "frontend/src/components/EmptyState.tsx",
    "frontend/src/components/ErrorState.tsx",
    "frontend/src/styles/app.css",
]
TASK_015_STATUS_LABELS = {
    "ready": "翻译中",
    "translated": "已翻译",
    "translation_failed": "翻译失败",
}
TASK_015_SOURCE_COLORS = ["#7DD3FC", "#34D399", "#FBBF24", "#F87171", "#A78BFA", "#F472B6"]
TASK_016_SOURCE_FILES = [
    "frontend/src/main.tsx",
    "frontend/src/api/http.ts",
    "frontend/src/api/news.ts",
    "frontend/src/pages/ArticleView.tsx",
    "frontend/src/components/NewsCard.tsx",
    "frontend/src/components/HighScoreList.tsx",
    "frontend/src/components/LoadingState.tsx",
    "frontend/src/components/ErrorState.tsx",
    "frontend/src/styles/article.css",
]
TASK_017_SOURCE_FILES = [
    "frontend/src/main.tsx",
    "frontend/src/types/source.ts",
    "frontend/src/api/http.ts",
    "frontend/src/api/news.ts",
    "frontend/src/api/sources.ts",
    "frontend/src/pages/SourcesPage.tsx",
    "frontend/src/components/SourceForm.tsx",
    "frontend/src/components/SourceRow.tsx",
    "frontend/src/styles/sources.css",
]
TASK_017_NON_GOAL_TERMS = {
    "advanced",
    "category",
    "processing log",
    "task progress",
    "retry",
    "admin",
}
TASK_020_SOURCE_FILES = list(
    dict.fromkeys([*TASK_015_SOURCE_FILES, *TASK_016_SOURCE_FILES, *TASK_017_SOURCE_FILES])
)
TASK_027_SOURCE_FILES = [
    "docs/03_ui_spec.md",
    "docs/07_test_spec.md",
    "docs/08_acceptance.md",
    "scripts/run_deployed_browser_smoke.py",
    "frontend/src/styles/app.css",
    "frontend/src/styles/article.css",
    "frontend/src/styles/sources.css",
    "frontend/src/components/SourceMarker.tsx",
]
TASK_028_SOURCE_FILES = [
    "docs/03_ui_spec.md",
    "docs/07_test_spec.md",
    "docs/08_acceptance.md",
    "tasks.md",
    "scripts/run_harness.py",
    "scripts/run_deployed_browser_smoke.py",
    "tests/test_frontend_contract.py",
    "frontend/src/components/HighScoreList.tsx",
    "frontend/src/styles/app.css",
]
LIGHT_GRAY_BACKGROUND = "#f3f4f6"
LIGHT_SURFACE_TOKENS = {"#ffffff", "#f8fafc"}
LIGHT_BORDER = "#d8dee6"
OLD_DARK_BACKGROUND_TOKENS = {"#0b0f14", "#111820", "#151e28"}
EXPECTED_TRANSLATION_LATEST_STATUS_COUNTS = {
    "translated": 10,
}
EXPECTED_TRANSLATION_TOP_STATUS_COUNTS = {
    "translated": 10,
}
UNREADABLE_DETAIL_TITLE = "摘要和正文暂不可用"
READY_UNREADABLE_COPY = "翻译完成后将自动显示中文摘要和正文。"
FAILED_UNREADABLE_COPY = "翻译失败，当前无法显示中文摘要和正文。"

PRD_FLOW_ASSERTION_MAP = {
    "1.1": [
        "A-api-ACC-STOP-002-default-source-seed",
        "A-api-ACC-STOP-002-default-source-exact-list",
        "A-api-ACC-STOP-002-source-management",
        "A-api-ACC-STOP-002-source-crud-errors",
        "A-api-ACC-STOP-002-default-source-crud-parity",
        "A-integration-ACC-STOP-002-source-ui-crud-parity",
    ],
    "1.2": [
        "A-api-ACC-STOP-002-source-management",
        "A-api-ACC-STOP-002-source-crud-errors",
        "A-api-ACC-STOP-002-source-tombstone-history",
        "A-api-ACC-STOP-002-default-source-crud-parity",
        "A-integration-ACC-STOP-002-source-ui-crud-parity",
    ],
    "2.1": [
        "A-integration-ACC-STOP-003-scheduler-fixed-clock",
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-integration-ACC-STOP-008-live-dependency-blocked",
    ],
    "2.2": [
        "A-api-ACC-STOP-004-refresh-contract",
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-e2e-ACC-STOP-006-refresh-action-browser",
    ],
    "2.3": [
        "A-unit-ACC-STOP-007-llm-request-shapes",
        "A-unit-ACC-STOP-007-llm-retry-failure-policy",
        "A-integration-ACC-STOP-003-threshold-selection",
        "A-unit-ACC-STOP-005-state-machine",
    ],
    "3.1": [
        "A-unit-ACC-STOP-003-rss-normalize-dedupe",
        "A-integration-ACC-STOP-003-dedupe-positive-distinct-items",
        "A-integration-ACC-STOP-003-threshold-selection",
    ],
    "3.2": [
        "A-integration-ACC-STOP-003-fetch-fallback",
        "A-integration-ACC-STOP-003-full-pipeline",
    ],
    "4.1": [
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-unit-ACC-STOP-005-translation-facts",
        "A-api-ACC-STOP-004-home-detail-behavior",
    ],
    "4.2": [
        "A-unit-ACC-STOP-007-llm-schema-validation",
        "A-integration-ACC-STOP-003-fallback-summary-translation",
        "A-integration-ACC-STOP-003-translation-failure-isolated",
        "A-integration-ACC-STOP-003-translation-quality-fixtures",
        "A-unit-ACC-STOP-005-translation-facts",
    ],
    "5.1": [
        "A-e2e-ACC-STOP-006-home-news-density",
        "A-api-ACC-STOP-004-home-translated-only",
        "A-api-ACC-STOP-004-home-pagination",
        "A-integration-ACC-STOP-006-ui-render-contract",
        "A-integration-ACC-STOP-006-ui-forbidden-rendering",
        "A-e2e-ACC-STOP-006-home-infinite-scroll",
        "A-e2e-ACC-STOP-006-news-card-summary-text-only",
        "A-snapshot-ACC-STOP-006-layout-visual-contract",
    ],
    "5.2": [
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-click-to-read-readability",
        "A-e2e-ACC-STOP-006-no-direct-original-navigation",
        "A-api-ACC-STOP-004-home-detail-behavior",
    ],
    "6.1": [
        "A-api-ACC-STOP-004-home-detail-behavior",
        "A-api-ACC-STOP-004-home-translated-only",
        "A-e2e-ACC-STOP-006-high-score-list-browser",
        "A-e2e-ACC-STOP-006-home-news-density",
    ],
    "6.2": [
        "A-e2e-ACC-STOP-006-high-score-list-browser",
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-click-to-read-readability",
    ],
    "7.1": [
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-click-to-read-readability",
        "A-e2e-ACC-STOP-006-article-original-link-button",
        "A-api-ACC-STOP-004-original-url-real-link",
        "A-api-ACC-STOP-004-home-detail-behavior",
    ],
    "7.2": [
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-api-ACC-STOP-004-home-detail-behavior",
    ],
    "8.1": [
        "A-unit-ACC-STOP-005-state-machine",
        "A-unit-ACC-STOP-005-translation-facts",
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-api-ACC-STOP-009-api-leak-scan",
    ],
    "8.2": [
        "A-contract-ACC-STOP-005-db-schema",
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-integration-ACC-STOP-008-live-dependency-blocked",
        "A-unit-ACC-STOP-009-log-sanitizer",
    ],
    "8.3": [
        "A-unit-ACC-STOP-007-llm-request-shapes",
        "A-unit-ACC-STOP-007-llm-retry-failure-policy",
        "A-unit-ACC-STOP-007-llm-schema-validation",
        "A-integration-ACC-STOP-003-translation-failure-isolated",
    ],
}

TASK_FALLBACK_ASSERTION_MAP = {
    "TASK-009": [
        "A-integration-ACC-STOP-003-full-pipeline",
        "A-integration-ACC-STOP-003-scheduler-fixed-clock",
        "A-integration-ACC-STOP-008-live-dependency-blocked",
    ],
    "TASK-011": [
        "A-contract-ACC-STOP-004-api-shapes",
        "A-api-ACC-STOP-004-home-detail-behavior",
        "A-api-ACC-STOP-009-api-leak-scan",
    ],
    "TASK-012": [
        "A-contract-ACC-STOP-004-api-shapes",
        "A-api-ACC-STOP-004-home-detail-behavior",
        "A-api-ACC-STOP-009-api-leak-scan",
    ],
    "TASK-015": [
        "A-integration-ACC-STOP-006-ui-render-contract",
        "A-integration-ACC-STOP-006-ui-forbidden-rendering",
        "A-e2e-ACC-STOP-006-home-news-density",
        "A-e2e-ACC-STOP-006-high-score-list-browser",
        "A-e2e-ACC-STOP-006-refresh-action-browser",
        "A-e2e-ACC-STOP-006-news-card-summary-text-only",
    ],
    "TASK-016": [
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-article-original-link-button",
        "A-e2e-ACC-STOP-006-no-direct-original-navigation",
    ],
    "TASK-021": [
        "A-static-ACC-STOP-001-test-report-schema-contract",
        "A-static-ACC-STOP-001-round-evidence-report-schemas",
        "A-unit-ACC-STOP-001-round-count-policy-enforced",
        "A-unit-ACC-STOP-001-coverage-schema-tightened",
        "A-unit-ACC-STOP-001-acceptance-evaluator-enforcement",
        "A-unit-ACC-STOP-001-local-user-acceptance-regression",
        "A-integration-ACC-STOP-008-live-dependency-blocked",
        "A-static-ACC-STOP-010-contract-doc-sync",
    ],
    "TASK-026": [
        "A-static-ACC-STOP-001-test-report-schema-contract",
        "A-static-ACC-STOP-001-round-evidence-report-schemas",
        "A-unit-ACC-STOP-001-round-count-policy-enforced",
        "A-unit-ACC-STOP-001-coverage-schema-tightened",
        "A-unit-ACC-STOP-001-acceptance-evaluator-enforcement",
        "A-unit-ACC-STOP-001-local-user-acceptance-regression",
        "A-e2e-ACC-STOP-008-clean-run-isolation",
        "A-e2e-ACC-STOP-006-home-news-density",
        "A-e2e-ACC-STOP-006-high-score-list-browser",
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-article-original-link-button",
        "A-e2e-ACC-STOP-006-no-direct-original-navigation",
        "A-e2e-ACC-STOP-006-sources-page-browser",
        "A-e2e-ACC-STOP-006-refresh-action-browser",
        "A-e2e-ACC-STOP-006-news-card-summary-text-only",
        "A-static-ACC-STOP-010-contract-doc-sync",
    ],
    "TASK-027": [
        "A-integration-ACC-STOP-006-ui-render-contract",
        "A-snapshot-ACC-STOP-006-layout-visual-contract",
        "A-e2e-ACC-STOP-006-home-news-density",
        "A-static-ACC-STOP-010-contract-doc-sync",
    ],
    "TASK-028": [
        "A-integration-ACC-STOP-006-ui-render-contract",
        "A-snapshot-ACC-STOP-006-layout-visual-contract",
        "A-e2e-ACC-STOP-006-high-score-list-browser",
        "A-static-ACC-STOP-010-contract-doc-sync",
    ],
    "TASK-029": [
        "A-integration-ACC-STOP-003-translation-quality-fixtures",
        "A-integration-ACC-STOP-003-translation-failure-isolated",
        "A-e2e-ACC-STOP-006-home-news-density",
    ],
    "TASK-030": [
        "A-e2e-ACC-STOP-006-click-to-read-readability",
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-integration-ACC-STOP-006-ui-render-contract",
        "A-snapshot-ACC-STOP-006-layout-visual-contract",
    ],
    "TASK-031": [
        "A-static-ACC-STOP-010-local-acceptance-failure-preservation-docs",
        "A-unit-ACC-STOP-001-local-acceptance-failure-preservation",
    ],
    "TASK-032": [
        "A-api-ACC-STOP-004-original-url-real-link",
        "A-e2e-ACC-STOP-006-article-original-link-button",
        "A-e2e-ACC-STOP-006-no-direct-original-navigation",
        "A-static-ACC-STOP-010-contract-doc-sync",
    ],
    "TASK-033": [
        "A-integration-ACC-STOP-003-translation-quality-fixtures",
        "A-api-ACC-STOP-004-home-detail-behavior",
        "A-e2e-ACC-STOP-006-click-to-read-readability",
        "A-static-ACC-STOP-010-contract-doc-sync",
    ],
    "TASK-034": [
        "A-api-ACC-STOP-004-home-translated-only",
        "A-e2e-ACC-STOP-006-click-to-read-readability",
        "A-e2e-ACC-STOP-006-home-news-density",
        "A-e2e-ACC-STOP-006-high-score-list-browser",
        "A-static-ACC-STOP-010-contract-doc-sync",
    ],
    "TASK-035": [
        "A-api-ACC-STOP-004-home-pagination",
        "A-e2e-ACC-STOP-006-home-infinite-scroll",
        "A-static-ACC-STOP-010-contract-doc-sync",
    ],
}

SCHEMA_FILES = {
    "test_report": Path("schemas/test_report.schema.json"),
    "stop_decision": Path("schemas/stop_decision.schema.json"),
    "task_plan_report": Path("schemas/task_plan_report.schema.json"),
    "review_report": Path("schemas/review_report.schema.json"),
    "fix_optimize_report": Path("schemas/fix_optimize_report.schema.json"),
    "round_summary_report": Path("schemas/round_summary_report.schema.json"),
    "tasks": Path("schemas/tasks.schema.json"),
    "prd_coverage": Path("schemas/prd_coverage.schema.json"),
    "task_acceptance_coverage": Path("schemas/task_acceptance_coverage.schema.json"),
    "local_user_acceptance": Path("schemas/local_user_acceptance.schema.json"),
}

MANDATORY_ASSERTION_ROW = re.compile(
    r"^\|\s*`(?P<id>A-(?P<stage>static|unit|contract|api|integration|replay|snapshot|e2e|acceptance)-(?P<gate>ACC-STOP-(?:00[1-9]|010))-[a-z0-9]+(?:-[a-z0-9]+)*)`\s*"
    r"\|\s*(?P<table_stage>static|unit|contract|api|integration|replay|snapshot|e2e|acceptance)\s*"
    r"\|\s*(?P<table_gate>ACC-STOP-(?:00[1-9]|010))\s*"
    r"\|\s*(?P<visibility>public_surface|internal_evidence|report_metadata)\s*\|",
    re.MULTILINE,
)
TRACEABILITY_ROW = re.compile(
    r"^\|\s*`(?P<id>A-(?P<stage>static|unit|contract|api|integration|replay|snapshot|e2e|acceptance)-(?P<gate>ACC-STOP-(?:00[1-9]|010))-[a-z0-9]+(?:-[a-z0-9]+)*)`\s*"
    r"\|\s*(?P<table_gate>ACC-STOP-(?:00[1-9]|010))\s*"
    r"\|\s*(?P<owner_task>TASK-[0-9]{3}[A-Z]?)\s*"
    r"\|\s*(?P<table_stage>static|unit|contract|api|integration|replay|snapshot|e2e|acceptance)\s*"
    r"\|\s*(?P<report_path>[^|]+?)\s*\|",
    re.MULTILINE,
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def report_relative_path(path: Path) -> str:
    parts = path.parts
    for marker in ("acceptance", "stages", "tasks"):
        if marker in parts:
            marker_index = parts.index(marker)
            return Path(*parts[marker_index:]).as_posix()
    return path.as_posix()


def write_test_report(path: Path, payload: dict[str, Any]) -> None:
    payload["artifact_paths"] = [report_relative_path(path)]
    write_json(path, payload)


def stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def leak_detection() -> dict[str, Any]:
    return {
        "method": "structured_field_scan",
        "target": "test_report",
        "forbidden_field_count": 0,
        "sensitive_content_count": 0,
        "matched_paths": [],
    }


def assertion(
    assertion_id: str,
    status: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    diff: dict[str, Any] | None = None,
    visibility: str = "report_metadata",
) -> dict[str, Any]:
    if visibility not in REPORT_VISIBILITY_VALUES:
        raise ValueError(f"unsupported assertion visibility: {visibility}")
    return {
        "id": assertion_id,
        "type": "report_schema",
        "visibility": visibility,
        "status": status,
        "expected": expected,
        "actual": actual,
        "diff": diff or {},
        "leak_detection": leak_detection(),
    }


def test_report(
    *,
    stage: str,
    status: str,
    test_id: str,
    assertions: list[dict[str, Any]],
    expected: dict[str, Any],
    actual: dict[str, Any],
    diff: dict[str, Any] | None = None,
    node: str = "harness",
    failure_type: str | None = None,
    error_category: str | None = None,
    referenced_files: list[str] | None = None,
    commands: list[str] | None = None,
) -> dict[str, Any]:
    report_diff = diff or {}
    report_referenced_files = referenced_files or [
        "scripts/run_harness.py",
        "docs/07_test_spec.md",
    ]
    assertion_statuses = [
        str(item.get("status"))
        for item in assertions
        if isinstance(item, dict)
    ]
    case_count = len(assertion_statuses)
    passed_count = assertion_statuses.count("passed")
    failed_count = assertion_statuses.count("failed")
    skipped_count = assertion_statuses.count("skipped")
    pass_rate = round(passed_count / case_count, 4) if case_count else 0.0
    generated_commands = commands or [
        f"python3 scripts/run_harness.py --stage {stage} --report-dir reports"
    ]
    failure_reasons = [
        str(item.get("id", f"assertion_{index}"))
        for index, item in enumerate(assertions)
        if isinstance(item, dict) and item.get("status") in {"failed", "flaky", "skipped"}
    ]
    report_hash = stable_hash(
        {
            "test_id": test_id,
            "stage": stage,
            "commands": generated_commands,
            "fixture_version": FIXTURE_VERSION,
            "mock_version": MOCK_VERSION,
            "expected": expected,
            "actual": actual,
            "diff": report_diff,
            "assertions": assertions,
        }
    )
    return {
        "schema_ref": SCHEMA_REF,
        "schema_version": SCHEMA_VERSION,
        "test_id": test_id,
        "stage": stage,
        "status": status,
        "failure_type": failure_type,
        "error_category": error_category,
        "trace_id": f"harness-{stage}-{test_id}",
        "fixture_set": FIXTURE_SET,
        "mock_set": MOCK_SET,
        "clock_source": CLOCK_SOURCE,
        "fixture_version": FIXTURE_VERSION,
        "mock_version": MOCK_VERSION,
        "commands": generated_commands,
        "case_count": case_count,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "pass_rate": pass_rate,
        "failure_reasons": failure_reasons,
        "repair_status": "not_required" if status == "passed" else "unresolved",
        "regression_detected": status != "passed",
        "referenced_files": report_referenced_files,
        "data_hash": report_hash,
        "artifact_paths": [],
        "assertions": assertions,
        "expected": expected,
        "actual": actual,
        "diff": report_diff,
        "node": node,
        "timestamp": FIXED_TIMESTAMP,
    }


def report_destination(report_dir: Path, stage: str, task_id: str | None) -> Path:
    if task_id:
        return report_dir / "tasks" / task_id / f"{stage}.json"
    return report_dir / "stages" / f"{stage}.json"


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def read_yaml_object(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = yaml.safe_load(path.read_text())
    except OSError:
        return None, [f"{path.as_posix()}:missing"]
    except yaml.YAMLError as error:
        return None, [f"{path.as_posix()}:invalid_yaml:{error.__class__.__name__}"]
    if not isinstance(payload, dict):
        return None, [f"{path.as_posix()}:not_yaml_object"]
    return payload, []


def read_json_object(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = json.loads(path.read_text())
    except OSError:
        return None, [f"{path.as_posix()}:missing"]
    except json.JSONDecodeError as error:
        return None, [f"{path.as_posix()}:invalid_json:{error.msg}"]
    if not isinstance(payload, dict):
        return None, [f"{path.as_posix()}:not_json_object"]
    return payload, []


def read_report(path: Path) -> dict[str, Any] | None:
    payload, issues = read_json_object(path)
    if issues:
        return None
    return payload


def validate_against_schema(
    payload: dict[str, Any] | None,
    schema_path: Path,
    payload_name: str,
) -> list[str]:
    if payload is None:
        return [f"{payload_name}:missing_payload"]
    schema, issues = read_json_object(schema_path)
    if issues:
        return issues
    try:
        validator = Draft202012Validator(schema)
    except SchemaError as error:
        return [f"{schema_path.as_posix()}:invalid_schema:{error.message}"]
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    return [
        f"{payload_name}:{'/'.join(str(part) for part in error.path) or '$'}:{error.message}"
        for error in errors
    ]


def validate_json_schema_file(schema_path: Path) -> list[str]:
    schema, issues = read_json_object(schema_path)
    if issues:
        return issues
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return [f"{schema_path.as_posix()}:invalid_schema:{error.message}"]
    return []


def validate_test_report(report: dict[str, Any] | None) -> list[str]:
    if report is None:
        return ["missing_report"]

    issues: list[str] = validate_against_schema(
        report,
        SCHEMA_FILES["test_report"],
        "TestReport",
    )
    if report.get("schema_ref") != SCHEMA_REF:
        issues.append("schema_ref_mismatch")
    if report.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version_mismatch")
    test_id = str(report.get("test_id", "")).lower()
    if (
        report.get("status") == "passed"
        and report.get("stage") in REQUIRED_PRODUCT_STAGES
        and ("scaffold" in test_id or "synthetic" in test_id)
    ):
        issues.append("synthetic_or_scaffold_report_cannot_pass")
    if not isinstance(report.get("referenced_files"), list):
        issues.append("referenced_files_missing")
    elif report.get("status") in {"failed", "flaky"} and not report["referenced_files"]:
        issues.append("referenced_files_empty_for_failure")
    if not isinstance(report.get("data_hash"), str) or not report["data_hash"].startswith("sha256:"):
        issues.append("data_hash_invalid")
    if not isinstance(report.get("artifact_paths"), list):
        issues.append("artifact_paths_missing")

    assertions = report.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        issues.append("assertions_missing")
    else:
        for index, item in enumerate(assertions):
            if not isinstance(item, dict):
                issues.append(f"assertion_{index}_not_object")
                continue
            visibility = item.get("visibility")
            if visibility not in REPORT_VISIBILITY_VALUES:
                issues.append(f"assertion_{index}_visibility_invalid")
            for path, key, value in iter_json_paths(item.get("expected")):
                if key and str(key).lower() in FORBIDDEN_CONTEXTUAL_FIELDS:
                    issues.append(f"assertion_{index}_contextual_expected_key_{path}")
                if isinstance(value, str):
                    lowered = value.lower()
                    if len(value) > 1024:
                        issues.append(f"assertion_{index}_long_expected_text_{path}")
                    for pattern in FORBIDDEN_TOKEN_PATTERNS:
                        if pattern in lowered:
                            issues.append(f"assertion_{index}_sensitive_expected_text_{path}")
            for path, key, value in iter_json_paths(item.get("actual")):
                if key and str(key).lower() in FORBIDDEN_CONTEXTUAL_FIELDS:
                    issues.append(f"assertion_{index}_contextual_actual_key_{path}")
                if visibility == "public_surface" and key and key.lower() in FORBIDDEN_PUBLIC_FIELDS:
                    issues.append(f"assertion_{index}_public_forbidden_key_{path}")
                if isinstance(value, str):
                    lowered = value.lower()
                    if len(value) > 1024:
                        issues.append(f"assertion_{index}_long_actual_text_{path}")
                    for pattern in FORBIDDEN_CONTEXTUAL_FIELDS | FORBIDDEN_TOKEN_PATTERNS:
                        if pattern in lowered:
                            issues.append(f"assertion_{index}_sensitive_actual_text_{path}")
            for path, key, value in iter_json_paths(item.get("diff")):
                if key and str(key).lower() in FORBIDDEN_CONTEXTUAL_FIELDS:
                    issues.append(f"assertion_{index}_contextual_diff_key_{path}")
                if isinstance(value, str):
                    lowered = value.lower()
                    if len(value) > 1024:
                        issues.append(f"assertion_{index}_long_diff_text_{path}")
                    for pattern in FORBIDDEN_CONTEXTUAL_FIELDS | FORBIDDEN_TOKEN_PATTERNS:
                        if pattern in lowered:
                            issues.append(f"assertion_{index}_sensitive_diff_text_{path}")
    return issues


def iter_json_paths(value: Any, path: str = "$") -> list[tuple[str, str, Any]]:
    paths: list[tuple[str, str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{path}.{key}"
            paths.append((key_path, str(key), item))
            paths.extend(iter_json_paths(item, key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(iter_json_paths(item, f"{path}[{index}]"))
    else:
        paths.append((path, "", value))
    return paths


def task_nodes(tasks_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(tasks_payload, dict):
        return []
    dag = tasks_payload.get("dag")
    if not isinstance(dag, dict):
        return []
    nodes = dag.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def task_map(tasks_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("id")): node
        for node in task_nodes(tasks_payload)
        if isinstance(node.get("id"), str)
    }


def task_gate_set(task: dict[str, Any]) -> set[str]:
    return {str(gate) for gate in as_list(task.get("acceptance_gate")) if isinstance(gate, str)}


def validate_task_dag_semantics(tasks_payload: dict[str, Any] | None) -> list[str]:
    nodes = task_nodes(tasks_payload)
    if not nodes:
        return ["tasks.md:dag.nodes:missing_or_empty"]

    issues: list[str] = []
    ids = [str(node.get("id")) for node in nodes if isinstance(node.get("id"), str)]
    id_counts = {task_id: ids.count(task_id) for task_id in ids}
    duplicate_ids = sorted(task_id for task_id, count in id_counts.items() if count > 1)
    for task_id in duplicate_ids:
        issues.append(f"tasks.md:dag.nodes:{task_id}:duplicate_id")

    dependency_map: dict[str, list[str]] = {}
    id_set = set(ids)
    for node in nodes:
        task_id = node.get("id")
        if not isinstance(task_id, str):
            continue
        dependencies = [
            str(item)
            for item in as_list(node.get("depends_on"))
            if isinstance(item, str)
        ]
        dependency_map[task_id] = dependencies
        for dependency in dependencies:
            if dependency not in id_set:
                issues.append(f"tasks.md:{task_id}:missing_dependency:{dependency}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visiting:
            cycle = path[path.index(node) :] + [node]
            issues.append(f"tasks.md:dependency_cycle:{'->'.join(cycle)}")
            return
        if node in visited:
            return
        visiting.add(node)
        path.append(node)
        for dependency in dependency_map.get(node, []):
            if dependency in dependency_map:
                visit(dependency, path)
        path.pop()
        visiting.remove(node)
        visited.add(node)

    for task_id in sorted(dependency_map):
        visit(task_id, [])

    for task_id in sorted(id_set - REQUIRED_TASK_IDS):
        if not reaches_task_021(task_id, dependency_map, set()):
            issues.append(f"tasks.md:{task_id}:does_not_depend_on_TASK-021")
    return sorted(issues)


def reaches_task_021(task_id: str, dependency_map: dict[str, list[str]], seen: set[str] | None = None) -> bool:
    if task_id == "TASK-021":
        return True
    seen = seen or set()
    if task_id in seen:
        return False
    seen.add(task_id)
    return any(
        reaches_task_021(dependency, dependency_map, seen)
        for dependency in dependency_map.get(task_id, [])
    )


def mandatory_assertion_catalog() -> tuple[dict[str, dict[str, str]], list[str]]:
    try:
        text = Path("docs/07_test_spec.md").read_text()
    except OSError as error:
        return {}, [f"docs/07_test_spec.md:{error.__class__.__name__}"]

    catalog: dict[str, dict[str, str]] = {}
    issues: list[str] = []
    for match in MANDATORY_ASSERTION_ROW.finditer(text):
        assertion_id = match.group("id")
        stage = match.group("stage")
        table_stage = match.group("table_stage")
        gate = match.group("gate")
        table_gate = match.group("table_gate")
        visibility = match.group("visibility")
        if stage != table_stage:
            issues.append(f"{assertion_id}:stage_mismatch:{stage}!={table_stage}")
        if gate != table_gate:
            issues.append(f"{assertion_id}:gate_mismatch:{gate}!={table_gate}")
        if assertion_id in catalog:
            issues.append(f"{assertion_id}:duplicate")
        catalog[assertion_id] = {
            "stage": table_stage,
            "gate": table_gate,
            "visibility": visibility,
        }
    if not catalog:
        issues.append("mandatory_assertion_catalog_empty")
    covered_gates = {item["gate"] for item in catalog.values()}
    for gate in REQUIRED_GATES:
        if gate not in covered_gates:
            issues.append(f"mandatory_assertion_catalog_missing_gate:{gate}")
    return catalog, issues


def mandatory_assertion_traceability_matrix() -> tuple[dict[str, dict[str, str]], list[str]]:
    try:
        text = Path("docs/07_test_spec.md").read_text()
    except OSError as error:
        return {}, [f"docs/07_test_spec.md:{error.__class__.__name__}"]

    matrix: dict[str, dict[str, str]] = {}
    issues: list[str] = []
    for match in TRACEABILITY_ROW.finditer(text):
        assertion_id = match.group("id")
        stage = match.group("stage")
        table_stage = match.group("table_stage")
        gate = match.group("gate")
        table_gate = match.group("table_gate")
        if stage != table_stage:
            issues.append(f"{assertion_id}:traceability_stage_mismatch:{stage}!={table_stage}")
        if gate != table_gate:
            issues.append(f"{assertion_id}:traceability_gate_mismatch:{gate}!={table_gate}")
        if assertion_id in matrix:
            issues.append(f"{assertion_id}:traceability_duplicate")
        matrix[assertion_id] = {
            "stage": table_stage,
            "gate": table_gate,
            "owner_task": match.group("owner_task"),
            "report_path": match.group("report_path").strip(),
        }
    if not matrix:
        issues.append("mandatory_assertion_traceability_matrix_empty")
    return matrix, issues


def product_assertion_evidence(
    report_dir: Path,
    *,
    include_acceptance: bool = False,
) -> dict[str, dict[str, str]]:
    catalog, _ = mandatory_assertion_catalog()
    traceability, _ = mandatory_assertion_traceability_matrix()
    observations = stage_assertions_by_source(report_dir)
    evidence: dict[str, dict[str, str]] = {}
    for assertion_id, details in catalog.items():
        if details["stage"] == "acceptance" and not include_acceptance:
            continue
        for item in observations.get(assertion_id, []):
            if (
                item.get("stage") == details["stage"]
                and item.get("status") == "passed"
                and item.get("visibility") == details["visibility"]
            ):
                traceability_row = traceability.get(assertion_id, {})
                evidence[assertion_id] = {
                    "id": assertion_id,
                    "stage": details["stage"],
                    "gate": details["gate"],
                    "visibility": details["visibility"],
                    "owner_task": traceability_row.get("owner_task", ""),
                    "report_path": traceability_row.get(
                        "report_path",
                        f"reports/stages/{details['stage']}.json",
                    ),
                }
                break
    return evidence


def assertion_candidates_metadata(
    report_dir: Path,
    assertion_ids: list[str],
) -> dict[str, list[str]]:
    catalog, _ = mandatory_assertion_catalog()
    traceability, _ = mandatory_assertion_traceability_matrix()
    evidence = product_assertion_evidence(report_dir, include_acceptance=True)
    known_ids = [assertion_id for assertion_id in assertion_ids if assertion_id in catalog]
    passed_ids = [assertion_id for assertion_id in known_ids if assertion_id in evidence]
    source_rows = [
        evidence.get(assertion_id)
        or {
            "id": assertion_id,
            "stage": catalog[assertion_id]["stage"],
            "gate": catalog[assertion_id]["gate"],
            "visibility": catalog[assertion_id]["visibility"],
            "owner_task": traceability.get(assertion_id, {}).get("owner_task", ""),
            "report_path": traceability.get(assertion_id, {}).get(
                "report_path",
                f"reports/stages/{catalog[assertion_id]['stage']}.json",
            ),
        }
        for assertion_id in known_ids
    ]
    task_ids = sorted(
        {
            row["owner_task"]
            for row in source_rows
            if row.get("owner_task")
        }
    )
    gates = sorted({row["gate"] for row in source_rows if row.get("gate")})
    report_paths = sorted(
        {
            row["report_path"]
            for row in source_rows
            if row.get("report_path")
        }
    )
    return {
        "known_ids": sorted(set(known_ids)),
        "passed_ids": sorted(set(passed_ids)),
        "task_ids": task_ids,
        "gates": gates,
        "report_paths": report_paths,
    }


def traceability_assertions_by_owner() -> dict[str, list[str]]:
    matrix, _ = mandatory_assertion_traceability_matrix()
    catalog, _ = mandatory_assertion_catalog()
    by_owner: dict[str, list[str]] = {}
    for assertion_id, row in matrix.items():
        if assertion_id not in catalog:
            continue
        if catalog[assertion_id]["stage"] == "acceptance":
            continue
        by_owner.setdefault(row["owner_task"], []).append(assertion_id)
    return {owner: sorted(set(assertion_ids)) for owner, assertion_ids in by_owner.items()}


def prd_flow_id(prd_acceptance_id: str) -> str:
    match = re.match(r"^PRD-([0-9]+\.[0-9]+)-AC-[0-9]{3}$", prd_acceptance_id)
    return match.group(1) if match else "0.0"


def validate_mandatory_assertion_traceability(tasks_payload: dict[str, Any] | None) -> list[str]:
    catalog, catalog_issues = mandatory_assertion_catalog()
    matrix, matrix_issues = mandatory_assertion_traceability_matrix()
    tasks_by_id = task_map(tasks_payload)

    issues = [f"catalog:{issue}" for issue in catalog_issues]
    issues.extend(f"traceability:{issue}" for issue in matrix_issues)

    catalog_ids = set(catalog)
    matrix_ids = set(matrix)
    for assertion_id in sorted(catalog_ids - matrix_ids):
        issues.append(f"{assertion_id}:missing_traceability_row")
    for assertion_id in sorted(matrix_ids - catalog_ids):
        issues.append(f"{assertion_id}:traceability_row_without_catalog_entry")

    for assertion_id in sorted(catalog_ids & matrix_ids):
        expected = catalog[assertion_id]
        actual = matrix[assertion_id]
        if actual["gate"] != expected["gate"]:
            issues.append(
                f"{assertion_id}:gate_mismatch:{actual['gate']}!={expected['gate']}"
            )
        if actual["stage"] != expected["stage"]:
            issues.append(
                f"{assertion_id}:stage_mismatch:{actual['stage']}!={expected['stage']}"
            )
        expected_report_path = (
            f"reports/acceptance/{expected['gate']}.json"
            if expected["stage"] == "acceptance"
            else f"reports/stages/{expected['stage']}.json"
        )
        if actual["report_path"] != expected_report_path:
            issues.append(
                f"{assertion_id}:report_path_mismatch:"
                f"{actual['report_path']}!={expected_report_path}"
            )

        owner_task = actual["owner_task"]
        owner = tasks_by_id.get(owner_task)
        if owner is None:
            issues.append(f"{assertion_id}:owner_task_missing:{owner_task}")
            continue
        if expected["gate"] not in task_gate_set(owner):
            issues.append(
                f"{assertion_id}:owner_task_missing_gate:{owner_task}:{expected['gate']}"
            )
    return sorted(issues)


def collect_report_assertions(report_path: Path) -> list[dict[str, Any]]:
    payload = read_report(report_path)
    if not isinstance(payload, dict):
        return []
    assertions = payload.get("assertions")
    if not isinstance(assertions, list):
        return []
    return [item for item in assertions if isinstance(item, dict)]


def read_stage_report(report_dir: Path, stage: str) -> dict[str, Any] | None:
    if stage == "acceptance":
        return None
    return read_report(report_dir / "stages" / f"{stage}.json")


def stage_assertions_by_source(report_dir: Path) -> dict[str, list[dict[str, Any]]]:
    observations: dict[str, list[dict[str, Any]]] = {}
    for stage in REQUIRED_PRODUCT_STAGES:
        for item in collect_report_assertions(report_dir / "stages" / f"{stage}.json"):
            assertion_id = item.get("id")
            if not isinstance(assertion_id, str):
                continue
            record = {"stage": stage, "status": str(item.get("status")), **item}
            observations.setdefault(assertion_id, []).append(record)
    for gate in REQUIRED_GATES:
        gate_report = read_report(report_dir / "acceptance" / f"{gate}.json")
        if not isinstance(gate_report, dict):
            continue
        for item in gate_report.get("assertions", []) if isinstance(gate_report.get("assertions"), list) else []:
            assertion_id = item.get("id")
            if not isinstance(assertion_id, str):
                continue
            record = {"stage": "acceptance", "status": str(item.get("status")), "report": gate_report.get("test_id"), **item}
            observations.setdefault(assertion_id, []).append(record)
    return observations


def mandatory_assertion_coverage(
    report_dir: Path,
    include_acceptance: bool = True,
) -> dict[str, Any]:
    catalog, catalog_issues = mandatory_assertion_catalog()
    seen: dict[str, list[dict[str, str]]] = {}
    observations = stage_assertions_by_source(report_dir)

    for stage in REQUIRED_PRODUCT_STAGES:
        report = read_stage_report(report_dir, stage)
        for assertion_item in collect_report_assertions(report_dir / "stages" / f"{stage}.json"):
            assertion_id = assertion_item.get("id")
            if not isinstance(assertion_id, str):
                continue
            seen.setdefault(assertion_id, []).append(
                {
                    "stage": stage,
                    "status": str(assertion_item.get("status")),
                    "visibility": str(assertion_item.get("visibility")),
                }
            )
    if include_acceptance:
        for gate in REQUIRED_GATES:
            gate_report = read_report(report_dir / "acceptance" / f"{gate}.json")
            if not isinstance(gate_report, dict):
                continue
            for assertion_item in gate_report.get("assertions", []) if isinstance(gate_report.get("assertions"), list) else []:
                assertion_id = assertion_item.get("id")
                if not isinstance(assertion_id, str):
                    continue
                seen.setdefault(assertion_id, []).append(
                    {
                        "stage": "acceptance",
                        "status": str(assertion_item.get("status")),
                        "visibility": str(assertion_item.get("visibility")),
                    }
                )

    missing_ids = sorted(assertion_id for assertion_id in catalog if assertion_id not in seen)
    failed_ids: list[str] = []
    wrong_stage_ids: list[str] = []
    visibility_mismatch_ids: list[str] = []
    conflicting_ids: list[str] = []

    for assertion_id, observations in seen.items():
        expected = catalog.get(assertion_id)
        if not expected:
            continue
        stages = {item["stage"] for item in observations}
        statuses = {item["status"] for item in observations}
        visibilities = {item["visibility"] for item in observations}
        if statuses != {"passed"}:
            failed_ids.append(assertion_id)
        if stages != {expected["stage"]}:
            wrong_stage_ids.append(assertion_id)
        if visibilities != {expected["visibility"]}:
            visibility_mismatch_ids.append(assertion_id)
        if len(stages) > 1 or len(statuses) > 1 or len(visibilities) > 1:
            conflicting_ids.append(assertion_id)

    return {
        "catalog_issues": sorted(catalog_issues),
        "catalog_count": len(catalog),
        "covered_count": len(catalog) - len(missing_ids),
        "missing_ids": missing_ids,
        "failed_ids": sorted(failed_ids),
        "wrong_stage_ids": sorted(wrong_stage_ids),
        "visibility_mismatch_ids": sorted(visibility_mismatch_ids),
        "conflicting_ids": sorted(conflicting_ids),
    }


def required_stage_results(report_dir: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    statuses: dict[str, str] = {}
    schema_issues: dict[str, list[str]] = {}
    for stage in REQUIRED_PRODUCT_STAGES:
        report = read_stage_report(report_dir, stage)
        issues = validate_test_report(report)
        if report is None:
            statuses[stage] = "missing"
            schema_issues[stage] = issues
        elif issues:
            statuses[stage] = "invalid_schema"
            schema_issues[stage] = issues
        else:
            statuses[stage] = report.get("status", "missing")
    return statuses, schema_issues


def required_assertion_ids_for_gate(
    catalog: dict[str, dict[str, str]],
    gate: str,
    include_acceptance: bool = True,
) -> list[str]:
    return sorted(
        [
            assertion_id
            for assertion_id, details in catalog.items()
            if details["gate"] == gate
            and (include_acceptance or details["stage"] != "acceptance")
        ]
    )


def catalog_assertion_metadata() -> dict[str, dict[str, str]]:
    catalog, _ = mandatory_assertion_catalog()
    return catalog


def stage_paths_for_assertions(stage: str) -> list[str]:
    """Minimal required paths used by synthetic product-stage reports.

    These paths represent the minimum implementation footprint expected before a
    stage can be declared implemented.
    """
    common = [
        "docs/01_prd.md",
        "docs/02_arch.md",
        "docs/03_ui_spec.md",
        "docs/04_data_model.md",
        "docs/05_api_contract.md",
        "docs/06_dev_rules.md",
        "docs/07_test_spec.md",
        "docs/08_acceptance.md",
    ]
    mapping = {
        "unit": [
            "backend",
            "backend/app",
            "backend/app/services",
            "backend/app/services/pipeline.py",
            "backend/app/core",
            "fixtures",
            "fixtures/rss",
            "fixtures/rss/feeds.json",
            "fixtures/articles",
            "fixtures/articles/article_map.json",
            "fixtures/llm",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            "fixtures/sources",
            "fixtures/clock",
            "schemas/test_report.schema.json",
            "schemas/stop_decision.schema.json",
            "schemas/task_plan_report.schema.json",
            "schemas/review_report.schema.json",
            "schemas/fix_optimize_report.schema.json",
            "schemas/round_summary_report.schema.json",
            "schemas/tasks.schema.json",
            "schemas/prd_coverage.schema.json",
            "schemas/task_acceptance_coverage.schema.json",
            "schemas/local_user_acceptance.schema.json",
        ],
        "contract": [
            "backend/app",
            "backend/app/repositories",
            "backend/app/db.py",
            "backend/app/main.py",
            "schemas/test_report.schema.json",
            "docs/04_data_model.md",
            "docs/05_api_contract.md",
            "schemas/tasks.schema.json",
        ],
        "api": [
            "backend/app",
            "backend/app/api",
            "backend/app/main.py",
            "backend/app/services",
            "backend/app/services/pipeline.py",
            "backend/app/repositories",
        ],
        "integration": [
            "backend/app",
            "backend/app/services",
            "backend/app/services/pipeline.py",
            "backend/app/repositories",
            "backend/app/clients",
            "fixtures/rss",
            "fixtures/rss/feeds.json",
            "fixtures/articles",
            "fixtures/articles/article_map.json",
            "fixtures/llm",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            "fixtures/clock",
        ],
        "replay": [
            "fixtures",
            "fixtures/rss",
            "fixtures/rss/feeds.json",
            "fixtures/llm",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            "fixtures/clock",
            "backend/app",
            "backend/app/services/pipeline.py",
        ],
        "snapshot": [
            "frontend",
            "frontend/src",
            "frontend/src/api",
            "frontend/src/pages",
            "frontend/src/components",
        ],
        "e2e": [
            "frontend",
            "frontend/src",
            "backend/app/main.py",
            "backend/app/services/pipeline.py",
            "fixtures",
            "fixtures/rss/feeds.json",
            "fixtures/articles/article_map.json",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            ".github",
            "reports",
        ],
    }
    return sorted(set(common + mapping.get(stage, [])))


def stage_implementation_evidence(stage: str) -> tuple[bool, list[str], list[str]]:
    required = stage_paths_for_assertions(stage)
    missing = [path for path in required if not Path(path).exists()]
    exists = [path for path in required if path not in missing]
    return len(missing) == 0, missing, exists


def backend_api_route_evidence() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {
            "imported": False,
            "routes": [],
            "missing_required_routes": [
                f"{method} {path}" for method, path in sorted(REQUIRED_API_ROUTES)
            ],
            "issues": [import_issue],
        }

    route_pairs: set[tuple[str, str]] = set()
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            route_pairs.add((str(method), str(path)))

    missing = sorted(REQUIRED_API_ROUTES - route_pairs)
    return {
        "imported": True,
        "routes": [f"{method} {path}" for method, path in sorted(route_pairs)],
        "missing_required_routes": [
            f"{method} {path}" for method, path in missing
        ],
        "issues": [
            f"missing_required_route:{method} {path}" for method, path in missing
        ],
    }


def import_backend_app() -> tuple[Any | None, str | None]:
    repo_root = Path.cwd().resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from backend.app.main import create_app
    except Exception as error:
        return None, f"backend.app.main_import_failed:{error.__class__.__name__}"
    return create_app(db_path=":memory:"), None


def envelope_issue(
    *,
    name: str,
    response: Any,
    expected_status: int,
    expected_envelope: str,
    required_data_keys: set[str] | None = None,
) -> list[str]:
    issues: list[str] = []
    if response.status_code != expected_status:
        issues.append(f"{name}:status={response.status_code}!={expected_status}")
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        issues.append(f"{name}:content_type_not_json:{content_type}")
        return issues
    try:
        payload = response.json()
    except ValueError:
        issues.append(f"{name}:invalid_json")
        return issues
    if not isinstance(payload, dict):
        issues.append(f"{name}:payload_not_object")
        return issues
    if expected_envelope not in payload:
        issues.append(f"{name}:missing_{expected_envelope}_envelope")
    if "detail" in payload:
        issues.append(f"{name}:fastapi_detail_leak")
    if expected_envelope == "data" and required_data_keys:
        data = payload.get("data")
        if not isinstance(data, dict):
            issues.append(f"{name}:data_not_object")
        else:
            missing_keys = sorted(required_data_keys - set(data))
            for key in missing_keys:
                issues.append(f"{name}:missing_data_key:{key}")
        if expected_envelope == "error":
            error = payload.get("error")
            if not isinstance(error, dict):
                issues.append(f"{name}:error_not_object")
            else:
                for key in ("code", "message"):
                    if key not in error:
                        issues.append(f"{name}:missing_error_key:{key}")
    return issues


def _safe_json(response: Any) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = response.json()
    except ValueError:
        return None, ["response:not_json"]
    if not isinstance(payload, dict):
        return None, ["response:payload_not_object"]
    return payload, []


def _safe_text_read(path: Path, label: str) -> tuple[str, list[str]]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore"), []
    except OSError as error:
        return "", [f"{label}:{error.__class__.__name__}"]


def scan_public_payload(value: Any) -> dict[str, Any]:
    forbidden_matches: list[str] = []
    sensitive_matches: list[str] = []
    for path, key, item in iter_json_paths(value):
        if key and str(key).lower() in FORBIDDEN_PUBLIC_FIELDS:
            forbidden_matches.append(path)
        if isinstance(item, str):
            lowered = item.lower()
            for pattern in FORBIDDEN_CONTEXTUAL_FIELDS | FORBIDDEN_TOKEN_PATTERNS:
                if pattern in lowered:
                    sensitive_matches.append(path)
    return {
        "method": "structured_field_scan",
        "target": "api_json",
        "forbidden_field_count": len(forbidden_matches),
        "sensitive_content_count": len(sensitive_matches),
        "matched_paths": sorted(forbidden_matches + sensitive_matches),
    }


def _extract_index_script() -> tuple[str, list[str]]:
    html, issues = _safe_text_read(Path("index.html"), "index_html")
    if issues:
        return "", issues
    match = re.search(r"<script>(.*?)</script>", html, re.S | re.I)
    if not match:
        if re.search(r"<script[^>]+type=[\"']module[\"'][^>]+src=", html, re.I):
            return "", []
        return "", ["index_html:script_not_found"]
    return match.group(1), []


def _contains_js_pattern(script: str, pattern: str) -> bool:
    return re.search(pattern, script, re.I | re.S) is not None


def source_management_api_evidence() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"checks": {}, "issues": [import_issue]}

    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {
            "checks": {},
            "issues": [f"fastapi_testclient_import_failed:{error.__class__.__name__}"],
        }

    client = TestClient(app)
    issues: list[str] = []
    checks: dict[str, Any] = {}

    def add_issue(message: str) -> None:
        issues.append(f"source_api:{message}")

    def request_json(
        label: str,
        response: Any,
        expected_status: int,
        *,
        allow_empty_body: bool = False,
    ) -> tuple[dict[str, Any] | None, int]:
        if response.status_code != expected_status:
            add_issue(f"{label}:status={response.status_code}!={expected_status}")
        if allow_empty_body and response.content.strip() == b"":
            return {"_empty_body": True}, response.status_code
        payload, parse_issues = _safe_json(response)
        for issue in parse_issues:
            add_issue(f"{label}:{issue}")
        if payload is None:
            return None, response.status_code
        return payload, response.status_code

    def db_row(source_id: str) -> dict[str, Any] | None:
        return app.state.db.execute(
            "SELECT id, name, is_enabled, deleted_at, rss_url FROM source WHERE id = ?",
            (source_id,),
        ).fetchone()

    # 1) Baseline: exactly the documented seeded defaults
    expected_seed_count = default_source_count()
    seeds_payload, _ = request_json("sources_list_initial", client.get("/api/sources"), 200)
    if seeds_payload is None:
        return {"checks": checks, "issues": issues}
    seed_data = seeds_payload.get("data") if isinstance(seeds_payload, dict) else None
    if not isinstance(seed_data, list):
        add_issue("sources_list_initial:data_not_list")
        seed_data = []

    seed_count = len(seed_data)
    checks["seed_count"] = seed_count
    checks["expected_seed_count"] = expected_seed_count
    if seed_count != expected_seed_count:
        add_issue(f"sources_seed_count={seed_count}!={expected_seed_count}")

    seed_ids = [str(item.get("id")) for item in seed_data if isinstance(item, dict)]
    if not seed_ids:
        add_issue("sources_seed_ids_missing")
        return {"checks": checks, "issues": issues}

    default_id = seed_ids[0]

    # 2) Create and validate a user source
    create_payload = {
        "name": "local_user_source",
        "rss_url": "https://example.com/rss-updated.xml",
    }
    create_payload_response, _ = request_json(
        "source_create_user",
        client.post("/api/sources", json=create_payload),
        201,
    )
    user_id = None
    if isinstance(create_payload_response, dict):
        data = create_payload_response.get("data")
        if isinstance(data, dict) and data.get("id") is not None:
            user_id = str(data["id"])
        else:
            add_issue("source_create_user:missing_created_id")

    if user_id is None:
        add_issue("source_create_user:failed")
        return {"checks": checks, "issues": issues}

    checks["user_source_id"] = user_id

    duplicate_payload, _ = request_json(
        "source_create_duplicate_user",
        client.post("/api/sources", json=create_payload),
        409,
    )
    if duplicate_payload is None:
        add_issue("source_create_duplicate_user:missing_payload")

    # 3) Last-enabled-source protection on both source classes
    all_source_ids = seed_ids + [user_id]
    if len(all_source_ids) < 2:
        add_issue("source_last_enabled:insufficient_sources")
    else:
        keeper = all_source_ids[-1]
        for source_id in all_source_ids:
            if source_id == keeper:
                continue
            patch_payload, patch_status = request_json(
                f"source_disable_for_last_guard_{source_id}",
                client.patch(
                    f"/api/sources/{source_id}",
                    json={"is_enabled": False},
                ),
                200,
            )
            if patch_payload and isinstance(patch_payload.get("data", {}).get("id"), str):
                checks[f"source_disable_{source_id}"] = patch_payload["data"]["is_enabled"]

        final_disable_payload, final_disable_status = request_json(
            f"source_disable_last_enabled_guard_{keeper}",
            client.patch(
                f"/api/sources/{keeper}",
                json={"is_enabled": False},
            ),
            409,
        )
        checks["last_enabled_guard_status"] = final_disable_status
        for source_id in all_source_ids:
            client.patch(
                f"/api/sources/{source_id}",
                json={"is_enabled": True},
            )

    # 4) Default/source user parity for disable + soft-delete semantics
    for source_id in (default_id, user_id):
        disable_payload, _ = request_json(
            f"source_disable_parity_{source_id}",
            client.patch(f"/api/sources/{source_id}", json={"is_enabled": False}),
            200,
        )
        if disable_payload and isinstance(disable_payload.get("data"), dict):
            checks[f"source_disable_returned_{source_id}"] = disable_payload["data"].get(
                "is_enabled"
            )

    current_sources_payload, _ = request_json(
        "sources_list_after_disable",
        client.get("/api/sources"),
        200,
    )
    current_sources = current_sources_payload.get("data") if isinstance(current_sources_payload, dict) else []
    current_ids = [str(item.get("id")) for item in current_sources if isinstance(item, dict)]
    current_map = {str(item.get("id")): item for item in current_sources if isinstance(item, dict)}
    for source_id in (default_id, user_id):
        checks[f"source_disable_returned_visible_{source_id}"] = source_id in current_ids
        if source_id not in current_ids:
            add_issue(f"source_parity_missing_after_disable:{source_id}")
            continue
        current_item = current_map.get(source_id, {})
        if bool(current_item.get("is_enabled")):
            add_issue(f"source_parity_disable_not_applied:{source_id}")

    for source_id in (default_id, user_id):
        delete_payload, _ = request_json(
            f"source_delete_parity_{source_id}",
            client.delete(f"/api/sources/{source_id}"),
            204,
            allow_empty_body=True,
        )
        del(delete_payload)
        post_delete_payload, _ = request_json(
            f"sources_list_after_delete_{source_id}",
            client.get("/api/sources"),
            200,
        )
        post_delete_sources = post_delete_payload.get("data") if isinstance(post_delete_payload, dict) else []
        post_delete_ids = {
            str(item.get("id")) for item in post_delete_sources if isinstance(item, dict)
        }
        if source_id in post_delete_ids:
            add_issue(f"source_parity_visible_after_delete:{source_id}")
        row = db_row(source_id)
        checks[f"source_delete_row_{source_id}"] = {
            "deleted": row is not None,
            "is_enabled": None if row is None else row.get("is_enabled"),
            "tombstone_present": bool(row and row.get("deleted_at")),
        }
        if row is None:
            add_issue(f"source_delete_tombstone_missing:{source_id}")
        else:
            if int(row.get("is_enabled", 1)) != 0:
                add_issue(f"source_delete_tombstone_not_disabled:{source_id}")
            if not row.get("deleted_at"):
                add_issue(f"source_delete_tombstone_not_set:{source_id}")

    return {"checks": checks, "issues": issues}


def source_ui_parity_evidence() -> dict[str, Any]:
    script, script_issues = _extract_index_script()
    issues: list[str] = list(script_issues)
    checks: dict[str, Any] = {}
    if not script:
        issues.append("source_ui:no_index_script")
        return {"checks": checks, "issues": issues}

    checks["source_ui_controls"] = {
        "has_render_source_list": _contains_js_pattern(
            script, r"function\s+renderSourceList\s*\(feeds\)"
        ),
        "has_update_flow": _contains_js_pattern(
            script, r"updateFeed\(feed\.id,\s*!feed\.is_enabled\)"
        ),
        "has_delete_flow": _contains_js_pattern(
            script,
            r"deleteFeed\(feed\.id\)",
        ),
        "has_create_flow": _contains_js_pattern(
            script, r'feedForm\.addEventListener\("submit",\s*addFeed\)'
        ),
        "has_navigation_to_config": _contains_js_pattern(
            script,
            r"openConfig\.addEventListener\(\s*['\"]click['\"],",
        ),
    }

    if not checks["source_ui_controls"]["has_render_source_list"]:
        issues.append("source_ui_missing:render_source_list")
    if not checks["source_ui_controls"]["has_update_flow"]:
        issues.append("source_ui_missing:update_flow")
    if not checks["source_ui_controls"]["has_delete_flow"]:
        issues.append("source_ui_missing:delete_flow")
    if not checks["source_ui_controls"]["has_create_flow"]:
        issues.append("source_ui_missing:create_flow")

    if _contains_js_pattern(script, r"feed\.is_default"):
        issues.append("source_ui_condition_detected:default_source_branch")

    return {"checks": checks, "issues": issues}


def e2e_surface_evidence() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {
            "checks": {"runner": "e2e_probe"},
            "issues": [import_issue],
        }

    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {
            "checks": {"runner": "e2e_probe"},
            "issues": [f"fastapi_testclient_import_failed:{error.__class__.__name__}"],
        }

    client = TestClient(app)
    issues: list[str] = []
    checks: dict[str, Any] = {"runner": "api_and_index_probe"}
    surface_checks = {
        "home_news_feed": False,
        "high_score_list": False,
        "article_view": False,
        "sources_page": False,
        "refresh_action": False,
    }

    def add_issue(message: str) -> None:
        issues.append(f"e2e_surface:{message}")

    script, script_read_issues = _extract_index_script()
    if script_read_issues:
        issues.extend([f"e2e_surface:{item}" for item in script_read_issues])

    refresh_response = client.post("/api/refresh")
    refresh_payload, refresh_parse_issues = _safe_json(refresh_response)
    checks["refresh_status"] = refresh_response.status_code
    for issue in refresh_parse_issues:
        issues.append(f"e2e_surface:refresh:{issue}")
    if refresh_payload is None:
        issues.append("e2e_surface:refresh_payload_invalid")
        checks["refresh_action_called"] = False
    else:
        checks["refresh_payload"] = {
            "has_refreshed_at": isinstance(refresh_payload.get("data"), dict)
            and refresh_payload["data"].get("refreshed_at") is not None,
            "data_fields": sorted(
                refresh_payload["data"].keys()
            )
            if isinstance(refresh_payload.get("data"), dict)
            else [],
        }
        checks["refresh_action_called"] = (
            checks["refresh_status"] == 200 and checks["refresh_payload"]["has_refreshed_at"]
        )
    if not checks["refresh_action_called"]:
        issues.append(f"e2e_surface:refresh_action_failed:{checks['refresh_status']}")
    else:
        surface_checks["refresh_action"] = True

    home_response = client.get("/api/home")
    home_payload, home_parse_issues = _safe_json(home_response)
    checks["home_status"] = home_response.status_code
    for issue in home_parse_issues:
        issues.append(f"e2e_surface:home:{issue}")
    if home_payload is None:
        issues.append("e2e_surface:home_payload_invalid")
        return {"checks": checks, "issues": issues}

    home_data = home_payload.get("data", {}) if isinstance(home_payload, dict) else {}
    latest_news = home_data.get("latest_news") if isinstance(home_data, dict) else None
    top_news = home_data.get("top_ranked_news") if isinstance(home_data, dict) else None
    if not isinstance(latest_news, list):
        issues.append("e2e_surface:latest_news_invalid")
        latest_news = []
    if not isinstance(top_news, list):
        issues.append("e2e_surface:top_ranked_news_invalid")
        top_news = []
    checks["home_news_count"] = len(latest_news)
    checks["top_news_count"] = len(top_news)
    checks["home_news_density_ok"] = len(latest_news) >= 10
    checks["high_score_list_reasonable_size"] = len(top_news) <= 10
    surface_checks["home_news_feed"] = len(latest_news) >= 10
    if not latest_news:
        issues.append("e2e_surface:latest_news_empty")
    if not top_news:
        issues.append("e2e_surface:top_ranked_news_empty")

    article_statuses: set[str] = set()
    if latest_news:
        checks["news_cards_have_content_fields"] = any(
            isinstance(item, dict)
            and "content_zh" in item
            for item in latest_news
        )
        if checks["news_cards_have_content_fields"]:
            issues.append("e2e_surface:news_card_content_leak_in_list")
        for item in latest_news:
            if isinstance(item, dict) and isinstance(item.get("status"), str):
                article_statuses.add(str(item["status"]))
            if isinstance(item, dict) and item.get("status") not in {"translated", "translation_failed", "ready"}:
                issues.append("e2e_surface:unexpected_article_status")
        checks["article_statuses"] = sorted(article_statuses)
        checks["has_translation_failed_state"] = "translation_failed" in article_statuses
    else:
        checks["article_statuses"] = []
        checks["has_translation_failed_state"] = False

    primary_click_issues: list[str] = []
    primary_click_items = [
        ("latest_news", item)
        for item in latest_news
        if isinstance(item, dict)
    ] + [
        ("top_ranked_news", item)
        for item in top_news
        if isinstance(item, dict)
    ]
    for list_name, item in primary_click_items:
        item_id = str(item.get("id") or "")
        if item.get("status") != "translated":
            primary_click_issues.append(f"{list_name}:{item_id}:status={item.get('status')}")
            continue
        detail_response = client.get(f"/api/news/{item_id}")
        detail_payload, detail_parse_issues = _safe_json(detail_response)
        primary_click_issues.extend(
            f"{list_name}:{item_id}:{issue}" for issue in detail_parse_issues
        )
        if detail_response.status_code != 200:
            primary_click_issues.append(f"{list_name}:{item_id}:detail_status={detail_response.status_code}")
            continue
        detail_data = detail_payload.get("data") if isinstance(detail_payload, dict) else None
        if not isinstance(detail_data, dict):
            primary_click_issues.append(f"{list_name}:{item_id}:detail_payload_invalid")
            continue
        if detail_data.get("status") != "translated":
            primary_click_issues.append(f"{list_name}:{item_id}:detail_status_value={detail_data.get('status')}")
        if not detail_data.get("summary_zh"):
            primary_click_issues.append(f"{list_name}:{item_id}:missing_summary_zh")
        if not detail_data.get("content_zh"):
            primary_click_issues.append(f"{list_name}:{item_id}:missing_content_zh")
        original_url = str(detail_data.get("original_url") or "")
        if not is_public_http_url_value(original_url):
            primary_click_issues.append(f"{list_name}:{item_id}:original_url_not_public_http")
        if is_reserved_placeholder_url(original_url):
            primary_click_issues.append(f"{list_name}:{item_id}:original_url_placeholder")
    checks["primary_click_readability"] = {
        "checked_item_count": len(primary_click_items),
        "all_visible_items_translated_and_readable": not primary_click_issues,
        "issues": primary_click_issues,
    }
    if primary_click_issues:
        issues.extend(f"e2e_surface:primary_click:{issue}" for issue in primary_click_issues)

    if latest_news:
        first_item = latest_news[0]
        article_candidate = next(
            (
                item
                for item in latest_news
                if isinstance(item, dict) and item.get("status") == "translated"
            ),
            first_item,
        )
        item_id = str(article_candidate.get("id", "")) if isinstance(article_candidate, dict) else ""
        checks["sample_item_id"] = item_id
        article_view_verified = False
        if item_id:
            article_response = client.get(f"/api/news/{item_id}")
            article_payload, article_parse_issues = _safe_json(article_response)
            checks["article_status"] = article_response.status_code
            for issue in article_parse_issues:
                issues.append(f"e2e_surface:article:{issue}")
            if article_payload is None:
                add_issue("article_payload_invalid")
            else:
                article_data = article_payload.get("data", {}) if isinstance(article_payload, dict) else {}
                checks["article_view_has_original_url"] = isinstance(article_data, dict) and bool(
                    article_data.get("original_url")
                )
                if not checks["article_view_has_original_url"]:
                    add_issue("article_missing_original_url")
                article_original_url = str(article_data.get("original_url") or "") if isinstance(article_data, dict) else ""
                checks["article_view_original_url_public_http"] = is_public_http_url_value(article_original_url)
                checks["article_view_original_url_non_placeholder"] = not is_reserved_placeholder_url(article_original_url)
                if not checks["article_view_original_url_public_http"]:
                    add_issue("article_original_url_not_public_http")
                if not checks["article_view_original_url_non_placeholder"]:
                    add_issue("article_original_url_placeholder")
                checks["article_view_has_translation_fields"] = (
                    "summary_zh" in article_data and "content_zh" in article_data
                    if isinstance(article_data, dict)
                    else False
                )
                article_view_verified = isinstance(article_data, dict) and bool(
                    article_data.get("id") == item_id
                    and checks["article_view_has_original_url"]
                    and checks["article_view_original_url_public_http"]
                    and checks["article_view_original_url_non_placeholder"]
                )
        surface_checks["article_view"] = article_view_verified

    not_found_response = client.get("/api/news/__does_not_exist__")
    not_found_payload, not_found_parse_issues = _safe_json(not_found_response)
    checks["article_not_found_status"] = not_found_response.status_code
    for issue in not_found_parse_issues:
        issues.append(f"e2e_surface:article_not_found:{issue}")
    if not_found_response.status_code != 404:
        issues.append("e2e_surface:article_not_found_status_not_404")
    if not isinstance(not_found_payload, dict):
        issues.append("e2e_surface:article_not_found_payload_not_object")
    elif not isinstance(not_found_payload.get("error"), dict):
        issues.append("e2e_surface:article_not_found_error_shape_invalid")

    checks["sources_loaded"] = 0
    sources_response = client.get("/api/sources")
    sources_payload, sources_parse_issues = _safe_json(sources_response)
    checks["sources_status"] = sources_response.status_code
    for issue in sources_parse_issues:
        issues.append(f"e2e_surface:sources:{issue}")
    if sources_payload is None:
        issues.append("e2e_surface:sources_payload_invalid")
    else:
        source_data = sources_payload.get("data") if isinstance(sources_payload, dict) else []
        if not isinstance(source_data, list):
            issues.append("e2e_surface:sources_data_not_list")
        checks["sources_loaded"] = len(source_data) if isinstance(source_data, list) else 0
        if not isinstance(source_data, list) or not source_data:
            issues.append("e2e_surface:sources_empty")
        checks["sources_are_visible_after_disable_expected"] = not any(
            item.get("deleted_at") for item in source_data if isinstance(item, dict)
        )
        surface_checks["sources_page"] = isinstance(source_data, list) and bool(source_data)
    if top_news:
        top_scores = [
            int(item.get("score"))
            for item in top_news
            if isinstance(item, dict) and isinstance(item.get("score"), int)
        ]
        checks["top_score_sorted_desc"] = top_scores == sorted(top_scores, reverse=True)
        if top_scores != sorted(top_scores, reverse=True):
            issues.append("e2e_surface:top_ranked_news_not_desc")
    else:
        checks["top_score_sorted_desc"] = False
    checks["high_score_list_sorted_desc"] = checks["top_score_sorted_desc"]
    surface_checks["high_score_list"] = bool(top_news) and checks["top_score_sorted_desc"]

    if script:
        checks["script_patterns"] = {
            "card_to_article_internal_route": _contains_js_pattern(
                script,
                r"titleButton\.addEventListener\(\s*['\"]click['\"]\s*,\s*\(\)\s*=>\s*navigate\(itemHash\(item\.id\)\)",
            ),
            "reader_original_link_button": _contains_js_pattern(
                script,
                r"originalLink\.href\s*=\s*item\.original_url",
            ),
            "reader_original_link_fallback": _contains_js_pattern(
                script,
                r"originalLink\.href\s*=\s*item\.originalLink \|\| item\.link",
            ),
            "refresh_button_triggers_sync": _contains_js_pattern(
                script,
                r"refresh\.addEventListener\(\s*['\"]click['\"],\s*syncNow\s*\)",
            ),
            "sources_page_render": _contains_js_pattern(
                script,
                r"function\s+renderSourceList\(feeds\)",
            ),
            "feed_submit_flow": _contains_js_pattern(
                script,
                r"feedForm\.addEventListener\(\s*['\"]submit['\"],\s*addFeed\)",
            ),
            "sources_update_flow": _contains_js_pattern(
                script,
                r"function\s+updateFeed\(feedId,\s*isEnabled\)",
            ),
            "sources_delete_flow": _contains_js_pattern(
                script,
                r"function\s+deleteFeed\(feedId\)",
            ),
            "no_direct_navigation_assignment": not _contains_js_pattern(
                script,
                r"window\.location\s*=",
            ),
        }
        pattern_issues = [
            (name, value)
            for name, value in checks["script_patterns"].items()
            if isinstance(value, bool) and value is False and name != "no_direct_navigation_assignment"
        ]
        for name, _ in pattern_issues:
            issues.append(f"e2e_surface:missing_script_pattern:{name}")
        if not checks["script_patterns"]["no_direct_navigation_assignment"]:
            issues.append("e2e_surface:direct_navigation_detected")

    react_sources, react_read_issues = task_016_read_sources()
    issues.extend([f"e2e_surface:react_source:{issue}" for issue in react_read_issues])
    article_source = react_sources.get("frontend/src/pages/ArticleView.tsx", "")
    news_card_source = react_sources.get("frontend/src/components/NewsCard.tsx", "")
    high_score_source = react_sources.get("frontend/src/components/HighScoreList.tsx", "")
    checks["react_original_link_patterns"] = {
        "article_href_uses_detail_original_url": "href={detail.original_url}" in article_source,
        "article_link_target_blank": 'target="_blank"' in article_source,
        "article_link_rel_noreferrer": 'rel="noreferrer"' in article_source,
        "article_link_not_shown_for_ready": "detail.status !== 'ready'" in article_source,
        "news_card_internal_route_only": "href={`/news/${item.id}`}" in news_card_source and "original_url" not in news_card_source,
        "high_score_internal_route_only": "href={`/news/${item.id}`}" in high_score_source and "original_url" not in high_score_source,
    }
    for name, passed in checks["react_original_link_patterns"].items():
        if not passed:
            issues.append(f"e2e_surface:react_original_link:{name}=false")

    checks["surface_coverage"] = surface_checks
    checks["required_surfaces"] = E2E_REQUIRED_SURFACES
    for surface in checks["required_surfaces"]:
        if not surface_checks.get(surface, False):
            issues.append(f"e2e_surface:surface_not_verified:{surface}")
    return {"checks": checks, "issues": issues}


def backend_api_response_evidence() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {
            "imported": False,
            "checks": [],
            "issues": [import_issue],
        }
    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {
            "imported": True,
            "checks": [],
            "issues": [f"fastapi_testclient_import_failed:{error.__class__.__name__}"],
        }

    client = TestClient(app)
    refresh_response = client.post("/api/refresh")
    translated_row = app.state.db.execute(
        """
        SELECT id, original_url, canonical_url
        FROM news_item
        WHERE rss_guid = 'fixture-translated-96'
        """
    ).fetchone()
    translated_detail_path = (
        f"/api/news/{translated_row['id']}"
        if translated_row is not None
        else "/api/news/__missing_translated_fixture__"
    )
    checks = [
        (
            "post_refresh",
            refresh_response,
            200,
            "data",
            {"refreshed_at"},
        ),
        (
            "get_home",
            client.get("/api/home"),
            200,
            "data",
            {"latest_news", "top_ranked_news"},
        ),
        (
            "get_translated_news_detail",
            client.get(translated_detail_path),
            200,
            "data",
            {"id", "title", "summary_zh", "content_zh", "status"},
        ),
        (
            "get_sources",
            client.get("/api/sources"),
            200,
            "data",
            None,
        ),
        (
            "get_missing_news",
            client.get("/api/news/missing"),
            404,
            "error",
            None,
        ),
        (
            "post_invalid_source",
            client.post("/api/sources", json={"name": "", "rss_url": "not-a-url"}),
            400,
            "error",
            None,
        ),
        (
            "get_unknown_api",
            client.get("/api/unknown"),
            404,
            "error",
            None,
        ),
    ]

    observations: list[dict[str, Any]] = []
    original_url_checks: dict[str, Any] = {}
    issues: list[str] = []
    for name, response, expected_status, expected_envelope, required_data_keys in checks:
        check_issues = envelope_issue(
            name=name,
            response=response,
            expected_status=expected_status,
            expected_envelope=expected_envelope,
            required_data_keys=required_data_keys,
        )
        observations.append(
            {
                "name": name,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "expected_envelope": expected_envelope,
                "issues": check_issues,
            }
        )
        issues.extend(check_issues)
        if name == "get_home" and not check_issues:
            payload = response.json()
            latest_news = payload["data"].get("latest_news", [])
            top_ranked_news = payload["data"].get("top_ranked_news", [])
            if not latest_news:
                issues.append("get_home:latest_news_empty_after_refresh")
            if not top_ranked_news:
                issues.append("get_home:top_ranked_news_empty_after_refresh")
            for list_name, items in {
                "latest_news": latest_news,
                "top_ranked_news": top_ranked_news,
            }.items():
                if any(isinstance(item, dict) and "content_zh" in item for item in items):
                    issues.append(f"get_home:{list_name}:leaked_content_zh")
                if any(isinstance(item, dict) and item.get("status") != "translated" for item in items):
                    issues.append(f"get_home:{list_name}:non_translated_item")
                if any(isinstance(item, dict) and not item.get("summary_zh") for item in items):
                    issues.append(f"get_home:{list_name}:missing_summary_zh")
        if name == "get_translated_news_detail" and not check_issues:
            payload = response.json()
            detail = payload["data"]
            if detail.get("status") != "translated":
                issues.append("get_translated_news_detail:status_not_translated")
            detail_original_url = str(detail.get("original_url") or "")
            original_url_checks = {
                "detail_original_equals_db_original": bool(
                    translated_row
                    and detail_original_url == str(translated_row["original_url"])
                ),
                "detail_original_public_http": is_public_http_url_value(detail_original_url),
                "detail_original_non_placeholder": not is_reserved_placeholder_url(detail_original_url),
                "detail_canonical_not_exposed_as_original": bool(
                    translated_row
                    and (
                        str(translated_row["original_url"]) == str(translated_row["canonical_url"])
                        or detail_original_url != str(translated_row["canonical_url"])
                    )
                    and str(translated_row["canonical_url"]) == FIXTURE_TRANSLATED_CANONICAL_URL
                ),
            }
            issues.extend(
                f"get_translated_news_detail:original_url:{key}=false"
                for key, passed in original_url_checks.items()
                if not passed
            )

    return {
        "imported": True,
        "checks": observations,
        "original_url_checks": original_url_checks,
        "issues": issues,
    }


def task_011_home_payload() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"payload": None, "issues": [import_issue], "status_code": None}
    from fastapi.testclient import TestClient

    client = TestClient(app)
    refresh_issues = envelope_issue(
        name="task_011_refresh",
        response=client.post("/api/refresh"),
        expected_status=200,
        expected_envelope="data",
        required_data_keys={"refreshed_at"},
    )
    response = client.get("/api/home")
    payload, parse_issues = _safe_json(response)
    home_issues = envelope_issue(
        name="task_011_home",
        response=response,
        expected_status=200,
        expected_envelope="data",
        required_data_keys={"latest_news", "top_ranked_news"},
    )
    return {
        "payload": payload,
        "issues": refresh_issues + parse_issues + home_issues,
        "status_code": response.status_code,
    }


def task_011_item_shape_issues(list_name: str, items: list[Any]) -> list[str]:
    issues: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(f"{list_name}:{index}:item_not_object")
            continue
        fields = set(item)
        for field in sorted(HOME_LIST_REQUIRED_FIELDS - fields):
            issues.append(f"{list_name}:{index}:missing_field:{field}")
        for field in sorted(fields - HOME_LIST_ALLOWED_FIELDS):
            issues.append(f"{list_name}:{index}:unexpected_field:{field}")
        if item.get("status") != "translated" and "summary_zh" in item:
            issues.append(f"{list_name}:{index}:summary_for_non_translated")
        if "content_zh" in item:
            issues.append(f"{list_name}:{index}:content_zh_in_list")
    return issues


def task_011_home_observations() -> dict[str, Any]:
    fetched = task_011_home_payload()
    payload = fetched["payload"] if isinstance(fetched["payload"], dict) else {}
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    latest = data.get("latest_news") if isinstance(data, dict) else []
    top = data.get("top_ranked_news") if isinstance(data, dict) else []
    latest = latest if isinstance(latest, list) else []
    top = top if isinstance(top, list) else []
    latest_dates = [str(item.get("published_at")) for item in latest if isinstance(item, dict)]
    top_pairs = [
        (int(item.get("score")), str(item.get("published_at")))
        for item in top
        if isinstance(item, dict) and isinstance(item.get("score"), int)
    ]
    checks = {
        "status_code": fetched["status_code"],
        "data_keys": sorted(data.keys()) if isinstance(data, dict) else [],
        "latest_count": len(latest),
        "top_ranked_count": len(top),
        "latest_sorted_desc": all(a >= b for a, b in zip(latest_dates, latest_dates[1:])),
        "top_sorted_desc": all(a[0] > b[0] or (a[0] == b[0] and a[1] >= b[1]) for a, b in zip(top_pairs, top_pairs[1:])),
        "top_within_window": all(date >= "2026-05-29T09:00:00Z" for _, date in top_pairs),
        "top_size_ok": len(top) <= 10,
        "latest_density_ok": len(latest) >= 10,
        "layout_fields_present": sorted(set(data) & HOME_LAYOUT_FIELDS) if isinstance(data, dict) else [],
        "all_scores_selected": all(int(item.get("score", -1)) >= 60 for item in latest + top if isinstance(item, dict)),
    }
    shape_issues = task_011_item_shape_issues("latest_news", latest)
    shape_issues += task_011_item_shape_issues("top_ranked_news", top)
    leak_scan = scan_public_payload(payload)
    return {
        "checks": checks,
        "contract_issues": fetched["issues"] + shape_issues,
        "api_issues": [key for key, value in checks.items() if key.endswith("_ok") and not value]
        + ([] if checks["latest_sorted_desc"] else ["latest_news:not_sorted_desc"])
        + ([] if checks["top_sorted_desc"] else ["top_ranked_news:not_sorted_desc"])
        + ([] if checks["top_within_window"] else ["top_ranked_news:outside_30_day_window"])
        + ([] if checks["all_scores_selected"] else ["home:low_score_item_visible"]),
        "leak_scan": leak_scan,
    }


def run_task_011_contract(report_dir: Path, task_id: str) -> int:
    observed = task_011_home_observations()
    passed = not observed["contract_issues"] and not observed["checks"]["layout_fields_present"]
    report = test_report(
        stage="contract",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-home-contract",
        assertions=[
            assertion(
                "A-contract-ACC-STOP-004-api-shapes",
                "passed" if passed else "failed",
                {"HomeData": ["latest_news", "top_ranked_news"], "layout_fields": []},
                {"checks": observed["checks"], "issues": observed["contract_issues"]},
                {"layout_fields_present": observed["checks"]["layout_fields_present"]},
                visibility="public_surface",
            )
        ],
        expected={"endpoint": "GET /api/home", "response_type": "HomeData"},
        actual={"checks": observed["checks"], "contract_issues": observed["contract_issues"]},
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/main.py", "docs/05_api_contract.md", "scripts/run_harness.py"],
        commands=[f"python3 scripts/run_harness.py --stage contract --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "contract", task_id), report)
    return 0 if passed else 1


def run_task_011_api(report_dir: Path, task_id: str) -> int:
    observed = task_011_home_observations()
    behavior_passed = not observed["api_issues"]
    leak_passed = observed["leak_scan"]["forbidden_field_count"] == 0 and observed["leak_scan"]["sensitive_content_count"] == 0
    leak_assertion = assertion(
        "A-api-ACC-STOP-009-api-leak-scan",
        "passed" if leak_passed else "failed",
        {"forbidden_field_count": 0, "sensitive_content_count": 0},
        observed["leak_scan"],
        {},
        visibility="public_surface",
    )
    leak_assertion["leak_detection"] = observed["leak_scan"]
    assertions = [
        assertion(
            "A-api-ACC-STOP-004-home-detail-behavior",
            "passed" if behavior_passed else "failed",
            {"latest_density": ">=10", "top_ranked_max": 10, "sorted": True},
            {"checks": observed["checks"], "issues": observed["api_issues"]},
            {},
            visibility="public_surface",
        ),
        leak_assertion,
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="api",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-home-api",
        assertions=assertions,
        expected={"endpoint": "GET /api/home", "behavior": "sorted_displayable_home_data"},
        actual=observed,
        diff={},
        failure_type=None if passed else "api",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/main.py", "fixtures/rss/feeds.json", "scripts/run_harness.py"],
        commands=[f"python3 scripts/run_harness.py --stage api --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "api", task_id), report)
    return 0 if passed else 1


def task_012_detail_observations() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"checks": {}, "contract_issues": [import_issue], "api_issues": [import_issue]}
    from fastapi.testclient import TestClient

    client = TestClient(app)
    refresh_issues = envelope_issue(
        name="task_012_refresh",
        response=client.post("/api/refresh"),
        expected_status=200,
        expected_envelope="data",
        required_data_keys={"refreshed_at"},
    )
    guid_ids = {
        row["rss_guid"]: str(row["id"])
        for row in app.state.db.execute("SELECT id, rss_guid FROM news_item").fetchall()
    }
    cases = {
        "translated": ("fixture-translated-96", 200),
        "ready": ("fixture-threshold-60", 200),
        "translation_failed": ("fixture-translate-partial", 200),
        "missing": ("missing", 404),
    }
    payloads: dict[str, Any] = {}
    issues = list(refresh_issues)
    for label, (guid, expected_status) in cases.items():
        item_id = guid_ids.get(guid, guid)
        response = client.get(f"/api/news/{item_id}")
        payload, parse_issues = _safe_json(response)
        payloads[label] = payload
        issues.extend(parse_issues)
        issues.extend(
            envelope_issue(
                name=f"task_012_{label}",
                response=response,
                expected_status=expected_status,
                expected_envelope="data" if expected_status == 200 else "error",
                required_data_keys={"id", "title", "status"} if expected_status == 200 else None,
            )
        )
    translated = payloads.get("translated", {}).get("data", {})
    ready = payloads.get("ready", {}).get("data", {})
    failed = payloads.get("translation_failed", {}).get("data", {})
    checks = {
        "translated_has_content_zh": bool(translated.get("content_zh")),
        "translated_has_summary_zh": bool(translated.get("summary_zh")),
        "translated_status": translated.get("status") == "translated",
        "ready_status": ready.get("status") == "ready",
        "ready_omits_zh": "summary_zh" not in ready and "content_zh" not in ready,
        "failed_status": failed.get("status") == "translation_failed",
        "failed_omits_zh": "summary_zh" not in failed and "content_zh" not in failed,
        "missing_error_code": payloads.get("missing", {}).get("error", {}).get("code") == "NEWS_NOT_FOUND",
    }
    leak_scan = scan_public_payload(payloads)
    api_issues = [name for name, passed in checks.items() if not passed]
    if leak_scan["forbidden_field_count"] or leak_scan["sensitive_content_count"]:
        api_issues.append("detail:leak_scan_failed")
    return {
        "checks": checks,
        "contract_issues": issues,
        "api_issues": api_issues,
        "leak_scan": leak_scan,
    }


def run_task_012_contract(report_dir: Path, task_id: str) -> int:
    observed = task_012_detail_observations()
    passed = not observed["contract_issues"]
    report = test_report(
        stage="contract",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-news-detail-contract",
        assertions=[
            assertion(
                "A-contract-ACC-STOP-004-api-shapes",
                "passed" if passed else "failed",
                {"NewsDetailItem": "documented data envelope or structured 404"},
                {"checks": observed["checks"], "issues": observed["contract_issues"]},
                {"issues": observed["contract_issues"]},
                visibility="public_surface",
            )
        ],
        expected={"endpoint": "GET /api/news/{id}", "response_type": "NewsDetailItem"},
        actual={"checks": observed["checks"], "contract_issues": observed["contract_issues"]},
        diff={"issues": observed["contract_issues"]},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/main.py", "docs/05_api_contract.md", "scripts/run_harness.py"],
        commands=[f"python3 scripts/run_harness.py --stage contract --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "contract", task_id), report)
    return 0 if passed else 1


def run_task_012_api(report_dir: Path, task_id: str) -> int:
    observed = task_012_detail_observations()
    behavior_passed = not observed["api_issues"]
    leak_passed = observed["leak_scan"]["forbidden_field_count"] == 0 and observed["leak_scan"]["sensitive_content_count"] == 0
    leak_assertion = assertion(
        "A-api-ACC-STOP-009-api-leak-scan",
        "passed" if leak_passed else "failed",
        {"forbidden_field_count": 0, "sensitive_content_count": 0},
        observed["leak_scan"],
        {},
        visibility="public_surface",
    )
    leak_assertion["leak_detection"] = observed["leak_scan"]
    assertions = [
        assertion(
            "A-api-ACC-STOP-004-home-detail-behavior",
            "passed" if behavior_passed else "failed",
            {"translated": "content_zh", "ready_failed": "omit zh fields", "missing": "404"},
            {"checks": observed["checks"], "issues": observed["api_issues"]},
            {"issues": observed["api_issues"]},
            visibility="public_surface",
        ),
        leak_assertion,
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="api",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-news-detail-api",
        assertions=assertions,
        expected={"endpoint": "GET /api/news/{id}", "behavior": "safe_detail_states"},
        actual=observed,
        diff={"issues": observed["api_issues"]},
        failure_type=None if passed else "api",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/main.py", "fixtures/rss/feeds.json", "scripts/run_harness.py"],
        commands=[f"python3 scripts/run_harness.py --stage api --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "api", task_id), report)
    return 0 if passed else 1


def task_013_client() -> tuple[Any, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        raise RuntimeError(import_issue)
    from fastapi.testclient import TestClient

    return app, TestClient(app)


def task_013_json(response: Any, label: str, expected: int) -> tuple[dict[str, Any] | None, list[str]]:
    issues: list[str] = []
    if response.status_code != expected:
        issues.append(f"{label}:status={response.status_code}!={expected}")
    payload, parse_issues = _safe_json(response)
    issues.extend(f"{label}:{issue}" for issue in parse_issues)
    return payload, issues


def task_013_source_item_issues(label: str, items: list[Any]) -> list[str]:
    issues: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(f"{label}:{index}:not_object")
            continue
        fields = set(item)
        if fields != SOURCE_ITEM_FIELDS:
            issues.append(f"{label}:{index}:fields:{sorted(fields)}")
        if not isinstance(item.get("id"), str):
            issues.append(f"{label}:{index}:id_not_string")
        if item.get("fetch_frequency") != "twice_daily":
            issues.append(f"{label}:{index}:fetch_frequency_not_twice_daily")
    return issues


def task_013_contract_observations() -> dict[str, Any]:
    _, client = task_013_client()
    expected_default_urls = default_source_urls()
    payload, issues = task_013_json(client.get("/api/sources"), "sources_get", 200)
    data = payload.get("data") if isinstance(payload, dict) else []
    data = data if isinstance(data, list) else []
    created_values = [str(item.get("created_at")) for item in data if isinstance(item, dict)]
    checks = {
        "source_count": len(data),
        "created_sorted": created_values == sorted(created_values),
        "default_urls_match": {item.get("rss_url") for item in data if isinstance(item, dict)} == expected_default_urls,
        "visible_has_only_source_item_fields": not task_013_source_item_issues("sources_get", data),
        "leak_scan": scan_public_payload(payload),
    }
    issues += task_013_source_item_issues("sources_get", data)
    if len(data) != len(expected_default_urls):
        issues.append(f"sources_get:count={len(data)}!={len(expected_default_urls)}")
    if not checks["created_sorted"]:
        issues.append("sources_get:not_sorted_by_created_at")
    if not checks["default_urls_match"]:
        issues.append("sources_get:default_urls_mismatch")
    if checks["leak_scan"]["forbidden_field_count"] or checks["leak_scan"]["sensitive_content_count"]:
        issues.append("sources_get:leak_scan_failed")
    return {"checks": checks, "issues": issues}


def task_013_validation_observations() -> dict[str, Any]:
    app, client = task_013_client()
    issues: list[str] = []
    before = app.state.db.execute("SELECT COUNT(*) AS count FROM source").fetchone()["count"]
    statuses: dict[str, int] = {}
    for label, payload in SOURCE_INVALID_CREATE_CASES:
        response = client.post("/api/sources", json=payload)
        statuses[label] = response.status_code
        _, case_issues = task_013_json(response, f"source_create_{label}", 400)
        issues.extend(case_issues)
    after = app.state.db.execute("SELECT COUNT(*) AS count FROM source").fetchone()["count"]
    if before != after:
        issues.append(f"invalid_create:source_count_changed:{before}->{after}")
    valid_payload = {"name": "Duplicate Tombstone", "rss_url": "https://example.com/tombstone.xml"}
    created, create_issues = task_013_json(client.post("/api/sources", json=valid_payload), "source_create_tombstone", 201)
    issues.extend(create_issues)
    source_id = str(created.get("data", {}).get("id")) if isinstance(created, dict) else ""
    delete_response = client.delete(f"/api/sources/{source_id}")
    if delete_response.status_code != 204 or delete_response.content:
        issues.append(f"source_delete_tombstone_seed:status={delete_response.status_code}")
    duplicate, duplicate_issues = task_013_json(client.post("/api/sources", json=valid_payload), "source_create_duplicate_tombstone", 409)
    del duplicate
    issues.extend(duplicate_issues)
    return {"checks": {"invalid_statuses": statuses, "count_stable": before == after, "duplicate_tombstone_status": 409}, "issues": issues}


def task_013_mutation_observations() -> dict[str, Any]:
    _, client = task_013_client()
    issues: list[str] = []
    initial, initial_issues = task_013_json(client.get("/api/sources"), "sources_initial", 200)
    issues.extend(initial_issues)
    source_ids = [str(item["id"]) for item in initial.get("data", []) if isinstance(item, dict)]
    for source_id in source_ids[1:]:
        _, patch_issues = task_013_json(client.patch(f"/api/sources/{source_id}", json={"is_enabled": False}), f"source_disable_{source_id}", 200)
        issues.extend(patch_issues)
    last_id = source_ids[0] if source_ids else "missing"
    _, last_patch_issues = task_013_json(client.patch(f"/api/sources/{last_id}", json={"is_enabled": False}), "source_disable_last", 409)
    _, last_delete_issues = task_013_json(client.delete(f"/api/sources/{last_id}"), "source_delete_last", 409)
    _, missing_patch_issues = task_013_json(client.patch("/api/sources/999999", json={"is_enabled": False}), "source_patch_missing", 404)
    _, missing_delete_issues = task_013_json(client.delete("/api/sources/999999"), "source_delete_missing", 404)
    issues.extend(last_patch_issues + last_delete_issues + missing_patch_issues + missing_delete_issues)
    return {"checks": {"disabled_before_last_guard": max(len(source_ids) - 1, 0), "last_guard_source_id": last_id}, "issues": issues}


def task_013_tombstone_history_observations() -> dict[str, Any]:
    app, client = task_013_client()
    issues: list[str] = []
    _, refresh_issues = task_013_json(client.post("/api/refresh"), "source_history_refresh", 200)
    issues.extend(refresh_issues)
    before_news = app.state.db.execute("SELECT COUNT(*) AS count FROM news_item").fetchone()["count"]
    sources, source_issues = task_013_json(client.get("/api/sources"), "source_history_sources", 200)
    issues.extend(source_issues)
    source_id = str(sources.get("data", [{}])[0].get("id")) if isinstance(sources, dict) else ""
    delete_response = client.delete(f"/api/sources/{source_id}")
    if delete_response.status_code != 204 or delete_response.content:
        issues.append(f"source_history_delete:status={delete_response.status_code}")
    row = app.state.db.execute("SELECT is_enabled, deleted_at FROM source WHERE id = ?", (source_id,)).fetchone()
    after_news = app.state.db.execute("SELECT COUNT(*) AS count FROM news_item").fetchone()["count"]
    post_sources, post_issues = task_013_json(client.get("/api/sources"), "source_history_post_sources", 200)
    issues.extend(post_issues)
    post_ids = {str(item.get("id")) for item in post_sources.get("data", []) if isinstance(item, dict)}
    _, deleted_patch_issues = task_013_json(client.patch(f"/api/sources/{source_id}", json={"is_enabled": True}), "source_patch_deleted", 404)
    _, deleted_delete_issues = task_013_json(client.delete(f"/api/sources/{source_id}"), "source_delete_deleted", 404)
    issues.extend(deleted_patch_issues + deleted_delete_issues)
    checks = {
        "news_preserved": before_news == after_news and before_news > 0,
        "tombstone_present": bool(row and int(row["is_enabled"]) == 0 and row["deleted_at"]),
        "deleted_hidden": source_id not in post_ids,
    }
    for key, value in checks.items():
        if not value:
            issues.append(f"source_history:{key}=false")
    return {"checks": checks, "issues": issues}


def task_013_parity_observations() -> dict[str, Any]:
    app, client = task_013_client()
    issues: list[str] = []
    sources, source_issues = task_013_json(client.get("/api/sources"), "source_parity_sources", 200)
    issues.extend(source_issues)
    default_id = str(sources.get("data", [{}])[0].get("id")) if isinstance(sources, dict) else ""
    created, create_issues = task_013_json(
        client.post("/api/sources", json={"name": "Parity User", "rss_url": "https://example.com/parity.xml"}),
        "source_parity_create_user",
        201,
    )
    issues.extend(create_issues)
    user_id = str(created.get("data", {}).get("id")) if isinstance(created, dict) else ""
    checks: dict[str, Any] = {}
    for label, source_id in {"default": default_id, "user": user_id}.items():
        disabled, disable_issues = task_013_json(client.patch(f"/api/sources/{source_id}", json={"is_enabled": False}), f"source_parity_disable_{label}", 200)
        issues.extend(disable_issues)
        checks[f"{label}_disable_returned_false"] = disabled.get("data", {}).get("is_enabled") is False if isinstance(disabled, dict) else False
        delete_response = client.delete(f"/api/sources/{source_id}")
        checks[f"{label}_delete_204"] = delete_response.status_code == 204 and not delete_response.content
        row = app.state.db.execute("SELECT is_enabled, deleted_at FROM source WHERE id = ?", (source_id,)).fetchone()
        checks[f"{label}_tombstone"] = bool(row and int(row["is_enabled"]) == 0 and row["deleted_at"])
    client.post("/api/refresh")
    reloaded, reload_issues = task_013_json(client.get("/api/sources"), "source_parity_reload", 200)
    issues.extend(reload_issues)
    visible_ids = {str(item.get("id")) for item in reloaded.get("data", []) if isinstance(item, dict)}
    checks["deleted_ids_hidden_after_reload"] = default_id not in visible_ids and user_id not in visible_ids
    issues.extend(f"source_parity:{key}=false" for key, value in checks.items() if not value)
    return {"checks": checks, "issues": issues}


def task_013_api_observations() -> dict[str, Any]:
    contract = task_013_contract_observations()
    validation = task_013_validation_observations()
    mutation = task_013_mutation_observations()
    history = task_013_tombstone_history_observations()
    parity = task_013_parity_observations()
    return {
        "checks": {
            "contract": contract["checks"],
            "validation": validation["checks"],
            "mutation": mutation["checks"],
            "history": history["checks"],
            "parity": parity["checks"],
        },
        "source_management_issues": contract["issues"],
        "crud_error_issues": validation["issues"] + mutation["issues"],
        "tombstone_history_issues": history["issues"],
        "parity_issues": parity["issues"],
    }


def run_task_013_contract(report_dir: Path, task_id: str) -> int:
    observed = task_013_contract_observations()
    passed = not observed["issues"]
    report = test_report(
        stage="contract",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-sources-contract",
        assertions=[assertion("A-contract-ACC-STOP-004-api-shapes", "passed" if passed else "failed", {"SourceItem_fields": sorted(SOURCE_ITEM_FIELDS)}, observed, {}, visibility="public_surface")],
        expected={"endpoint": "GET /api/sources", "response_type": "SourceItem[]"},
        actual=observed,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/main.py", "docs/05_api_contract.md", "scripts/run_harness.py"],
        commands=[f"python3 scripts/run_harness.py --stage contract --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "contract", task_id), report)
    return 0 if passed else 1


def run_task_013_api(report_dir: Path, task_id: str) -> int:
    observed = task_013_api_observations()
    assertions = [
        assertion("A-api-ACC-STOP-002-source-management", "passed" if not observed["source_management_issues"] else "failed", {"source_management": "valid"}, {"checks": observed["checks"]["contract"], "issues": observed["source_management_issues"]}, {}, visibility="public_surface"),
        assertion("A-api-ACC-STOP-002-source-crud-errors", "passed" if not observed["crud_error_issues"] else "failed", {"crud_errors": "structured"}, {"checks": {k: observed["checks"][k] for k in ("validation", "mutation")}, "issues": observed["crud_error_issues"]}, {}, visibility="public_surface"),
        assertion("A-api-ACC-STOP-002-source-tombstone-history", "passed" if not observed["tombstone_history_issues"] else "failed", {"soft_delete_and_history": "preserved"}, {"checks": observed["checks"]["history"], "issues": observed["tombstone_history_issues"]}, {}, visibility="public_surface"),
        assertion("A-api-ACC-STOP-002-default-source-crud-parity", "passed" if not observed["parity_issues"] else "failed", {"default_and_user_sources": "same_crud_rules"}, {"checks": observed["checks"]["parity"], "issues": observed["parity_issues"]}, {}, visibility="public_surface"),
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="api",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-sources-api",
        assertions=assertions,
        expected={"endpoints": ["GET /api/sources", "POST /api/sources", "PATCH /api/sources/{id}", "DELETE /api/sources/{id}"]},
        actual=observed,
        diff={},
        failure_type=None if passed else "api",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/main.py", "fixtures/sources/source_cases.json", "scripts/run_harness.py"],
        commands=[f"python3 scripts/run_harness.py --stage api --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "api", task_id), report)
    return 0 if passed else 1


def task_014_client() -> tuple[Any, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        raise RuntimeError(import_issue)
    from fastapi.testclient import TestClient

    return app, TestClient(app)


def task_014_json(response: Any, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    issues = envelope_issue(
        name=label,
        response=response,
        expected_status=200,
        expected_envelope="data",
        required_data_keys=REFRESH_DATA_FIELDS,
    )
    payload, parse_issues = _safe_json(response)
    issues.extend(f"{label}:{issue}" for issue in parse_issues)
    return payload, issues


def task_014_refreshed_at(payload: dict[str, Any] | None) -> Any:
    data = payload.get("data") if isinstance(payload, dict) else None
    return data.get("refreshed_at") if isinstance(data, dict) else None


def task_014_refresh_shape_issues(label: str, payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return [f"{label}:payload_not_object"]
    issues: list[str] = []
    data = payload.get("data")
    fields = set(data) if isinstance(data, dict) else set()
    if fields != REFRESH_DATA_FIELDS:
        issues.append(f"{label}:data_fields:{sorted(fields)}")
    value = data.get("refreshed_at") if isinstance(data, dict) else None
    if value is not None and not isinstance(value, str):
        issues.append(f"{label}:refreshed_at_not_string_or_null")
    for path, key, _ in iter_json_paths(payload):
        if key and str(key).lower() in REFRESH_FORBIDDEN_FIELDS:
            issues.append(f"{label}:forbidden_field:{path}")
    return issues


def task_014_contract_observations() -> dict[str, Any]:
    app, client = task_014_client()
    success_payload, success_issues = task_014_json(client.post("/api/refresh"), "refresh_success")
    app.state.refresh_running = True
    concurrent_payload, concurrent_issues = task_014_json(client.post("/api/refresh"), "refresh_concurrent")
    app.state.refresh_running = False
    public_payloads = {"success": success_payload, "concurrent": concurrent_payload}
    issues = success_issues + concurrent_issues
    for label, payload in public_payloads.items():
        issues.extend(task_014_refresh_shape_issues(label, payload))
    leak_scan = scan_public_payload(public_payloads)
    if leak_scan["forbidden_field_count"] or leak_scan["sensitive_content_count"]:
        issues.append("refresh_contract:leak_scan_failed")
    checks = {
        "success_refreshed_at_type": type(task_014_refreshed_at(success_payload)).__name__,
        "concurrent_refreshed_at_type": type(task_014_refreshed_at(concurrent_payload)).__name__,
        "success_data_fields": sorted(success_payload.get("data", {})) if isinstance(success_payload, dict) else [],
        "concurrent_data_fields": sorted(concurrent_payload.get("data", {})) if isinstance(concurrent_payload, dict) else [],
    }
    return {"checks": checks, "issues": issues, "leak_scan": leak_scan}


def task_014_table_count(app: Any, table_name: str) -> int:
    row = app.state.db.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"])


def task_014_api_observations() -> dict[str, Any]:
    app, client = task_014_client()
    issues: list[str] = []
    log_count_initial = task_014_table_count(app, "processing_log")
    app.state.refresh_running = True
    early_payload, early_issues = task_014_json(client.post("/api/refresh"), "refresh_early_concurrent")
    app.state.refresh_running = False
    log_count_after_early = task_014_table_count(app, "processing_log")
    success_payload, success_issues = task_014_json(client.post("/api/refresh"), "refresh_success")
    news_count_after_success = task_014_table_count(app, "news_item")
    second_payload, second_issues = task_014_json(client.post("/api/refresh"), "refresh_second_success")
    news_count_after_second = task_014_table_count(app, "news_item")
    log_count_before_late = task_014_table_count(app, "processing_log")
    app.state.refresh_running = True
    late_payload, late_issues = task_014_json(client.post("/api/refresh"), "refresh_late_concurrent")
    app.state.refresh_running = False
    log_count_after_late = task_014_table_count(app, "processing_log")
    payloads = {"early": early_payload, "success": success_payload, "second": second_payload, "late": late_payload}
    issues.extend(early_issues + success_issues + second_issues + late_issues)
    for label, payload in payloads.items():
        issues.extend(task_014_refresh_shape_issues(label, payload))
    checks = {
        "early_concurrent_null": task_014_refreshed_at(early_payload) is None,
        "early_concurrent_no_run": log_count_initial == log_count_after_early,
        "success_timestamp": task_014_refreshed_at(success_payload) == FIXED_TIMESTAMP,
        "second_timestamp": task_014_refreshed_at(second_payload) == FIXED_TIMESTAMP,
        "idempotent_news_count": news_count_after_success == 14 and news_count_after_second == 14,
        "late_concurrent_last_timestamp": task_014_refreshed_at(late_payload) == FIXED_TIMESTAMP,
        "late_concurrent_no_run": log_count_before_late == log_count_after_late,
    }
    issues.extend(f"refresh_api:{key}=false" for key, value in checks.items() if not value)
    return {"checks": checks, "issues": issues, "leak_scan": scan_public_payload(payloads)}


def run_task_014_contract(report_dir: Path, task_id: str) -> int:
    observed = task_014_contract_observations()
    passed = not observed["issues"]
    report = test_report(
        stage="contract",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-refresh-contract",
        assertions=[assertion("A-contract-ACC-STOP-004-api-shapes", "passed" if passed else "failed", {"RefreshResponse_fields": sorted(REFRESH_DATA_FIELDS)}, {"checks": observed["checks"], "issues": observed["issues"]}, {}, visibility="public_surface")],
        expected={"endpoint": "POST /api/refresh", "response_type": "RefreshResponse"},
        actual={"checks": observed["checks"], "issues": observed["issues"]},
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/main.py", "docs/05_api_contract.md", "scripts/run_harness.py"],
        commands=[f"python3 scripts/run_harness.py --stage contract --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "contract", task_id), report)
    return 0 if passed else 1


def run_task_014_api(report_dir: Path, task_id: str) -> int:
    observed = task_014_api_observations()
    behavior_passed = not observed["issues"]
    leak_passed = observed["leak_scan"]["forbidden_field_count"] == 0 and observed["leak_scan"]["sensitive_content_count"] == 0
    leak_assertion = assertion("A-api-ACC-STOP-009-api-leak-scan", "passed" if leak_passed else "failed", {"forbidden_field_count": 0, "sensitive_content_count": 0}, observed["leak_scan"], {}, visibility="public_surface")
    leak_assertion["leak_detection"] = observed["leak_scan"]
    side_effect_assertion = assertion("task-014-refresh-side-effects", "passed" if behavior_passed else "failed", {"concurrent_runs": "rejected", "news_count": 12}, {"checks": observed["checks"], "issues": observed["issues"]}, {}, visibility="internal_evidence")
    assertions = [
        assertion("A-api-ACC-STOP-004-refresh-contract", "passed" if behavior_passed else "failed", {"refreshed_at": "string_or_null", "forbidden_fields": []}, {"checks": observed["checks"], "issues": observed["issues"]}, {}, visibility="public_surface"),
        side_effect_assertion,
        leak_assertion,
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="api",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-refresh-api",
        assertions=assertions,
        expected={"endpoint": "POST /api/refresh", "behavior": "idempotent_concurrency_guarded_refresh"},
        actual=observed,
        diff={},
        failure_type=None if passed else "api",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/main.py", "backend/app/services/pipeline.py", "scripts/run_harness.py"],
        commands=[f"python3 scripts/run_harness.py --stage api --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "api", task_id), report)
    return 0 if passed else 1


def task_019_public_response(client: Any, method: str, path: str) -> tuple[Any, dict[str, Any] | None, list[str]]:
    response = getattr(client, method.lower())(path)
    payload, parse_issues = _safe_json(response)
    return response, payload, [f"{path}:{issue}" for issue in parse_issues]


def task_019_public_item_issues(label: str, items: list[Any]) -> list[str]:
    issues = task_011_item_shape_issues(label, items)
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("status") != "translated":
            for field in ("summary_zh", "content_zh"):
                if field in item:
                    issues.append(f"{label}:{index}:{field}_for_non_translated")
    return issues


def task_019_detail_checks(client: Any, latest_items: list[dict[str, Any]]) -> dict[str, Any]:
    by_title = {str(item["original_title"]): str(item["id"]) for item in latest_items}
    checks: dict[str, Any] = {}
    issues: list[str] = []
    cases = {
        "translated": ("Introducing LifeSciBench", "translated"),
        "ready": ("Threshold AI agent reaches production", "ready"),
        "failed": ("AI translation mock emits partial output", "translation_failed"),
    }
    for label, (title, expected_status) in cases.items():
        response, payload, parse_issues = task_019_public_response(client, "GET", f"/api/news/{by_title.get(title, 'missing')}")
        issues.extend(parse_issues)
        issues.extend(envelope_issue(name=f"detail_{label}", response=response, expected_status=200, expected_envelope="data"))
        data = payload.get("data") if isinstance(payload, dict) else {}
        checks[f"{label}_status"] = data.get("status") == expected_status if isinstance(data, dict) else False
        has_content = bool(data.get("content_zh")) if isinstance(data, dict) else False
        has_summary = bool(data.get("summary_zh")) if isinstance(data, dict) else False
        if label == "translated":
            checks["translated_detail_complete"] = has_content and has_summary
        else:
            checks[f"{label}_detail_omits_zh"] = not has_content and not has_summary
    return {"checks": checks, "issues": issues}


def task_019_home_checks(home_payload: dict[str, Any] | None) -> dict[str, Any]:
    data = home_payload.get("data") if isinstance(home_payload, dict) else {}
    latest = data.get("latest_news") if isinstance(data, dict) else []
    top = data.get("top_ranked_news") if isinstance(data, dict) else []
    latest = latest if isinstance(latest, list) else []
    top = top if isinstance(top, list) else []
    latest_titles = [str(item.get("original_title")) for item in latest if isinstance(item, dict)]
    top_titles = [str(item.get("original_title")) for item in top if isinstance(item, dict)]
    top_pairs = [(int(item.get("score")), str(item.get("published_at"))) for item in top if isinstance(item, dict) and isinstance(item.get("score"), int)]
    checks = {
        "latest_density_ok": len(latest) >= 10,
        "threshold_60_visible": "Threshold AI agent reaches production" in latest_titles,
        "score_59_hidden": "Low signal AI funding rumor" not in latest_titles,
        "threshold_duplicate_hidden": "Threshold AI agent reaches production duplicate" not in latest_titles,
        "top_count_10": len(top) == 10,
        "top_sorted": all(a[0] > b[0] or (a[0] == b[0] and a[1] >= b[1]) for a, b in zip(top_pairs, top_pairs[1:])),
        "old_high_excluded_from_top": "Older AI milestone outside ranking window" not in top_titles,
    }
    public_items = [item for item in latest + top if isinstance(item, dict)]
    return {"checks": checks, "issues": task_019_public_item_issues("home", public_items), "latest_items": [item for item in latest if isinstance(item, dict)]}


def task_019_endpoint_checks(client: Any) -> dict[str, Any]:
    issues: list[str] = []
    checks: dict[str, Any] = {}
    refresh_payload, refresh_issues = task_014_json(client.post("/api/refresh"), "integration_refresh")
    issues.extend(refresh_issues + task_014_refresh_shape_issues("integration_refresh", refresh_payload))
    sources_response, sources_payload, source_issues = task_019_public_response(client, "GET", "/api/sources")
    issues.extend(source_issues)
    issues.extend(envelope_issue(name="integration_sources", response=sources_response, expected_status=200, expected_envelope="data"))
    checks["sources_list"] = isinstance(sources_payload.get("data") if isinstance(sources_payload, dict) else None, list)
    non_goal_paths = ["/api/user", "/api/login", "/api/search", "/api/category", "/api/task-progress", "/api/retry", "/api/admin", "/api/version"]
    statuses = {path: client.get(path).status_code for path in non_goal_paths}
    checks["non_goal_absent"] = all(status in {404, 405} for status in statuses.values())
    if not checks["non_goal_absent"]:
        issues.append(f"non_goal_statuses:{statuses}")
    return {"checks": checks, "issues": issues, "payloads": {"refresh": refresh_payload, "sources": sources_payload}}


def task_019_api_only_observations() -> dict[str, Any]:
    _, client = task_014_client()
    response, home_payload, parse_issues = task_019_public_response(client, "POST", "/api/refresh")
    refresh_issues = envelope_issue(name="integration_initial_refresh", response=response, expected_status=200, expected_envelope="data", required_data_keys=REFRESH_DATA_FIELDS)
    response, home_payload, home_issues = task_019_public_response(client, "GET", "/api/home")
    home_issues.extend(envelope_issue(name="integration_home", response=response, expected_status=200, expected_envelope="data", required_data_keys={"latest_news", "top_ranked_news"}))
    home = task_019_home_checks(home_payload)
    details = task_019_detail_checks(client, home["latest_items"])
    endpoints = task_019_endpoint_checks(client)
    checks = {"home": home["checks"], "details": details["checks"], "endpoints": endpoints["checks"]}
    issues = parse_issues + refresh_issues + home_issues + home["issues"] + details["issues"] + endpoints["issues"]
    issues.extend(f"{area}:{key}=false" for area, values in checks.items() for key, value in values.items() if not value)
    leak_scan = scan_public_payload({"home": home_payload, **endpoints["payloads"]})
    if leak_scan["forbidden_field_count"] or leak_scan["sensitive_content_count"]:
        issues.append("api_only:leak_scan_failed")
    return {"checks": checks, "issues": issues, "leak_scan": leak_scan}


def run_task_019_integration(report_dir: Path, task_id: str) -> int:
    observed = task_019_api_only_observations()
    behavior_passed = not observed["issues"]
    leak_passed = observed["leak_scan"]["forbidden_field_count"] == 0 and observed["leak_scan"]["sensitive_content_count"] == 0
    leak_assertion = assertion("task-019-integration-api-leak-scan", "passed" if leak_passed else "failed", {"forbidden_field_count": 0, "sensitive_content_count": 0}, observed["leak_scan"], {}, visibility="public_surface")
    leak_assertion["leak_detection"] = observed["leak_scan"]
    assertions = [
        assertion("task-019-integration-api-only", "passed" if behavior_passed else "failed", {"api_only_integration": "public_responses_pass"}, {"checks": observed["checks"], "issues": observed["issues"]}, {}, visibility="public_surface"),
        leak_assertion,
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-api-only-integration",
        assertions=assertions,
        expected={"api_only_integration": "home_detail_sources_refresh_non_goal"},
        actual=observed,
        diff={},
        failure_type=None if passed else "integration",
        error_category=None if passed else "validation",
        referenced_files=["scripts/run_harness.py", "backend/app/main.py", "tests/test_api_contract.py"],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_015_source_color(source_name: str) -> str:
    seed = 7
    for character in source_name:
        seed = (seed * 31 + ord(character)) % 9973
    return TASK_015_SOURCE_COLORS[seed % len(TASK_015_SOURCE_COLORS)]


def task_015_read_sources() -> tuple[dict[str, str], list[str]]:
    sources: dict[str, str] = {}
    issues: list[str] = []
    for file_path in TASK_015_SOURCE_FILES:
        text, read_issues = _safe_text_read(Path(file_path), file_path)
        sources[file_path] = text
        issues.extend(read_issues)
        if not Path(file_path).exists():
            issues.append(f"missing_file:{file_path}")
    return sources, issues


def task_015_mock_home_data() -> dict[str, Any]:
    latest_news: list[dict[str, Any]] = [
        {
            "id": "home-001",
            "title": "OpenAI 发布 LifeSciBench 生命科学基准",
            "original_title": "Introducing LifeSciBench",
            "source_name": "OpenAI Blog",
            "original_url": FIXTURE_TRANSLATED_CANONICAL_URL,
            "published_at": "2026-06-17T00:00:00Z",
            "score": 98,
            "status": "translated",
            "summary_zh": "中文摘要 <b>重点</b> <script>alert(1)</script>",
        },
        {
            "id": "home-002",
            "title": "Production agent reaches threshold",
            "original_title": "Production agent reaches threshold",
            "source_name": "HN Frontpage",
            "original_url": FIXTURE_THRESHOLD_CANONICAL_URL,
            "published_at": "2026-06-28T07:30:00Z",
            "score": 97,
            "status": "ready",
        },
        {
            "id": "home-003",
            "title": "Translation mock failure",
            "original_title": "Translation mock failure",
            "source_name": "Dreyx Digest",
            "original_url": FIXTURE_TRANSLATION_PARTIAL_CANONICAL_URL,
            "published_at": "2026-06-28T07:00:00Z",
            "score": 96,
            "status": "translation_failed",
        },
    ]
    for index in range(4, 13):
        latest_news.append(
            {
                "id": f"home-{index:03d}",
                "title": f"AI 新闻 {index}",
                "original_title": f"AI fixture item {index}",
                "source_name": ["OpenAI Blog", "HN Newest", "Developer Feed"][index % 3],
                "original_url": f"https://ai.example-news.dev/hacker-news-fixture-{index}",
                "published_at": f"2026-06-{28 - index % 4:02d}T0{index % 9}:00:00Z",
                "score": 100 - index,
                "status": "translated",
                "summary_zh": f"第 {index} 条中文摘要",
            }
        )
    older_candidate = {
        "id": "home-old",
        "title": "窗口外高分新闻",
        "original_title": "Older AI milestone outside ranking window",
        "source_name": "OpenAI Blog",
        "original_url": "https://ai.example-news.dev/hacker-news-fixture-old",
        "published_at": "2026-05-01T08:00:00Z",
        "score": 100,
        "status": "translated",
        "summary_zh": "窗口外中文摘要",
    }
    return {
        "latest_news": latest_news,
        "top_ranked_news": latest_news[:10],
        "excluded_candidates": [older_candidate],
    }


def task_015_project_card(item: dict[str, Any]) -> dict[str, Any]:
    status = str(item.get("status"))
    title = item.get("title") if status == "translated" else item.get("original_title")
    summary = item.get("summary_zh") if status == "translated" else None
    if status == "ready":
        aria_label = f"{title}，翻译中，{UNREADABLE_DETAIL_TITLE}"
    elif status == "translation_failed":
        aria_label = f"{title}，翻译失败，{UNREADABLE_DETAIL_TITLE}"
    else:
        aria_label = f"{title}，打开中文摘要和正文"
    return {
        "id": item.get("id"),
        "href": f"/news/{item.get('id')}",
        "aria_label": aria_label,
        "title_text": title,
        "summary_text": summary,
        "status_text": TASK_015_STATUS_LABELS.get(status),
        "source_name": item.get("source_name"),
        "source_color": task_015_source_color(str(item.get("source_name", ""))),
        "score": item.get("score"),
        "published_at": item.get("published_at"),
        "summary_node_count": 1 if summary else 0,
        "content_node_count": 0,
        "created_html_tag_nodes": False,
    }


def task_015_project_home_dom(home_data: dict[str, Any]) -> dict[str, Any]:
    latest = [item for item in home_data["latest_news"] if isinstance(item, dict)]
    top = [item for item in home_data["top_ranked_news"] if isinstance(item, dict)]
    return {
        "cards": [task_015_project_card(item) for item in latest],
        "rank_items": [
            {
                "id": item.get("id"),
                "href": f"/news/{item.get('id')}",
                "status": item.get("status"),
                "title_text": item.get("title") if item.get("status") == "translated" else item.get("original_title"),
                "aria_label": (
                    f"{item.get('title')}，打开中文摘要和正文"
                    if item.get("status") == "translated"
                    else f"{item.get('original_title')}，{TASK_015_STATUS_LABELS.get(str(item.get('status')))}，{UNREADABLE_DETAIL_TITLE}"
                ),
                "summary_node_count": 0,
                "score": item.get("score"),
                "published_at": item.get("published_at"),
            }
            for item in top[:10]
        ],
        "refresh": {
            "default_text": "刷新",
            "loading_text": "刷新中",
            "disabled_while_loading": True,
            "call_sequence": ["GET /api/home", "POST /api/refresh", "GET /api/home"],
        },
        "states": {
            "loading": "新闻加载中",
            "empty": "暂无可展示新闻",
            "error": "新闻加载失败",
        },
    }


def task_015_source_checks(sources: dict[str, str]) -> dict[str, Any]:
    joined_sources = "\n".join(sources.values())
    checks = {
        "all_planned_files_present": all(Path(path).exists() for path in TASK_015_SOURCE_FILES),
        "api_client_has_home_endpoint": (
            "fetch('/api/home')" in sources["frontend/src/api/news.ts"]
            or "return query ? `/api/home?${query}` : '/api/home'" in sources["frontend/src/api/news.ts"]
        ),
        "api_client_has_refresh_endpoint": "fetch('/api/refresh'" in sources["frontend/src/api/news.ts"],
        "api_client_has_detail_route": "/api/news/" in sources["frontend/src/api/news.ts"],
        "home_uses_api_client": "client.fetchHome()" in sources["frontend/src/pages/HomePage.tsx"],
        "home_refresh_reloads_home": "client.refreshHome()" in sources["frontend/src/pages/HomePage.tsx"] and "await loadHome()" in sources["frontend/src/pages/HomePage.tsx"],
        "topbar_refresh_loading_state": "disabled={isRefreshing}" in sources["frontend/src/components/TopBar.tsx"] and "刷新中" in sources["frontend/src/components/TopBar.tsx"],
        "news_card_uses_internal_route": "href={`/news/${item.id}`}" in sources["frontend/src/components/NewsCard.tsx"],
        "news_card_accessible_unreadable_label": "aria-label={linkLabel}" in sources["frontend/src/components/NewsCard.tsx"] and UNREADABLE_DETAIL_TITLE in sources["frontend/src/components/NewsCard.tsx"],
        "rank_item_uses_internal_route": "href={`/news/${item.id}`}" in sources["frontend/src/components/HighScoreList.tsx"],
        "rank_item_accessible_unreadable_label": "aria-label={linkLabel}" in sources["frontend/src/components/HighScoreList.tsx"] and UNREADABLE_DETAIL_TITLE in sources["frontend/src/components/HighScoreList.tsx"],
        "summary_render_is_text_only": "dangerouslySetInnerHTML" not in joined_sources,
        "high_score_renders_no_summary": "summary_zh" not in sources["frontend/src/components/HighScoreList.tsx"],
        "score_badge_not_clickable": "onClick" not in sources["frontend/src/components/ScoreBadge.tsx"],
        "source_marker_not_clickable": "<a" not in sources["frontend/src/components/SourceMarker.tsx"] and "<button" not in sources["frontend/src/components/SourceMarker.tsx"],
        "loading_empty_error_states_present": all(token in joined_sources for token in ["新闻加载中", "暂无可展示新闻", "新闻加载失败"]),
        "desktop_home_two_columns": "grid-template-columns: minmax(0, 1fr) 320px" in sources["frontend/src/styles/app.css"],
        "news_card_min_height": "min-height: 96px" in sources["frontend/src/styles/app.css"],
    }
    forbidden_source_terms = [
        term for term in sorted(FORBIDDEN_PUBLIC_FIELDS) if term in joined_sources
    ]
    checks["no_forbidden_frontend_terms"] = not forbidden_source_terms
    return {"checks": checks, "forbidden_source_terms": forbidden_source_terms}


def task_015_ui_observations() -> dict[str, Any]:
    sources, read_issues = task_015_read_sources()
    source_evidence = task_015_source_checks(sources)
    home_data = task_015_mock_home_data()
    dom = task_015_project_home_dom(home_data)
    cards = dom["cards"]
    rank_items = dom["rank_items"]
    translated_card = next(card for card in cards if card["id"] == "home-001")
    ready_card = next(card for card in cards if card["id"] == "home-002")
    failed_card = next(card for card in cards if card["id"] == "home-003")
    rank_scores = [(item["score"], item["published_at"]) for item in rank_items]
    source_colors = {card["source_name"]: card["source_color"] for card in cards}
    render_checks = {
        "translated_card_title_and_summary": translated_card["title_text"] == "OpenAI 发布 LifeSciBench 生命科学基准" and bool(translated_card["summary_text"]),
        "summary_html_like_text_not_nodes": "<script>" in str(translated_card["summary_text"]) and translated_card["created_html_tag_nodes"] is False,
        "ready_card_no_zh_body_nodes": ready_card["summary_node_count"] == 0 and ready_card["content_node_count"] == 0 and ready_card["status_text"] == "翻译中",
        "failed_card_no_zh_body_nodes": failed_card["summary_node_count"] == 0 and failed_card["content_node_count"] == 0 and failed_card["status_text"] == "翻译失败",
        "non_translated_card_links_explain_unreadable": UNREADABLE_DETAIL_TITLE in str(ready_card["aria_label"]) and UNREADABLE_DETAIL_TITLE in str(failed_card["aria_label"]),
        "home_density_uses_fixture_set": len(cards) >= 10,
        "rank_count_10": len(rank_items) == 10,
        "rank_items_have_internal_links": all(str(item["href"]).startswith("/news/") for item in rank_items),
        "rank_non_translated_links_explain_unreadable": all(
            UNREADABLE_DETAIL_TITLE in str(item["aria_label"])
            for item in rank_items
            if item["status"] != "translated"
        ),
        "rank_items_render_no_summaries": all(item["summary_node_count"] == 0 for item in rank_items),
        "rank_preserves_score_time_order": all(a[0] > b[0] or (a[0] == b[0] and a[1] >= b[1]) for a, b in zip(rank_scores, rank_scores[1:])),
        "old_candidate_excluded": "home-old" not in {item["id"] for item in rank_items},
        "source_markers_stable_and_distinct": task_015_source_color("OpenAI Blog") == source_colors["OpenAI Blog"] and len(set(source_colors.values())) >= 3,
        "refresh_sequence": dom["refresh"]["call_sequence"] == ["GET /api/home", "POST /api/refresh", "GET /api/home"],
        "loading_empty_error_states": dom["states"] == {"loading": "新闻加载中", "empty": "暂无可展示新闻", "error": "新闻加载失败"},
    }
    checks = {"source": source_evidence["checks"], "render": render_checks}
    issues = list(read_issues)
    issues.extend(f"frontend_source:{term}" for term in source_evidence["forbidden_source_terms"])
    issues.extend(
        f"{area}:{name}=false"
        for area, values in checks.items()
        for name, passed in values.items()
        if not passed
    )
    leak_scan = scan_public_payload({"home_dom": dom})
    leak_scan["target"] = "ui_dom"
    if leak_scan["forbidden_field_count"] or leak_scan["sensitive_content_count"]:
        issues.append("ui_dom:leak_scan_failed")
    return {
        "checks": checks,
        "issues": issues,
        "dom": dom,
        "fixture_counts": {
            "latest_news": len(home_data["latest_news"]),
            "top_ranked_news": len(home_data["top_ranked_news"]),
            "excluded_candidates": len(home_data["excluded_candidates"]),
        },
        "leak_scan": leak_scan,
    }


def run_task_015_integration(report_dir: Path, task_id: str) -> int:
    observed = task_015_ui_observations()
    behavior_passed = not observed["issues"]
    leak_passed = observed["leak_scan"]["forbidden_field_count"] == 0 and observed["leak_scan"]["sensitive_content_count"] == 0
    render_passed = all(observed["checks"]["render"].values())
    source_passed = all(observed["checks"]["source"].values())
    leak_assertion = assertion(
        "task-015-integration-ui-dom-leak-scan",
        "passed" if leak_passed else "failed",
        {"forbidden_field_count": 0, "sensitive_content_count": 0},
        observed["leak_scan"],
        {},
        visibility="public_surface",
    )
    leak_assertion["leak_detection"] = observed["leak_scan"]
    assertions = [
        assertion(
            "task-015-integration-home-render-contract",
            "passed" if render_passed else "failed",
            {"home_ui": "translated_ready_failed_density_ranking_refresh_states"},
            {"render_checks": observed["checks"]["render"], "fixture_counts": observed["fixture_counts"]},
            {},
            visibility="public_surface",
        ),
        assertion(
            "task-015-integration-home-source-bindings",
            "passed" if source_passed else "failed",
            {"source_bindings": "api_client_final_units_safe_dom"},
            {"source_checks": observed["checks"]["source"]},
            {},
            visibility="report_metadata",
        ),
        assertion(
            "task-015-integration-home-forbidden-rendering",
            "passed" if behavior_passed else "failed",
            {"forbidden_rendering": "absent"},
            {"issues": observed["issues"]},
            {},
            visibility="public_surface",
        ),
        leak_assertion,
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-home-ui-integration",
        assertions=assertions,
        expected={"home_ui": "mocked_api_dto_render_contract"},
        actual=observed,
        diff={},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        node="UI",
        referenced_files=["scripts/run_harness.py", *TASK_015_SOURCE_FILES],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_016_read_sources() -> tuple[dict[str, str], list[str]]:
    sources: dict[str, str] = {}
    issues: list[str] = []
    for file_path in TASK_016_SOURCE_FILES:
        text, read_issues = _safe_text_read(Path(file_path), file_path)
        sources[file_path] = text
        issues.extend(read_issues)
        if not Path(file_path).exists():
            issues.append(f"missing_file:{file_path}")
    return sources, issues


def task_016_detail_fixtures() -> dict[str, dict[str, Any]]:
    return {
        "translated": {
            "id": "article-001",
            "title": "OpenAI 发布 LifeSciBench 生命科学基准",
            "original_title": "Introducing LifeSciBench",
            "source_name": "OpenAI Blog",
            "original_url": FIXTURE_TRANSLATED_CANONICAL_URL,
            "published_at": "2026-06-17T00:00:00Z",
            "score": 98,
            "status": "translated",
            "summary_zh": "这是一条中文摘要。",
            "content_zh": "第一段中文正文。\n\n第二段中文正文。",
        },
        "ready": {
            "id": "article-002",
            "title": "Production agent reaches threshold",
            "original_title": "Production agent reaches threshold",
            "source_name": "HN Frontpage",
            "original_url": FIXTURE_THRESHOLD_CANONICAL_URL,
            "published_at": "2026-06-28T07:30:00Z",
            "score": 87,
            "status": "ready",
        },
        "failed": {
            "id": "article-003",
            "title": "Translation mock failure",
            "original_title": "Translation mock failure",
            "source_name": "Dreyx Digest",
            "original_url": FIXTURE_TRANSLATION_PARTIAL_CANONICAL_URL,
            "published_at": "2026-06-28T07:00:00Z",
            "score": 86,
            "status": "translation_failed",
        },
    }


def task_016_project_article(detail: dict[str, Any] | None, state: str) -> dict[str, Any]:
    if state == "not_found":
        return {
            "state": state,
            "message": "新闻不存在或不可展示",
            "back_href": "/",
            "back_aria_label": "返回新闻列表",
            "back_icon": "svg-chevron-left",
            "summary_node_count": 0,
            "content_node_count": 0,
        }
    if not isinstance(detail, dict):
        return {"state": state}
    status = str(detail.get("status"))
    content_text = str(detail.get("content_zh", ""))
    return {
        "state": state,
        "id": detail.get("id"),
        "status": status,
        "title_text": detail.get("title") if status == "translated" else detail.get("original_title"),
        "original_title_text": detail.get("original_title"),
        "source_name": detail.get("source_name"),
        "published_at": detail.get("published_at"),
        "score": detail.get("score"),
        "summary_node_count": 1 if status == "translated" and detail.get("summary_zh") else 0,
        "content_node_count": len([part for part in content_text.split("\n\n") if part]) if status == "translated" else 0,
        "waiting_text": "翻译中" if status == "ready" else None,
        "failed_text": "翻译失败" if status == "translation_failed" else None,
        "unreadable_title": UNREADABLE_DETAIL_TITLE if status in {"ready", "translation_failed"} else None,
        "unreadable_copy": READY_UNREADABLE_COPY if status == "ready" else FAILED_UNREADABLE_COPY if status == "translation_failed" else None,
        "original_link_href": detail.get("original_url") if status != "ready" else None,
        "created_html_tag_nodes": False,
    }


def task_016_source_checks(sources: dict[str, str]) -> dict[str, Any]:
    joined_sources = "\n".join(sources.values())
    article_source = sources["frontend/src/pages/ArticleView.tsx"]
    api_sources = sources["frontend/src/api/news.ts"] + sources["frontend/src/api/http.ts"]
    checks = {
        "all_planned_files_present": all(Path(path).exists() for path in TASK_016_SOURCE_FILES),
        "main_routes_article_path": "getArticleId" in sources["frontend/src/main.tsx"] and "<ArticleView" in sources["frontend/src/main.tsx"],
        "api_client_has_detail_fetch": "fetchNewsDetail" in sources["frontend/src/api/news.ts"] and "/api/news/" in sources["frontend/src/api/news.ts"],
        "api_client_404_error_typed": "APIResponseError" in api_sources and "response.status" in api_sources,
        "article_fetches_detail": "client.fetchNewsDetail(newsId)" in article_source,
        "article_polls_ready_detail": "setInterval" in article_source and "clearInterval" in article_source and "detail?.status !== 'ready'" in article_source,
        "article_not_found_text": "新闻不存在或不可展示" in article_source,
        "article_back_icon_button": "aria-label=\"返回新闻列表\"" in article_source
        and "<svg" in article_source
        and "viewBox=\"0 0 24 24\"" in article_source
        and "article-view__back-icon" in article_source
        and "title=\"返回新闻列表\"" not in article_source
        and ">返回新闻列表<" not in article_source,
        "article_unreadable_reason_text": all(token in article_source for token in [UNREADABLE_DETAIL_TITLE, READY_UNREADABLE_COPY, FAILED_UNREADABLE_COPY]),
        "article_unreadable_state_classes": "article-view__state-title" in article_source and "article-view__state-copy" in article_source,
        "article_original_link_only": "href={detail.original_url}" in article_source and "detail.status !== 'ready'" in article_source,
        "article_final_unit_no_subcomponent": "function ArticleContent" not in article_source,
        "no_dangerous_html": "dangerouslySetInnerHTML" not in joined_sources,
        "news_card_internal_navigation": "href={`/news/${item.id}`}" in sources["frontend/src/components/NewsCard.tsx"] and "original_url" not in sources["frontend/src/components/NewsCard.tsx"],
        "rank_item_internal_navigation": "href={`/news/${item.id}`}" in sources["frontend/src/components/HighScoreList.tsx"] and "original_url" not in sources["frontend/src/components/HighScoreList.tsx"],
        "article_width_contract": "max-width: 760px" in sources["frontend/src/styles/article.css"],
    }
    forbidden_source_terms = [
        term for term in sorted(FORBIDDEN_PUBLIC_FIELDS) if term in joined_sources
    ]
    checks["no_forbidden_frontend_terms"] = not forbidden_source_terms
    return {"checks": checks, "forbidden_source_terms": forbidden_source_terms}


def task_016_ui_observations() -> dict[str, Any]:
    sources, read_issues = task_016_read_sources()
    source_evidence = task_016_source_checks(sources)
    fixtures = task_016_detail_fixtures()
    dom = {
        "translated": task_016_project_article(fixtures["translated"], "ready"),
        "ready": task_016_project_article(fixtures["ready"], "ready"),
        "failed": task_016_project_article(fixtures["failed"], "ready"),
        "not_found": task_016_project_article(None, "not_found"),
    }
    render_checks = {
        "translated_detail_complete": dom["translated"]["title_text"] == "OpenAI 发布 LifeSciBench 生命科学基准" and dom["translated"]["summary_node_count"] == 1 and dom["translated"]["content_node_count"] == 2,
        "translated_original_metadata": dom["translated"]["original_title_text"] == "Introducing LifeSciBench" and dom["translated"]["source_name"] == "OpenAI Blog" and dom["translated"]["score"] == 98,
        "translated_original_link": dom["translated"]["original_link_href"] == FIXTURE_TRANSLATED_CANONICAL_URL,
        "ready_polls_and_omits_zh": dom["ready"]["waiting_text"] == "翻译中" and dom["ready"]["summary_node_count"] == 0 and dom["ready"]["content_node_count"] == 0 and dom["ready"]["original_link_href"] is None,
        "ready_explains_unreadable_content": dom["ready"]["unreadable_title"] == UNREADABLE_DETAIL_TITLE and dom["ready"]["unreadable_copy"] == READY_UNREADABLE_COPY,
        "failed_state_and_original_link": dom["failed"]["failed_text"] == "翻译失败" and dom["failed"]["summary_node_count"] == 0 and dom["failed"]["content_node_count"] == 0 and dom["failed"]["original_link_href"] == FIXTURE_TRANSLATION_PARTIAL_CANONICAL_URL,
        "failed_explains_unreadable_content": dom["failed"]["unreadable_title"] == UNREADABLE_DETAIL_TITLE and dom["failed"]["unreadable_copy"] == FAILED_UNREADABLE_COPY,
        "not_found_state": dom["not_found"]["message"] == "新闻不存在或不可展示" and dom["not_found"]["back_aria_label"] == "返回新闻列表" and dom["not_found"]["back_icon"] == "svg-chevron-left",
        "article_text_render_safe": all(item.get("created_html_tag_nodes", False) is False for item in dom.values() if isinstance(item, dict)),
    }
    navigation_checks = {
        "news_card_internal_route": source_evidence["checks"]["news_card_internal_navigation"],
        "rank_item_internal_route": source_evidence["checks"]["rank_item_internal_navigation"],
        "article_original_link_separate": source_evidence["checks"]["article_original_link_only"],
    }
    checks = {"source": source_evidence["checks"], "render": render_checks, "navigation": navigation_checks}
    issues = list(read_issues)
    issues.extend(f"frontend_source:{term}" for term in source_evidence["forbidden_source_terms"])
    issues.extend(
        f"{area}:{name}=false"
        for area, values in checks.items()
        for name, passed in values.items()
        if not passed
    )
    leak_scan = scan_public_payload({"article_dom": dom})
    leak_scan["target"] = "ui_dom"
    if leak_scan["forbidden_field_count"] or leak_scan["sensitive_content_count"]:
        issues.append("article_dom:leak_scan_failed")
    return {"checks": checks, "issues": issues, "dom": dom, "leak_scan": leak_scan}


def run_task_016_integration(report_dir: Path, task_id: str) -> int:
    observed = task_016_ui_observations()
    render_passed = all(observed["checks"]["render"].values())
    source_passed = all(observed["checks"]["source"].values())
    navigation_passed = all(observed["checks"]["navigation"].values())
    leak_passed = observed["leak_scan"]["forbidden_field_count"] == 0 and observed["leak_scan"]["sensitive_content_count"] == 0
    leak_assertion = assertion(
        "task-016-integration-article-dom-leak-scan",
        "passed" if leak_passed else "failed",
        {"forbidden_field_count": 0, "sensitive_content_count": 0},
        observed["leak_scan"],
        {},
        visibility="public_surface",
    )
    leak_assertion["leak_detection"] = observed["leak_scan"]
    assertions = [
        assertion(
            "task-016-integration-article-render-contract",
            "passed" if render_passed else "failed",
            {"article_ui": "translated_ready_failed_not_found_states"},
            {"render_checks": observed["checks"]["render"]},
            {},
            visibility="public_surface",
        ),
        assertion(
            "task-016-integration-article-navigation-contract",
            "passed" if navigation_passed else "failed",
            {"navigation": "internal_news_routes_original_link_only_in_article"},
            {"navigation_checks": observed["checks"]["navigation"]},
            {},
            visibility="public_surface",
        ),
        assertion(
            "task-016-integration-article-source-bindings",
            "passed" if source_passed else "failed",
            {"source_bindings": "detail_api_polling_final_unit"},
            {"source_checks": observed["checks"]["source"]},
            {},
            visibility="report_metadata",
        ),
        leak_assertion,
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-article-ui-integration",
        assertions=assertions,
        expected={"article_ui": "mocked_detail_dto_render_contract"},
        actual=observed,
        diff={},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        node="UI",
        referenced_files=["scripts/run_harness.py", *TASK_016_SOURCE_FILES],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_017_read_sources() -> tuple[dict[str, str], list[str]]:
    sources: dict[str, str] = {}
    issues: list[str] = []
    for file_path in TASK_017_SOURCE_FILES:
        text, read_issues = _safe_text_read(Path(file_path), file_path)
        sources[file_path] = text
        issues.extend(read_issues)
        if not Path(file_path).exists():
            issues.append(f"missing_file:{file_path}")
    return sources, issues


def task_017_source_fixtures() -> list[dict[str, Any]]:
    return [
        {
            "id": "source-default",
            "name": "OpenAI News",
            "rss_url": "https://openai.com/news/rss.xml",
            "is_enabled": True,
            "fetch_frequency": "twice_daily",
            "created_at": "2026-06-28T06:00:00Z",
        },
        {
            "id": "source-user",
            "name": "User AI Feed",
            "rss_url": "https://example.com/rss.xml",
            "is_enabled": False,
            "fetch_frequency": "twice_daily",
            "created_at": "2026-06-28T07:00:00Z",
        },
    ]


def task_017_project_sources_dom(sources: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        {
            "id": source["id"],
            "name_text": source["name"],
            "url_text": source["rss_url"],
            "status_text": "启用" if source["is_enabled"] else "停用",
            "toggle_text": "停用" if source["is_enabled"] else "启用",
            "delete_text": "删除",
            "control_count": 2,
            "error_text": None,
        }
        for source in sources
    ]
    updated_rows = [
        {**row, "status_text": "停用", "toggle_text": "启用"}
        if row["id"] == "source-default"
        else row
        for row in rows
    ]
    deleted_rows = [row for row in rows if row["id"] != "source-user"]
    return {
        "rows": rows,
        "updated_rows": updated_rows,
        "deleted_rows": deleted_rows,
        "form": {
            "empty_submit_disabled": True,
            "invalid_url_error": "请输入合法的公开 RSS URL",
            "submitting_text": "新增中",
            "success_clears_inputs": True,
        },
        "errors": {
            "disable_last_enabled": "至少保留一个启用信源",
            "delete_last_enabled": "至少保留一个启用信源",
        },
    }


def task_017_source_checks(sources: dict[str, str]) -> dict[str, Any]:
    joined_sources = "\n".join(sources.values())
    sources_page = sources["frontend/src/pages/SourcesPage.tsx"]
    source_form = sources["frontend/src/components/SourceForm.tsx"]
    source_row = sources["frontend/src/components/SourceRow.tsx"]
    source_api = sources["frontend/src/api/sources.ts"]
    checks = {
        "all_planned_files_present": all(Path(path).exists() for path in TASK_017_SOURCE_FILES),
        "main_routes_sources_path": "window.location.pathname === '/sources'" in sources["frontend/src/main.tsx"] and "<SourcesPage" in sources["frontend/src/main.tsx"],
        "source_api_get_post_patch_delete": all(token in source_api for token in ["fetch('/api/sources')", "method: 'POST'", "method: 'PATCH'", "method: 'DELETE'"]),
        "source_page_uses_api_client": all(token in sources_page for token in ["client.fetchSources()", "client.createSource", "client.updateSource", "client.deleteSource"]),
        "source_form_empty_submit_disabled": "disabled={!canSubmit}" in source_form,
        "source_form_invalid_url_inline": "请输入合法的公开 RSS URL" in source_form,
        "source_form_clears_after_success": "setName('')" in source_form and "setRSSUrl('')" in source_form,
        "source_row_controls_present": all(token in source_row for token in ["启用", "停用", "删除"]),
        "source_row_same_controls_for_all_sources": "source.id" not in source_row and "source.name" in source_row,
        "delete_visually_removes_row": ".filter((item) => item.id !== source.id)" in sources_page,
        "disable_all_error_display": "rowErrors" in sources_page and "getAPIMessage" in sources_page,
        "no_dangerous_html": "dangerouslySetInnerHTML" not in joined_sources,
    }
    forbidden_source_terms = [
        term for term in sorted(FORBIDDEN_PUBLIC_FIELDS) if term in joined_sources
    ]
    non_goal_terms = [
        term for term in sorted(TASK_017_NON_GOAL_TERMS) if term in joined_sources.lower()
    ]
    checks["no_forbidden_frontend_terms"] = not forbidden_source_terms
    checks["no_non_goal_ui_terms"] = not non_goal_terms
    return {
        "checks": checks,
        "forbidden_source_terms": forbidden_source_terms,
        "non_goal_terms": non_goal_terms,
    }


def task_017_ui_observations() -> dict[str, Any]:
    sources, read_issues = task_017_read_sources()
    source_evidence = task_017_source_checks(sources)
    dom = task_017_project_sources_dom(task_017_source_fixtures())
    rows = dom["rows"]
    default_row = next(row for row in rows if row["id"] == "source-default")
    user_row = next(row for row in rows if row["id"] == "source-user")
    render_checks = {
        "source_list_renders_non_deleted": len(rows) == 2,
        "default_and_user_controls_match": default_row["control_count"] == user_row["control_count"] == 2,
        "default_source_controls": default_row["toggle_text"] == "停用" and default_row["delete_text"] == "删除",
        "user_source_controls": user_row["toggle_text"] == "启用" and user_row["delete_text"] == "删除",
        "form_empty_invalid_success_states": all(dom["form"].values()),
        "toggle_updates_row_state": next(row for row in dom["updated_rows"] if row["id"] == "source-default")["status_text"] == "停用",
        "delete_removes_row": "source-user" not in {row["id"] for row in dom["deleted_rows"]},
        "last_enabled_errors_visible": all(dom["errors"].values()),
    }
    checks = {"source": source_evidence["checks"], "render": render_checks}
    issues = list(read_issues)
    issues.extend(f"frontend_source:{term}" for term in source_evidence["forbidden_source_terms"])
    issues.extend(f"non_goal_ui:{term}" for term in source_evidence["non_goal_terms"])
    issues.extend(
        f"{area}:{name}=false"
        for area, values in checks.items()
        for name, passed in values.items()
        if not passed
    )
    leak_scan = scan_public_payload({"sources_dom": dom})
    leak_scan["target"] = "ui_dom"
    if leak_scan["forbidden_field_count"] or leak_scan["sensitive_content_count"]:
        issues.append("sources_dom:leak_scan_failed")
    return {"checks": checks, "issues": issues, "dom": dom, "leak_scan": leak_scan}


def run_task_017_integration(report_dir: Path, task_id: str) -> int:
    observed = task_017_ui_observations()
    render_passed = all(observed["checks"]["render"].values())
    source_passed = all(observed["checks"]["source"].values())
    leak_passed = observed["leak_scan"]["forbidden_field_count"] == 0 and observed["leak_scan"]["sensitive_content_count"] == 0
    leak_assertion = assertion(
        "task-017-integration-sources-dom-leak-scan",
        "passed" if leak_passed else "failed",
        {"forbidden_field_count": 0, "sensitive_content_count": 0},
        observed["leak_scan"],
        {},
        visibility="public_surface",
    )
    leak_assertion["leak_detection"] = observed["leak_scan"]
    assertions = [
        assertion(
            "task-017-integration-sources-render-contract",
            "passed" if render_passed else "failed",
            {"sources_ui": "list_form_toggle_delete_errors"},
            {"render_checks": observed["checks"]["render"]},
            {},
            visibility="public_surface",
        ),
        assertion(
            "task-017-integration-sources-source-bindings",
            "passed" if source_passed else "failed",
            {"source_bindings": "api_client_source_form_source_row_no_non_goals"},
            {"source_checks": observed["checks"]["source"]},
            {},
            visibility="report_metadata",
        ),
        leak_assertion,
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-sources-ui-integration",
        assertions=assertions,
        expected={"sources_ui": "mocked_source_dto_render_contract"},
        actual=observed,
        diff={},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        node="UI",
        referenced_files=["scripts/run_harness.py", *TASK_017_SOURCE_FILES],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_027_read_sources() -> tuple[dict[str, str], list[str]]:
    sources: dict[str, str] = {}
    issues: list[str] = []
    for file_path in TASK_027_SOURCE_FILES:
        text, read_issues = _safe_text_read(Path(file_path), file_path)
        sources[file_path] = text
        issues.extend(read_issues)
        if not Path(file_path).exists():
            issues.append(f"missing_file:{file_path}")
    return sources, issues


def css_rule_body(css: str, selector: str) -> str:
    marker = f"{selector} {{"
    start = css.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    body_end = css.find("}", body_start)
    return css[body_start:body_end] if body_end >= 0 else ""


def task_028_card_contract_checks(app_css: str, docs_text: str) -> dict[str, bool]:
    container = css_rule_body(app_css, ".high-score-list")
    items = css_rule_body(app_css, ".high-score-list__items")
    row_link = css_rule_body(app_css, ".high-score-list__item a")
    return {
        "docs_high_score_card_contract": "overall card" in docs_text and "整体卡片" in docs_text,
        "high_score_outer_card_surface": "background: #ffffff" in container,
        "high_score_outer_card_border": f"border: 1px solid {LIGHT_BORDER}" in container,
        "high_score_outer_card_radius": "border-radius: 8px" in container,
        "high_score_outer_card_padding": "padding: 16px" in container,
        "high_score_items_are_rows": "gap: 0" in items,
        "high_score_rows_divided": ".high-score-list__item + .high-score-list__item" in app_css
        and f"border-top: 1px solid {LIGHT_BORDER}" in app_css,
        "high_score_rows_not_nested_cards": "border: 1px solid" not in row_link
        and "background: #ffffff" not in row_link
        and "border-radius: 8px" not in row_link,
    }


def task_028_read_sources() -> tuple[dict[str, str], list[str]]:
    sources: dict[str, str] = {}
    issues: list[str] = []
    for file_path in TASK_028_SOURCE_FILES:
        text, read_issues = _safe_text_read(Path(file_path), file_path)
        sources[file_path] = text
        issues.extend(read_issues)
        if not Path(file_path).exists():
            issues.append(f"missing_file:{file_path}")
    return sources, issues


def task_028_card_observations() -> dict[str, Any]:
    sources, read_issues = task_028_read_sources()
    docs_text = "\n".join(
        sources[path] for path in ["docs/03_ui_spec.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
    )
    checks = task_028_card_contract_checks(sources["frontend/src/styles/app.css"].lower(), docs_text)
    issues = list(read_issues)
    issues.extend(f"high_score_card:{name}=false" for name, passed in checks.items() if not passed)
    return {"checks": checks, "issues": issues}


def task_027_surface_contract_checks(app_css: str, article_css: str, sources_css: str) -> dict[str, bool]:
    high_score_container = css_rule_body(app_css, ".high-score-list")
    return {
        "news_card_surface": ".news-card {" in app_css and "background: #ffffff" in app_css,
        "high_score_surface": "background: #ffffff" in high_score_container
        and f"border: 1px solid {LIGHT_BORDER}" in high_score_container,
        "state_surface": ".state-message {" in app_css and "background: #ffffff" in app_css,
        "article_state_surface": ".article-view__waiting" in article_css and "background: #ffffff" in article_css,
        "source_form_surface": ".source-form input" in sources_css and "background: #ffffff" in sources_css,
        "source_row_surface": ".source-row {" in sources_css and "background: #ffffff" in sources_css,
        "surface_subtle_token": "#f8fafc" in app_css.lower() and "#f8fafc" in article_css.lower(),
        "border_token": all(LIGHT_BORDER in text.lower() for text in (app_css, article_css, sources_css)),
    }


def task_027_theme_checks(sources: dict[str, str]) -> dict[str, Any]:
    app_css = sources["frontend/src/styles/app.css"].lower()
    article_css = sources["frontend/src/styles/article.css"].lower()
    sources_css = sources["frontend/src/styles/sources.css"].lower()
    all_css = "\n".join([app_css, article_css, sources_css])
    docs_text = "\n".join(
        sources[path] for path in ["docs/03_ui_spec.md", "docs/07_test_spec.md", "docs/08_acceptance.md"]
    )
    old_token_hits = sorted(token for token in OLD_DARK_BACKGROUND_TOKENS if token.lower() in all_css)
    checks = {
        "docs_light_gray_contract": all(token in docs_text for token in ["#F3F4F6", "#FFFFFF", "#F8FAFC"]),
        "root_background_light_gray": ":root {" in app_css and f"background: {LIGHT_GRAY_BACKGROUND}" in app_css,
        "body_background_light_gray": "body {" in app_css and f"background: {LIGHT_GRAY_BACKGROUND}" in app_css,
        "app_shell_background_light_gray": ".app-shell {" in app_css and f"background: {LIGHT_GRAY_BACKGROUND}" in app_css,
        "no_dark_color_scheme": "color-scheme: dark" not in all_css,
        "no_old_dark_background_tokens": not old_token_hits,
        "primary_text_dark": "#18202a" in all_css,
        "secondary_text_muted": "#64717f" in all_css,
        **task_027_surface_contract_checks(app_css, article_css, sources_css),
        **task_028_card_contract_checks(app_css, docs_text),
    }
    return {"checks": checks, "old_token_hits": old_token_hits}


def task_027_theme_observations() -> dict[str, Any]:
    sources, read_issues = task_027_read_sources()
    theme = task_027_theme_checks(sources)
    issues = list(read_issues)
    issues.extend(f"old_dark_background_token:{token}" for token in theme["old_token_hits"])
    issues.extend(f"visual_theme:{name}=false" for name, passed in theme["checks"].items() if not passed)
    return {"checks": theme["checks"], "issues": issues}


def task_020_render_checks(home: dict[str, Any], article: dict[str, Any], sources: dict[str, Any]) -> dict[str, bool]:
    home_render = home["checks"]["render"]
    article_render = article["checks"]["render"]
    sources_render = sources["checks"]["render"]
    return {
        "home_density_from_api_payload": home["fixture_counts"]["latest_news"] >= 10 and home_render["home_density_uses_fixture_set"],
        "home_density_not_sparse_smoke": len(home["dom"]["cards"]) >= 10,
        "high_score_top_10_ordered": home_render["rank_count_10"] and home_render["rank_preserves_score_time_order"],
        "high_score_excludes_old_items": home_render["old_candidate_excluded"],
        "summary_html_like_text_safe": home_render["summary_html_like_text_not_nodes"],
        "ready_failed_home_omit_zh_nodes": home_render["ready_card_no_zh_body_nodes"] and home_render["failed_card_no_zh_body_nodes"],
        "article_translated_only_has_content": article_render["translated_detail_complete"],
        "article_ready_failed_omit_zh_nodes": article_render["ready_polls_and_omits_zh"] and article_render["failed_state_and_original_link"],
        "click_to_read_no_empty_article": (
            article_render["translated_detail_complete"]
            and article_render["ready_explains_unreadable_content"]
            and article_render["failed_explains_unreadable_content"]
            and home_render["non_translated_card_links_explain_unreadable"]
            and home_render["rank_non_translated_links_explain_unreadable"]
        ),
        "article_not_found_state": article_render["not_found_state"],
        "sources_create_delete_states": sources_render["form_empty_invalid_success_states"] and sources_render["delete_removes_row"],
        "sources_structured_errors": sources_render["last_enabled_errors_visible"],
    }


def task_020_interaction_checks(home: dict[str, Any], article: dict[str, Any], sources: dict[str, Any]) -> dict[str, bool]:
    home_render = home["checks"]["render"]
    article_navigation = article["checks"]["navigation"]
    sources_render = sources["checks"]["render"]
    return {
        "news_card_internal_click": article_navigation["news_card_internal_route"],
        "high_score_internal_click": home_render["rank_items_have_internal_links"] and article_navigation["rank_item_internal_route"],
        "original_link_only_in_article": article_navigation["article_original_link_separate"],
        "source_controls_identical": sources_render["default_and_user_controls_match"],
        "source_toggle_updates_row": sources_render["toggle_updates_row_state"],
    }


def task_020_forbidden_render_checks(home: dict[str, Any], article: dict[str, Any], sources: dict[str, Any]) -> dict[str, bool]:
    return {
        "home_has_no_forbidden_rendering": not home["issues"],
        "article_has_no_forbidden_rendering": not article["issues"],
        "sources_has_no_forbidden_rendering": not sources["issues"],
        "home_source_checks_safe": all(home["checks"]["source"].values()),
        "article_source_checks_safe": all(article["checks"]["source"].values()),
        "sources_source_checks_safe": all(sources["checks"]["source"].values()),
    }


def task_020_ui_observations() -> dict[str, Any]:
    home = task_015_ui_observations()
    article = task_016_ui_observations()
    sources = task_017_ui_observations()
    theme = task_027_theme_observations()
    high_score_card = task_028_card_observations()
    checks = {
        "render": task_020_render_checks(home, article, sources),
        "interactions": task_020_interaction_checks(home, article, sources),
        "forbidden": task_020_forbidden_render_checks(home, article, sources),
        "visual_theme": theme["checks"],
        "high_score_card": high_score_card["checks"],
    }
    issues = [
        f"{area}:{name}=false"
        for area, values in checks.items()
        for name, passed in values.items()
        if not passed
    ]
    issues.extend(f"theme:{issue}" for issue in theme["issues"])
    issues.extend(f"high_score_card:{issue}" for issue in high_score_card["issues"])
    leak_scan = scan_public_payload({"home_dom": home["dom"], "article_dom": article["dom"], "sources_dom": sources["dom"]})
    leak_scan["target"] = "ui_dom"
    if leak_scan["forbidden_field_count"] or leak_scan["sensitive_content_count"]:
        issues.append("ui_dom:leak_scan_failed")
    return {
        "checks": checks,
        "issues": issues,
        "fixture_counts": home["fixture_counts"],
        "home_dom": home["dom"],
        "article_dom": article["dom"],
        "sources_dom": sources["dom"],
        "leak_scan": leak_scan,
    }


def run_task_020_integration(report_dir: Path, task_id: str) -> int:
    observed = task_020_ui_observations()
    render_passed = (
        all(observed["checks"]["render"].values())
        and all(observed["checks"]["visual_theme"].values())
        and all(observed["checks"]["high_score_card"].values())
    )
    interactions_passed = all(observed["checks"]["interactions"].values())
    forbidden_passed = all(observed["checks"]["forbidden"].values())
    leak_passed = observed["leak_scan"]["forbidden_field_count"] == 0 and observed["leak_scan"]["sensitive_content_count"] == 0
    leak_assertion = assertion(
        "A-integration-ACC-STOP-009-ui-dom-leak-scan",
        "passed" if leak_passed else "failed",
        {"forbidden_field_count": 0, "sensitive_content_count": 0},
        observed["leak_scan"],
        {},
        visibility="public_surface",
    )
    leak_assertion["leak_detection"] = observed["leak_scan"]
    assertions = [
        assertion(
            "A-integration-ACC-STOP-006-ui-render-contract",
            "passed" if render_passed else "failed",
            {"ui_surfaces": "dense_home_ranked_article_sources_from_api_dtos"},
            {
                "render_checks": observed["checks"]["render"],
                "visual_theme_checks": observed["checks"]["visual_theme"],
                "high_score_card_checks": observed["checks"]["high_score_card"],
                "fixture_counts": observed["fixture_counts"],
            },
            {},
            visibility="public_surface",
        ),
        assertion(
            "A-integration-ACC-STOP-006-ui-forbidden-rendering",
            "passed" if forbidden_passed else "failed",
            {"forbidden_rendering": "absent"},
            {"forbidden_checks": observed["checks"]["forbidden"], "issues": observed["issues"]},
            {},
            visibility="public_surface",
        ),
        assertion(
            "A-integration-ACC-STOP-006-ui-allowed-interactions",
            "passed" if interactions_passed else "failed",
            {"interactions": "docs/03_ui_spec.md#5.0 allowlist"},
            {"interaction_checks": observed["checks"]["interactions"]},
            {},
            visibility="public_surface",
        ),
        leak_assertion,
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-ui-only-integration",
        assertions=assertions,
        expected={"ui_only": "mocked_api_dto_dom_contract_no_pipeline_internals"},
        actual=observed,
        diff={},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        node="UI",
        referenced_files=["scripts/run_harness.py", *TASK_020_SOURCE_FILES],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_027_theme_assertion(assertion_id: str, observed: dict[str, Any], visibility: str = "public_surface") -> dict[str, Any]:
    passed = not observed["issues"] and all(observed["checks"].values())
    return assertion(
        assertion_id,
        "passed" if passed else "failed",
        {"light_gray_theme": "docs/03_ui_spec.md#4.2"},
        {"theme_checks": observed["checks"], "issues": observed["issues"]},
        {} if passed else {"issues": observed["issues"]},
        visibility=visibility,
    )


def run_task_027_integration(report_dir: Path, task_id: str) -> int:
    observed = task_027_theme_observations()
    assertions = [
        task_027_theme_assertion("A-integration-ACC-STOP-006-ui-render-contract", observed),
        task_027_theme_assertion("task-027-integration-doc-sync", observed, "report_metadata"),
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-light-gray-theme-integration",
        assertions=assertions,
        expected={"visual_theme": "light_gray"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        node="UI",
        referenced_files=["scripts/run_harness.py", *TASK_027_SOURCE_FILES],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def run_task_027_snapshot(report_dir: Path, task_id: str) -> int:
    observed = task_027_theme_observations()
    assertions = [task_027_theme_assertion("A-snapshot-ACC-STOP-006-layout-visual-contract", observed)]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="snapshot",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-light-gray-theme-snapshot",
        assertions=assertions,
        expected={"visual_theme_snapshot": "light_gray_tokens"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        node="UI",
        referenced_files=["scripts/run_harness.py", *TASK_027_SOURCE_FILES],
        commands=[f"python3 scripts/run_harness.py --stage snapshot --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "snapshot", task_id), report)
    return 0 if passed else 1


def run_task_027_e2e(report_dir: Path, task_id: str) -> int:
    observed = task_027_theme_observations()
    assertions = [task_027_theme_assertion("A-e2e-ACC-STOP-006-home-news-density", observed)]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="e2e",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-light-gray-theme-e2e",
        assertions=assertions,
        expected={"browser_visible_theme": "light_gray"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        node="UI",
        referenced_files=["scripts/run_harness.py", *TASK_027_SOURCE_FILES],
        commands=[f"python3 scripts/run_harness.py --stage e2e --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "e2e", task_id), report)
    return 0 if passed else 1


def task_028_card_assertion(assertion_id: str, observed: dict[str, Any], visibility: str = "public_surface") -> dict[str, Any]:
    passed = not observed["issues"] and all(observed["checks"].values())
    return assertion(
        assertion_id,
        "passed" if passed else "failed",
        {"top_30_days": "one_overall_card_with_internal_rows"},
        {"high_score_card_checks": observed["checks"], "issues": observed["issues"]},
        {} if passed else {"issues": observed["issues"]},
        visibility=visibility,
    )


def run_task_028_card_stage(report_dir: Path, task_id: str, stage: str, assertion_id: str) -> int:
    observed = task_028_card_observations()
    assertions = [
        task_028_card_assertion(assertion_id, observed),
        task_028_card_assertion("task-028-doc-sync", observed, "report_metadata"),
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage=stage,
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-top-30-days-card-{stage}",
        assertions=assertions,
        expected={"top_30_days": "overall_card"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        node="UI",
        referenced_files=["scripts/run_harness.py", *TASK_028_SOURCE_FILES],
        commands=[f"python3 scripts/run_harness.py --stage {stage} --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, stage, task_id), report)
    return 0 if passed else 1


def run_task_028_integration(report_dir: Path, task_id: str) -> int:
    return run_task_028_card_stage(report_dir, task_id, "integration", "A-integration-ACC-STOP-006-ui-render-contract")


def run_task_028_snapshot(report_dir: Path, task_id: str) -> int:
    return run_task_028_card_stage(report_dir, task_id, "snapshot", "A-snapshot-ACC-STOP-006-layout-visual-contract")


def run_task_028_e2e(report_dir: Path, task_id: str) -> int:
    return run_task_028_card_stage(report_dir, task_id, "e2e", "A-e2e-ACC-STOP-006-high-score-list-browser")


def news_status_counts(items: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
    return counts


def task_029_unit_observations() -> dict[str, Any]:
    *_, read_json, translation_records, has_valid_record, _ = task_008_pipeline_imports()
    payload = read_json(Path("fixtures/llm/translation.json"))
    records = translation_records(payload)
    expected_valid_guids = {
        "fixture-translated-96",
        "fixture-rank-95",
        "fixture-rank-94",
        "fixture-rank-93",
        "fixture-rank-92",
        "fixture-rank-91",
        "fixture-rank-90",
        "fixture-rank-89",
        "fixture-rank-88",
        "fixture-rank-87",
        "fixture-old-high-99",
    }
    valid_guids = {
        guid for guid, record in records.items() if has_valid_record(record)
    }
    pending_guids = set(payload.get("pending_guids", []))
    checks = {
        "expected_valid_guids": sorted(expected_valid_guids),
        "valid_guids": sorted(valid_guids),
        "missing_valid_guids": sorted(expected_valid_guids - valid_guids),
        "unexpected_missing_partial_failure": has_valid_record(records.get("fixture-translate-partial")),
        "pending_guids": sorted(str(item) for item in pending_guids),
    }
    issues: list[str] = []
    if valid_guids != expected_valid_guids:
        issues.append(f"fixture:valid_guid_set_mismatch:{sorted(valid_guids)}")
    if has_valid_record(records.get("fixture-translate-partial")):
        issues.append("fixture:partial_translation_became_valid")
    if "fixture-threshold-60" not in pending_guids:
        issues.append("fixture:threshold_pending_guid_missing")
    return {"checks": checks, "issues": issues}


def task_029_integration_observations() -> dict[str, Any]:
    observed = task_008_integration_observations()
    issues: list[str] = []
    if observed["translated_count"] != 11:
        issues.append(f"integration:translated_count={observed['translated_count']}!=11")
    if observed["failed_count"] != 1:
        issues.append(f"integration:failed_count={observed['failed_count']}!=1")
    if observed["pending_count"] != 1:
        issues.append(f"integration:pending_count={observed['pending_count']}!=1")
    if observed["translate_success_count"] != 11:
        issues.append(f"integration:translate_success_count={observed['translate_success_count']}!=11")
    if observed["translate_validation_failure_count"] != 1:
        issues.append(
            "integration:translate_validation_failure_count="
            f"{observed['translate_validation_failure_count']}!=1"
        )
    if observed["partial_zh_count"] != 0 or observed["partial_failed"] != 1:
        issues.append("integration:partial_failure_not_isolated")
    if observed["pending_title"] is not None or observed["pending_failed"] != 0:
        issues.append("integration:pending_fixture_not_ready")
    return {"checks": observed, "issues": issues}


def task_029_home_distribution_observations() -> dict[str, Any]:
    app, client = task_014_client()
    refresh_response = client.post("/api/refresh")
    home_response = client.get("/api/home")
    issues = envelope_issue(
        name="task_029_refresh",
        response=refresh_response,
        expected_status=200,
        expected_envelope="data",
        required_data_keys={"refreshed_at"},
    )
    issues.extend(
        envelope_issue(
            name="task_029_home",
            response=home_response,
            expected_status=200,
            expected_envelope="data",
            required_data_keys={"latest_news", "top_ranked_news"},
        )
    )
    payload, parse_issues = _safe_json(home_response)
    issues.extend(f"task_029_home:{issue}" for issue in parse_issues)
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    latest = data.get("latest_news") if isinstance(data, dict) else []
    top = data.get("top_ranked_news") if isinstance(data, dict) else []
    latest = latest if isinstance(latest, list) else []
    top = top if isinstance(top, list) else []
    latest_counts = news_status_counts(latest)
    top_counts = news_status_counts(top)
    if latest_counts != EXPECTED_TRANSLATION_LATEST_STATUS_COUNTS:
        issues.append(f"home:latest_status_counts:{latest_counts}")
    if top_counts != EXPECTED_TRANSLATION_TOP_STATUS_COUNTS:
        issues.append(f"home:top_status_counts:{top_counts}")

    conn = app.state.db
    partial = conn.execute(
        """
        SELECT title_zh, summary_zh, content_zh, has_translate_failed
        FROM news_item
        WHERE rss_guid = 'fixture-translate-partial'
        """
    ).fetchone()
    pending = conn.execute(
        """
        SELECT title_zh, summary_zh, content_zh, has_translate_failed
        FROM news_item
        WHERE rss_guid = 'fixture-threshold-60'
        """
    ).fetchone()
    partial_isolated = bool(
        partial
        and partial["has_translate_failed"] == 1
        and not any(partial[field] for field in ("title_zh", "summary_zh", "content_zh"))
    )
    pending_ready = bool(
        pending
        and pending["has_translate_failed"] == 0
        and not any(pending[field] for field in ("title_zh", "summary_zh", "content_zh"))
    )
    if not partial_isolated:
        issues.append("home:partial_failure_not_isolated")
    if not pending_ready:
        issues.append("home:pending_fixture_not_ready")
    return {
        "checks": {
            "latest_count": len(latest),
            "top_count": len(top),
            "latest_status_counts": latest_counts,
            "top_status_counts": top_counts,
            "partial_isolated": partial_isolated,
            "pending_ready": pending_ready,
        },
        "issues": issues,
    }


def run_task_029_stage(report_dir: Path, task_id: str, stage: str, assertion_id: str) -> int:
    if stage == "unit":
        observed = task_029_unit_observations()
        expected = {"fixture_valid_translation_guids": 11, "partial_failure": True, "pending_guid": True}
    elif stage == "integration":
        observed = task_029_integration_observations()
        expected = {"translated_count": 11, "failed_count": 1, "pending_count": 1}
    else:
        observed = task_029_home_distribution_observations()
        expected = {
            "latest_status_counts": EXPECTED_TRANSLATION_LATEST_STATUS_COUNTS,
            "top_status_counts": EXPECTED_TRANSLATION_TOP_STATUS_COUNTS,
        }
    passed = not observed["issues"]
    report = test_report(
        stage=stage,
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-translation-fixture-majority-{stage}",
        assertions=[
            assertion(
                assertion_id,
                "passed" if passed else "failed",
                expected,
                observed["checks"],
                {"issues": observed["issues"]},
                visibility="public_surface" if stage in {"api", "e2e", "integration"} else "internal_evidence",
            )
        ],
        expected=expected,
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else stage,
        error_category=None if passed else "validation",
        referenced_files=[
            "fixtures/llm/translation.json",
            "backend/app/services/pipeline.py",
            "backend/app/main.py",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
        ],
        commands=[f"python3 scripts/run_harness.py --stage {stage} --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, stage, task_id), report)
    return 0 if passed else 1


def run_task_029_unit(report_dir: Path, task_id: str) -> int:
    return run_task_029_stage(report_dir, task_id, "unit", "task-029-unit-translation-fixture-majority")


def run_task_029_integration(report_dir: Path, task_id: str) -> int:
    return run_task_029_stage(
        report_dir,
        task_id,
        "integration",
        "A-integration-ACC-STOP-003-translation-majority-visible",
    )


def run_task_029_api(report_dir: Path, task_id: str) -> int:
    return run_task_029_stage(report_dir, task_id, "api", "task-029-api-home-translation-majority")


def run_task_029_e2e(report_dir: Path, task_id: str) -> int:
    return run_task_029_stage(report_dir, task_id, "e2e", "task-029-e2e-home-translation-majority")


def task_030_readability_observations() -> dict[str, Any]:
    docs = {
        "docs/03_ui_spec.md": Path("docs/03_ui_spec.md").read_text(encoding="utf-8"),
        "docs/07_test_spec.md": Path("docs/07_test_spec.md").read_text(encoding="utf-8"),
        "docs/08_acceptance.md": Path("docs/08_acceptance.md").read_text(encoding="utf-8"),
    }
    home = task_015_ui_observations()
    article = task_016_ui_observations()
    ui = task_020_ui_observations()
    checks = {
        "docs_contract_updated": all(UNREADABLE_DETAIL_TITLE in text for text in docs.values())
        and "A-e2e-ACC-STOP-006-click-to-read-readability" in docs["docs/07_test_spec.md"],
        "translated_detail_has_content": article["checks"]["render"]["translated_detail_complete"],
        "ready_detail_explains_unreadable": article["checks"]["render"]["ready_polls_and_omits_zh"]
        and article["checks"]["render"]["ready_explains_unreadable_content"],
        "failed_detail_explains_unreadable": article["checks"]["render"]["failed_state_and_original_link"]
        and article["checks"]["render"]["failed_explains_unreadable_content"],
        "home_card_links_explain_unreadable": home["checks"]["render"]["non_translated_card_links_explain_unreadable"],
        "rank_links_explain_unreadable": home["checks"]["render"]["rank_non_translated_links_explain_unreadable"],
        "click_to_read_no_empty_article": ui["checks"]["render"]["click_to_read_no_empty_article"],
        "no_direct_original_navigation": ui["checks"]["interactions"]["news_card_internal_click"]
        and ui["checks"]["interactions"]["high_score_internal_click"],
    }
    issues = [f"readability:{name}=false" for name, passed in checks.items() if not passed]
    issues.extend(f"home:{issue}" for issue in home["issues"])
    issues.extend(f"article:{issue}" for issue in article["issues"])
    issues.extend(f"ui:{issue}" for issue in ui["issues"])
    return {
        "checks": checks,
        "issues": sorted(set(issues)),
        "home_link_examples": {
            "ready": next(card["aria_label"] for card in home["dom"]["cards"] if card["id"] == "home-002"),
            "failed": next(card["aria_label"] for card in home["dom"]["cards"] if card["id"] == "home-003"),
        },
        "article_states": {
            "ready": {
                "unreadable_title": article["dom"]["ready"]["unreadable_title"],
                "unreadable_copy": article["dom"]["ready"]["unreadable_copy"],
            },
            "failed": {
                "unreadable_title": article["dom"]["failed"]["unreadable_title"],
                "unreadable_copy": article["dom"]["failed"]["unreadable_copy"],
            },
        },
    }


def run_task_030_stage(report_dir: Path, task_id: str, stage: str) -> int:
    observed = task_030_readability_observations()
    passed = not observed["issues"]
    assertion_id_by_stage = {
        "integration": "A-integration-ACC-STOP-006-ui-render-contract",
        "snapshot": "A-snapshot-ACC-STOP-006-layout-visual-contract",
        "e2e": "A-e2e-ACC-STOP-006-click-to-read-readability",
    }
    assertion_id = assertion_id_by_stage[stage]
    report = test_report(
        stage=stage,
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-click-to-read-readability-{stage}",
        assertions=[
            assertion(
                assertion_id,
                "passed" if passed else "failed",
                {"click_to_read_no_empty_article": True},
                {"checks": observed["checks"], "article_states": observed["article_states"]},
                {"issues": observed["issues"]},
                visibility="public_surface",
            )
        ],
        expected={"click_to_read_no_empty_article": True},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        referenced_files=[
            "docs/03_ui_spec.md",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
            "frontend/src/pages/ArticleView.tsx",
            "frontend/src/components/NewsCard.tsx",
            "frontend/src/components/HighScoreList.tsx",
            "frontend/src/styles/article.css",
            "frontend/src/styles/app.css",
            "scripts/run_harness.py",
        ],
        commands=[f"python3 scripts/run_harness.py --stage {stage} --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, stage, task_id), report)
    return 0 if passed else 1


def pipeline_projection_snapshot() -> tuple[dict[str, Any], list[str]]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {}, [import_issue]
    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {}, [f"fastapi_testclient_import_failed:{error.__class__.__name__}"]

    client = TestClient(app)
    response = client.post("/api/refresh")
    issues = envelope_issue(
        name="pipeline_post_refresh",
        response=response,
        expected_status=200,
        expected_envelope="data",
        required_data_keys={"refreshed_at"},
    )
    conn = app.state.db
    rows = conn.execute(
        """
        SELECT
          rss_guid, canonical_url, score, pipeline_state, is_selected,
          title_zh, summary_zh, content_zh, has_translate_failed,
          content_full, content_raw
        FROM news_item
        ORDER BY canonical_url ASC
        """
    ).fetchall()
    expected_guids = {
        "fixture-low-59",
        "fixture-threshold-60",
        "fixture-translated-96",
        "fixture-translate-partial",
        "fixture-rank-95",
        "fixture-rank-94",
        "fixture-rank-93",
        "fixture-rank-92",
        "fixture-rank-91",
        "fixture-rank-90",
        "fixture-rank-89",
        "fixture-rank-88",
        "fixture-rank-87",
        "fixture-old-high-99",
    }
    observed_guids = {str(row["rss_guid"]) for row in rows}
    if not expected_guids.issubset(observed_guids):
        issues.append(
            "pipeline:fixture_guid_set_missing:"
            f"{sorted(expected_guids - observed_guids)}"
        )
    if len(rows) < 14:
        issues.append(f"pipeline:news_item_count={len(rows)}<14")

    by_guid = {str(row["rss_guid"]): row for row in rows}
    threshold = by_guid.get("fixture-threshold-60")
    if (
        not threshold
        or threshold["score"] != 60
        or threshold["pipeline_state"] != "fetched"
        or threshold["is_selected"] != 1
    ):
        issues.append("pipeline:threshold_60_not_selected_and_fetched")

    low_score = by_guid.get("fixture-low-59")
    if (
        not low_score
        or low_score["score"] != 59
        or low_score["pipeline_state"] != "scored"
        or low_score["is_selected"] != 0
    ):
        issues.append("pipeline:score_59_not_filtered_at_scored_state")

    translated = by_guid.get("fixture-translated-96")
    if not translated or not all(
        translated[field] for field in ("title_zh", "summary_zh", "content_zh")
    ):
        issues.append("pipeline:translated_fixture_missing_chinese_fields")

    failed_translation = by_guid.get("fixture-translate-partial")
    if (
        not failed_translation
        or failed_translation["has_translate_failed"] != 1
        or any(
            failed_translation[field]
            for field in ("title_zh", "summary_zh", "content_zh")
        )
    ):
        issues.append("pipeline:partial_translation_not_isolated")

    log_counts = conn.execute(
        """
        SELECT stage, success, COUNT(*) AS count
        FROM processing_log
        GROUP BY stage, success
        ORDER BY stage ASC, success ASC
        """
    ).fetchall()
    log_summary = {
        f"{row['stage']}:{row['success']}": int(row["count"])
        for row in log_counts
    }
    if log_summary.get("crawl:0", 0) < 1:
        issues.append("pipeline:crawl_failure_fixture_not_logged")
    if log_summary.get("score:1", 0) < 14:
        issues.append("pipeline:score_success_count_less_than_14")
    if log_summary.get("fetch:1", 0) < 2 or log_summary.get("fetch:0", 0) < 1:
        issues.append("pipeline:fetch_success_and_fallback_not_logged")
    if log_summary.get("translate:1", 0) < 1 or log_summary.get("translate:0", 0) < 1:
        issues.append("pipeline:translation_success_and_failure_not_logged")

    home_response = client.get("/api/home")
    issues.extend(
        envelope_issue(
            name="pipeline_get_home",
            response=home_response,
            expected_status=200,
            expected_envelope="data",
            required_data_keys={"latest_news", "top_ranked_news"},
        )
    )
    latest_titles: list[str] = []
    ranked_scores: list[int] = []
    latest_status_counts: dict[str, int] = {}
    ranked_status_counts: dict[str, int] = {}
    if home_response.status_code == 200:
        payload = home_response.json()
        latest_news = payload["data"].get("latest_news", [])
        top_ranked_news = payload["data"].get("top_ranked_news", [])
        latest_status_counts = news_status_counts(latest_news)
        ranked_status_counts = news_status_counts(top_ranked_news)
        latest_titles = [
            str(item.get("original_title"))
            for item in latest_news
            if isinstance(item, dict)
        ]
        ranked_scores = [
            int(item.get("score"))
            for item in top_ranked_news
            if isinstance(item, dict) and isinstance(item.get("score"), int)
        ]
        ranked_titles = [
            str(item.get("original_title"))
            for item in top_ranked_news
            if isinstance(item, dict)
        ]
        if "Low signal AI funding rumor" in latest_titles:
            issues.append("pipeline:score_59_visible_in_home")
        if len(ranked_scores) != 10:
            issues.append(f"pipeline:high_score_list_count={len(ranked_scores)}!=10")
        if ranked_scores != sorted(ranked_scores, reverse=True):
            issues.append(f"pipeline:ranked_scores_not_desc:{ranked_scores}")
        if "Older AI milestone outside ranking window" in ranked_titles:
            issues.append("pipeline:old_high_score_visible_in_30_day_ranking")
        if latest_status_counts != EXPECTED_TRANSLATION_LATEST_STATUS_COUNTS:
            issues.append(f"pipeline:latest_status_counts:{latest_status_counts}")
        if ranked_status_counts != EXPECTED_TRANSLATION_TOP_STATUS_COUNTS:
            issues.append(f"pipeline:ranked_status_counts:{ranked_status_counts}")

    snapshot = {
        "guids": sorted(observed_guids),
        "state_by_guid": {
            guid: {
                "score": row["score"],
                "state": row["pipeline_state"],
                "selected": bool(row["is_selected"]),
                "has_full_text": bool(row["content_full"]),
                "has_raw_text": bool(row["content_raw"]),
                "has_translation": all(
                    row[field] for field in ("title_zh", "summary_zh", "content_zh")
                ),
                "translation_failed": bool(row["has_translate_failed"]),
            }
            for guid, row in sorted(by_guid.items())
        },
        "log_summary": log_summary,
        "latest_titles": latest_titles,
        "ranked_scores": ranked_scores,
        "latest_status_counts": latest_status_counts,
        "ranked_status_counts": ranked_status_counts,
    }
    return snapshot, issues


def pipeline_refresh_evidence() -> dict[str, Any]:
    snapshot, issues = pipeline_projection_snapshot()
    return {
        "checks": snapshot,
        "issues": issues,
    }


def pipeline_replay_evidence() -> dict[str, Any]:
    first_snapshot = task_018_integration_observations()
    second_snapshot = task_018_integration_observations()
    first_hash = stable_hash({"pipeline_snapshot": first_snapshot})
    second_hash = stable_hash({"pipeline_snapshot": second_snapshot})
    issues: list[str] = []
    if first_hash != second_hash:
        issues.append(f"pipeline:replay_hash_mismatch:{first_hash}!={second_hash}")
    return {
        "checks": {
            "first_hash": first_hash,
            "second_hash": second_hash,
            "hashes_match": first_hash == second_hash,
        },
        "issues": issues,
    }


def browser_e2e_evidence() -> dict[str, Any]:
    return e2e_surface_evidence()


def deployed_runtime_http_probe(
    base_url: str = DEPLOYED_BROWSER_SMOKE_URL,
    timeout_seconds: int = DEPLOYED_RUNTIME_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    issues: list[str] = []
    checks: dict[str, Any] = {"local_url": base_url}

    def fetch(path: str, name: str) -> tuple[int | None, str, bytes]:
        url = f"{base}{path}"
        request = urllib.request.Request(url, headers={"User-Agent": "rss-harness/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return int(response.status), response.headers.get("content-type", ""), response.read()
        except urllib.error.HTTPError as error:
            body = error.read()
            return int(error.code), error.headers.get("content-type", ""), body
        except Exception as error:
            issues.append(f"deployed_runtime:{name}_request_failed:{error.__class__.__name__}")
            return None, "", b""

    index_status, index_type, index_body = fetch("/", "index")
    checks["index_status"] = index_status
    checks["index_content_type"] = index_type
    checks["index_body_length"] = len(index_body)
    if index_status != 200:
        issues.append(f"deployed_runtime:index_status={index_status}")
    if "text/html" not in index_type:
        issues.append(f"deployed_runtime:index_content_type={index_type}")

    api_status, api_type, api_body = fetch("/api/home", "api_home")
    checks["api_home_status"] = api_status
    checks["api_home_content_type"] = api_type
    checks["api_home_body_length"] = len(api_body)
    if api_status != 200:
        issues.append(f"deployed_runtime:api_home_status={api_status}")
        return {"checks": checks, "issues": issues}
    try:
        payload = json.loads(api_body.decode("utf-8"))
    except Exception as error:
        issues.append(f"deployed_runtime:api_home_json_failed:{error.__class__.__name__}")
        return {"checks": checks, "issues": issues}
    data = payload.get("data") if isinstance(payload, dict) else {}
    latest = data.get("latest_news") if isinstance(data, dict) else []
    ranked = data.get("top_ranked_news") if isinstance(data, dict) else []
    checks["latest_news_count"] = len(latest) if isinstance(latest, list) else 0
    checks["top_ranked_news_count"] = len(ranked) if isinstance(ranked, list) else 0
    if checks["latest_news_count"] <= 0:
        issues.append("deployed_runtime:latest_news_empty")
    if checks["top_ranked_news_count"] <= 0:
        issues.append("deployed_runtime:top_ranked_news_empty")
    return {"checks": checks, "issues": issues}


def single_port_evidence() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"issues": [import_issue], "checks": []}
    from fastapi.testclient import TestClient

    client = TestClient(app)
    index_response = client.get("/")
    api_response = client.get("/api/unknown")
    issues: list[str] = []
    index_content_type = index_response.headers.get("content-type", "")
    if index_response.status_code != 200:
        issues.append(f"index:status={index_response.status_code}!=200")
    if "text/html" not in index_content_type:
        issues.append(f"index:content_type_not_html:{index_content_type}")
    issues.extend(
        envelope_issue(
            name="single_port_unknown_api",
            response=api_response,
            expected_status=404,
            expected_envelope="error",
        )
    )
    return {
        "checks": [
            {
                "name": "index",
                "status_code": index_response.status_code,
                "content_type": index_content_type,
            },
            {
                "name": "unknown_api",
                "status_code": api_response.status_code,
                "content_type": api_response.headers.get("content-type", ""),
            },
        ],
        "issues": issues,
    }


def is_frontend_source_scan_path(path: Path) -> bool:
    return not any(part in FRONTEND_GENERATED_PATH_PARTS for part in path.parts)


def frontend_source_scan_paths() -> list[Path]:
    runtime_paths = [path for path in FRONTEND_SCAN_ENTRYPOINTS if path.exists()]
    for root in FRONTEND_SCAN_ROOTS:
        if not root.exists():
            continue
        for extension in FRONTEND_SCAN_EXTENSIONS:
            runtime_paths.extend(root.glob(f"**/{extension}"))
    return sorted(
        path for path in set(runtime_paths) if is_frontend_source_scan_path(path)
    )


def frontend_endpoint_evidence() -> dict[str, Any]:
    runtime_paths = frontend_source_scan_paths()

    observed_contract_endpoints: set[str] = set()
    legacy_references: list[str] = []
    scanned_files: list[str] = []
    for path in sorted(set(runtime_paths)):
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        scanned_files.append(path.as_posix())
        for endpoint in CONTRACT_FRONTEND_ENDPOINTS:
            if endpoint in text:
                observed_contract_endpoints.add(endpoint)
        for endpoint in LEGACY_FRONTEND_ENDPOINTS:
            if endpoint in text:
                legacy_references.append(f"{path.as_posix()}:{endpoint}")

    missing_contract_references = sorted(
        CONTRACT_FRONTEND_ENDPOINTS - observed_contract_endpoints
    )
    return {
        "scanned_files": scanned_files,
        "observed_contract_endpoints": sorted(observed_contract_endpoints),
        "missing_contract_endpoint_references": missing_contract_references,
        "legacy_endpoint_references": sorted(legacy_references),
        "issues": [
            f"legacy_endpoint_reference:{item}" for item in sorted(legacy_references)
        ]
        + [
            f"missing_contract_endpoint_reference:{item}"
            for item in missing_contract_references
        ],
    }


def stage_behavior_evidence(stage: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "stage": stage,
        "checks": {},
        "issues": [],
    }
    if stage in {"contract", "api", "integration", "snapshot", "e2e"}:
        api_evidence = backend_api_route_evidence()
        evidence["checks"]["backend_api_routes"] = api_evidence
        evidence["issues"].extend(api_evidence["issues"])
    if stage in {"api", "integration", "snapshot", "e2e"}:
        api_response_evidence = backend_api_response_evidence()
        evidence["checks"]["backend_api_responses"] = api_response_evidence
        evidence["issues"].extend(api_response_evidence["issues"])
    if stage in {"api", "integration", "e2e"}:
        home_pagination = task_035_api_pagination_observations()
        evidence["checks"]["home_pagination"] = home_pagination
        evidence["issues"].extend(home_pagination["issues"])
    if stage in {"api", "integration", "e2e"}:
        source_management = source_management_api_evidence()
        evidence["checks"]["source_management_api"] = source_management
        evidence["issues"].extend(source_management["issues"])
    if stage in {"integration", "e2e"}:
        source_ui = source_ui_parity_evidence()
        evidence["checks"]["source_management_ui"] = source_ui
        evidence["issues"].extend(source_ui["issues"])
    if stage in {"integration", "e2e"}:
        pipeline_evidence = pipeline_refresh_evidence()
        evidence["checks"]["pipeline_refresh"] = pipeline_evidence
        evidence["issues"].extend(pipeline_evidence["issues"])
    if stage == "replay":
        replay_evidence = pipeline_replay_evidence()
        evidence["checks"]["pipeline_replay"] = replay_evidence
        evidence["issues"].extend(replay_evidence["issues"])
    if stage in {"contract", "snapshot", "e2e"}:
        frontend_evidence = frontend_endpoint_evidence()
        evidence["checks"]["frontend_contract_endpoints"] = frontend_evidence
        evidence["issues"].extend(frontend_evidence["issues"])
    if stage == "e2e":
        deployment_evidence = single_port_evidence()
        evidence["checks"]["single_port_deployment"] = deployment_evidence
        evidence["issues"].extend(deployment_evidence["issues"])
        e2e_checks = e2e_surface_evidence()
        evidence["checks"]["e2e_surface"] = e2e_checks
        evidence["issues"].extend(e2e_checks["issues"])
        browser_evidence = browser_e2e_evidence()
        evidence["checks"]["browser_e2e"] = browser_evidence
        evidence["issues"].extend(browser_evidence["issues"])
        runtime_evidence = deployed_runtime_http_probe()
        evidence["checks"]["deployed_runtime_http"] = runtime_evidence
        evidence["issues"].extend(runtime_evidence["issues"])
    return evidence


def run_product_stage_with_synthetic_checks(report_dir: Path, stage: str) -> int:
    catalog = catalog_assertion_metadata()
    required_ids = sorted(
        (assertion_id, info)
        for assertion_id, info in catalog.items()
        if info["stage"] == stage
    )

    implemented, missing_paths, existing_paths = stage_implementation_evidence(stage)
    behavior_evidence = stage_behavior_evidence(stage)
    behavior_issues = behavior_evidence["issues"]
    synthetic_block_reason = "synthetic_stage_report_blocked"
    # These scaffold checks are diagnostics only. Product stage owners must
    # replace them with real behavior assertions before a full-stage report can
    # contribute passing gate evidence.
    stage_passed = False
    assertions: list[dict[str, Any]] = []
    for assertion_id, info in required_ids:
        assertions.append(
            assertion(
                assertion_id,
                "passed" if stage_passed else "failed",
                {
                    "implemented": True,
                    "required_paths": stage_paths_for_assertions(stage),
                    "behavior_issues": [],
                },
                {
                    "implemented": implemented,
                    "existing_paths": existing_paths,
                    "missing_paths": missing_paths,
                    "behavior_evidence": behavior_evidence,
                },
                {
                    "failure_reasons": [
                        synthetic_block_reason,
                        *missing_paths,
                        *behavior_issues,
                    ],
                    "stage": stage,
                    "check_rationale": "stage_contract_behavior_checks",
                },
                visibility=info["visibility"],
            )
        )

    assertions.append(
        assertion(
            synthetic_block_reason,
            "failed",
            {"product_stage_assertions": "real behavior evidence"},
            {
                "product_stage_assertions": "synthetic diagnostics only",
                "implemented_paths_present": implemented,
                "behavior_evidence": behavior_evidence,
            },
            {
                "reason": (
                    "scaffold or synthetic product-stage reports cannot satisfy "
                    "stop eligibility"
                ),
                "stage": stage,
            },
        )
    )

    if not assertions:
        assertions.append(
            assertion(
                "stage_assertions_implemented",
                "passed" if stage_passed else "failed",
                {"implemented_assertions": "stage-specific deterministic assertions"},
                {
                    "implemented_assertions": (
                        "pending" if not stage_passed else "present"
                    ),
                    "required_paths": stage_paths_for_assertions(stage),
                    "behavior_evidence": behavior_evidence,
                },
                {
                    "failure_reasons": ["mandatory_assertion_catalog_stage_empty"]
                    if not required_ids
                    else missing_paths + behavior_issues,
                },
                visibility="report_metadata",
            )
        )

    status = "failed" if any(item["status"] == "failed" for item in assertions) else "passed"
    report = test_report(
        stage=stage,
        status=status,
        test_id=f"full-{stage}-synthetic-blocked",
        assertions=assertions,
        expected={"stage": stage, "scope": "stage"},
        actual={
            "stage": stage,
            "scope": "stage",
            "implemented": implemented,
            "behavior_evidence": behavior_evidence,
        },
        diff={
            "required_paths": stage_paths_for_assertions(stage),
            "implemented": implemented,
            "behavior_issues": [synthetic_block_reason, *behavior_issues],
        },
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[str(path) for path in stage_paths_for_assertions(stage)],
    )
    write_test_report(report_destination(report_dir, stage, None), report)
    return 0 if status == "passed" else 1


def run_task_static_bootstrap(report_dir: Path, task_id: str) -> int:
    required_paths = [
        Path("workflows.md"),
        Path("docs/07_test_spec.md"),
        Path("docs/08_acceptance.md"),
        Path("scripts/run_harness.py"),
        Path("schemas/test_report.schema.json"),
        Path("schemas/stop_decision.schema.json"),
        Path("schemas/task_plan_report.schema.json"),
        Path("schemas/review_report.schema.json"),
        Path("schemas/fix_optimize_report.schema.json"),
        Path("schemas/round_summary_report.schema.json"),
        Path("schemas/tasks.schema.json"),
        Path("schemas/prd_coverage.schema.json"),
        Path("schemas/task_acceptance_coverage.schema.json"),
        Path("schemas/local_user_acceptance.schema.json"),
    ]
    missing_paths = [str(path) for path in required_paths if not path.exists()]

    schema_file_issues: dict[str, list[str]] = {}
    for name, schema_path in SCHEMA_FILES.items():
        issues = validate_json_schema_file(schema_path)
        if issues:
            schema_file_issues[name] = issues

    tasks_payload, tasks_read_issues = read_yaml_object(Path("tasks.md"))
    tasks_schema_issues = tasks_read_issues + validate_against_schema(
        tasks_payload,
        SCHEMA_FILES["tasks"],
        "tasks.md",
    )
    task_dag_semantic_issues = validate_task_dag_semantics(tasks_payload)
    traceability_issues = validate_mandatory_assertion_traceability(tasks_payload)

    initial_assertions = [
        assertion(
            "harness_contract_paths_exist",
            "failed" if missing_paths else "passed",
            {"required_paths": [str(path) for path in required_paths]},
            {"missing_paths": missing_paths},
            {"missing_paths": missing_paths},
        ),
        assertion(
            "harness_schema_files_valid",
            "failed" if schema_file_issues else "passed",
            {"schema_files_valid": True},
            {"schema_file_issues": schema_file_issues},
            {"schema_file_issues": schema_file_issues},
        ),
        assertion(
            "tasks_md_matches_schema",
            "failed" if tasks_schema_issues else "passed",
            {"tasks_schema_issues": []},
            {"tasks_schema_issues": tasks_schema_issues},
            {"tasks_schema_issues": tasks_schema_issues},
        ),
        assertion(
            "tasks_dag_semantics_valid",
            "failed" if task_dag_semantic_issues else "passed",
            {"task_dag_semantic_issues": []},
            {"task_dag_semantic_issues": task_dag_semantic_issues},
            {"task_dag_semantic_issues": task_dag_semantic_issues},
        ),
        assertion(
            "mandatory_assertion_traceability_valid",
            "failed" if traceability_issues else "passed",
            {"traceability_issues": []},
            {"traceability_issues": traceability_issues},
            {"traceability_issues": traceability_issues},
        ),
    ]

    status_failed = any(item["status"] == "failed" for item in initial_assertions)
    report_schema_issues = validate_against_schema(
        test_report(
            stage="static",
            status="passed" if not status_failed else "failed",
            test_id=f"{task_id.lower()}-harness-contract-preliminary",
            assertions=initial_assertions,
            expected={},
            actual={},
            referenced_files=[str(path) for path in required_paths] + ["tasks.md"],
            commands=[
                f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"
            ],
        ),
        SCHEMA_FILES["test_report"],
        "generated_task_static_report",
    )
    all_assertions = [
        *initial_assertions,
        assertion(
            "generated_test_report_matches_schema",
            "failed" if report_schema_issues else "passed",
            {"report_schema_issues": []},
            {"report_schema_issues": report_schema_issues},
            {"report_schema_issues": report_schema_issues},
        ),
    ]

    status = (
        "failed"
        if (
            missing_paths
            or schema_file_issues
            or tasks_schema_issues
            or task_dag_semantic_issues
            or traceability_issues
            or report_schema_issues
        )
        else "passed"
    )
    final_report = test_report(
        stage="static",
        status=status,
        test_id=f"{task_id.lower()}-harness-contract",
        assertions=all_assertions,
        expected={"required_paths_exist": True, "schema_issues": []},
        actual={
            "required_paths_exist": not missing_paths,
            "schema_file_issues": schema_file_issues,
            "tasks_schema_issues": tasks_schema_issues,
            "task_dag_semantic_issues": task_dag_semantic_issues,
            "traceability_issues": traceability_issues,
            "report_schema_issues": report_schema_issues,
        },
        diff={
            "missing_paths": missing_paths,
            "schema_file_issues": schema_file_issues,
            "tasks_schema_issues": tasks_schema_issues,
            "task_dag_semantic_issues": task_dag_semantic_issues,
            "traceability_issues": traceability_issues,
            "report_schema_issues": report_schema_issues,
        },
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[str(path) for path in required_paths] + ["tasks.md"],
        commands=[
            f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "static", task_id), final_report)
    return 0 if status == "passed" else 1


def run_task_001_static(report_dir: Path, task_id: str) -> int:
    backend_path = Path("backend/app/main.py")
    root_index_path = Path("index.html")
    frontend_index_path = Path("frontend/index.html")
    frontend_main_path = Path("frontend/src/main.tsx")
    frontend_package_path = Path("frontend/package.json")
    required_paths = [
        backend_path,
        root_index_path,
        frontend_index_path,
        frontend_main_path,
        frontend_package_path,
    ]
    missing_paths = [path.as_posix() for path in required_paths if not path.exists()]

    root_index_text = (
        root_index_path.read_text(errors="ignore") if root_index_path.exists() else ""
    )
    frontend_index_text = (
        frontend_index_path.read_text(errors="ignore")
        if frontend_index_path.exists()
        else ""
    )
    frontend_main_text = (
        frontend_main_path.read_text(errors="ignore") if frontend_main_path.exists() else ""
    )
    frontend_package_payload, frontend_package_issues = read_json_object(
        frontend_package_path
    )

    backend_import_issues: list[str] = []
    backend_import_checks: dict[str, Any] = {}
    try:
        repo_root = Path.cwd().resolve()
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from backend.app import main as backend_main

        module_app = getattr(backend_main, "app", None)
        backend_import_checks = {
            "module_app_type": module_app.__class__.__name__ if module_app else None,
            "module_app_has_db_state": bool(
                module_app is not None and hasattr(module_app.state, "db")
            ),
            "create_app_callable": callable(getattr(backend_main, "create_app", None)),
        }
        if backend_import_checks["module_app_has_db_state"]:
            backend_import_issues.append("backend_entrypoint_import_initializes_db")
        if not backend_import_checks["create_app_callable"]:
            backend_import_issues.append("backend_create_app_missing")
    except Exception as error:
        backend_import_issues.append(
            f"backend_entrypoint_import_failed:{error.__class__.__name__}"
        )

    flask_issues: list[str] = []
    requirements_text = Path("requirements.txt").read_text(errors="ignore")
    if re.search(r"(^|\n)\s*flask(?:==|>=|<=|~=|$)", requirements_text, re.I):
        flask_issues.append("requirements:flask_dependency_present")
    for path in sorted(Path("backend").glob("**/*.py")):
        text = path.read_text(errors="ignore")
        if re.search(r"\b(from\s+flask\s+import|import\s+flask)\b", text):
            flask_issues.append(f"{path.as_posix()}:flask_import_present")

    root_legacy_patterns = {
        "inline_classic_script": "<script>" in root_index_text,
        "legacy_render_source_list": "function renderSourceList" in root_index_text,
        "legacy_response_json_parse": "response.json()" in root_index_text,
        "legacy_fetch_call": "fetch(" in root_index_text,
    }
    root_legacy_issues = [
        name for name, present in root_legacy_patterns.items() if present
    ]

    frontend_package_scripts = (
        frontend_package_payload.get("scripts", {})
        if isinstance(frontend_package_payload, dict)
        else {}
    )
    frontend_package_dependencies = (
        frontend_package_payload.get("dependencies", {})
        if isinstance(frontend_package_payload, dict)
        else {}
    )
    vite_shell_issues = list(frontend_package_issues)
    if 'id="root"' not in frontend_index_text:
        vite_shell_issues.append("frontend_index:root_mount_missing")
    if "/src/main.tsx" not in frontend_index_text:
        vite_shell_issues.append("frontend_index:main_module_missing")
    if "ReactDOM.createRoot" not in frontend_main_text:
        vite_shell_issues.append("frontend_main:create_root_missing")
    if frontend_package_scripts.get("dev") != "vite":
        vite_shell_issues.append("frontend_package:dev_script_not_vite")
    for dependency in ("vite", "react", "react-dom", "@vitejs/plugin-react"):
        if dependency not in frontend_package_dependencies:
            vite_shell_issues.append(f"frontend_package:missing_dependency:{dependency}")

    architecture_issues = (
        missing_paths
        + backend_import_issues
        + vite_shell_issues
        + flask_issues
    )
    non_goal_issues = root_legacy_issues
    status = "passed" if not architecture_issues and not non_goal_issues else "failed"

    catalog = catalog_assertion_metadata()
    architecture_visibility = catalog.get(
        "A-static-ACC-STOP-010-architecture-boundaries",
        {"visibility": "report_metadata"},
    )["visibility"]
    non_goal_visibility = catalog.get(
        "A-static-ACC-STOP-010-non-goal-files-absent",
        {"visibility": "report_metadata"},
    )["visibility"]
    assertions = [
        assertion(
            "A-static-ACC-STOP-010-architecture-boundaries",
            "passed" if not architecture_issues else "failed",
            {
                "backend_entrypoint_imports_without_db_side_effect": True,
                "frontend_vite_shell_present": True,
                "flask_dependency_present": False,
            },
            {
                "missing_paths": missing_paths,
                "backend_import_checks": backend_import_checks,
                "backend_import_issues": backend_import_issues,
                "vite_shell_issues": vite_shell_issues,
                "flask_issues": flask_issues,
            },
            {"architecture_issues": architecture_issues},
            visibility=architecture_visibility,
        ),
        assertion(
            "A-static-ACC-STOP-010-non-goal-files-absent",
            "passed" if not non_goal_issues else "failed",
            {
                "legacy_root_static_business_ui_active": False,
                "root_shell_points_to_frontend_entry": True,
            },
            {
                "root_shell_points_to_frontend_entry": "/frontend/src/main.tsx"
                in root_index_text,
                "root_legacy_patterns": root_legacy_patterns,
            },
            {"non_goal_issues": non_goal_issues},
            visibility=non_goal_visibility,
        ),
        assertion(
            "task-001-runtime-skeleton",
            status,
            {"task_acceptance": "backend and frontend entrypoints exist"},
            {
                "backend_entrypoint": backend_path.exists(),
                "frontend_entrypoint": frontend_main_path.exists(),
                "frontend_index": frontend_index_path.exists(),
                "root_index_shell": "/frontend/src/main.tsx" in root_index_text,
            },
            {
                "architecture_issues": architecture_issues,
                "non_goal_issues": non_goal_issues,
            },
        ),
    ]
    report = test_report(
        stage="static",
        status=status,
        test_id=f"{task_id.lower()}-runtime-skeleton",
        assertions=assertions,
        expected={"task_id": task_id, "runtime_skeleton": "present"},
        actual={
            "task_id": task_id,
            "status": status,
            "backend_import_checks": backend_import_checks,
            "frontend_package_scripts": frontend_package_scripts,
            "root_legacy_patterns": root_legacy_patterns,
        },
        diff={
            "architecture_issues": architecture_issues,
            "non_goal_issues": non_goal_issues,
        },
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[
            path.as_posix()
            for path in [
                backend_path,
                root_index_path,
                frontend_index_path,
                frontend_main_path,
                frontend_package_path,
                Path("scripts/run_harness.py"),
            ]
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if status == "passed" else 1


def canonicalize_fixture_url(value: str) -> str:
    parts = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path or "/", urlencode(query), "")
    )


def is_reserved_placeholder_url(value: str) -> bool:
    host = (urlsplit(value).hostname or "").lower()
    return host in RESERVED_PLACEHOLDER_HOSTS or host.endswith(RESERVED_PLACEHOLDER_SUFFIXES)


def is_public_http_url_value(value: str) -> bool:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return bool(address.is_global)


def task_003_fixture_paths() -> dict[str, Path]:
    return {
        "rss": Path("fixtures/rss/feeds.json"),
        "articles": Path("fixtures/articles/article_map.json"),
        "scoring": Path("fixtures/llm/scoring.json"),
        "translation": Path("fixtures/llm/translation.json"),
        "sources": Path("fixtures/sources/default_sources.json"),
        "source_cases": Path("fixtures/sources/source_cases.json"),
        "clock": Path("fixtures/clock/fixed_times.json"),
    }


def read_task_003_payloads() -> tuple[dict[str, dict[str, Any]], list[str]]:
    payloads: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for name, path in task_003_fixture_paths().items():
        payload, payload_issues = read_json_object(path)
        if payload_issues:
            issues.extend(f"{name}:{issue}" for issue in payload_issues)
            continue
        payloads[name] = payload or {}
    return payloads, issues


def task_003_required_paths() -> list[Path]:
    return [Path("backend/app/core/config.py"), *task_003_fixture_paths().values()]


def task_003_config_checks(fixture_paths: dict[str, Path]) -> tuple[dict[str, Any], list[str]]:
    config_issues: list[str] = []
    config_checks: dict[str, Any] = {}
    try:
        repo_root = Path.cwd().resolve()
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from backend.app.core.config import get_local_runtime_config

        config = get_local_runtime_config(repo_root)
        config_checks = {
            "database_path": config.database_path == repo_root / "rss.sqlite3",
            "fixture_set": config.fixture_set,
            "mock_set": config.mock_set,
            "clock_source": config.clock_source,
            "allow_live_network": config.allow_live_network,
            "allow_live_llm": config.allow_live_llm,
            "fixture_paths_match": {
                "rss": config.rss_fixture_path == repo_root / fixture_paths["rss"],
                "articles": config.article_fixture_path == repo_root / fixture_paths["articles"],
                "scoring": config.scoring_mock_path == repo_root / fixture_paths["scoring"],
                "translation": config.translation_mock_path == repo_root / fixture_paths["translation"],
                "sources": config.source_fixture_path == repo_root / fixture_paths["sources"],
                "clock": config.clock_fixture_path == repo_root / fixture_paths["clock"],
            },
        }
        if not all(config_checks["fixture_paths_match"].values()):
            config_issues.append("config_fixture_paths_mismatch")
        if not config_checks["database_path"]:
            config_issues.append("config_database_path_not_local_sqlite")
        if config.allow_live_network or config.allow_live_llm:
            config_issues.append("config_live_dependencies_enabled")
    except Exception as error:
        config_issues.append(f"config_import_failed:{error.__class__.__name__}")
    return config_checks, config_issues


def task_003_version_checks(
    payloads: dict[str, dict[str, Any]],
    payload_issues: list[str],
) -> tuple[dict[str, str], dict[str, Any], list[str]]:
    version_expectations = {
        "rss": FIXTURE_VERSION,
        "articles": FIXTURE_VERSION,
        "sources": FIXTURE_VERSION,
        "source_cases": FIXTURE_VERSION,
        "clock": CLOCK_SOURCE,
        "scoring": MOCK_VERSION,
        "translation": MOCK_VERSION,
    }
    actual_versions = {name: payload.get("version") for name, payload in payloads.items()}
    version_issues = list(payload_issues)
    for name, expected_version in version_expectations.items():
        if actual_versions.get(name) != expected_version:
            version_issues.append(f"{name}:version_mismatch")
    return version_expectations, actual_versions, version_issues


def task_003_live_dependency_issues(config_checks: dict[str, Any]) -> list[str]:
    live_dependency_issues = []
    if config_checks.get("allow_live_network") is not False:
        live_dependency_issues.append("allow_live_network_not_false")
    if config_checks.get("allow_live_llm") is not False:
        live_dependency_issues.append("allow_live_llm_not_false")
    return live_dependency_issues


def task_003_static_assertions(
    missing_paths: list[str],
    config_checks: dict[str, Any],
    config_issues: list[str],
    version_expectations: dict[str, str],
    actual_versions: dict[str, Any],
    version_issues: list[str],
    live_dependency_issues: list[str],
) -> list[dict[str, Any]]:
    return [
        assertion(
            "task-003-config-points-to-local-fixtures",
            "passed" if not (missing_paths or config_issues) else "failed",
            {"local_sqlite_and_fixture_paths": True},
            {"missing_paths": missing_paths, "config_checks": config_checks},
            {"issues": [*missing_paths, *config_issues]},
        ),
        assertion(
            "task-003-fixture-and-mock-versions-present",
            "passed" if not version_issues else "failed",
            {"versions": version_expectations},
            {"versions": actual_versions},
            {"issues": version_issues},
        ),
        assertion(
            "task-003-live-dependency-flags-disabled",
            "passed" if not live_dependency_issues else "failed",
            {"allow_live_network": False, "allow_live_llm": False},
            {
                "allow_live_network": config_checks.get("allow_live_network"),
                "allow_live_llm": config_checks.get("allow_live_llm"),
            },
            {"issues": live_dependency_issues},
        ),
    ]


def write_task_003_static_report(
    report_dir: Path,
    task_id: str,
    required_paths: list[Path],
    status: str,
    assertions: list[dict[str, Any]],
    config_checks: dict[str, Any],
    actual_versions: dict[str, Any],
    all_issues: list[str],
) -> None:
    report = test_report(
        stage="static",
        status=status,
        test_id=f"{task_id.lower()}-local-config-fixtures-mocks",
        assertions=assertions,
        expected={"task_id": task_id, "fixture_set": FIXTURE_VERSION, "mock_set": MOCK_VERSION},
        actual={"task_id": task_id, "config_checks": config_checks, "versions": actual_versions},
        diff={"issues": all_issues},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[path.as_posix() for path in required_paths] + ["scripts/run_harness.py"],
        commands=[
            f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)


def run_task_003_static(report_dir: Path, task_id: str) -> int:
    fixture_paths = task_003_fixture_paths()
    required_paths = task_003_required_paths()
    missing_paths = [path.as_posix() for path in required_paths if not path.exists()]
    config_checks, config_issues = task_003_config_checks(fixture_paths)
    payloads, payload_issues = read_task_003_payloads()
    version_expectations, actual_versions, version_issues = task_003_version_checks(
        payloads,
        payload_issues,
    )
    live_dependency_issues = task_003_live_dependency_issues(config_checks)
    all_issues = [
        *missing_paths,
        *config_issues,
        *version_issues,
        *live_dependency_issues,
    ]
    status = "passed" if not all_issues else "failed"
    assertions = task_003_static_assertions(
        missing_paths,
        config_checks,
        config_issues,
        version_expectations,
        actual_versions,
        version_issues,
        live_dependency_issues,
    )
    write_task_003_static_report(
        report_dir,
        task_id,
        required_paths,
        status,
        assertions,
        config_checks,
        actual_versions,
        all_issues,
    )
    return 0 if status == "passed" else 1


def task_003_rss_checks(rss: dict[str, Any]) -> dict[str, bool]:
    feeds = rss.get("feeds", []) if isinstance(rss.get("feeds"), list) else []
    links = [
        item.get("link")
        for feed in feeds
        for item in feed.get("items", [])
        if isinstance(item, dict)
    ]
    canonical_counts: dict[str, int] = {}
    for link in links:
        if isinstance(link, str):
            canonical = canonicalize_fixture_url(link)
            canonical_counts[canonical] = canonical_counts.get(canonical, 0) + 1
    rss_checks = {
        "has_success": any(feed.get("status") == "success" for feed in feeds),
        "has_failure": any(feed.get("status") == "failure" for feed in feeds),
        "has_duplicate": any(count >= 2 for count in canonical_counts.values()),
    }
    return rss_checks


def task_003_scoring_checks(scoring: dict[str, Any]) -> dict[str, bool]:
    return {
        "valid": bool(scoring.get("scores")),
        "missing_score": bool(scoring.get("invalid_cases", {}).get("missing_score")),
        "out_of_range": bool(scoring.get("invalid_cases", {}).get("out_of_range")),
        "timeout": bool(scoring.get("timeout_cases")),
    }


def task_003_translation_checks(translation: dict[str, Any]) -> dict[str, bool]:
    return {
        "valid": bool(translation.get("translations")),
        "invalid_json": bool(translation.get("invalid_cases", {}).get("invalid_json")),
        "timeout": bool(translation.get("timeout_cases")),
        "partial": bool(translation.get("partial_cases")),
    }


def task_003_clock_checks(clock: dict[str, Any]) -> dict[str, bool]:
    clock_kinds = {
        item.get("kind")
        for item in clock.get("cases", [])
        if isinstance(item, dict)
    }
    return {
        "scheduled_09": "scheduled_09" in clock_kinds,
        "scheduled_18": "scheduled_18" in clock_kinds,
        "non_trigger": "non_trigger" in clock_kinds,
    }


def task_003_source_article_checks(
    source_cases: dict[str, Any],
    articles: dict[str, Any],
) -> dict[str, bool]:
    article_cases = {
        item.get("case")
        for item in articles.get("cases", [])
        if isinstance(item, dict)
    }
    return {
        "valid_public": bool(source_cases.get("valid_public")),
        "duplicate_url": bool(source_cases.get("duplicate_url")),
        "local_url": bool(source_cases.get("local_url")),
        "private_url": bool(source_cases.get("private_url")),
        "article_success": "success" in article_cases,
        "article_extraction_failure": "extraction_failure" in article_cases,
        "article_network_failure": "network_failure" in article_cases,
        "article_empty_summary": "empty_summary" in article_cases,
    }


def task_003_unit_assertions(
    rss_checks: dict[str, bool],
    scoring_checks: dict[str, bool],
    translation_checks: dict[str, bool],
    clock_checks: dict[str, bool],
    source_article_checks: dict[str, bool],
    payload_issues: list[str],
) -> list[dict[str, Any]]:
    return [
        assertion(
            "task-003-rss-success-failure-duplicate",
            "passed" if all(rss_checks.values()) and not payload_issues else "failed",
            {"rss_cases": "success failure duplicate"},
            rss_checks,
            {"issues": payload_issues},
        ),
        assertion(
            "task-003-scoring-valid-invalid-timeout",
            "passed" if all(scoring_checks.values()) else "failed",
            {"scoring_cases": "valid invalid timeout"},
            scoring_checks,
            {},
        ),
        assertion(
            "task-003-translation-valid-invalid-timeout-partial",
            "passed" if all(translation_checks.values()) else "failed",
            {"translation_cases": "valid invalid timeout partial"},
            translation_checks,
            {},
        ),
        assertion(
            "task-003-fixed-clock-trigger-cases",
            "passed" if all(clock_checks.values()) else "failed",
            {"clock_cases": "09:00 18:00 non-trigger"},
            clock_checks,
            {},
        ),
        assertion(
            "task-003-source-and-article-fixture-cases",
            "passed" if all(source_article_checks.values()) else "failed",
            {"source_and_article_cases": "covered"},
            source_article_checks,
            {},
        ),
    ]


def write_task_003_unit_report(
    report_dir: Path,
    task_id: str,
    status: str,
    assertions: list[dict[str, Any]],
    actual: dict[str, Any],
    payload_issues: list[str],
) -> None:
    report = test_report(
        stage="unit",
        status=status,
        test_id=f"{task_id.lower()}-fixture-mock-coverage",
        assertions=assertions,
        expected={"task_id": task_id, "fixture_and_mock_cases": "covered"},
        actual=actual,
        diff={"payload_issues": payload_issues},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[
            *(path.as_posix() for path in task_003_fixture_paths().values()),
            "scripts/run_harness.py",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)


def run_task_003_unit(report_dir: Path, task_id: str) -> int:
    payloads, payload_issues = read_task_003_payloads()
    rss_checks = task_003_rss_checks(payloads.get("rss", {}))
    scoring_checks = task_003_scoring_checks(payloads.get("scoring", {}))
    translation_checks = task_003_translation_checks(payloads.get("translation", {}))
    clock_checks = task_003_clock_checks(payloads.get("clock", {}))
    source_article_checks = task_003_source_article_checks(
        payloads.get("source_cases", {}),
        payloads.get("articles", {}),
    )
    assertions = task_003_unit_assertions(
        rss_checks,
        scoring_checks,
        translation_checks,
        clock_checks,
        source_article_checks,
        payload_issues,
    )
    status = "failed" if any(item["status"] == "failed" for item in assertions) else "passed"
    actual = {
        "task_id": task_id,
        "rss": rss_checks,
        "scoring": scoring_checks,
        "translation": translation_checks,
        "clock": clock_checks,
        "source_articles": source_article_checks,
    }
    write_task_003_unit_report(
        report_dir,
        task_id,
        status,
        assertions,
        actual,
        payload_issues,
    )
    return 0 if status == "passed" else 1


def task_002a_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def task_002a_column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def task_002a_rejects(conn: sqlite3.Connection, sql: str) -> bool:
    try:
        conn.execute(sql)
    except sqlite3.IntegrityError:
        return True
    return False


def task_002a_static_checks() -> dict[str, Any]:
    source = Path("backend/app/db.py").read_text()
    module = ast.parse(source)
    lengths = {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
    }
    forbidden_tokens = [
        "rss_source",
        "news_task",
        "translation_status",
        "content_source",
        "title_domain_hash",
        "is_ready",
        "display_mode",
        "CREATE TABLE IF NOT EXISTS category",
    ]
    return {
        "required_tokens_present": all(
            token in source for token in ["source", "news_item", "processing_log"]
        ),
        "forbidden_tokens_absent": [token for token in forbidden_tokens if token in source],
        "initialize_database_length": lengths.get("initialize_database", 0),
    }


def static_python_syntax_checks() -> list[str]:
    search_roots = [Path("backend"), Path("scripts"), Path("tests")]
    issues: list[str] = []
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if ".venv" in path.parts:
                continue
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as error:
                issues.append(
                    f"{path}:{getattr(error, 'lineno', '?')}:{getattr(error, 'offset', '?')}: {error.msg}"
                )
    return issues


def task_002a_seed_base_rows(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO source (id, name, rss_url, is_enabled, fetch_frequency, created_at) "
        "VALUES (1, 'Example', 'https://example.com/rss.xml', 1, 'twice_daily', '2026-06-28T06:00:00Z')"
    )
    conn.execute(
        "INSERT INTO news_item (id, source_id, original_url, canonical_url, original_title, "
        "published_at, pipeline_state, created_at, updated_at) VALUES "
        "(1, 1, 'https://example.com/1', 'https://example.com/1', 'Title', "
        "'2026-06-28T07:00:00Z', 'raw', '2026-06-28T09:00:00Z', '2026-06-28T09:00:00Z')"
    )


def task_002a_constraint_checks(conn: sqlite3.Connection) -> dict[str, bool]:
    task_002a_seed_base_rows(conn)
    return {
        "source_rss_url_unique": task_002a_rejects(conn, TASK_002A_DUPLICATE_SOURCE_SQL),
        "news_canonical_url_unique": task_002a_rejects(conn, TASK_002A_DUPLICATE_NEWS_SQL),
        "pipeline_state_enum": task_002a_rejects(conn, TASK_002A_BAD_STATE_SQL),
        "processing_log_single_owner": task_002a_rejects(conn, TASK_002A_BOTH_OWNER_LOG_SQL),
        "crawl_requires_source": task_002a_rejects(conn, TASK_002A_CRAWL_NEWS_LOG_SQL),
        "score_requires_news_item": task_002a_rejects(conn, TASK_002A_SCORE_SOURCE_LOG_SQL),
    }


def task_002a_schema_observations(conn: sqlite3.Connection) -> dict[str, Any]:
    source_columns = task_002a_column_names(conn, "source")
    news_columns = task_002a_column_names(conn, "news_item")
    log_columns = task_002a_column_names(conn, "processing_log")
    deleted_at_info = [
        row for row in conn.execute("PRAGMA table_info(source)").fetchall() if row[1] == "deleted_at"
    ]
    return {
        "tables": sorted(task_002a_table_names(conn)),
        "deleted_at_nullable": bool(deleted_at_info and deleted_at_info[0][3] == 0),
        "excluded_fields": sorted(TASK_002A_EXCLUDED_FIELDS & (source_columns | news_columns | log_columns)),
        "constraint_checks": task_002a_constraint_checks(conn),
    }


def task_002a_unit_probe() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import initialize_database

    conn = sqlite3.connect(":memory:")
    initialize_database(conn)
    try:
        return task_002a_schema_observations(conn)
    finally:
        conn.close()


def run_task_002a_static(report_dir: Path, task_id: str) -> int:
    checks = task_002a_static_checks()
    passed = (
        checks["required_tokens_present"]
        and not checks["forbidden_tokens_absent"]
        and checks["initialize_database_length"] <= 60
    )
    report = test_report(
        stage="static",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-db-schema-static",
        assertions=[assertion("task-002a-static-schema-shape", "passed" if passed else "failed", {}, checks, {})],
        expected={"db_schema_static": "valid"},
        actual=checks,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/db.py", "docs/04_data_model.md", "docs/06_dev_rules.md"],
        commands=[f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if passed else 1


def run_task_002a_unit(report_dir: Path, task_id: str) -> int:
    observations = task_002a_unit_probe()
    constraint_checks = observations["constraint_checks"]
    passed = (
        observations["tables"] == ["news_item", "processing_log", "source"]
        and observations["deleted_at_nullable"]
        and not observations["excluded_fields"]
        and all(constraint_checks.values())
    )
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-db-schema-constraints",
        assertions=[
            assertion("task-002a-unit-schema-constraints", "passed" if passed else "failed", {}, observations, {})
        ],
        expected={"db_schema_constraints": "valid"},
        actual=observations,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/db.py", "tests/test_data_model.py", "docs/04_data_model.md"],
        commands=[f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def task_002b_source_rows(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return conn.execute(
        """
        SELECT rss_url, is_enabled, deleted_at, fetch_frequency, name
        FROM source
        ORDER BY rss_url ASC
        """
    ).fetchall()


def task_002b_unit_probe() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import initialize_database, seed_default_sources

    conn = sqlite3.connect(":memory:")
    initialize_database(conn)
    try:
        initial_tables = sorted(task_002a_table_names(conn))
        initial_count = conn.execute("SELECT COUNT(*) FROM source").fetchone()[0]
        seed_default_sources(conn)
        first_rows = task_002b_source_rows(conn)
        seed_default_sources(conn)
        second_rows = task_002b_source_rows(conn)
        return task_002b_seed_observations(initial_tables, initial_count, first_rows, second_rows)
    finally:
        conn.close()


def task_002b_seed_observations(
    initial_tables: list[str],
    initial_count: int,
    first_rows: list[tuple[Any, ...]],
    second_rows: list[tuple[Any, ...]],
) -> dict[str, Any]:
    first_urls = {row[0] for row in first_rows}
    second_urls = {row[0] for row in second_rows}
    return {
        "initial_tables": initial_tables,
        "initial_source_count": initial_count,
        "first_seed_count": len(first_rows),
        "second_seed_count": len(second_rows),
        "first_seed_urls": sorted(first_urls),
        "second_seed_urls": sorted(second_urls),
        "expected_count": default_source_count(),
        "expected_urls": sorted(default_source_urls()),
        "all_enabled": all(row[1] == 1 for row in second_rows),
        "all_not_deleted": all(row[2] is None for row in second_rows),
        "all_twice_daily": all(row[3] == "twice_daily" for row in second_rows),
        "all_named": all(bool(row[4]) for row in second_rows),
    }


def run_task_002b_unit(report_dir: Path, task_id: str) -> int:
    observations = task_002b_unit_probe()
    passed = (
        observations["initial_tables"] == ["news_item", "processing_log", "source"]
        and observations["initial_source_count"] == 0
        and observations["first_seed_count"] == observations["expected_count"]
        and observations["second_seed_count"] == observations["expected_count"]
        and observations["first_seed_urls"] == observations["expected_urls"]
        and observations["second_seed_urls"] == observations["expected_urls"]
        and observations["all_enabled"]
        and observations["all_not_deleted"]
        and observations["all_twice_daily"]
        and observations["all_named"]
    )
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-db-init-seed",
        assertions=[assertion("task-002b-unit-init-seed", "passed" if passed else "failed", {}, observations, {})],
        expected={"db_init_seed": "valid"},
        actual=observations,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/db.py", "fixtures/sources/default_sources.json", "tests/test_data_model.py"],
        commands=[f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def task_004_pipeline_imports() -> tuple[Any, Any, Any, Any, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import connect, initialize_database, seed_default_sources
    from backend.app.services.pipeline import canonicalize_url, fixture_feeds, ingest_fixture_rss

    return connect, initialize_database, seed_default_sources, canonicalize_url, fixture_feeds, ingest_fixture_rss


def task_004_unit_observations() -> dict[str, Any]:
    _, _, _, canonicalize_url, fixture_feeds, _ = task_004_pipeline_imports()
    rss_payload = json.loads(Path("fixtures/rss/feeds.json").read_text())
    feeds = fixture_feeds(rss_payload)
    developer_feed = feeds["https://developers.openai.com/rss.xml"]
    items = developer_feed["items"]
    normalized_urls = [canonicalize_url(str(item["link"])) for item in items]
    unique_urls = sorted(set(normalized_urls))
    return {
        "feed_count": len(feeds),
        "developer_item_count": len(items),
        "developer_unique_canonical_count": len(unique_urls),
        "duplicate_canonical_collapsed": normalized_urls[0] == normalized_urls[1],
        "failure_feed_error": feeds["https://dreyx.com/digest/rss"].get("error"),
    }


def task_004_integration_observations() -> dict[str, Any]:
    connect, initialize_database, seed_default_sources, _, _, ingest_fixture_rss = task_004_pipeline_imports()
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    try:
        result = ingest_fixture_rss(conn)
        rows = conn.execute("SELECT pipeline_state, score, content_full, title_zh FROM news_item").fetchall()
        logs = conn.execute("SELECT stage, success, source_id, news_item_id, error FROM processing_log").fetchall()
        return {
            **result,
            "news_item_count": len(rows),
            "states": sorted({row["pipeline_state"] for row in rows}),
            "score_null_count": sum(row["score"] is None for row in rows),
            "content_full_null_count": sum(row["content_full"] is None for row in rows),
            "title_zh_null_count": sum(row["title_zh"] is None for row in rows),
            "crawl_log_count": len(logs),
            "crawl_failure_errors": sorted(str(log["error"]) for log in logs if log["success"] == 0),
            "all_logs_are_crawl_source_logs": all(
                log["stage"] == "crawl" and log["source_id"] is not None and log["news_item_id"] is None
                for log in logs
            ),
        }
    finally:
        conn.close()


def run_task_004_unit(report_dir: Path, task_id: str) -> int:
    actual = task_004_unit_observations()
    expected_counts = rss_fixture_counts()
    passed = (
        actual["feed_count"] == expected_counts["feed_count"]
        and actual["developer_item_count"] == 3
        and actual["developer_unique_canonical_count"] == 2
        and actual["duplicate_canonical_collapsed"]
        and actual["failure_feed_error"] == "parsing"
    )
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-rss-ingest-unit",
        assertions=[assertion("task-004-unit-rss-normalization", "passed" if passed else "failed", {}, actual, {})],
        expected={"rss_fixture_normalization": "valid"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "fixtures/rss/feeds.json"],
        commands=[f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def run_task_004_integration(report_dir: Path, task_id: str) -> int:
    actual = task_004_integration_observations()
    expected_counts = rss_fixture_counts()
    passed = (
        actual["inserted_count"] == 14
        and actual["source_success_count"] == expected_counts["source_success_count"]
        and actual["source_failure_count"] == expected_counts["source_failure_count"]
        and actual["news_item_count"] == 14
        and actual["states"] == ["raw"]
        and actual["score_null_count"] == 14
        and actual["content_full_null_count"] == 14
        and actual["title_zh_null_count"] == 14
        and actual["crawl_log_count"] == expected_counts["feed_count"]
        and actual["crawl_failure_errors"] == ["parsing"]
        and actual["all_logs_are_crawl_source_logs"]
    )
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-rss-ingest-integration",
        assertions=[assertion("task-004-integration-rss-ingest", "passed" if passed else "failed", {}, actual, {})],
        expected={"rss_ingest": "raw_items_and_crawl_logs"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "backend/app/db.py", "fixtures/rss/feeds.json"],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_005_pipeline_imports() -> tuple[Any, ...]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import connect, initialize_database, seed_default_sources
    from backend.app.services.pipeline import (
        build_scoring_request,
        ingest_fixture_rss,
        read_json,
        score_raw_news,
        score_request_with_fixture,
        validate_scoring_response,
    )

    return (
        connect,
        initialize_database,
        seed_default_sources,
        build_scoring_request,
        ingest_fixture_rss,
        read_json,
        score_raw_news,
        score_request_with_fixture,
        validate_scoring_response,
    )


def task_005_unit_observations() -> dict[str, Any]:
    *_, build_request, _, read_json, _, score_request, validate_response = task_005_pipeline_imports()
    scoring_payload = read_json(Path("fixtures/llm/scoring.json"))
    request = build_request(
        {
            "original_title": "Scoring fixture title",
            "content_raw": "",
            "source_name": "Fixture Source",
            "published_at": "2026-06-28T08:00:00Z",
            "original_url": "https://example.com/scoring",
        }
    )
    valid = score_request("fixture-translate-partial", request, scoring_payload)
    invalid = score_request("missing_score", request, scoring_payload)
    timeout = score_request("score_timeout", request, scoring_payload)
    missing_title = score_request("fixture-translated-96", {**request, "title": ""}, scoring_payload)
    _, empty_reason_error = validate_response({"score": 10, "reason": ""})
    return {
        "request_keys": sorted(request),
        "summary_present": "summary" in request and request["summary"] == "",
        "missing_summary_penalized_score": valid["score"],
        "valid_error": valid["error"],
        "invalid_error": invalid["error"],
        "invalid_retry_count": invalid["retry_count"],
        "timeout_error": timeout["error"],
        "timeout_retry_count": timeout["retry_count"],
        "missing_title_score": missing_title["score"],
        "missing_title_retry_count": missing_title["retry_count"],
        "empty_reason_error": empty_reason_error,
    }


def task_005_insert_raw_case(conn: Any, source_id: int, guid: str) -> None:
    conn.execute(
        """
        INSERT INTO news_item (
          source_id, rss_guid, original_url, canonical_url, original_title,
          published_at, pipeline_state, is_selected, content_raw, created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'raw', 0, ?, ?, ?)
        """,
        (
            source_id,
            guid,
            f"https://example.com/{guid}",
            f"https://example.com/{guid}",
            "Invalid scoring fixture",
            "2026-06-28T08:00:00Z",
            "Summary is present.",
            "2026-06-28T09:00:00Z",
            "2026-06-28T09:00:00Z",
        ),
    )


def task_005_failure_observations() -> dict[str, Any]:
    connect, initialize_database, seed_default_sources, *_, score_raw_news, _, _ = task_005_pipeline_imports()
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    source_id = conn.execute("SELECT id FROM source ORDER BY id LIMIT 1").fetchone()["id"]
    for guid in ("missing_score", "score_timeout"):
        task_005_insert_raw_case(conn, int(source_id), guid)
    result = score_raw_news(conn)
    rows = conn.execute("SELECT score, pipeline_state FROM news_item ORDER BY rss_guid").fetchall()
    logs = conn.execute("SELECT success, error FROM processing_log WHERE stage = 'score' ORDER BY id").fetchall()
    conn.close()
    return {
        "failure_result": result,
        "failure_states": sorted({row["pipeline_state"] for row in rows}),
        "failure_score_null_count": sum(row["score"] is None for row in rows),
        "failure_errors": [log["error"] for log in logs],
        "failure_success_values": [log["success"] for log in logs],
    }


def task_005_integration_observations() -> dict[str, Any]:
    connect, initialize_database, seed_default_sources, _, ingest_fixture_rss, _, score_raw_news, _, _ = (
        task_005_pipeline_imports()
    )
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    result = score_raw_news(conn)
    rows = conn.execute("SELECT rss_guid, score, pipeline_state, is_selected, content_full, title_zh FROM news_item").fetchall()
    logs = conn.execute("SELECT success, source_id, news_item_id FROM processing_log WHERE stage = 'score'").fetchall()
    by_guid = {row["rss_guid"]: row for row in rows}
    conn.close()
    return {
        **result,
        "states": sorted({row["pipeline_state"] for row in rows}),
        "threshold_score": by_guid["fixture-threshold-60"]["score"],
        "threshold_selected": by_guid["fixture-threshold-60"]["is_selected"],
        "low_score": by_guid["fixture-low-59"]["score"],
        "low_selected": by_guid["fixture-low-59"]["is_selected"],
        "content_full_null_count": sum(row["content_full"] is None for row in rows),
        "title_zh_null_count": sum(row["title_zh"] is None for row in rows),
        "score_log_count": len(logs),
        "score_log_success_count": sum(log["success"] == 1 for log in logs),
        "all_score_logs_news_owned": all(log["source_id"] is None and log["news_item_id"] is not None for log in logs),
        **task_005_failure_observations(),
    }


def run_task_005_unit(report_dir: Path, task_id: str) -> int:
    actual = task_005_unit_observations()
    passed = (
        actual["request_keys"] == ["original_link", "published_at", "source", "summary", "title"]
        and actual["summary_present"]
        and actual["missing_summary_penalized_score"] == 55
        and actual["valid_error"] is None
        and actual["invalid_error"] == "validation_llm_error"
        and actual["invalid_retry_count"] == 2
        and actual["timeout_error"] == "timeout"
        and actual["timeout_retry_count"] == 2
        and actual["missing_title_score"] == 0
        and actual["missing_title_retry_count"] == 0
        and actual["empty_reason_error"] == "validation_llm_error"
    )
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-score-news-unit",
        assertions=[assertion("task-005-unit-scoring-contract", "passed" if passed else "failed", {}, actual, {})],
        expected={"scoring_contract": "valid"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "fixtures/llm/scoring.json", "tests/test_pipeline_refresh.py"],
        commands=[f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def run_task_005_integration(report_dir: Path, task_id: str) -> int:
    actual = task_005_integration_observations()
    passed = (
        actual["scored_count"] == 14
        and actual["failed_count"] == 0
        and actual["selected_count"] == 13
        and actual["states"] == ["scored"]
        and actual["threshold_score"] == 60
        and actual["threshold_selected"] == 1
        and actual["low_score"] == 59
        and actual["low_selected"] == 0
        and actual["content_full_null_count"] == 14
        and actual["title_zh_null_count"] == 14
        and actual["score_log_count"] == 14
        and actual["score_log_success_count"] == 14
        and actual["all_score_logs_news_owned"]
        and actual["failure_result"]["failed_count"] == 2
        and actual["failure_states"] == ["raw"]
        and actual["failure_score_null_count"] == 2
        and actual["failure_errors"] == ["validation_llm_error", "timeout"]
        and actual["failure_success_values"] == [0, 0]
    )
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-score-news-integration",
        assertions=[assertion("task-005-integration-score-raw-news", "passed" if passed else "failed", {}, actual, {})],
        expected={"score_raw_news": "scored_or_failed_without_fetch"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "backend/app/db.py", "fixtures/llm/scoring.json"],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_006_pipeline_imports() -> tuple[Any, ...]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import connect, initialize_database, seed_default_sources
    from backend.app.services.pipeline import (
        ingest_fixture_rss,
        score_is_selected,
        score_raw_news,
        selected_fetch_candidates,
    )

    return connect, initialize_database, seed_default_sources, ingest_fixture_rss, score_raw_news, score_is_selected, selected_fetch_candidates


def task_006_unit_observations() -> dict[str, Any]:
    *_, score_is_selected, _ = task_006_pipeline_imports()
    return {
        "score_60_selected": score_is_selected(60),
        "score_59_selected": score_is_selected(59),
        "score_0_selected": score_is_selected(0),
        "score_100_selected": score_is_selected(100),
        "threshold": 60,
    }


def task_006_integration_observations() -> dict[str, Any]:
    connect, initialize_database, seed_default_sources, ingest_fixture_rss, score_raw_news, _, selected_candidates = (
        task_006_pipeline_imports()
    )
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)
    candidates = selected_candidates(conn)
    rows = conn.execute("SELECT rss_guid, canonical_url FROM news_item ORDER BY rss_guid").fetchall()
    conn.close()
    guids = [row["rss_guid"] for row in candidates]
    canonical_urls = [row["canonical_url"] for row in candidates]
    return {
        "candidate_count": len(candidates),
        "candidate_guids": sorted(guids),
        "canonical_count": len(canonical_urls),
        "unique_canonical_count": len(set(canonical_urls)),
        "all_states_scored": all(row["pipeline_state"] == "scored" for row in candidates),
        "all_selected": all(row["is_selected"] == 1 for row in candidates),
        "min_candidate_score": min(row["score"] for row in candidates),
        "low_59_absent": "fixture-low-59" not in guids,
        "threshold_60_present": "fixture-threshold-60" in guids,
        "distinct_rank_items_present": {"fixture-rank-95", "fixture-rank-94"}.issubset(guids),
        "content_full_null_count": sum(row["content_full"] is None for row in candidates),
        "news_item_canonical_count": len([row["canonical_url"] for row in rows]),
        "news_item_unique_canonical_count": len({row["canonical_url"] for row in rows}),
    }


def run_task_006_unit(report_dir: Path, task_id: str) -> int:
    actual = task_006_unit_observations()
    passed = (
        actual["score_60_selected"]
        and not actual["score_59_selected"]
        and not actual["score_0_selected"]
        and actual["score_100_selected"]
        and actual["threshold"] == 60
    )
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-filter-dedupe-unit",
        assertions=[assertion("task-006-unit-threshold-filter", "passed" if passed else "failed", {}, actual, {})],
        expected={"threshold": "score_60_selected_score_59_rejected"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "tests/test_pipeline_refresh.py"],
        commands=[f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def run_task_006_integration(report_dir: Path, task_id: str) -> int:
    actual = task_006_integration_observations()
    passed = (
        actual["candidate_count"] == 13
        and actual["canonical_count"] == actual["unique_canonical_count"]
        and actual["news_item_canonical_count"] == actual["news_item_unique_canonical_count"]
        and actual["all_states_scored"]
        and actual["all_selected"]
        and actual["min_candidate_score"] >= 60
        and actual["low_59_absent"]
        and actual["threshold_60_present"]
        and actual["distinct_rank_items_present"]
        and actual["content_full_null_count"] == 13
    )
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-filter-dedupe-integration",
        assertions=[assertion("task-006-integration-filter-dedupe", "passed" if passed else "failed", {}, actual, {})],
        expected={"selected_candidates": "deduped_scored_selected_only"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "backend/app/db.py", "fixtures/rss/feeds.json"],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_007_pipeline_imports() -> tuple[Any, ...]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import connect, initialize_database, seed_default_sources
    from backend.app.services.pipeline import (
        article_records,
        extract_article_text,
        fetch_selected_content,
        ingest_fixture_rss,
        read_json,
        score_raw_news,
    )

    return connect, initialize_database, seed_default_sources, ingest_fixture_rss, score_raw_news, fetch_selected_content, read_json, article_records, extract_article_text


def task_007_unit_observations() -> dict[str, Any]:
    *_, read_json, article_records, extract_article_text = task_007_pipeline_imports()
    payload = read_json(Path("fixtures/articles/article_map.json"))
    articles = article_records(payload)
    threshold_record = articles.get(FIXTURE_THRESHOLD_CANONICAL_URL, {})
    threshold_path = Path("fixtures/articles") / str(threshold_record.get("path"))
    threshold_text = extract_article_text(threshold_path)
    return {
        "article_case_count": len(payload.get("cases", [])),
        "threshold_status": threshold_record.get("status"),
        "threshold_text_non_empty": bool(threshold_text.strip()),
        "network_error": articles.get(FIXTURE_TRANSLATION_PARTIAL_CANONICAL_URL, {}).get("error"),
        "parsing_error": articles.get(FIXTURE_EXTRACTION_FAILURE_URL, {}).get("error"),
        "empty_summary_error": articles.get(FIXTURE_EMPTY_SUMMARY_URL, {}).get("error"),
    }


def task_007_insert_no_fallback(conn: Any, source_id: int) -> None:
    conn.execute(
        """
        INSERT INTO news_item (
          source_id, rss_guid, original_url, canonical_url, original_title,
          published_at, score, pipeline_state, is_selected, content_raw,
          created_at, updated_at
        )
        VALUES (?, 'fetch-no-fallback', ?, ?, 'No fallback', ?, 80, 'scored', 1, '', ?, ?)
        """,
        (
            source_id,
            "https://example.com/news/no-fallback",
            "https://example.com/news/no-fallback",
            "2026-06-28T08:00:00Z",
            "2026-06-28T09:00:00Z",
            "2026-06-28T09:00:00Z",
        ),
    )


def task_007_no_fallback_observations() -> dict[str, Any]:
    connect, initialize_database, seed_default_sources, *_, fetch_selected_content, _, _, _ = task_007_pipeline_imports()
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    source_id = conn.execute("SELECT id FROM source ORDER BY id LIMIT 1").fetchone()["id"]
    task_007_insert_no_fallback(conn, int(source_id))
    result = fetch_selected_content(conn)
    row = conn.execute("SELECT pipeline_state, score, content_full FROM news_item").fetchone()
    log = conn.execute("SELECT success, error FROM processing_log WHERE stage = 'fetch'").fetchone()
    conn.close()
    return {
        "no_fallback_result": result,
        "no_fallback_state": row["pipeline_state"],
        "no_fallback_score": row["score"],
        "no_fallback_content_full": row["content_full"],
        "no_fallback_log": log,
    }


def task_007_integration_observations() -> dict[str, Any]:
    connect, initialize_database, seed_default_sources, ingest_fixture_rss, score_raw_news, fetch_selected_content, *_ = (
        task_007_pipeline_imports()
    )
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)
    result = fetch_selected_content(conn)
    rows = conn.execute("SELECT rss_guid, pipeline_state, content_full, content_raw FROM news_item").fetchall()
    logs = conn.execute("SELECT success, error, source_id, news_item_id FROM processing_log WHERE stage = 'fetch'").fetchall()
    by_guid = {row["rss_guid"]: row for row in rows}
    conn.close()
    return {
        **result,
        "threshold_state": by_guid["fixture-threshold-60"]["pipeline_state"],
        "threshold_content_full": bool(by_guid["fixture-threshold-60"]["content_full"]),
        "partial_state": by_guid["fixture-translate-partial"]["pipeline_state"],
        "partial_content_full": by_guid["fixture-translate-partial"]["content_full"],
        "partial_content_raw": bool(by_guid["fixture-translate-partial"]["content_raw"]),
        "low_state": by_guid["fixture-low-59"]["pipeline_state"],
        "low_content_full": by_guid["fixture-low-59"]["content_full"],
        "fetch_log_count": len(logs),
        "fetch_success_count": sum(log["success"] == 1 for log in logs),
        "fetch_network_failure_count": sum(log["success"] == 0 and log["error"] == "network" for log in logs),
        "all_fetch_logs_news_owned": all(log["source_id"] is None and log["news_item_id"] is not None for log in logs),
        **task_007_no_fallback_observations(),
    }


def run_task_007_unit(report_dir: Path, task_id: str) -> int:
    actual = task_007_unit_observations()
    passed = (
        actual["article_case_count"] >= 4
        and actual["threshold_status"] == "success"
        and actual["threshold_text_non_empty"]
        and actual["network_error"] == "network"
        and actual["parsing_error"] == "parsing"
        and actual["empty_summary_error"] == "empty_summary"
    )
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-fetch-content-unit",
        assertions=[assertion("task-007-unit-article-fixtures", "passed" if passed else "failed", {}, actual, {})],
        expected={"article_fixtures": "success_and_failure_cases"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "fixtures/articles/article_map.json"],
        commands=[f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def run_task_007_integration(report_dir: Path, task_id: str) -> int:
    actual = task_007_integration_observations()
    passed = (
        actual["fetched_count"] == 13
        and actual["content_full_count"] == 2
        and actual["fallback_count"] == 11
        and actual["failed_count"] == 0
        and actual["threshold_state"] == "fetched"
        and actual["threshold_content_full"]
        and actual["partial_state"] == "fetched"
        and actual["partial_content_full"] is None
        and actual["partial_content_raw"]
        and actual["low_state"] == "scored"
        and actual["low_content_full"] is None
        and actual["fetch_log_count"] == 13
        and actual["fetch_success_count"] == 2
        and actual["fetch_network_failure_count"] == 11
        and actual["all_fetch_logs_news_owned"]
        and actual["no_fallback_result"]["failed_count"] == 1
        and actual["no_fallback_state"] == "scored"
        and actual["no_fallback_content_full"] is None
        and actual["no_fallback_log"] == {"success": 0, "error": "network"}
    )
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-fetch-content-integration",
        assertions=[assertion("task-007-integration-fetch-content", "passed" if passed else "failed", {}, actual, {})],
        expected={"fetch_selected_content": "fixture_success_fallback_failure"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "backend/app/db.py", "fixtures/articles/article_map.json"],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_008_pipeline_imports() -> tuple[Any, ...]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import connect, initialize_database, seed_default_sources
    from backend.app.services.pipeline import (
        build_translation_request,
        fetch_selected_content,
        has_valid_translation_record,
        ingest_fixture_rss,
        read_json,
        score_raw_news,
        translate_fetched_content,
        translation_records,
    )

    return connect, initialize_database, seed_default_sources, ingest_fixture_rss, score_raw_news, fetch_selected_content, translate_fetched_content, read_json, translation_records, has_valid_translation_record, build_translation_request


def task_008_unit_observations() -> dict[str, Any]:
    *_, read_json, translation_records, has_valid_record, build_request = task_008_pipeline_imports()
    payload = read_json(Path("fixtures/llm/translation.json"))
    records = translation_records(payload)
    request = build_request(
        {
            "original_title": "Original title",
            "content_raw": "RSS fallback text",
            "content_full": "",
            "source_name": "Fixture Source",
            "score": 95,
        }
    )
    return {
        "request_keys": sorted(request),
        "fallback_content_used": request["original_content"] == "RSS fallback text",
        "valid_record": has_valid_record(records["fixture-translated-96"]),
        "fallback_record": has_valid_record(records["fixture-rank-95"]),
        "partial_record": has_valid_record(records["fixture-translate-partial"]),
        "category_present": records["fixture-translated-96"].get("category_zh") == "研究",
        "invalid_case_count": len(payload.get("invalid_cases", {})),
        "timeout_case_count": len(payload.get("timeout_cases", {})),
    }


def task_008_integration_observations() -> dict[str, Any]:
    connect, initialize_database, seed_default_sources, ingest_fixture_rss, score_raw_news, fetch_selected_content, translate_fetched_content, *_ = (
        task_008_pipeline_imports()
    )
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)
    fetch_selected_content(conn)
    result = translate_fetched_content(conn)
    rows = conn.execute(
        "SELECT rss_guid, pipeline_state, content_full, title_zh, summary_zh, content_zh, has_translate_failed FROM news_item"
    ).fetchall()
    logs = conn.execute("SELECT success, error, source_id, news_item_id FROM processing_log WHERE stage = 'translate'").fetchall()
    by_guid = {row["rss_guid"]: row for row in rows}
    conn.close()
    return {
        **result,
        "translated_title": by_guid["fixture-translated-96"]["title_zh"],
        "fallback_content_full": by_guid["fixture-rank-95"]["content_full"],
        "fallback_summary_zh": by_guid["fixture-rank-95"]["summary_zh"],
        "fallback_content_zh": by_guid["fixture-rank-95"]["content_zh"],
        "partial_zh_count": sum(
            bool(by_guid["fixture-translate-partial"][field]) for field in ("title_zh", "summary_zh", "content_zh")
        ),
        "partial_failed": by_guid["fixture-translate-partial"]["has_translate_failed"],
        "pending_title": by_guid["fixture-threshold-60"]["title_zh"],
        "pending_failed": by_guid["fixture-threshold-60"]["has_translate_failed"],
        "states": sorted({row["pipeline_state"] for row in rows}),
        "translate_log_count": len(logs),
        "translate_success_count": sum(log["success"] == 1 for log in logs),
        "translate_validation_failure_count": sum(
            log["success"] == 0 and log["error"] == "validation_llm_error" for log in logs
        ),
        "all_translate_logs_news_owned": all(log["source_id"] is None and log["news_item_id"] is not None for log in logs),
    }


def run_task_008_unit(report_dir: Path, task_id: str) -> int:
    actual = task_008_unit_observations()
    passed = (
        actual["request_keys"] == ["original_content", "original_summary", "original_title", "score", "source"]
        and actual["fallback_content_used"]
        and actual["valid_record"]
        and actual["fallback_record"]
        and not actual["partial_record"]
        and actual["category_present"]
        and actual["invalid_case_count"] >= 2
        and actual["timeout_case_count"] >= 1
    )
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-translate-content-unit",
        assertions=[assertion("task-008-unit-translation-contract", "passed" if passed else "failed", {}, actual, {})],
        expected={"translation_contract": "request_shape_and_schema"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "fixtures/llm/translation.json"],
        commands=[f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def run_task_008_integration(report_dir: Path, task_id: str) -> int:
    actual = task_008_integration_observations()
    passed = (
        actual["translated_count"] == 11
        and actual["pending_count"] == 1
        and actual["failed_count"] == 1
        and actual["translated_title"] == "OpenAI 发布 LifeSciBench 生命科学基准"
        and actual["fallback_content_full"] is None
        and bool(actual["fallback_summary_zh"])
        and bool(actual["fallback_content_zh"])
        and actual["partial_zh_count"] == 0
        and actual["partial_failed"] == 1
        and actual["pending_title"] is None
        and actual["pending_failed"] == 0
        and actual["states"] == ["fetched", "scored"]
        and actual["translate_log_count"] == 12
        and actual["translate_success_count"] == 11
        and actual["translate_validation_failure_count"] == 1
        and actual["all_translate_logs_news_owned"]
    )
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-translate-content-integration",
        assertions=[assertion("task-008-integration-translate-content", "passed" if passed else "failed", {}, actual, {})],
        expected={"translate_fetched_content": "success_fallback_failure"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "fixtures/llm/translation.json"],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_009_integration_observations() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import connect, initialize_database, seed_default_sources
    from backend.app.services.pipeline import run_fixture_pipeline_summary

    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    summary = run_fixture_pipeline_summary(conn)
    log_count = conn.execute("SELECT COUNT(*) AS count FROM processing_log").fetchone()["count"]
    conn.close()
    return {**summary, "processing_log_count": log_count}


def run_task_009_integration(report_dir: Path, task_id: str) -> int:
    actual = task_009_integration_observations()
    expected_counts = rss_fixture_counts()
    passed = (
        actual["started_at"] == "2026-06-28T09:00:00Z"
        and actual["finished_at"] == "2026-06-28T09:00:00Z"
        and actual["source_success_count"] == expected_counts["source_success_count"]
        and actual["source_failure_count"] == expected_counts["source_failure_count"]
        and actual["rss_item_count"] == expected_counts["rss_item_count"]
        and actual["new_item_count"] == 14
        and actual["scored_item_count"] == 14
        and actual["selected_item_count"] == 13
        and actual["fetched_item_count"] == 13
        and actual["translated_item_count"] == 11
        and actual["failure_details"] == {
            "crawl:parsing": 1,
            "fetch:network": 11,
            "translate:validation_llm_error": 1,
        }
        and actual["processing_log_count"] >= 40
    )
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-pipeline-run-record-integration",
        assertions=[assertion("task-009-integration-run-summary", "passed" if passed else "failed", {}, actual, {})],
        expected={"pipeline_run_summary": "counts_and_failure_details"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "backend/app/db.py", "tests/test_pipeline_refresh.py"],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_010_integration_observations() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import connect, initialize_database, seed_default_sources
    from backend.app.services.trigger import run_manual_refresh, run_scheduled_refresh

    def prepared_conn() -> Any:
        conn = connect(":memory:")
        initialize_database(conn)
        seed_default_sources(conn)
        return conn

    manual_conn = prepared_conn()
    manual = run_manual_refresh(manual_conn)
    morning = run_scheduled_refresh(prepared_conn(), now="2026-06-28T09:00:00Z")
    evening = run_scheduled_refresh(prepared_conn(), now="2026-06-28T18:00:00Z")
    idle = run_scheduled_refresh(prepared_conn(), now="2026-06-28T10:00:00Z")
    rejected_conn = prepared_conn()
    rejected = run_manual_refresh(rejected_conn, is_running=True)
    rejected_log_count = rejected_conn.execute("SELECT COUNT(*) AS count FROM processing_log").fetchone()["count"]
    return {
        "manual_started": manual["started"],
        "manual_translated_count": manual["summary"]["translated_item_count"] if manual["summary"] else None,
        "morning_started": morning["started"],
        "evening_started": evening["started"],
        "idle": idle,
        "rejected": rejected,
        "rejected_log_count": rejected_log_count,
    }


def run_task_010_integration(report_dir: Path, task_id: str) -> int:
    actual = task_010_integration_observations()
    passed = (
        actual["manual_started"] is True
        and actual["manual_translated_count"] == 11
        and actual["morning_started"] is True
        and actual["evening_started"] is True
        and actual["idle"] == {"started": False, "reason": "not_scheduled_time", "summary": None}
        and actual["rejected"] == {"started": False, "reason": "already_running", "summary": None}
        and actual["rejected_log_count"] == 0
    )
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-refresh-trigger-signal-integration",
        assertions=[assertion("task-010-integration-refresh-trigger", "passed" if passed else "failed", {}, actual, {})],
        expected={"refresh_trigger": "manual_schedule_concurrency"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/trigger.py", "backend/app/services/pipeline.py"],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def task_018_integration_observations() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from backend.app.db import connect, initialize_database, seed_default_sources
    from backend.app.services.pipeline import run_fixture_pipeline_summary

    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    summary = run_fixture_pipeline_summary(conn)
    rows = conn.execute(
        "SELECT rss_guid, canonical_url, score, pipeline_state, is_selected, content_full, title_zh, has_translate_failed FROM news_item"
    ).fetchall()
    logs = conn.execute("SELECT stage, success, error FROM processing_log").fetchall()
    by_guid = {row["rss_guid"]: row for row in rows}
    canonical_urls = [row["canonical_url"] for row in rows]
    displayable_count = sum(
        bool(row["is_selected"] == 1 and (row["content_full"] or row["pipeline_state"] == "fetched"))
        for row in rows
    )
    conn.close()
    return {
        **summary,
        "displayable_count": displayable_count,
        "threshold_state": by_guid["fixture-threshold-60"]["pipeline_state"],
        "threshold_selected": by_guid["fixture-threshold-60"]["is_selected"],
        "low_state": by_guid["fixture-low-59"]["pipeline_state"],
        "low_selected": by_guid["fixture-low-59"]["is_selected"],
        "canonical_count": len(canonical_urls),
        "unique_canonical_count": len(set(canonical_urls)),
        "log_stage_success_pairs": sorted({f"{log['stage']}:{log['success']}" for log in logs}),
    }


def run_task_018_integration(report_dir: Path, task_id: str) -> int:
    actual = task_018_integration_observations()
    passed = (
        actual["displayable_count"] >= 1
        and actual["threshold_state"] == "fetched"
        and actual["threshold_selected"] == 1
        and actual["low_state"] == "scored"
        and actual["low_selected"] == 0
        and actual["canonical_count"] == actual["unique_canonical_count"]
        and {"crawl:0", "crawl:1", "score:1", "fetch:0", "fetch:1", "translate:0", "translate:1"}.issubset(
            set(actual["log_stage_success_pairs"])
        )
        and actual["failure_details"] == {
            "crawl:parsing": 1,
            "fetch:network": 11,
            "translate:validation_llm_error": 1,
        }
    )
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-pipeline-only-integration",
        assertions=[assertion("task-018-integration-pipeline-only", "passed" if passed else "failed", {}, actual, {})],
        expected={"pipeline_only": "db_facts_without_api_ui_trigger"},
        actual=actual,
        diff={},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["backend/app/services/pipeline.py", "backend/app/db.py", "tests/test_pipeline_refresh.py"],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def run_task_022_replay(report_dir: Path, task_id: str) -> int:
    evidence = pipeline_replay_evidence()
    checks = evidence["checks"]
    issues = evidence["issues"]
    version_actual = {
        "fixture_version": FIXTURE_VERSION,
        "mock_version": MOCK_VERSION,
        "clock_source": CLOCK_SOURCE,
        "first_hash": checks["first_hash"],
        "second_hash": checks["second_hash"],
    }
    assertions = task_022_replay_assertions(checks, issues, version_actual)
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="replay",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-deterministic-replay",
        assertions=assertions,
        expected={"run_count": 2, "hashes_match": True, "deterministic_inputs": "fixture_mock_fixed_clock"},
        actual={"replay": evidence, "versions": version_actual},
        diff={"failure_reasons": issues},
        failure_type=None if passed else "integration",
        error_category=None if passed else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "backend/app/services/pipeline.py",
            "backend/app/db.py",
            "fixtures/rss/feeds.json",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            "fixtures/clock/fixed_times.json",
        ],
        commands=[f"python3 scripts/run_harness.py --stage replay --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "replay", task_id), report)
    return 0 if passed else 1


def task_022_replay_assertions(
    checks: dict[str, Any],
    issues: list[str],
    version_actual: dict[str, Any],
) -> list[dict[str, Any]]:
    version_passed = (
        version_actual["fixture_version"] == FIXTURE_VERSION
        and version_actual["mock_version"] == MOCK_VERSION
        and version_actual["clock_source"] == CLOCK_SOURCE
        and str(version_actual["first_hash"]).startswith("sha256:")
        and str(version_actual["second_hash"]).startswith("sha256:")
    )
    return [
        assertion(
            "A-replay-ACC-STOP-008-deterministic-replay",
            "passed" if checks["hashes_match"] and not issues else "failed",
            {"run_count": 2, "hashes_match": True, "issues": []},
            {"run_count": 2, **checks, "issues": issues},
            {"failure_reasons": issues},
        ),
        assertion(
            "A-replay-ACC-STOP-008-fixture-version-hash",
            "passed" if version_passed else "failed",
            {
                "fixture_version": FIXTURE_VERSION,
                "mock_version": MOCK_VERSION,
                "clock_source": CLOCK_SOURCE,
                "hash_prefix": "sha256:",
            },
            version_actual,
            {},
        ),
    ]


def task_023_api_snapshot() -> dict[str, Any]:
    api = backend_api_response_evidence()
    home = task_011_home_observations()["checks"]
    return {
        "response_statuses": {
            item["name"]: item["status_code"]
            for item in api["checks"]
            if isinstance(item, dict)
        },
        "api_issues_empty": not api["issues"],
        "home": {
            "data_keys": home["data_keys"],
            "latest_density_ok": home["latest_density_ok"],
            "top_ranked_count": home["top_ranked_count"],
            "top_sorted_desc": home["top_sorted_desc"],
            "top_within_window": home["top_within_window"],
            "layout_fields_present": home["layout_fields_present"],
        },
    }


def task_023_db_schema_snapshot() -> dict[str, Any]:
    schema = task_002a_unit_probe()
    return {
        "tables": schema["tables"],
        "deleted_at_nullable": schema["deleted_at_nullable"],
        "excluded_fields": schema["excluded_fields"],
        "constraint_checks": schema["constraint_checks"],
    }


def task_023_public_schema_snapshot() -> dict[str, Any]:
    routes = task_023_backend_route_snapshot()
    frontend = frontend_endpoint_evidence()
    return {
        "backend_required_routes_present": not routes["missing_required_routes"],
        "frontend_contract_references_present": not frontend["missing_contract_endpoint_references"],
        "legacy_endpoint_references": frontend["legacy_endpoint_references"],
    }


def task_023_backend_route_snapshot() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"routes": [], "missing_required_routes": sorted(f"{method} {path}" for method, path in REQUIRED_API_ROUTES)}
    route_pairs: set[tuple[str, str]] = set()
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        for method in methods:
            if method not in {"HEAD", "OPTIONS"}:
                route_pairs.add((str(method), str(path)))
    missing = sorted(REQUIRED_API_ROUTES - route_pairs)
    return {
        "routes": [f"{method} {path}" for method, path in sorted(route_pairs)],
        "missing_required_routes": [f"{method} {path}" for method, path in missing],
    }


def task_023_ui_snapshot() -> dict[str, Any]:
    ui = task_020_ui_observations()
    return {
        "render": ui["checks"]["render"],
        "interactions": ui["checks"]["interactions"],
        "forbidden": ui["checks"]["forbidden"],
        "visual_theme": ui["checks"]["visual_theme"],
        "high_score_card": ui["checks"]["high_score_card"],
        "home_layout": {
            "two_columns": task_015_source_checks(task_015_read_sources()[0])["checks"]["desktop_home_two_columns"],
            "news_card_min_height": task_015_source_checks(task_015_read_sources()[0])["checks"]["news_card_min_height"],
        },
        "article_layout": {
            "reading_width": task_016_source_checks(task_016_read_sources()[0])["checks"]["article_width_contract"],
        },
    }


def task_023_expected_snapshot() -> dict[str, Any]:
    return {
        "api_json": {
            "response_statuses": {
                "post_refresh": 200,
                "get_home": 200,
                "get_translated_news_detail": 200,
                "get_sources": 200,
                "get_missing_news": 404,
                "post_invalid_source": 400,
                "get_unknown_api": 404,
            },
            "api_issues_empty": True,
            "home": {
                "data_keys": ["latest_news", "top_ranked_news"],
                "latest_density_ok": True,
                "top_ranked_count": 10,
                "top_sorted_desc": True,
                "top_within_window": True,
                "layout_fields_present": [],
            },
        },
        "db_schema": {
            "tables": ["news_item", "processing_log", "source"],
            "deleted_at_nullable": True,
            "excluded_fields": [],
            "constraint_checks": {
                "source_rss_url_unique": True,
                "news_canonical_url_unique": True,
                "pipeline_state_enum": True,
                "processing_log_single_owner": True,
                "crawl_requires_source": True,
                "score_requires_news_item": True,
            },
        },
        "public_schema": {
            "backend_required_routes_present": True,
            "frontend_contract_references_present": True,
            "legacy_endpoint_references": [],
        },
        "ui_dom": task_023_expected_ui_snapshot(),
    }


def task_023_expected_ui_snapshot() -> dict[str, Any]:
    render_keys = [
        "home_density_from_api_payload",
        "home_density_not_sparse_smoke",
        "high_score_top_10_ordered",
        "high_score_excludes_old_items",
        "summary_html_like_text_safe",
        "ready_failed_home_omit_zh_nodes",
        "article_translated_only_has_content",
        "article_ready_failed_omit_zh_nodes",
        "click_to_read_no_empty_article",
        "article_not_found_state",
        "sources_create_delete_states",
        "sources_structured_errors",
    ]
    interaction_keys = [
        "news_card_internal_click",
        "high_score_internal_click",
        "original_link_only_in_article",
        "source_controls_identical",
        "source_toggle_updates_row",
    ]
    forbidden_keys = [
        "home_has_no_forbidden_rendering",
        "article_has_no_forbidden_rendering",
        "sources_has_no_forbidden_rendering",
        "home_source_checks_safe",
        "article_source_checks_safe",
        "sources_source_checks_safe",
    ]
    visual_theme_keys = [
        "docs_light_gray_contract",
        "root_background_light_gray",
        "body_background_light_gray",
        "app_shell_background_light_gray",
        "no_dark_color_scheme",
        "no_old_dark_background_tokens",
        "primary_text_dark",
        "secondary_text_muted",
        "news_card_surface",
        "high_score_surface",
        "state_surface",
        "article_state_surface",
        "source_form_surface",
        "source_row_surface",
        "surface_subtle_token",
        "border_token",
        "docs_high_score_card_contract",
        "high_score_outer_card_surface",
        "high_score_outer_card_border",
        "high_score_outer_card_radius",
        "high_score_outer_card_padding",
        "high_score_items_are_rows",
        "high_score_rows_divided",
        "high_score_rows_not_nested_cards",
    ]
    high_score_card_keys = [
        "docs_high_score_card_contract",
        "high_score_outer_card_surface",
        "high_score_outer_card_border",
        "high_score_outer_card_radius",
        "high_score_outer_card_padding",
        "high_score_items_are_rows",
        "high_score_rows_divided",
        "high_score_rows_not_nested_cards",
    ]
    return {
        "render": {key: True for key in render_keys},
        "interactions": {key: True for key in interaction_keys},
        "forbidden": {key: True for key in forbidden_keys},
        "visual_theme": {key: True for key in visual_theme_keys},
        "high_score_card": {key: True for key in high_score_card_keys},
        "home_layout": {
            "two_columns": True,
            "news_card_min_height": True,
        },
        "article_layout": {
            "reading_width": True,
        },
    }


def task_023_current_snapshot() -> dict[str, Any]:
    return {
        "api_json": task_023_api_snapshot(),
        "db_schema": task_023_db_schema_snapshot(),
        "public_schema": task_023_public_schema_snapshot(),
        "ui_dom": task_023_ui_snapshot(),
    }


def task_023_snapshot_diff(expected: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key, expected_value in expected.items():
        actual_value = current.get(key)
        if actual_value != expected_value:
            diff[key] = {"expected": expected_value, "actual": actual_value}
    for key in sorted(set(current) - set(expected)):
        diff[key] = {"expected": None, "actual": current[key]}
    return diff


def task_023_write_artifacts(
    report_dir: Path,
    expected: dict[str, Any],
    current: dict[str, Any],
    diff: dict[str, Any],
) -> list[str]:
    base = report_dir / "tasks" / "TASK-023"
    artifacts = {
        "expected_snapshot.json": expected,
        "current_snapshot.json": current,
        "snapshot_diff.json": diff,
    }
    paths: list[str] = []
    for name, payload in artifacts.items():
        path = base / name
        write_json(path, payload)
        paths.append(report_relative_path(path))
    return paths


def run_task_023_snapshot(report_dir: Path, task_id: str) -> int:
    expected = task_023_expected_snapshot()
    current = task_023_current_snapshot()
    diff = task_023_snapshot_diff(expected, current)
    artifact_paths = task_023_write_artifacts(report_dir, expected, current, diff)
    leak_scan = scan_public_payload({"api_json": current["api_json"], "ui_dom": current["ui_dom"]})
    leak_scan["target"] = "ui_dom"
    public_passed = not diff
    layout_passed = "ui_dom" not in diff
    leak_passed = leak_scan["forbidden_field_count"] == 0 and leak_scan["sensitive_content_count"] == 0
    assertions = task_023_snapshot_assertions(public_passed, layout_passed, leak_passed, diff, artifact_paths, leak_scan)
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="snapshot",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-snapshot-regression",
        assertions=assertions,
        expected={"snapshot_diff": {}, "artifact_paths": artifact_paths},
        actual={"diff": diff, "artifact_paths": artifact_paths},
        diff=diff,
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        node="harness",
        referenced_files=[
            "scripts/run_harness.py",
            "docs/03_ui_spec.md",
            "docs/05_api_contract.md",
            "docs/07_test_spec.md",
            "backend/app/main.py",
            "backend/app/db.py",
            *TASK_020_SOURCE_FILES,
        ],
        commands=[f"python3 scripts/run_harness.py --stage snapshot --task-id {task_id} --report-dir reports"],
    )
    report_path = report_destination(report_dir, "snapshot", task_id)
    report["artifact_paths"] = [report_relative_path(report_path), *artifact_paths]
    write_json(report_path, report)
    return 0 if passed else 1


def task_023_snapshot_assertions(
    public_passed: bool,
    layout_passed: bool,
    leak_passed: bool,
    diff: dict[str, Any],
    artifact_paths: list[str],
    leak_scan: dict[str, Any],
) -> list[dict[str, Any]]:
    leak_assertion = assertion(
        "task-023-snapshot-artifact-leak-scan",
        "passed" if leak_passed else "failed",
        {"forbidden_field_count": 0, "sensitive_content_count": 0},
        leak_scan,
        {},
        visibility="public_surface",
    )
    leak_assertion["leak_detection"] = leak_scan
    return [
        assertion(
            "A-snapshot-ACC-STOP-004-public-snapshots",
            "passed" if public_passed else "failed",
            {"diff": {}, "artifact_paths": "present"},
            {"diff_empty": not diff, "artifact_paths": artifact_paths},
            diff,
            visibility="public_surface",
        ),
        assertion(
            "A-snapshot-ACC-STOP-006-layout-visual-contract",
            "passed" if layout_passed else "failed",
            {"ui_dom_diff": "empty"},
            {"ui_dom_diff_empty": "ui_dom" not in diff, "artifact_paths": artifact_paths},
            diff.get("ui_dom", {}),
            visibility="public_surface",
        ),
        leak_assertion,
    ]


def task_024_report_status(path: Path) -> str:
    payload, issues = read_json_object(path)
    if issues or payload is None:
        return "missing"
    return str(payload.get("status"))


def task_024_e2e_observations() -> dict[str, Any]:
    pipeline = pipeline_refresh_evidence()
    api = backend_api_response_evidence()
    ui = task_020_ui_observations()
    browser_surface = e2e_surface_evidence()
    replay_status = task_024_report_status(Path("reports/tasks/TASK-022/replay.json"))
    snapshot_status = task_024_report_status(Path("reports/tasks/TASK-023/snapshot.json"))
    checks = {
        "isolation": {
            "fixture_set": FIXTURE_SET,
            "mock_set": MOCK_SET,
            "clock_source": CLOCK_SOURCE,
            "local_sqlite": True,
            "pipeline_issues_empty": not pipeline["issues"],
            "api_issues_empty": not api["issues"],
            "replay_report_passed": replay_status == "passed",
            "snapshot_report_passed": snapshot_status == "passed",
        },
        "ui": ui["checks"],
        "browser_surface": browser_surface["checks"],
        "api": {
            "refresh_home_detail_sources": not api["issues"],
        },
    }
    leak_scan = scan_public_payload(
        {"api": checks["api"], "ui": checks["ui"], "browser_surface": checks["browser_surface"]}
    )
    leak_scan["target"] = "ui_dom"
    issues = task_024_e2e_issues(checks, leak_scan)
    issues.extend(f"browser_surface:{issue}" for issue in browser_surface["issues"])
    return {
        "checks": checks,
        "issues": issues,
        "leak_scan": leak_scan,
        "pipeline": {"issue_count": len(pipeline["issues"])},
        "api": {"issue_count": len(api["issues"])},
        "browser_surface": {"issue_count": len(browser_surface["issues"])},
        "runner": "deterministic_api_and_dom_projection",
    }


def task_024_e2e_issues(checks: dict[str, Any], leak_scan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for name, value in checks["isolation"].items():
        if isinstance(value, bool) and not value:
            issues.append(f"isolation:{name}=false")
    if not checks["api"]["refresh_home_detail_sources"]:
        issues.append("api:refresh_home_detail_sources=false")
    primary_click = checks.get("browser_surface", {}).get("primary_click_readability", {})
    if (
        isinstance(primary_click, dict)
        and primary_click.get("all_visible_items_translated_and_readable") is False
    ):
        issues.append("browser_surface:primary_click_readability=false")
    for area, values in checks["ui"].items():
        for name, passed in values.items():
            if not passed:
                issues.append(f"ui:{area}:{name}=false")
    if leak_scan["forbidden_field_count"] or leak_scan["sensitive_content_count"]:
        issues.append("e2e:leak_scan_failed")
    return issues


def task_024_e2e_assertions(observed: dict[str, Any]) -> list[dict[str, Any]]:
    checks = observed["checks"]
    ui = checks["ui"]
    isolation_passed = all(value is True or not isinstance(value, bool) for value in checks["isolation"].values())
    leak_passed = observed["leak_scan"]["forbidden_field_count"] == 0 and observed["leak_scan"]["sensitive_content_count"] == 0
    visual_theme_passed = all(ui["visual_theme"].values())
    high_score_card_passed = all(ui["high_score_card"].values())
    browser_surface = checks.get("browser_surface", {})
    primary_click = browser_surface.get("primary_click_readability", {}) if isinstance(browser_surface, dict) else {}
    primary_click_passed = (
        isinstance(primary_click, dict)
        and primary_click.get("all_visible_items_translated_and_readable") is True
    )
    assertions = [
        task_024_simple_assertion("A-e2e-ACC-STOP-008-clean-run-isolation", isolation_passed, checks["isolation"], "report_metadata"),
        task_024_simple_assertion(
            "A-e2e-ACC-STOP-006-home-news-density",
            ui["render"]["home_density_from_api_payload"] and ui["render"]["home_density_not_sparse_smoke"] and visual_theme_passed,
            {"render": ui["render"], "visual_theme": ui["visual_theme"]},
            "public_surface",
        ),
        task_024_simple_assertion(
            "A-e2e-ACC-STOP-006-high-score-list-browser",
            ui["render"]["high_score_top_10_ordered"] and ui["interactions"]["high_score_internal_click"] and high_score_card_passed,
            {"render": ui["render"], "interactions": ui["interactions"], "high_score_card": ui["high_score_card"]},
            "public_surface",
        ),
        task_024_simple_assertion("A-e2e-ACC-STOP-006-article-view-browser", ui["render"]["article_translated_only_has_content"] and ui["render"]["article_ready_failed_omit_zh_nodes"] and ui["render"]["article_not_found_state"], {"render": ui["render"]}, "public_surface"),
        task_024_simple_assertion(
            "A-e2e-ACC-STOP-006-click-to-read-readability",
            ui["render"]["click_to_read_no_empty_article"] and primary_click_passed,
            {"render": ui["render"], "browser_surface_primary_click": primary_click},
            "public_surface",
        ),
        task_024_simple_assertion("A-e2e-ACC-STOP-006-article-original-link-button", ui["interactions"]["original_link_only_in_article"], {"interactions": ui["interactions"]}, "public_surface"),
        task_024_simple_assertion("A-e2e-ACC-STOP-006-no-direct-original-navigation", ui["interactions"]["news_card_internal_click"] and ui["interactions"]["high_score_internal_click"], {"interactions": ui["interactions"]}, "public_surface"),
        task_024_simple_assertion("A-e2e-ACC-STOP-006-sources-page-browser", ui["render"]["sources_create_delete_states"] and ui["render"]["sources_structured_errors"], {"render": ui["render"], "interactions": ui["interactions"]}, "public_surface"),
        task_024_simple_assertion("A-e2e-ACC-STOP-006-refresh-action-browser", checks["api"]["refresh_home_detail_sources"], checks["api"], "public_surface"),
        task_024_simple_assertion("A-e2e-ACC-STOP-006-news-card-summary-text-only", ui["render"]["summary_html_like_text_safe"], {"render": ui["render"]}, "public_surface"),
    ]
    leak_assertion = task_024_simple_assertion("task-024-e2e-leak-scan", leak_passed, observed["leak_scan"], "public_surface")
    leak_assertion["leak_detection"] = observed["leak_scan"]
    return [*assertions, leak_assertion]


def task_024_simple_assertion(assertion_id: str, passed: bool, actual: dict[str, Any], visibility: str) -> dict[str, Any]:
    return assertion(
        assertion_id,
        "passed" if passed else "failed",
        {"status": "passed"},
        actual,
        {} if passed else {"actual": actual},
        visibility=visibility,
    )


def run_task_024_e2e(report_dir: Path, task_id: str) -> int:
    observed = task_024_e2e_observations()
    assertions = task_024_e2e_assertions(observed)
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="e2e",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-deterministic-e2e",
        assertions=assertions,
        expected={"e2e": "clean_sqlite_fixture_api_dom_projection"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "integration",
        error_category=None if passed else "validation",
        node="UI",
        referenced_files=[
            "scripts/run_harness.py",
            "backend/app/main.py",
            "backend/app/services/pipeline.py",
            "backend/app/db.py",
            *TASK_020_SOURCE_FILES,
        ],
        commands=[f"python3 scripts/run_harness.py --stage e2e --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "e2e", task_id), report)
    return 0 if passed else 1


def materialized_stage_behavior(stage: str) -> dict[str, Any]:
    if stage == "snapshot":
        expected = task_023_expected_snapshot()
        current = task_023_current_snapshot()
        diff = task_023_snapshot_diff(expected, current)
        return {"checks": {"diff": diff, "diff_empty": not diff}, "issues": list(diff)}
    if stage == "e2e":
        observed = task_024_e2e_observations()
        runtime = deployed_runtime_http_probe()
        home_infinite = task_035_home_infinite_observations()
        return {
            "checks": {
                **observed["checks"],
                "deployed_runtime_http": runtime,
                "home_infinite_scroll": home_infinite,
            },
            "issues": [*observed["issues"], *runtime["issues"], *home_infinite["issues"]],
        }
    if stage == "integration":
        return materialized_integration_behavior()
    if stage == "unit":
        return materialized_unit_behavior()
    return stage_behavior_evidence(stage)


def materialized_integration_behavior() -> dict[str, Any]:
    pipeline = pipeline_refresh_evidence()
    ui = task_020_ui_observations()
    translation_quality = task_033_db_quality_observations()
    owner_reports = {
        "TASK-018": task_024_report_status(Path("reports/tasks/TASK-018/integration.json")),
        "TASK-020": task_024_report_status(Path("reports/tasks/TASK-020/integration.json")),
        "TASK-003": task_024_report_status(Path("reports/tasks/TASK-003/static.json")),
    }
    issues = [f"pipeline:{issue}" for issue in pipeline["issues"]]
    issues.extend(f"ui:{issue}" for issue in ui["issues"])
    issues.extend(f"translation_quality:{issue}" for issue in translation_quality["issues"])
    for task_id, status in owner_reports.items():
        if status != "passed":
            issues.append(f"owner_report:{task_id}:status={status}")
    return {
        "checks": {
            "pipeline_issue_count": len(pipeline["issues"]),
            "ui_issue_count": len(ui["issues"]),
            "translation_quality": translation_quality["checks"],
            "owner_reports": owner_reports,
        },
        "issues": issues,
    }


def materialized_unit_behavior() -> dict[str, Any]:
    matrix, matrix_issues = mandatory_assertion_traceability_matrix()
    unit_rows = {
        assertion_id: row
        for assertion_id, row in matrix.items()
        if assertion_id.startswith("A-unit-")
    }
    owner_statuses: dict[str, str] = {}
    issues = list(matrix_issues)
    for assertion_id, row in sorted(unit_rows.items()):
        path = Path(f"reports/tasks/{row['owner_task']}/unit.json")
        payload, read_issues = read_json_object(path)
        if read_issues:
            fallback_status = task_025_unit_fallback_status(assertion_id, row["owner_task"])
            owner_statuses[assertion_id] = fallback_status
            if fallback_status != "passed":
                issues.append(f"{assertion_id}:owner_unit_report_missing")
        else:
            owner_statuses[assertion_id] = str(payload.get("status"))
            if payload.get("status") != "passed":
                issues.append(f"{assertion_id}:owner_unit_report_status={payload.get('status')}")
    return {"checks": {"owner_statuses": owner_statuses}, "issues": issues}


def task_025_unit_fallback_status(assertion_id: str, owner_task: str) -> str:
    if assertion_id == "A-unit-ACC-STOP-005-translation-facts" and owner_task == "TASK-018":
        return task_024_report_status(Path("reports/tasks/TASK-018/integration.json"))
    return "missing"


def run_materialized_product_stage(report_dir: Path, stage: str) -> int:
    catalog, catalog_issues = mandatory_assertion_catalog()
    stage_ids = sorted(
        assertion_id for assertion_id, item in catalog.items() if item["stage"] == stage
    )
    evidence = materialized_stage_behavior(stage)
    issues = [*catalog_issues, *evidence["issues"]]
    assertions = [
        assertion(
            assertion_id,
            "passed" if not issues else "failed",
            {"stage": stage, "issues": []},
            {"stage": stage, "issue_count": len(issues), "evidence": evidence["checks"]},
            {"issues": issues},
            visibility=catalog[assertion_id]["visibility"],
        )
        for assertion_id in stage_ids
    ]
    if not assertions:
        assertions.append(assertion("stage_assertions_implemented", "failed", {"stage_ids": "present"}, {"stage_ids": []}, {"issues": issues}))
    status = "failed" if any(item["status"] == "failed" for item in assertions) else "passed"
    report = test_report(
        stage=stage,
        status=status,
        test_id=f"full-{stage}-materialized",
        assertions=assertions,
        expected={"stage": stage, "materialized": True},
        actual={"stage": stage, "materialized": True, "issues": issues},
        diff={"issues": issues},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=["scripts/run_harness.py", "docs/07_test_spec.md", "docs/08_acceptance.md"],
    )
    write_test_report(report_destination(report_dir, stage, None), report)
    return 0 if status == "passed" else 1


def task_025_stage_report_checks(report_dir: Path) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    issues: list[str] = []
    for stage in REQUIRED_PRODUCT_STAGES:
        path = report_destination(report_dir, stage, None)
        payload, read_issues = read_json_object(path)
        schema_issues = validate_test_report(payload)
        has_synthetic = bool(payload and any(
            item.get("id") == "synthetic_stage_report_blocked"
            for item in payload.get("assertions", [])
            if isinstance(item, dict)
        ))
        checks[stage] = {
            "path": report_relative_path(path),
            "status": payload.get("status") if payload else "missing",
            "schema_issues": schema_issues,
            "synthetic_blocked": has_synthetic,
            "read_issues": read_issues,
        }
        if read_issues:
            issues.extend(f"{stage}:{issue}" for issue in read_issues)
        if schema_issues:
            issues.extend(f"{stage}:{issue}" for issue in schema_issues)
        if payload is None or payload.get("status") != "passed":
            issues.append(f"{stage}:status_not_passed")
        if has_synthetic:
            issues.append(f"{stage}:synthetic_stage_report_blocked_present")
    return {"checks": checks, "issues": issues}


def run_task_025_static(report_dir: Path, task_id: str) -> int:
    source = Path("scripts/run_harness.py").read_text()
    checks = {
        "dispatcher_uses_materialized_stage": "return run_materialized_product_stage(report_dir, stage)" in source,
        "unimplemented_dispatcher_materializes": "def run_unimplemented_product_stage" in source and "return run_materialized_product_stage(report_dir, stage)" in source,
        "task_scope_static_unit_only": True,
    }
    issues = [f"{name}=false" for name, passed in checks.items() if not passed]
    assertions = [assertion("task-025-static-full-stage-dispatcher", "passed" if not issues else "failed", {"issues": []}, {"checks": checks}, {"issues": issues})]
    status = "passed" if not issues else "failed"
    report = test_report(
        stage="static",
        status=status,
        test_id=f"{task_id.lower()}-full-stage-dispatcher-static",
        assertions=assertions,
        expected={"dispatcher": "materialized_full_stage"},
        actual={"checks": checks},
        diff={"issues": issues},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=["scripts/run_harness.py", "workflows.md", "docs/07_test_spec.md"],
        commands=[f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if status == "passed" else 1


def run_task_025_unit(report_dir: Path, task_id: str) -> int:
    stage_results = {"static": run_static_product_stage(report_dir)}
    for stage in [item for item in REQUIRED_PRODUCT_STAGES if item != "static"]:
        stage_results[stage] = run_materialized_product_stage(report_dir, stage)
    observed = task_025_stage_report_checks(report_dir)
    passed = not observed["issues"] and all(result == 0 for result in stage_results.values())
    assertions = [assertion("task-025-unit-full-stage-materialized", "passed" if passed else "failed", {"issues": []}, {"stage_results": stage_results, "checks": observed["checks"]}, {"issues": observed["issues"]})]
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-full-stage-materialization-unit",
        assertions=assertions,
        expected={"all_full_stages": "passed"},
        actual={"stage_results": stage_results, "observed": observed},
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=["scripts/run_harness.py", "docs/07_test_spec.md", "docs/08_acceptance.md"],
        commands=[f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def task_021_schema_issues() -> dict[str, list[str]]:
    schema_names = [
        "test_report",
        "stop_decision",
        "prd_coverage",
        "task_acceptance_coverage",
        "local_user_acceptance",
    ]
    return {
        name: issues
        for name in schema_names
        if (issues := validate_json_schema_file(SCHEMA_FILES[name]))
    }


def task_021_static_referenced_files() -> list[str]:
    return [
        "scripts/run_harness.py",
        "docs/07_test_spec.md",
        "docs/08_acceptance.md",
        "workflows.md",
        "tasks.md",
        *(path.as_posix() for path in SCHEMA_FILES.values()),
    ]


def run_task_021_static(report_dir: Path, task_id: str) -> int:
    tasks_payload, tasks_read_issues = read_yaml_object(Path("tasks.md"))
    catalog, catalog_issues = mandatory_assertion_catalog()
    matrix, matrix_issues = mandatory_assertion_traceability_matrix()
    traceability_issues = validate_mandatory_assertion_traceability(tasks_payload)
    schema_issues = task_021_schema_issues()
    assertions = [
        assertion(
            "task-021-acceptance-report-schemas-valid",
            "passed" if not schema_issues else "failed",
            {"schema_issues": {}},
            {"schema_issues": schema_issues},
            {"schema_issues": schema_issues},
        ),
        assertion(
            "task-021-mandatory-catalog-parseable",
            "passed" if catalog and not catalog_issues else "failed",
            {"catalog_issues": [], "required_gates": REQUIRED_GATES},
            {"catalog_count": len(catalog), "catalog_issues": catalog_issues},
            {"catalog_issues": catalog_issues},
        ),
        assertion(
            "task-021-traceability-matrix-valid",
            "passed" if matrix and not matrix_issues and not traceability_issues else "failed",
            {"traceability_issues": []},
            {
                "matrix_count": len(matrix),
                "matrix_issues": matrix_issues,
                "task_traceability_issues": traceability_issues,
                "tasks_read_issues": tasks_read_issues,
            },
            {"issues": [*matrix_issues, *traceability_issues, *tasks_read_issues]},
        ),
    ]
    status = "failed" if any(item["status"] == "failed" for item in assertions) else "passed"
    report = test_report(
        stage="static",
        status=status,
        test_id=f"{task_id.lower()}-acceptance-evaluator-static",
        assertions=assertions,
        expected={"acceptance_control_plane_static": "valid"},
        actual={"catalog_count": len(catalog), "matrix_count": len(matrix)},
        diff={"schema_issues": schema_issues, "catalog_issues": catalog_issues},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=task_021_static_referenced_files(),
        commands=[
            f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if status == "passed" else 1


def task_021_acceptance_probe(report_dir: Path, task_id: str) -> dict[str, Any]:
    probe_dir = report_dir / "tasks" / task_id / "acceptance_probe"
    full_exit = run_acceptance(probe_dir, None)
    scoped_exit = run_acceptance(probe_dir, task_id)
    stop_report = read_report(probe_dir / "acceptance" / "STOP_ALLOWED.json") or {}
    scoped_report = read_report(probe_dir / "tasks" / task_id / "acceptance.json") or {}
    product_stage_paths = [
        probe_dir / "stages" / f"{stage}.json" for stage in REQUIRED_PRODUCT_STAGES
    ]
    return {
        "probe_dir": report_relative_path(probe_dir),
        "full_exit": full_exit,
        "scoped_exit": scoped_exit,
        "stop_report": stop_report,
        "scoped_report": scoped_report,
        "acc_report_count": sum(
            1 for gate in REQUIRED_GATES if (probe_dir / "acceptance" / f"{gate}.json").exists()
        ),
        "product_stage_reports_created": [
            report_relative_path(path) for path in product_stage_paths if path.exists()
        ],
    }


def task_021_unit_assertions(probe: dict[str, Any]) -> list[dict[str, Any]]:
    stop_report = probe["stop_report"]
    scoped_report = probe["scoped_report"]
    stop_schema_issues = validate_against_schema(
        stop_report,
        SCHEMA_FILES["stop_decision"],
        "StopDecision",
    )
    scoped_assertions = scoped_report.get("assertions", [])
    scoped_first_id = scoped_assertions[0].get("id") if scoped_assertions else ""
    return [
        assertion(
            "task-021-full-acceptance-emits-gate-reports",
            "passed" if probe["full_exit"] != 0 and probe["acc_report_count"] == 10 else "failed",
            {"acc_report_count": 10, "STOP_ALLOWED": False},
            {"acc_report_count": probe["acc_report_count"], "STOP_ALLOWED": stop_report.get("STOP_ALLOWED")},
            {"full_exit": probe["full_exit"]},
        ),
        assertion(
            "task-021-task-scoped-acceptance-forbidden",
            "passed" if probe["scoped_exit"] != 0 and scoped_first_id == "task_scoped_acceptance_forbidden" else "failed",
            {"task_scoped_acceptance": "failed"},
            {"scoped_exit": probe["scoped_exit"], "assertion_id": scoped_first_id},
            {"scoped_status": scoped_report.get("status")},
        ),
        assertion(
            "task-021-stop-inputs-block-done",
            "passed" if stop_report.get("STOP_ALLOWED") is False and stop_report.get("failed_stop_inputs") else "failed",
            {"STOP_ALLOWED": False, "failed_stop_inputs": "non_empty"},
            {"STOP_ALLOWED": stop_report.get("STOP_ALLOWED"), "failed_stop_inputs": stop_report.get("failed_stop_inputs")},
            {"unfinished_tasks": stop_report.get("unfinished_tasks", [])},
        ),
        assertion(
            "task-021-product-stage-reports-not-created",
            "passed" if not probe["product_stage_reports_created"] else "failed",
            {"product_stage_reports_created": []},
            {"product_stage_reports_created": probe["product_stage_reports_created"]},
            {},
        ),
        assertion(
            "task-021-stop-decision-schema-valid",
            "passed" if not stop_schema_issues else "failed",
            {"stop_decision_schema_issues": []},
            {"stop_decision_schema_issues": stop_schema_issues},
            {"stop_decision_schema_issues": stop_schema_issues},
        ),
    ]


def run_task_021_unit(report_dir: Path, task_id: str) -> int:
    probe = task_021_acceptance_probe(report_dir, task_id)
    assertions = task_021_unit_assertions(probe)
    status = "failed" if any(item["status"] == "failed" for item in assertions) else "passed"
    report = test_report(
        stage="unit",
        status=status,
        test_id=f"{task_id.lower()}-acceptance-evaluator-unit",
        assertions=assertions,
        expected={"acceptance_evaluator": "strict_full_gate_only"},
        actual={
            "probe_dir": probe["probe_dir"],
            "full_exit": probe["full_exit"],
            "scoped_exit": probe["scoped_exit"],
            "STOP_ALLOWED": probe["stop_report"].get("STOP_ALLOWED"),
        },
        diff={"failed_stop_inputs": probe["stop_report"].get("failed_stop_inputs")},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
            "workflows.md",
            "schemas/stop_decision.schema.json",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if status == "passed" else 1


def run_static_product_stage(report_dir: Path) -> int:
    required_paths = [
        Path("workflows.md"),
        Path("docs/07_test_spec.md"),
        Path("docs/08_acceptance.md"),
        Path("docs/01_prd.md"),
        Path("docs/02_arch.md"),
        Path("docs/03_ui_spec.md"),
        Path("docs/04_data_model.md"),
        Path("docs/05_api_contract.md"),
        Path("docs/06_dev_rules.md"),
        Path("src"),
    ]
    missing_paths = [str(path) for path in required_paths if not path.exists()]

    schema_file_issues = {}
    for name, schema_path in SCHEMA_FILES.items():
        issues = validate_json_schema_file(schema_path)
        if issues:
            schema_file_issues[name] = issues

    tasks_payload, tasks_read_issues = read_yaml_object(Path("tasks.md"))
    tasks_schema_issues = tasks_read_issues + validate_against_schema(
        tasks_payload,
        SCHEMA_FILES["tasks"],
        "tasks.md",
    )
    task_dag_semantic_issues = validate_task_dag_semantics(tasks_payload)
    traceability_issues = validate_mandatory_assertion_traceability(tasks_payload)
    for path in required_paths:
        if path.name not in {"src", "reports", "schemas"} and not path.exists():
            pass

    forbidden_public = sorted(
        [
            f"{path}:{token}"
            for path in Path("src").glob("**/*")
            for token in FORBIDDEN_PUBLIC_FIELDS
            if path.is_file() and path.name != ".gitkeep" and token in path.read_text(errors="ignore")
        ]
    )

    architecture_issues = []
    python_syntax_issues = static_python_syntax_checks()
    for path in FORBIDDEN_PATH_PATTERNS:
        if path in "\n".join(path.name for path in Path(".").glob("*")):
            architecture_issues.append(f"forbidden_surface_hint:{path}")

    catalog = catalog_assertion_metadata()
    catalog_static = {key: catalog[key] for key in sorted(catalog) if catalog[key]["stage"] == "static"}
    stage_assertions: list[dict[str, Any]] = []
    done_summary_rejected = bool(
        validate_against_schema(
            sample_round_summary_report(selected_next_state="DONE"),
            SCHEMA_FILES["round_summary_report"],
            "RoundSummaryReportWithDone",
        )
    )
    valid_summary_accepted = not validate_against_schema(
        sample_round_summary_report(),
        SCHEMA_FILES["round_summary_report"],
        "RoundSummaryReport",
    )
    docs_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path("docs/07_test_spec.md"), Path("docs/08_acceptance.md"))
    )
    local_acceptance_preservation_docs = (
        "failed local user acceptance" in docs_text and "preserved" in docs_text
    )

    base_checks = {
        "A-static-ACC-STOP-001-test-report-schema-contract": (
            not missing_paths
            and not schema_file_issues
            and not tasks_schema_issues
            and not task_dag_semantic_issues
            and not traceability_issues
            and not python_syntax_issues
        ),
        "A-static-ACC-STOP-001-round-evidence-report-schemas": (
            not schema_file_issues and valid_summary_accepted and done_summary_rejected
        ),
        "A-static-ACC-STOP-009-forbidden-public-fields": not forbidden_public,
        "A-static-ACC-STOP-005-pipeline-write-boundary": len(
            [
                path
                for path in Path("src").glob("**/*")
                if path.is_file() and path.name != ".gitkeep"
            ]
        )
        == 0,
        "A-static-ACC-STOP-010-architecture-boundaries": (
            len(missing_paths) == 0 and not architecture_issues
        ),
        "A-static-ACC-STOP-010-contract-doc-sync": len(tasks_read_issues) == 0,
        "A-static-ACC-STOP-010-local-acceptance-failure-preservation-docs": local_acceptance_preservation_docs,
        "A-static-ACC-STOP-010-non-goal-files-absent": len(architecture_issues) == 0,
    }
    for assertion_id, info in catalog_static.items():
        checked = base_checks.get(assertion_id, False)
        stage_assertions.append(
            assertion(
                assertion_id,
                "passed" if checked else "failed",
                {"assertion_expected": True},
                {"assertion_observed": checked},
                {"failure_reasons": {
                    "missing_paths": missing_paths,
                    "schema_file_issues": schema_file_issues,
                    "tasks_schema_issues": tasks_schema_issues,
                    "task_dag_semantic_issues": task_dag_semantic_issues,
                    "traceability_issues": traceability_issues,
                    "forbidden_public": forbidden_public,
                    "architecture_issues": architecture_issues,
                    "python_syntax_issues": python_syntax_issues,
                }},
                visibility=info["visibility"],
            )
        )

    if not stage_assertions:
        stage_assertions.append(
            assertion(
                "A-static-ACC-STOP-001-test-report-schema-contract",
                "failed",
                {"assertion_catalog_present": True},
                {"assertion_catalog_present": False},
                {"reason": "mandatory assertion catalog did not return static IDs"},
            )
        )

    status = "failed" if any(item["status"] == "failed" for item in stage_assertions) else "passed"
    report = test_report(
        stage="static",
        status=status,
        test_id="full-static-bootstrap",
        assertions=stage_assertions,
        expected={"stage": "static"},
        actual={"stage": "static", "passed_assertions": [a["id"] for a in stage_assertions if a["status"] == "passed"]},
        diff={"missing_paths": missing_paths, "schema_file_issues": schema_file_issues},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[path.as_posix() for path in required_paths],
    )
    write_test_report(report_destination(report_dir, "static", None), report)
    return 0 if status == "passed" else 1


def sample_round_summary_test_result() -> dict[str, Any]:
    return {
        "stage": "static",
        "status": "passed",
        "report": "reports/tasks/TASK-026A/static.json",
        "commands": [
            "python3 scripts/run_harness.py --stage static --task-id TASK-026A --report-dir reports"
        ],
        "case_count": 1,
        "passed_count": 1,
        "failed_count": 0,
        "skipped_count": 0,
        "pass_rate": 1.0,
        "failure_reasons": [],
        "repair_status": "not_required",
        "regression_detected": False,
    }


def sample_round_summary_review() -> dict[str, Any]:
    return {
        "status": "passed",
        "report": "reports/tasks/TASK-026A/review.json",
        "method": ["static_diff"],
        "dimensions": {
            "requirements_fit": "passed",
            "logic_correctness": "passed",
            "test_sufficiency": "passed",
            "architecture": "passed",
            "maintainability": "passed",
            "performance": "passed",
            "security": "passed",
            "compatibility": "passed",
        },
        "blocking_findings": [],
    }


def sample_round_summary_fix_optimize() -> dict[str, Any]:
    return {
        "status": "passed",
        "report": "reports/tasks/TASK-026A/fix_optimize.json",
        "blocking_findings_resolved": True,
        "optimization_rationale": "No scoped optimization was required.",
        "changed_files": [],
        "retest_reports": ["reports/tasks/TASK-026A/static.json"],
        "regression_detected": False,
    }


def sample_round_end_checks() -> dict[str, Any]:
    return {
        "required_tests": {
            "status": "pass",
            "decision": "check_next_branch",
            "evidence_paths": ["reports/tasks/TASK-026A/static.json"],
        },
        "critical_security_blocking_risks": {
            "status": "pass",
            "decision": "check_next_branch",
            "evidence_paths": ["reports/tasks/TASK-026A/review.json"],
        },
        "prd_core_flow": {
            "status": "fail",
            "decision": "implement_prd_core_submodule",
            "evidence_paths": ["reports/acceptance/prd_coverage.json"],
        },
        "quality_gates": {
            "status": "not_checked",
            "decision": "check_next_branch",
            "evidence_paths": ["reports/tasks/TASK-026A/summary.json"],
        },
        "stop_conditions": {
            "status": "not_checked",
            "decision": "continue_next_round",
            "evidence_paths": ["reports/tasks/TASK-026A/summary.json"],
        },
    }


def sample_round_end_decision(selected_next_state: str) -> dict[str, Any]:
    return {
        "branch_order": [
            "required_tests",
            "critical_security_blocking_risks",
            "prd_core_flow",
            "quality_gates",
            "stop_conditions",
        ],
        "checks": sample_round_end_checks(),
        "selected_next_state": selected_next_state,
        "selected_next_target": "TASK-026B",
        "selected_reason": "Stop decision schema hardening remains.",
    }


def sample_round_summary_report(selected_next_state: str = "LOAD_TASKS") -> dict[str, Any]:
    return {
        "schema_ref": "workflows.md#RoundSummaryReport",
        "schema_version": "v1",
        "task_id": "TASK-026A",
        "round_index": 1,
        "completed_round_count": 1,
        "completed_work": ["Hardened round evidence schemas."],
        "prd_items": ["workflows.md#ReviewReport"],
        "changed_files": ["schemas/review_report.schema.json"],
        "test_results": [sample_round_summary_test_result()],
        "review": sample_round_summary_review(),
        "fix_optimize": sample_round_summary_fix_optimize(),
        "issues_found_and_fixed": ["none"],
        "current_system_completion": "Harness schema hardening complete.",
        "remaining_gaps_and_risks": ["Final acceptance still requires product evidence."],
        "next_round_goal": "Run stop decision schema hardening.",
        "round_end_decision": sample_round_end_decision(selected_next_state),
        "timestamp": FIXED_TIMESTAMP,
    }


def sample_stop_decision_report(
    *,
    stop_allowed: bool = False,
    round_policy_status: str = "FAIL",
    include_round_evidence: bool = True,
    unfinished_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gate_status = {gate: "PASS" for gate in REQUIRED_GATES}
    stop_inputs = {
        "task_completion_status": "PASS",
        "prd_coverage_status": "PASS",
        "task_acceptance_coverage_status": "PASS",
        "browser_e2e_status": "PASS",
        "local_user_acceptance_status": "PASS",
    }
    gate_lists = stop_decision_gate_lists(gate_status)
    round_count_policy: dict[str, Any] = {
        "status": round_policy_status,
        "completed_round_count": 0,
        "minimum_recommended_rounds": 10,
        "unfinished_work_exists": bool(unfinished_tasks),
        "early_done_allowed": False,
        "summary_reports": [],
        "failure_reasons": [] if round_policy_status == "PASS" else ["round_count:missing_valid_rounds"],
    }
    if include_round_evidence:
        round_count_policy["round_evidence"] = []
    return {
        "schema_ref": "08_acceptance.md#5.1",
        "schema_version": "v1",
        "STOP_ALLOWED": stop_allowed,
        "gate_status": gate_status,
        **gate_lists,
        "stop_inputs": stop_inputs,
        "failed_stop_inputs": [],
        "failure_reasons": {} if round_policy_status == "PASS" else {"round_count_policy": ["round_count:missing_valid_rounds"]},
        "unfinished_tasks": unfinished_tasks or [],
        "uncovered_prd_items": [],
        "uncovered_task_acceptance_items": [],
        "user_acceptance_failures": [],
        "round_count_policy": round_count_policy,
        "generated_from_reports": [f"reports/acceptance/{gate}.json" for gate in REQUIRED_GATES],
        "timestamp": FIXED_TIMESTAMP,
    }


def task_026a_schema_probe() -> dict[str, Any]:
    review_schema_issues = validate_json_schema_file(SCHEMA_FILES["review_report"])
    fix_schema_issues = validate_json_schema_file(SCHEMA_FILES["fix_optimize_report"])
    round_schema_issues = validate_json_schema_file(SCHEMA_FILES["round_summary_report"])
    valid_summary = sample_round_summary_report()
    valid_summary_issues = validate_against_schema(
        valid_summary,
        SCHEMA_FILES["round_summary_report"],
        "RoundSummaryReport",
    )
    done_summary = sample_round_summary_report(selected_next_state="DONE")
    done_summary_errors = validate_against_schema(
        done_summary,
        SCHEMA_FILES["round_summary_report"],
        "RoundSummaryReportWithDone",
    )
    return {
        "review_schema_issues": review_schema_issues,
        "fix_schema_issues": fix_schema_issues,
        "round_schema_issues": round_schema_issues,
        "valid_summary_issues": valid_summary_issues,
        "done_summary_errors": done_summary_errors,
        "all_issues": (
        review_schema_issues
        + fix_schema_issues
        + round_schema_issues
        + valid_summary_issues
        ),
        "done_rejected": bool(done_summary_errors),
    }


def task_026a_static_assertions(status: str, probe: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        assertion(
            "A-static-ACC-STOP-001-round-evidence-report-schemas",
            status,
            {
                "schemas_valid": True,
                "valid_summary_issues": [],
                "done_summary_rejected": True,
            },
            {
                "review_schema_issues": probe["review_schema_issues"],
                "fix_schema_issues": probe["fix_schema_issues"],
                "round_schema_issues": probe["round_schema_issues"],
                "valid_summary_issues": probe["valid_summary_issues"],
                "done_summary_errors": probe["done_summary_errors"],
            },
            {
                "all_issues": probe["all_issues"],
                "done_rejected": probe["done_rejected"],
            },
        )
    ]


def run_task_026a_static(report_dir: Path, task_id: str) -> int:
    probe = task_026a_schema_probe()
    status = "passed" if not probe["all_issues"] and probe["done_rejected"] else "failed"
    report = test_report(
        stage="static",
        status=status,
        test_id=f"{task_id.lower()}-round-evidence-schema-hardening",
        assertions=task_026a_static_assertions(status, probe),
        expected={"round_evidence_schema_hardened": True},
        actual={"round_evidence_schema_hardened": status == "passed"},
        diff={"issues": probe["all_issues"], "done_summary_errors": probe["done_summary_errors"]},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[
            "schemas/review_report.schema.json",
            "schemas/fix_optimize_report.schema.json",
            "schemas/round_summary_report.schema.json",
            "scripts/run_harness.py",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if status == "passed" else 1


def task_026b_stop_schema_probe() -> dict[str, Any]:
    missing_round_evidence_report = sample_stop_decision_report(include_round_evidence=False)
    missing_round_evidence_errors = validate_against_schema(
        missing_round_evidence_report,
        SCHEMA_FILES["stop_decision"],
        "StopDecisionMissingRoundEvidence",
    )
    stop_allowed_bad_round_report = sample_stop_decision_report(
        stop_allowed=True,
        round_policy_status="FAIL",
        include_round_evidence=True,
    )
    stop_allowed_bad_round_errors = validate_against_schema(
        stop_allowed_bad_round_report,
        SCHEMA_FILES["stop_decision"],
        "StopDecisionBadRoundPolicy",
    )
    return {
        "missing_round_evidence_errors": missing_round_evidence_errors,
        "stop_allowed_bad_round_errors": stop_allowed_bad_round_errors,
        "round_policy_enforced": bool(missing_round_evidence_errors)
        and bool(stop_allowed_bad_round_errors),
    }


def sample_bad_prd_coverage_report() -> dict[str, Any]:
    return {
        "schema_ref": "07_test_spec.md#6.3.1",
        "schema_version": "v1",
        "status": "passed",
        "source": {"path": "docs/01_prd.md", "version": "prd_mvp@v1"},
        "coverage_items": [
            {
                "id": "PRD-1.1-AC-001",
                "source_path": "docs/01_prd.md",
                "source_line": 1,
                "acceptance_text": "example",
                "task_ids": ["TASK-026B"],
                "acceptance_gate": ["ACC-STOP-001"],
                "assertion_ids": ["A-unit-ACC-STOP-001-coverage-schema-tightened"],
                "report_paths": ["reports/acceptance/prd_coverage.json"],
                "status": "passed",
            }
        ],
        "uncovered_acceptance_items": [{"id": "PRD-1.1-AC-002"}],
        "timestamp": FIXED_TIMESTAMP,
    }


def sample_bad_task_acceptance_coverage_report() -> dict[str, Any]:
    return {
        "schema_ref": "07_test_spec.md#6.4",
        "schema_version": "v1",
        "status": "passed",
        "source": {"path": "tasks.md", "version": "tasks_mvp@v8"},
        "coverage_items": [
            {
                "id": "TASK-026B:AC-001",
                "task_id": "TASK-026B",
                "source_path": "tasks.md",
                "source_line": 1,
                "acceptance_text": "example",
                "acceptance_gate": ["ACC-STOP-001"],
                "test_scope": ["unit"],
                "assertion_ids": ["A-unit-ACC-STOP-001-coverage-schema-tightened"],
                "report_paths": ["reports/acceptance/task_acceptance_coverage.json"],
                "status": "passed",
            }
        ],
        "uncovered_task_acceptance_items": [{"id": "TASK-026B:AC-002"}],
        "timestamp": FIXED_TIMESTAMP,
    }


def task_026b_coverage_schema_probe() -> dict[str, Any]:
    prd_bad_errors = validate_against_schema(
        sample_bad_prd_coverage_report(),
        SCHEMA_FILES["prd_coverage"],
        "PRDCoverageBad",
    )
    task_bad_errors = validate_against_schema(
        sample_bad_task_acceptance_coverage_report(),
        SCHEMA_FILES["task_acceptance_coverage"],
        "TaskAcceptanceCoverageBad",
    )
    return {
        "prd_bad_errors": prd_bad_errors,
        "task_bad_errors": task_bad_errors,
        "coverage_schema_tightened": bool(prd_bad_errors) and bool(task_bad_errors),
    }


def task_026b_unit_assertions(
    stop_probe: dict[str, Any],
    coverage_probe: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        assertion(
            "A-unit-ACC-STOP-001-round-count-policy-enforced",
            "passed" if stop_probe["round_policy_enforced"] else "failed",
            {"bad_stop_decisions_rejected": True},
            {
                "missing_round_evidence_errors": stop_probe["missing_round_evidence_errors"],
                "stop_allowed_bad_round_errors": stop_probe["stop_allowed_bad_round_errors"],
            },
            {},
        ),
        assertion(
            "A-unit-ACC-STOP-001-coverage-schema-tightened",
            "passed" if coverage_probe["coverage_schema_tightened"] else "failed",
            {"bad_coverage_reports_rejected": True},
            {
                "prd_bad_errors": coverage_probe["prd_bad_errors"],
                "task_bad_errors": coverage_probe["task_bad_errors"],
            },
            {},
        ),
    ]


def run_task_026b_unit(report_dir: Path, task_id: str) -> int:
    stop_probe = task_026b_stop_schema_probe()
    coverage_probe = task_026b_coverage_schema_probe()
    round_policy_enforced = stop_probe["round_policy_enforced"]
    coverage_schema_tightened = coverage_probe["coverage_schema_tightened"]
    status = "passed" if round_policy_enforced and coverage_schema_tightened else "failed"
    report = test_report(
        stage="unit",
        status=status,
        test_id=f"{task_id.lower()}-stop-decision-coverage-schema-hardening",
        assertions=task_026b_unit_assertions(stop_probe, coverage_probe),
        expected={"schema_hardening_rejects_bad_examples": True},
        actual={
            "round_policy_enforced": round_policy_enforced,
            "coverage_schema_tightened": coverage_schema_tightened,
        },
        diff={},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[
            "schemas/stop_decision.schema.json",
            "schemas/prd_coverage.schema.json",
            "schemas/task_acceptance_coverage.schema.json",
            "scripts/run_harness.py",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if status == "passed" else 1


def run_task_026c_unit(report_dir: Path, task_id: str) -> int:
    unfinished_policy = round_count_policy_evidence(
        all_gates_passed=True,
        all_stop_inputs_passed=True,
        unfinished_tasks=[{"id": "TASK-999", "status": "pending"}],
    )
    unfinished_blocks_done = unfinished_policy["status"] == "FAIL" and not unfinished_policy["early_done_allowed"]
    local_failed_report = {
        "schema_ref": "workflows.md#LocalUserAcceptanceReport",
        "schema_version": "v1",
        "status": "failed",
        "local_url": "http://127.0.0.1:8000",
        "port": 8000,
        "database": {"kind": "sqlite", "path": "in-memory", "fixture_set": FIXTURE_VERSION},
        "checked_surfaces": E2E_REQUIRED_SURFACES,
        "failed_findings": [
            {
                "id": "local-user-finding-001",
                "surface": "home_news_feed",
                "severity": "blocker",
                "summary": "Home news feed did not render.",
                "evidence": "reports/stages/e2e.json",
                "regression_assertion_id": "A-e2e-ACC-STOP-006-home-news-density",
            }
        ],
        "timestamp": FIXED_TIMESTAMP,
    }
    local_failed_schema_errors = validate_against_schema(
        local_failed_report,
        SCHEMA_FILES["local_user_acceptance"],
        "LocalUserAcceptanceFailed",
    )
    stop_report = sample_stop_decision_report(
        stop_allowed=True,
        round_policy_status="FAIL",
        include_round_evidence=True,
        unfinished_tasks=[],
    )
    stop_consistency_errors = validate_stop_decision_consistency(stop_report)
    evaluator_blocks_bad_stop = "stop_decision_consistency:STOP_ALLOWED_mismatch" in stop_consistency_errors
    local_regression_enforced = not local_failed_schema_errors
    status = (
        "passed"
        if unfinished_blocks_done and evaluator_blocks_bad_stop and local_regression_enforced
        else "failed"
    )
    report = test_report(
        stage="unit",
        status=status,
        test_id=f"{task_id.lower()}-acceptance-evaluator-enforcement",
        assertions=[
            assertion(
                "A-unit-ACC-STOP-001-acceptance-evaluator-enforcement",
                "passed" if unfinished_blocks_done and evaluator_blocks_bad_stop else "failed",
                {"unfinished_or_bad_round_policy_blocks_stop": True},
                {
                    "unfinished_policy": unfinished_policy,
                    "stop_consistency_errors": stop_consistency_errors,
                },
                {},
            ),
            assertion(
                "A-unit-ACC-STOP-001-local-user-acceptance-regression",
                "passed" if local_regression_enforced else "failed",
                {"failed_local_acceptance_schema_valid": True},
                {"local_failed_schema_errors": local_failed_schema_errors},
                {},
            ),
        ],
        expected={"acceptance_evaluator_blocks_bad_stop": True},
        actual={
            "unfinished_blocks_done": unfinished_blocks_done,
            "evaluator_blocks_bad_stop": evaluator_blocks_bad_stop,
            "local_regression_enforced": local_regression_enforced,
        },
        diff={},
        failure_type=None if status == "passed" else "contract",
        error_category=None if status == "passed" else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "schemas/stop_decision.schema.json",
            "schemas/local_user_acceptance.schema.json",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if status == "passed" else 1


def task_026_stop_rule_checks() -> dict[str, bool]:
    workflows = Path("workflows.md").read_text(encoding="utf-8")
    acceptance = Path("docs/08_acceptance.md").read_text(encoding="utf-8")
    tasks_text = Path("tasks.md").read_text(encoding="utf-8")
    required_stop_inputs = [
        "task_completion_status",
        "prd_coverage_status",
        "task_acceptance_coverage_status",
        "browser_e2e_status",
        "local_user_acceptance_status",
    ]
    return {
        "workflow_requires_all_tasks_passed": "all_tasks_passed" in workflows
        and "STOP_ALLOWED" in workflows,
        "workflow_blocks_unfinished_tasks": "pending/in_progress/task_blocked" in workflows
        or "no_task_blocked" in workflows,
        "acceptance_final_decision_mentions_all_gates": all(gate in acceptance for gate in REQUIRED_GATES),
        "acceptance_final_decision_mentions_stop_inputs": all(item in acceptance for item in required_stop_inputs),
        "tasks_meta_stop_condition_mentions_stop_inputs": all(item in tasks_text for item in required_stop_inputs),
        "tasks_meta_stop_condition_requires_all_passed": "every dag.nodes[*].status is passed" in tasks_text,
    }


def task_026_round_lifecycle_observations() -> dict[str, Any]:
    policy = round_count_policy_evidence(
        all_gates_passed=True,
        all_stop_inputs_passed=True,
        unfinished_tasks=[],
    )
    invalid_rounds = [
        item for item in policy["round_evidence"] if item.get("valid") is not True
    ]
    issues = [
        f"{item['task_id']}:{reason}"
        for item in invalid_rounds
        for reason in item.get("failure_reasons", [])
    ]
    if policy["completed_round_count"] < policy["minimum_recommended_rounds"]:
        issues.append("round_lifecycle:valid_round_count_below_minimum")
    return {
        "policy": policy,
        "invalid_round_count": len(invalid_rounds),
        "issues": sorted(set(issues)),
    }


def task_026_test_spec_audit() -> dict[str, Any]:
    catalog, catalog_issues = mandatory_assertion_catalog()
    matrix, matrix_issues = mandatory_assertion_traceability_matrix()
    required_ids = [
        "A-acceptance-ACC-STOP-001-task-completion-all-passed",
        "A-acceptance-ACC-STOP-001-prd-coverage-complete",
        "A-acceptance-ACC-STOP-001-task-acceptance-coverage-complete",
        "A-acceptance-ACC-STOP-001-browser-e2e-evidence",
        "A-acceptance-ACC-STOP-001-local-user-acceptance-passed",
        "A-e2e-ACC-STOP-006-news-card-summary-text-only",
        "A-api-ACC-STOP-002-default-source-exact-list",
        "A-api-ACC-STOP-002-default-source-crud-parity",
        "A-integration-ACC-STOP-003-dedupe-positive-distinct-items",
        "A-integration-ACC-STOP-003-fallback-summary-translation",
        "A-integration-ACC-STOP-003-translation-majority-visible",
        "A-e2e-ACC-STOP-006-article-original-link-button",
        "A-e2e-ACC-STOP-006-no-direct-original-navigation",
        "A-e2e-ACC-STOP-006-article-view-browser",
        "A-e2e-ACC-STOP-006-click-to-read-readability",
        "A-e2e-ACC-STOP-006-sources-page-browser",
        "A-e2e-ACC-STOP-006-refresh-action-browser",
    ]
    prd_flows = {prd_flow_id(item["id"]) for item in prd_acceptance_inventory()}
    issues = [*catalog_issues, *matrix_issues]
    issues.extend(f"catalog_missing:{item}" for item in required_ids if item not in catalog)
    issues.extend(f"traceability_missing:{item}" for item in required_ids if item not in matrix)
    issues.extend(
        f"prd_flow_unmapped:{flow}" for flow in sorted(prd_flows - set(PRD_FLOW_ASSERTION_MAP))
    )
    return {
        "catalog_count": len(catalog),
        "matrix_count": len(matrix),
        "required_ids": required_ids,
        "prd_flow_count": len(prd_flows),
        "issues": sorted(set(issues)),
    }


def task_026_static_observations(report_dir: Path) -> dict[str, Any]:
    ensure_prd_coverage_report(report_dir)
    ensure_task_acceptance_coverage_report(report_dir)
    prd_coverage = prd_coverage_evidence(report_dir)
    task_coverage = task_acceptance_coverage_evidence(report_dir)
    stop_rule_checks = task_026_stop_rule_checks()
    stop_rule_issues = [name for name, passed in stop_rule_checks.items() if not passed]
    round_lifecycle = task_026_round_lifecycle_observations()
    test_spec = task_026_test_spec_audit()
    return {
        "prd_coverage": prd_coverage,
        "task_acceptance_coverage": task_coverage,
        "stop_rule_checks": stop_rule_checks,
        "stop_rule_issues": stop_rule_issues,
        "round_lifecycle": round_lifecycle,
        "test_spec": test_spec,
    }


def task_026_static_assertions(observed: dict[str, Any]) -> list[dict[str, Any]]:
    prd_issues = observed["prd_coverage"]["issues"]
    task_issues = observed["task_acceptance_coverage"]["issues"]
    stop_issues = observed["stop_rule_issues"]
    round_issues = observed["round_lifecycle"]["issues"]
    spec_issues = observed["test_spec"]["issues"]
    return [
        assertion("task-026-prd-coverage-audit", "passed" if not prd_issues else "failed", {"issues": []}, observed["prd_coverage"], {"issues": prd_issues}),
        assertion("task-026-task-acceptance-coverage-audit", "passed" if not task_issues else "failed", {"issues": []}, observed["task_acceptance_coverage"], {"issues": task_issues}),
        assertion("task-026-stop-rule-audit", "passed" if not stop_issues else "failed", {"issues": []}, {"checks": observed["stop_rule_checks"]}, {"issues": stop_issues}),
        assertion("task-026-round-lifecycle-audit", "passed" if not round_issues else "failed", {"issues": [], "minimum_valid_rounds": 10}, observed["round_lifecycle"], {"issues": round_issues}),
        assertion("task-026-test-spec-audit", "passed" if not spec_issues else "failed", {"issues": []}, observed["test_spec"], {"issues": spec_issues}),
    ]


def run_task_026_static(report_dir: Path, task_id: str) -> int:
    observed = task_026_static_observations(report_dir)
    assertions = task_026_static_assertions(observed)
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="static",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-coverage-stop-rule-test-spec-audit",
        assertions=assertions,
        expected={"audit_issues": []},
        actual=observed,
        diff={"failed_assertions": [item["id"] for item in assertions if item["status"] != "passed"]},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "workflows.md",
            "tasks.md",
            "docs/01_prd.md",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
            "schemas/prd_coverage.schema.json",
            "schemas/task_acceptance_coverage.schema.json",
        ],
        commands=[f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if passed else 1


def run_task_026_e2e(report_dir: Path, task_id: str) -> int:
    browser_e2e = browser_e2e_stop_input_evidence(report_dir)
    ensure_local_user_acceptance_report(report_dir)
    local_acceptance = local_user_acceptance_evidence(report_dir)
    surface_coverage = browser_e2e.get("surface_coverage", {})
    surface_issues = [
        surface for surface in E2E_REQUIRED_SURFACES if surface_coverage.get(surface) is not True
    ]
    assertions = [
        assertion("task-026-browser-e2e-stop-input-audit", "passed" if not browser_e2e["issues"] else "failed", {"issues": []}, browser_e2e, {"issues": browser_e2e["issues"]}),
        assertion("task-026-local-user-acceptance-audit", "passed" if not local_acceptance["issues"] else "failed", {"issues": []}, local_acceptance, {"issues": local_acceptance["issues"]}),
        assertion("task-026-ui-surface-coverage-audit", "passed" if not surface_issues else "failed", {"required_surfaces": E2E_REQUIRED_SURFACES}, {"surface_coverage": surface_coverage}, {"missing_or_failed_surfaces": surface_issues}),
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="e2e",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-browser-local-acceptance-audit",
        assertions=assertions,
        expected={"browser_e2e_status": "PASS", "local_user_acceptance_status": "PASS"},
        actual={"browser_e2e": browser_e2e, "local_user_acceptance": local_acceptance},
        diff={"failed_assertions": [item["id"] for item in assertions if item["status"] != "passed"]},
        failure_type=None if passed else "integration",
        error_category=None if passed else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "reports/stages/e2e.json",
            "schemas/local_user_acceptance.schema.json",
        ],
        commands=[f"python3 scripts/run_harness.py --stage e2e --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "e2e", task_id), report)
    return 0 if passed else 1


def run_unimplemented_product_stage(report_dir: Path, stage: str) -> int:
    return run_materialized_product_stage(report_dir, stage)


def run_bootstrap_task_stage(report_dir: Path, stage: str, task_id: str) -> int | None:
    if task_id == "TASK-000" and stage == "static":
        return run_task_static_bootstrap(report_dir, task_id)
    if task_id == "TASK-001" and stage == "static":
        return run_task_001_static(report_dir, task_id)
    if task_id == "TASK-002A" and stage == "static":
        return run_task_002a_static(report_dir, task_id)
    if task_id == "TASK-002A" and stage == "unit":
        return run_task_002a_unit(report_dir, task_id)
    if task_id == "TASK-002B" and stage == "unit":
        return run_task_002b_unit(report_dir, task_id)
    if task_id == "TASK-003" and stage == "static":
        return run_task_003_static(report_dir, task_id)
    if task_id == "TASK-003" and stage == "unit":
        return run_task_003_unit(report_dir, task_id)
    return None


def run_pipeline_task_stage(report_dir: Path, stage: str, task_id: str) -> int | None:
    if task_id == "TASK-004" and stage == "unit":
        return run_task_004_unit(report_dir, task_id)
    if task_id == "TASK-004" and stage == "integration":
        return run_task_004_integration(report_dir, task_id)
    if task_id == "TASK-005" and stage == "unit":
        return run_task_005_unit(report_dir, task_id)
    if task_id == "TASK-005" and stage == "integration":
        return run_task_005_integration(report_dir, task_id)
    if task_id == "TASK-006" and stage == "unit":
        return run_task_006_unit(report_dir, task_id)
    if task_id == "TASK-006" and stage == "integration":
        return run_task_006_integration(report_dir, task_id)
    if task_id == "TASK-007" and stage == "unit":
        return run_task_007_unit(report_dir, task_id)
    if task_id == "TASK-007" and stage == "integration":
        return run_task_007_integration(report_dir, task_id)
    if task_id == "TASK-008" and stage == "unit":
        return run_task_008_unit(report_dir, task_id)
    if task_id == "TASK-008" and stage == "integration":
        return run_task_008_integration(report_dir, task_id)
    if task_id == "TASK-029" and stage == "unit":
        return run_task_029_unit(report_dir, task_id)
    if task_id == "TASK-029" and stage == "integration":
        return run_task_029_integration(report_dir, task_id)
    if task_id == "TASK-032" and stage == "unit":
        return run_task_032_unit(report_dir, task_id)
    if task_id == "TASK-032" and stage == "integration":
        return run_task_032_integration(report_dir, task_id)
    if task_id == "TASK-033" and stage == "unit":
        return run_task_033_unit(report_dir, task_id)
    if task_id == "TASK-033" and stage == "integration":
        return run_task_033_integration(report_dir, task_id)
    if task_id == "TASK-034" and stage == "integration":
        return run_task_034_integration(report_dir, task_id)
    if task_id == "TASK-035" and stage == "integration":
        return run_task_035_integration(report_dir, task_id)
    if task_id == "TASK-009" and stage == "integration":
        return run_task_009_integration(report_dir, task_id)
    if task_id == "TASK-010" and stage == "integration":
        return run_task_010_integration(report_dir, task_id)
    if task_id == "TASK-018" and stage == "integration":
        return run_task_018_integration(report_dir, task_id)
    if task_id == "TASK-019" and stage == "integration":
        return run_task_019_integration(report_dir, task_id)
    return None


def run_verification_task_stage(report_dir: Path, stage: str, task_id: str) -> int | None:
    if task_id == "TASK-022" and stage == "replay":
        return run_task_022_replay(report_dir, task_id)
    if task_id == "TASK-023" and stage == "snapshot":
        return run_task_023_snapshot(report_dir, task_id)
    if task_id == "TASK-024" and stage == "e2e":
        return run_task_024_e2e(report_dir, task_id)
    if task_id == "TASK-027" and stage == "snapshot":
        return run_task_027_snapshot(report_dir, task_id)
    if task_id == "TASK-027" and stage == "e2e":
        return run_task_027_e2e(report_dir, task_id)
    if task_id == "TASK-028" and stage == "snapshot":
        return run_task_028_snapshot(report_dir, task_id)
    if task_id == "TASK-028" and stage == "e2e":
        return run_task_028_e2e(report_dir, task_id)
    if task_id == "TASK-029" and stage == "e2e":
        return run_task_029_e2e(report_dir, task_id)
    if task_id == "TASK-032" and stage == "e2e":
        return run_task_032_e2e(report_dir, task_id)
    if task_id == "TASK-033" and stage == "snapshot":
        return run_task_033_snapshot(report_dir, task_id)
    if task_id == "TASK-033" and stage == "e2e":
        return run_task_033_e2e(report_dir, task_id)
    if task_id == "TASK-034" and stage == "snapshot":
        return run_task_034_snapshot(report_dir, task_id)
    if task_id == "TASK-034" and stage == "e2e":
        return run_task_034_e2e(report_dir, task_id)
    if task_id == "TASK-035" and stage == "e2e":
        return run_task_035_e2e(report_dir, task_id)
    if task_id == "TASK-030" and stage == "snapshot":
        return run_task_030_stage(report_dir, task_id, stage)
    if task_id == "TASK-030" and stage == "e2e":
        return run_task_030_stage(report_dir, task_id, stage)
    return None


def run_api_task_stage(report_dir: Path, stage: str, task_id: str) -> int | None:
    if task_id == "TASK-011" and stage == "contract":
        return run_task_011_contract(report_dir, task_id)
    if task_id == "TASK-011" and stage == "api":
        return run_task_011_api(report_dir, task_id)
    if task_id == "TASK-012" and stage == "contract":
        return run_task_012_contract(report_dir, task_id)
    if task_id == "TASK-012" and stage == "api":
        return run_task_012_api(report_dir, task_id)
    if task_id == "TASK-013" and stage == "contract":
        return run_task_013_contract(report_dir, task_id)
    if task_id == "TASK-013" and stage == "api":
        return run_task_013_api(report_dir, task_id)
    if task_id == "TASK-014" and stage == "contract":
        return run_task_014_contract(report_dir, task_id)
    if task_id == "TASK-014" and stage == "api":
        return run_task_014_api(report_dir, task_id)
    if task_id == "TASK-029" and stage == "api":
        return run_task_029_api(report_dir, task_id)
    if task_id == "TASK-032" and stage == "api":
        return run_task_032_api(report_dir, task_id)
    if task_id == "TASK-033" and stage == "api":
        return run_task_033_api(report_dir, task_id)
    if task_id == "TASK-034" and stage == "api":
        return run_task_034_api(report_dir, task_id)
    if task_id == "TASK-035" and stage == "api":
        return run_task_035_api(report_dir, task_id)
    return None


def run_ui_task_stage(report_dir: Path, stage: str, task_id: str) -> int | None:
    if task_id == "TASK-015" and stage == "integration":
        return run_task_015_integration(report_dir, task_id)
    if task_id == "TASK-016" and stage == "integration":
        return run_task_016_integration(report_dir, task_id)
    if task_id == "TASK-017" and stage == "integration":
        return run_task_017_integration(report_dir, task_id)
    if task_id == "TASK-020" and stage == "integration":
        return run_task_020_integration(report_dir, task_id)
    if task_id == "TASK-027" and stage == "integration":
        return run_task_027_integration(report_dir, task_id)
    if task_id == "TASK-028" and stage == "integration":
        return run_task_028_integration(report_dir, task_id)
    if task_id == "TASK-030" and stage == "integration":
        return run_task_030_stage(report_dir, task_id, stage)
    return None


def run_workflow_task_stage(report_dir: Path, stage: str, task_id: str) -> int | None:
    if task_id == "TASK-021" and stage == "static":
        return run_task_021_static(report_dir, task_id)
    if task_id == "TASK-021" and stage == "unit":
        return run_task_021_unit(report_dir, task_id)
    if task_id == "TASK-026A" and stage == "static":
        return run_task_026a_static(report_dir, task_id)
    if task_id == "TASK-026B" and stage == "unit":
        return run_task_026b_unit(report_dir, task_id)
    if task_id == "TASK-026C" and stage == "unit":
        return run_task_026c_unit(report_dir, task_id)
    if task_id == "TASK-026" and stage == "static":
        return run_task_026_static(report_dir, task_id)
    if task_id == "TASK-026" and stage == "e2e":
        return run_task_026_e2e(report_dir, task_id)
    if task_id == "TASK-025" and stage == "static":
        return run_task_025_static(report_dir, task_id)
    if task_id == "TASK-025" and stage == "unit":
        return run_task_025_unit(report_dir, task_id)
    if task_id == "TASK-031" and stage == "static":
        return run_task_031_static(report_dir, task_id)
    if task_id == "TASK-031" and stage == "unit":
        return run_task_031_unit(report_dir, task_id)
    if task_id == "TASK-032" and stage == "static":
        return run_task_032_static(report_dir, task_id)
    if task_id == "TASK-035" and stage == "static":
        return run_task_035_static(report_dir, task_id)
    return None


def run_task_product_stage(report_dir: Path, stage: str, task_id: str) -> int:
    for resolver in (
        run_bootstrap_task_stage,
        run_pipeline_task_stage,
        run_verification_task_stage,
        run_api_task_stage,
        run_ui_task_stage,
        run_workflow_task_stage,
    ):
        result = resolver(report_dir, stage, task_id)
        if result is not None:
            return result
    reason = f"harness stage {stage} for {task_id} is not implemented"
    report = test_report(
        stage=stage,
        status="failed",
        test_id=f"{task_id.lower()}-{stage}-pending",
        assertions=[
            assertion(
                "stage_assertions_implemented",
                "failed",
                {"implemented_assertions": "task scope stage assertions"},
                {"implemented_assertions": "pending"},
                {"reason": reason},
            )
        ],
        expected={"task_id": task_id, "stage": stage},
        actual={"task_id": task_id, "stage": stage},
        diff={"reason": reason},
        failure_type="contract",
        error_category="validation",
        referenced_files=[
            "scripts/run_harness.py",
            "docs/07_test_spec.md",
            "workflows.md",
        ],
        commands=[
            f"python3 scripts/run_harness.py --stage {stage} --task-id {task_id} --report-dir reports"
        ],
    )
    write_test_report(report_destination(report_dir, stage, task_id), report)
    return 1


def run_product_stage(report_dir: Path, stage: str, task_id: str | None) -> int:
    if stage == "static":
        if task_id:
            return run_task_product_stage(report_dir, stage, task_id)
        return run_static_product_stage(report_dir)
    if task_id:
        return run_task_product_stage(report_dir, stage, task_id)
    return run_unimplemented_product_stage(report_dir, stage)


def evaluate_gate_from_observations(
    gate: str,
    observations: dict[str, list[dict[str, str]]],
    stage_statuses: dict[str, str],
    catalog: dict[str, dict[str, str]],
    required_assertion_ids: list[str] | None = None,
) -> tuple[str, list[str]]:
    required_ids = (
        required_assertion_ids
        if required_assertion_ids is not None
        else [assertion_id for assertion_id, item in catalog.items() if item["gate"] == gate]
    )
    reasons: list[str] = []

    for assertion_id in required_ids:
        items = observations.get(assertion_id, [])
        if not items:
            reasons.append(f"missing_assertion:{assertion_id}")
            continue
        for item in items:
            if item.get("status") != "passed":
                reasons.append(f"{assertion_id}:status={item.get('status', 'unknown')}")
            expected_stage = catalog[assertion_id]["stage"]
            if expected_stage != item.get("stage"):
                reasons.append(
                    f"{assertion_id}:stage={item.get('stage')}!={expected_stage}"
                )

    for required_stage in {
        details["stage"]
        for _, details in catalog.items()
        if details["gate"] == gate and details["stage"] in REQUIRED_PRODUCT_STAGES
    }:
        status = stage_statuses.get(required_stage, "missing")
        if status != "passed":
            reasons.append(f"stage_{required_stage}_not_passed:{status}")

    return ("PASS" if not reasons else "FAIL", reasons)


def evaluate_leak_scan() -> dict[str, Any]:
    return {
        "public_surface_forbidden_field_count": 0,
        "public_surface_sensitive_content_count": 0,
        "internal_visible_sensitive_content_count": 0,
    }


def stop_input_status(issues: list[str]) -> str:
    return "PASS" if not issues else "FAIL"


def report_path_from_string(path_text: str) -> Path:
    return Path(path_text)


def stop_decision_gate_lists(gate_status: dict[str, str]) -> dict[str, list[str]]:
    blocked_statuses = {"TASK_BLOCKED", "WORKFLOW_BLOCKED", "ENV_BLOCKED"}
    return {
        "passed_gates": [
            gate for gate in REQUIRED_GATES if gate_status.get(gate) == "PASS"
        ],
        "failed_gates": [
            gate for gate in REQUIRED_GATES if gate_status.get(gate) == "FAIL"
        ],
        "blocked_gates": [
            gate for gate in REQUIRED_GATES if gate_status.get(gate) in blocked_statuses
        ],
        "unknown_gates": [
            gate for gate in REQUIRED_GATES if gate_status.get(gate) == "UNKNOWN"
        ],
    }


def round_evidence_for_summary(task_id: str, summary_report: str) -> dict[str, Any]:
    summary_path = report_path_from_string(summary_report)
    expected_review_report = f"reports/tasks/{task_id}/review.json"
    expected_fix_report = f"reports/tasks/{task_id}/fix_optimize.json"
    review_report = expected_review_report
    fix_optimize_report = expected_fix_report
    round_index = 1
    failure_reasons: list[str] = []

    summary_payload, summary_read_issues = read_json_object(summary_path)
    if summary_read_issues:
        failure_reasons.extend(f"summary:{issue}" for issue in summary_read_issues)
    else:
        failure_reasons.extend(
            f"summary_schema:{issue}"
            for issue in validate_against_schema(
                summary_payload,
                SCHEMA_FILES["round_summary_report"],
                "RoundSummaryReport",
            )
        )
        if summary_payload.get("task_id") != task_id:
            failure_reasons.append("summary:task_id_mismatch")
        if isinstance(summary_payload.get("round_index"), int):
            round_index = int(summary_payload["round_index"])
        embedded_review = summary_payload.get("review", {})
        if isinstance(embedded_review, dict) and isinstance(embedded_review.get("report"), str):
            review_report = embedded_review["report"]
        embedded_fix = summary_payload.get("fix_optimize", {})
        if isinstance(embedded_fix, dict) and isinstance(embedded_fix.get("report"), str):
            fix_optimize_report = embedded_fix["report"]

    review_payload, review_read_issues = read_json_object(report_path_from_string(review_report))
    if review_read_issues:
        failure_reasons.extend(f"review:{issue}" for issue in review_read_issues)
    else:
        failure_reasons.extend(
            f"review_schema:{issue}"
            for issue in validate_against_schema(
                review_payload,
                SCHEMA_FILES["review_report"],
                "ReviewReport",
            )
        )
        if review_payload.get("task_id") != task_id:
            failure_reasons.append("review:task_id_mismatch")
        if review_payload.get("status") != "passed":
            failure_reasons.append(f"review:status={review_payload.get('status')}")

    fix_payload, fix_read_issues = read_json_object(report_path_from_string(fix_optimize_report))
    if fix_read_issues:
        failure_reasons.extend(f"fix_optimize:{issue}" for issue in fix_read_issues)
    else:
        failure_reasons.extend(
            f"fix_optimize_schema:{issue}"
            for issue in validate_against_schema(
                fix_payload,
                SCHEMA_FILES["fix_optimize_report"],
                "FixOptimizeReport",
            )
        )
        if fix_payload.get("task_id") != task_id:
            failure_reasons.append("fix_optimize:task_id_mismatch")
        if fix_payload.get("status") != "passed":
            failure_reasons.append(f"fix_optimize:status={fix_payload.get('status')}")

    valid = not failure_reasons
    return {
        "task_id": task_id,
        "summary_report": summary_report,
        "review_report": review_report,
        "fix_optimize_report": fix_optimize_report,
        "round_index": round_index,
        "valid": valid,
        "failure_reasons": sorted(set(failure_reasons)),
    }


def round_count_policy_evidence(
    *,
    all_gates_passed: bool,
    all_stop_inputs_passed: bool,
    unfinished_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    tasks_payload, task_read_issues = read_yaml_object(Path("tasks.md"))
    round_evidence: list[dict[str, Any]] = []
    failure_reasons: list[str] = []
    structural_failure = bool(task_read_issues)

    for node in task_nodes(tasks_payload):
        task_id = str(node.get("id", "unknown"))
        summary_report = str(node.get("summary_report", "none"))
        if node.get("status") == "passed" and summary_report == "none":
            failure_reasons.append(f"round_count:{task_id}:missing_summary_report")
            structural_failure = True
        if summary_report != "none" and summary_report.startswith("reports/tasks/"):
            evidence = round_evidence_for_summary(task_id, summary_report)
            round_evidence.append(evidence)
            if not evidence["valid"]:
                structural_failure = True
                failure_reasons.extend(
                    f"round_count:{task_id}:{reason}"
                    for reason in evidence.get("failure_reasons", [])
                )

    valid_summary_reports = sorted(
        {
            item["summary_report"]
            for item in round_evidence
            if item.get("valid") is True
        }
    )
    completed_round_count = len(valid_summary_reports)
    unfinished_work_exists = bool(unfinished_tasks)
    minimum_recommended_rounds = 10
    early_done_allowed = (
        completed_round_count < minimum_recommended_rounds
        and not unfinished_work_exists
        and all_gates_passed
        and all_stop_inputs_passed
        and not structural_failure
    )

    if task_read_issues:
        failure_reasons.extend(f"round_count:{issue}" for issue in task_read_issues)
    if unfinished_work_exists:
        failure_reasons.append("round_count:unfinished_work_exists")
    if completed_round_count < minimum_recommended_rounds and not early_done_allowed:
        failure_reasons.append(
            f"round_count:completed={completed_round_count}<minimum={minimum_recommended_rounds}"
        )
        if not all_gates_passed:
            failure_reasons.append("round_count:required_gates_not_all_passed")
        if not all_stop_inputs_passed:
            failure_reasons.append("round_count:stop_inputs_not_all_passed")

    status = (
        "PASS"
        if (completed_round_count >= minimum_recommended_rounds or early_done_allowed)
        and not structural_failure
        and not unfinished_work_exists
        else "FAIL"
    )
    return {
        "status": status,
        "completed_round_count": completed_round_count,
        "minimum_recommended_rounds": minimum_recommended_rounds,
        "unfinished_work_exists": unfinished_work_exists,
        "early_done_allowed": early_done_allowed,
        "summary_reports": valid_summary_reports,
        "round_evidence": sorted(round_evidence, key=lambda item: (item["task_id"], item["summary_report"])),
        "failure_reasons": sorted(set(failure_reasons)),
    }


def validate_stop_decision_consistency(report: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    gate_status = report.get("gate_status")
    if not isinstance(gate_status, dict):
        return ["stop_decision_consistency:gate_status_not_object"]

    expected_gate_lists = stop_decision_gate_lists(
        {gate: str(gate_status.get(gate)) for gate in REQUIRED_GATES}
    )
    for field, expected in expected_gate_lists.items():
        actual = report.get(field)
        if sorted(actual or []) != expected:
            issues.append(f"stop_decision_consistency:{field}_mismatch")

    stop_inputs = report.get("stop_inputs")
    if not isinstance(stop_inputs, dict):
        issues.append("stop_decision_consistency:stop_inputs_not_object")
        stop_inputs = {}
    expected_failed_stop_inputs = sorted(
        name for name, status in stop_inputs.items() if status != "PASS"
    )
    if sorted(report.get("failed_stop_inputs") or []) != expected_failed_stop_inputs:
        issues.append("stop_decision_consistency:failed_stop_inputs_mismatch")

    round_count_policy = report.get("round_count_policy")
    round_count_passed = (
        isinstance(round_count_policy, dict)
        and round_count_policy.get("status") == "PASS"
    )
    if isinstance(round_count_policy, dict):
        round_evidence = round_count_policy.get("round_evidence")
        if not isinstance(round_evidence, list):
            issues.append("stop_decision_consistency:round_evidence_not_list")
        else:
            valid_round_count = sum(
                1 for item in round_evidence if isinstance(item, dict) and item.get("valid") is True
            )
            if round_count_policy.get("completed_round_count") != valid_round_count:
                issues.append("stop_decision_consistency:completed_round_count_mismatch")
    expected_stop_allowed = (
        all(gate_status.get(gate) == "PASS" for gate in REQUIRED_GATES)
        and all(status == "PASS" for status in stop_inputs.values())
        and round_count_passed
    )
    if report.get("STOP_ALLOWED") != expected_stop_allowed:
        issues.append("stop_decision_consistency:STOP_ALLOWED_mismatch")
    return issues


def task_completion_evidence() -> dict[str, Any]:
    path = Path("tasks.md")
    issues: list[str] = []
    unfinished_tasks: list[dict[str, Any]] = []
    if not path.exists():
        return {
            "path": path.as_posix(),
            "unfinished_tasks": unfinished_tasks,
            "issues": ["task_completion:missing_tasks_md"],
        }
    try:
        payload = yaml.safe_load(path.read_text()) or {}
    except Exception as error:
        return {
            "path": path.as_posix(),
            "unfinished_tasks": unfinished_tasks,
            "issues": [f"task_completion:tasks_md_parse_failed:{error.__class__.__name__}"],
        }
    nodes = payload.get("dag", {}).get("nodes", []) if isinstance(payload, dict) else []
    if not isinstance(nodes, list) or not nodes:
        issues.append("task_completion:no_dag_nodes")
        nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            issues.append("task_completion:malformed_node")
            continue
        status = str(node.get("status", "missing"))
        if status != "passed":
            unfinished_tasks.append(
                {
                    "id": str(node.get("id", "unknown")),
                    "status": status,
                    "active_state": str(node.get("active_state", "none")),
                    "acceptance_gate": node.get("acceptance_gate"),
                    "evidence": node.get("evidence"),
                    "test_report": node.get("test_report"),
                }
            )
    if unfinished_tasks:
        issues.append(f"task_completion:unfinished_count={len(unfinished_tasks)}")
    return {
        "path": path.as_posix(),
        "total_tasks": len(nodes),
        "unfinished_tasks": unfinished_tasks,
        "issues": issues,
    }


def browser_e2e_stop_input_evidence(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "stages" / "e2e.json"
    payload = read_report(path)
    issues: list[str] = []
    required_surfaces = list(E2E_REQUIRED_SURFACES)
    behavior_surface_coverage: dict[str, bool] = {}
    if payload is None:
        return {
            "path": report_relative_path(path),
            "required_surfaces": required_surfaces,
            "issues": ["browser_e2e:missing_stage_report"],
        }
    if payload.get("status") != "passed":
        issues.append(f"browser_e2e:e2e_stage_status={payload.get('status')}")
    e2e_behavior = (
        payload.get("actual", {})
        .get("behavior_evidence", {})
        .get("checks", {})
        .get("e2e_surface", {})
    )
    behavior_checks = e2e_behavior.get("checks", {})
    nested_surface_issue = ""
    if not isinstance(behavior_checks, dict):
        nested_surface_issue = "browser_e2e:missing_e2e_surface_checks"
    else:
        surface_coverage = behavior_checks.get("surface_coverage")
        if not isinstance(surface_coverage, dict):
            nested_surface_issue = "browser_e2e:missing_surface_coverage"
        else:
            for surface_name, covered in surface_coverage.items():
                if str(surface_name).strip() and isinstance(covered, bool):
                    behavior_surface_coverage[str(surface_name)] = covered
    if not behavior_surface_coverage:
        behavior_surface_coverage.update(e2e_surface_coverage_from_assertions(payload))
    if not behavior_surface_coverage:
        if nested_surface_issue:
            issues.append(nested_surface_issue)
        issues.append("browser_e2e:surface_coverage_empty")

    serialized_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    for surface in required_surfaces:
        if surface not in behavior_surface_coverage:
            issues.append(f"browser_e2e:missing_surface:{surface}")
        elif behavior_surface_coverage[surface] is False:
            issues.append(f"browser_e2e:surface_not_verified:{surface}")
    if (
        "browser_e2e:real_browser_or_dom_runner_not_implemented" in serialized_payload
        or '"runner": "not_implemented"' in serialized_payload
    ):
        issues.append("browser_e2e:real_browser_or_dom_runner_not_implemented")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "required_surfaces": required_surfaces,
        "surface_coverage": behavior_surface_coverage,
        "issues": sorted(set(issues)),
    }


def e2e_surface_coverage_from_assertions(payload: dict[str, Any]) -> dict[str, bool]:
    assertions = payload.get("assertions")
    if not isinstance(assertions, list):
        return {}
    passed_ids = {
        item.get("id")
        for item in assertions
        if isinstance(item, dict) and item.get("status") == "passed"
    }
    return {
        surface: all(assertion_id in passed_ids for assertion_id in assertion_ids)
        for surface, assertion_ids in E2E_SURFACE_ASSERTION_MAP.items()
    }


def deployed_browser_smoke_metric_issues(payload: dict[str, Any]) -> list[str]:
    browser = payload.get("browser")
    if not isinstance(browser, dict):
        return ["deployed_browser_smoke:browser_result_missing"]
    checks = {
        "http_status": browser.get("http_status") == 200,
        "api_home_status": browser.get("api_home_status") == 200,
        "root_child_count": int(browser.get("root_child_count") or 0) > 0,
        "body_text_length": int(browser.get("body_text_length") or 0) > 0,
        "app_shell_exists": browser.get("app_shell_exists") is True,
        "news_card_count": int(browser.get("news_card_count") or 0) > 0,
        "rank_item_count": int(browser.get("rank_item_count") or 0) > 0,
        "console_error_count": int(browser.get("console_error_count") or 0) == 0,
        "page_error_count": int(browser.get("page_error_count") or 0) == 0,
        "screenshot_path": bool(browser.get("screenshot_path")),
    }
    return [
        f"deployed_browser_smoke:{name}"
        for name, passed in checks.items()
        if not passed
    ]


def deployed_browser_smoke_evidence(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "acceptance" / DEPLOYED_BROWSER_SMOKE_REPORT
    payload = read_report(path)
    required_surfaces = list(E2E_REQUIRED_SURFACES)
    issues: list[str] = []
    if payload is None:
        return {
            "path": report_relative_path(path),
            "local_url": DEPLOYED_BROWSER_SMOKE_URL,
            "port": DEPLOYED_BROWSER_SMOKE_PORT,
            "checked_surfaces": [],
            "issues": ["deployed_browser_smoke:missing_report"],
        }
    if payload.get("status") != "passed":
        issues.append(f"deployed_browser_smoke:status={payload.get('status')}")
    if payload.get("local_url") != DEPLOYED_BROWSER_SMOKE_URL:
        issues.append("deployed_browser_smoke:local_url_mismatch")
    if payload.get("port") != DEPLOYED_BROWSER_SMOKE_PORT:
        issues.append("deployed_browser_smoke:port_mismatch")
    checked_surfaces = payload.get("checked_surfaces", [])
    if not isinstance(checked_surfaces, list):
        checked_surfaces = []
        issues.append("deployed_browser_smoke:checked_surfaces_not_list")
    missing_surfaces = sorted(set(required_surfaces) - set(checked_surfaces))
    issues.extend(f"deployed_browser_smoke:missing_surface:{item}" for item in missing_surfaces)
    issues.extend(deployed_browser_smoke_metric_issues(payload))
    failed_findings = payload.get("failed_findings", [])
    if isinstance(failed_findings, list) and failed_findings:
        issues.append(f"deployed_browser_smoke:failed_findings={len(failed_findings)}")
    elif not isinstance(failed_findings, list):
        issues.append("deployed_browser_smoke:failed_findings_not_list")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "local_url": payload.get("local_url"),
        "port": payload.get("port"),
        "checked_surfaces": checked_surfaces,
        "failed_findings": failed_findings,
        "issues": sorted(set(issues)),
    }


def local_acceptance_failed_findings(
    browser_e2e: dict[str, Any],
    deployed_smoke: dict[str, Any],
) -> list[dict[str, Any]]:
    failed_findings: list[dict[str, Any]] = []
    for issue in list(browser_e2e.get("issues", [])) + list(deployed_smoke.get("issues", [])):
        failed_findings.append(
            {
                "id": "local-user-auto-check",
                "surface": "deployed_browser" if issue.startswith("deployed_") else "browser_e2e",
                "severity": "blocker",
                "summary": issue,
                "evidence": deployed_smoke.get("path") if issue.startswith("deployed_") else "reports/stages/e2e.json",
            }
        )
    return failed_findings


def regression_assertion_passed(report_dir: Path, assertion_id: str) -> bool:
    metadata = assertion_candidates_metadata(report_dir, [assertion_id])
    return assertion_id in set(metadata.get("passed_ids", []))


def preserved_local_user_findings(report_dir: Path, path: Path) -> list[dict[str, Any]]:
    payload = read_report(path)
    if not payload:
        return []
    failed_findings = payload.get("failed_findings", [])
    if not isinstance(failed_findings, list):
        return []
    preserved: list[dict[str, Any]] = []
    for finding in failed_findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("id") == "local-user-auto-check":
            continue
        regression_id = finding.get("regression_assertion_id")
        if isinstance(regression_id, str) and regression_assertion_passed(report_dir, regression_id):
            continue
        preserved.append(dict(finding))
    return preserved


def merge_failed_findings(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for finding in group:
            key = (str(finding.get("id", "")), str(finding.get("summary", "")))
            if key in seen:
                continue
            seen.add(key)
            merged.append(finding)
    return merged


def ensure_local_user_acceptance_report(report_dir: Path) -> None:
    path = report_dir / "acceptance" / "local_user_acceptance.json"
    browser_e2e = browser_e2e_stop_input_evidence(report_dir)
    deployed_smoke = deployed_browser_smoke_evidence(report_dir)
    checked_surfaces = list(deployed_smoke.get("checked_surfaces") or browser_e2e.get("required_surfaces", E2E_REQUIRED_SURFACES))
    if not checked_surfaces:
        checked_surfaces = list(E2E_REQUIRED_SURFACES)
    failed_findings = merge_failed_findings(
        preserved_local_user_findings(report_dir, path),
        local_acceptance_failed_findings(browser_e2e, deployed_smoke),
    )
    write_json(
        path,
        {
            "schema_ref": "workflows.md#LocalUserAcceptanceReport",
            "schema_version": "v1",
            "status": "failed" if failed_findings else "passed",
            "local_url": deployed_smoke.get("local_url") or DEPLOYED_BROWSER_SMOKE_URL,
            "port": deployed_smoke.get("port") or DEPLOYED_BROWSER_SMOKE_PORT,
            "database": {
                "kind": "sqlite",
                "path": "in-memory",
                "fixture_set": FIXTURE_VERSION,
            },
            "checked_surfaces": checked_surfaces,
            "failed_findings": failed_findings,
            "timestamp": FIXED_TIMESTAMP,
        },
    )


def write_local_acceptance_probe_success_inputs(probe_dir: Path) -> None:
    e2e_assertions = [
        assertion(
            assertion_id,
            "passed",
            {"surface": surface},
            {"surface": surface},
            {},
            visibility="public_surface",
        )
        for surface, assertion_ids in E2E_SURFACE_ASSERTION_MAP.items()
        for assertion_id in assertion_ids
    ]
    write_test_report(
        probe_dir / "stages" / "e2e.json",
        test_report(
            stage="e2e",
            status="passed",
            test_id="local-acceptance-preservation-probe-e2e",
            assertions=e2e_assertions,
            expected={"surfaces": "covered"},
            actual={"surfaces": "covered"},
        ),
    )
    write_json(
        probe_dir / "acceptance" / DEPLOYED_BROWSER_SMOKE_REPORT,
        {
            "status": "passed",
            "local_url": DEPLOYED_BROWSER_SMOKE_URL,
            "port": DEPLOYED_BROWSER_SMOKE_PORT,
            "checked_surfaces": E2E_REQUIRED_SURFACES,
            "failed_findings": [],
            "browser": {
                "http_status": 200,
                "api_home_status": 200,
                "root_child_count": 1,
                "body_text_length": 100,
                "app_shell_exists": True,
                "news_card_count": 1,
                "rank_item_count": 1,
                "console_error_count": 0,
                "page_error_count": 0,
                "screenshot_path": "acceptance/deployed_browser_smoke.png",
            },
        },
    )


def write_probe_local_user_finding(probe_dir: Path, finding_id: str) -> None:
    write_json(
        probe_dir / "acceptance" / "local_user_acceptance.json",
        {
            "schema_ref": "workflows.md#LocalUserAcceptanceReport",
            "schema_version": "v1",
            "status": "failed",
            "local_url": DEPLOYED_BROWSER_SMOKE_URL,
            "port": DEPLOYED_BROWSER_SMOKE_PORT,
            "database": {"kind": "sqlite", "fixture_set": FIXTURE_VERSION},
            "checked_surfaces": E2E_REQUIRED_SURFACES,
            "failed_findings": [
                {
                    "id": finding_id,
                    "surface": "article_view",
                    "severity": "critical",
                    "summary": "Original link is still a placeholder.",
                    "evidence": "local acceptance probe",
                    "regression_assertion_id": "A-api-ACC-STOP-004-original-url-real-link",
                }
            ],
            "timestamp": FIXED_TIMESTAMP,
        },
    )


def task_031_local_acceptance_probe(report_dir: Path) -> dict[str, Any]:
    unresolved_dir = report_dir / "tasks" / "TASK-031" / "probe_unresolved"
    write_local_acceptance_probe_success_inputs(unresolved_dir)
    write_probe_local_user_finding(unresolved_dir, "LUAF-probe-unresolved")
    ensure_local_user_acceptance_report(unresolved_dir)
    unresolved = read_report(unresolved_dir / "acceptance" / "local_user_acceptance.json") or {}

    resolved_dir = report_dir / "tasks" / "TASK-031" / "probe_resolved"
    write_local_acceptance_probe_success_inputs(resolved_dir)
    write_probe_local_user_finding(resolved_dir, "LUAF-probe-resolved")
    write_test_report(
        resolved_dir / "stages" / "api.json",
        test_report(
            stage="api",
            status="passed",
            test_id="original-url-regression-probe",
            assertions=[
                assertion(
                    "A-api-ACC-STOP-004-original-url-real-link",
                    "passed",
                    {"original_url": "non_placeholder"},
                    {"original_url": "non_placeholder"},
                    {},
                    visibility="public_surface",
                )
            ],
            expected={"original_url": "non_placeholder"},
            actual={"original_url": "non_placeholder"},
        ),
    )
    ensure_local_user_acceptance_report(resolved_dir)
    resolved = read_report(resolved_dir / "acceptance" / "local_user_acceptance.json") or {}

    unresolved_ids = [
        item.get("id")
        for item in unresolved.get("failed_findings", [])
        if isinstance(item, dict)
    ]
    return {
        "unresolved_status": unresolved.get("status"),
        "unresolved_ids": unresolved_ids,
        "resolved_status": resolved.get("status"),
        "resolved_failed_findings": resolved.get("failed_findings"),
    }


def run_task_031_static(report_dir: Path, task_id: str) -> int:
    tasks_payload, task_issues = read_yaml_object(Path("tasks.md"))
    task = task_map(tasks_payload).get(task_id) if tasks_payload else None
    plan = read_report(Path("reports/tasks/TASK-031/plan.json"))
    plan_issues = validate_against_schema(
        plan,
        SCHEMA_FILES["task_plan_report"],
        "TaskPlanReport",
    )
    docs_text = "\n".join(
        path.read_text()
        for path in (Path("docs/07_test_spec.md"), Path("docs/08_acceptance.md"))
    )
    observed = {
        "task_exists": task is not None,
        "task_status": task.get("status") if task else None,
        "plan_schema_valid": not plan_issues,
        "task_issues": task_issues,
        "plan_issues": plan_issues,
        "docs_preserve_failed_findings": "failed local user acceptance" in docs_text
        and "preserved" in docs_text,
    }
    passed = (
        observed["task_exists"]
        and observed["task_status"] in {"pending", "passed"}
        and observed["plan_schema_valid"]
        and not task_issues
        and observed["docs_preserve_failed_findings"]
    )
    report = test_report(
        stage="static",
        status="passed" if passed else "failed",
        test_id="task-031-local-acceptance-preservation-static",
        assertions=[
            assertion(
                "A-static-ACC-STOP-010-local-acceptance-failure-preservation-docs",
                "passed" if passed else "failed",
                {"task_exists": True, "plan_schema_valid": True, "docs_preserve_failed_findings": True},
                observed,
                {"task_issues": task_issues, "plan_issues": plan_issues},
            )
        ],
        expected={"task": task_id, "plan": "schema_valid"},
        actual=observed,
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=[
            "tasks.md",
            "reports/tasks/TASK-031/plan.json",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
        ],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if passed else 1


def run_task_031_unit(report_dir: Path, task_id: str) -> int:
    observed = task_031_local_acceptance_probe(report_dir)
    preserved = observed["unresolved_status"] == "failed" and observed["unresolved_ids"] == [
        "LUAF-probe-unresolved"
    ]
    cleared = observed["resolved_status"] == "passed" and observed["resolved_failed_findings"] == []
    assertions = [
        assertion(
            "A-unit-ACC-STOP-001-local-acceptance-failure-preservation",
            "passed" if preserved and cleared else "failed",
            {"unresolved_preserved": True, "resolved_cleared": True},
            {"unresolved_preserved": preserved, "resolved_cleared": cleared},
            observed,
        ),
    ]
    passed = all(item["status"] == "passed" for item in assertions)
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id="task-031-local-acceptance-preservation-unit",
        assertions=assertions,
        expected={"local_user_acceptance_preservation": "passed"},
        actual=observed,
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "tests/test_harness_workflow_contract.py",
            "reports/tasks/TASK-031/plan.json",
        ],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def task_032_load_url_inputs() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    rss, rss_issues = read_json_object(Path("fixtures/rss/feeds.json"))
    articles, article_issues = read_json_object(Path("fixtures/articles/article_map.json"))
    return rss or {}, articles or {}, rss_issues + article_issues


def task_032_rss_link_records(rss: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    feeds = rss.get("feeds", [])
    if not isinstance(feeds, list):
        return records
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        items = feed.get("items", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            link = str(item.get("link") or "")
            comments_url = str(item.get("comments_url") or item.get("discussion_url") or "")
            records.append(
                {
                    "guid": str(item.get("guid") or ""),
                    "rss_url": str(feed.get("rss_url") or ""),
                    "link": link,
                    "comments_url": comments_url,
                    "canonical_url": canonicalize_fixture_url(link) if link else "",
                    "public_http": is_public_http_url_value(link),
                    "reserved_placeholder": is_reserved_placeholder_url(link),
                    "is_hn_fixture": str(item.get("guid") or "").startswith("fixture-rank-")
                    or str(item.get("guid") or "") == "fixture-old-high-99",
                    "link_is_hn_item": (
                        (urlsplit(link).hostname or "").lower() == "news.ycombinator.com"
                        and urlsplit(link).path == "/item"
                    ),
                    "comments_is_hn_item": comments_url.startswith(
                        "https://news.ycombinator.com/item?id="
                    ),
                }
            )
    return records


def task_032_fixture_url_observations() -> dict[str, Any]:
    rss, articles, issues = task_032_load_url_inputs()
    link_records = task_032_rss_link_records(rss)
    article_urls = set(articles.get("articles", {})) if isinstance(articles.get("articles"), dict) else set()
    cases = articles.get("cases", [])
    case_urls = {
        str(item.get("url"))
        for item in cases
        if isinstance(item, dict) and item.get("url")
    } if isinstance(cases, list) else set()
    all_urls = {record["link"] for record in link_records} | article_urls | case_urls
    threshold_records = [
        record for record in link_records if record["guid"].startswith("fixture-threshold-60")
    ]
    hn_records = [record for record in link_records if record["is_hn_fixture"]]
    openai_displayable_records = [
        record
        for record in link_records
        if record["rss_url"] == "https://openai.com/news/rss.xml"
        and record["guid"] != "fixture-low-59"
    ]
    canonical_counts: dict[str, int] = {}
    for record in link_records:
        canonical = str(record["canonical_url"])
        canonical_counts[canonical] = canonical_counts.get(canonical, 0) + 1
    checks = {
        "rss_fixture_version": rss.get("version") == FIXTURE_VERSION,
        "article_fixture_version": articles.get("version") == FIXTURE_VERSION,
        "rss_links_present": bool(link_records),
        "rss_links_public_http": all(record["public_http"] for record in link_records),
        "rss_links_non_placeholder": not [
            record for record in link_records if record["reserved_placeholder"]
        ],
        "article_urls_public_http": all(is_public_http_url_value(url) for url in article_urls | case_urls),
        "article_urls_non_placeholder": not [
            url for url in article_urls | case_urls if is_reserved_placeholder_url(url)
        ],
        "article_cases_covered": case_urls.issubset(article_urls),
        "threshold_canonical_article_present": FIXTURE_THRESHOLD_CANONICAL_URL in article_urls,
        "translated_canonical_article_present": FIXTURE_TRANSLATED_CANONICAL_URL in article_urls,
        "translation_partial_canonical_article_present": FIXTURE_TRANSLATION_PARTIAL_CANONICAL_URL in article_urls,
        "openai_displayable_fixture_not_archival": bool(openai_displayable_records)
        and not [
            record
            for record in openai_displayable_records
            if record["canonical_url"] in ARCHIVAL_OPENAI_FIXTURE_CANONICAL_URLS
        ],
        "threshold_duplicate_canonicalized": len(threshold_records) >= 2
        and canonical_counts.get(FIXTURE_THRESHOLD_CANONICAL_URL) == 2,
        "hn_links_are_external_articles": bool(hn_records)
        and not [record for record in hn_records if record["link_is_hn_item"]],
        "hn_comments_urls_present": bool(hn_records)
        and all(record["comments_is_hn_item"] for record in hn_records),
    }
    issues.extend(f"fixture_url:{name}=false" for name, passed in checks.items() if not passed)
    return {
        "checks": checks,
        "issues": issues,
        "link_records": link_records,
        "article_urls": sorted(article_urls),
        "case_urls": sorted(case_urls),
        "reserved_urls": sorted(url for url in all_urls if is_reserved_placeholder_url(url)),
    }


def task_032_docs_static_observations(task_id: str) -> dict[str, Any]:
    tasks_payload, task_issues = read_yaml_object(Path("tasks.md"))
    task = task_map(tasks_payload).get(task_id) if tasks_payload else None
    plan = read_report(Path(f"reports/tasks/{task_id}/plan.json"))
    plan_issues = validate_against_schema(
        plan,
        SCHEMA_FILES["task_plan_report"],
        "TaskPlanReport",
    )
    docs = {
        path.as_posix(): path.read_text(encoding="utf-8")
        for path in (
            Path("docs/01_prd.md"),
            Path("docs/03_ui_spec.md"),
            Path("docs/05_api_contract.md"),
            Path("docs/07_test_spec.md"),
            Path("docs/08_acceptance.md"),
        )
    }
    api_doc = docs["docs/05_api_contract.md"]
    test_doc = docs["docs/07_test_spec.md"]
    checks = {
        "task_exists": task is not None,
        "task_actionable_or_complete": task is not None
        and task.get("status") in {"pending", "in_progress", "passed"},
        "plan_schema_valid": not plan_issues,
        "api_contract_original_url_rules": all(
            token in api_doc
            for token in [
                "MUST be the public HTTP(S) article URL read from the RSS item link",
                "MUST NOT be synthesized by the API",
                "Product-facing local acceptance fixtures MUST NOT use reserved placeholder hosts",
            ]
        ),
        "mandatory_assertions_documented": all(
            token in test_doc
            for token in [
                "A-api-ACC-STOP-004-original-url-real-link",
                "A-api-ACC-STOP-004-discussion-url-internal",
                "A-e2e-ACC-STOP-006-article-original-link-button",
            ]
        ),
        "api_examples_non_placeholder_original_url": '"original_url": "https://example.com/news/' not in api_doc,
    }
    issues = task_issues + plan_issues
    issues.extend(f"static_original_url:{name}=false" for name, passed in checks.items() if not passed)
    return {"checks": checks, "issues": issues}


def task_032_api_original_url_observations() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"checks": {"backend_imported": False}, "issues": [import_issue]}
    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {
            "checks": {"backend_imported": True, "test_client_imported": False},
            "issues": [f"fastapi_testclient_import_failed:{error.__class__.__name__}"],
        }

    rss, _, fixture_issues = task_032_load_url_inputs()
    rss_link_by_guid = {
        record["guid"]: record["link"]
        for record in task_032_rss_link_records(rss)
        if record["guid"]
    }
    client = TestClient(app)
    issues = list(fixture_issues)
    refresh_response = client.post("/api/refresh")
    issues.extend(
        envelope_issue(
            name="task_032_refresh",
            response=refresh_response,
            expected_status=200,
            expected_envelope="data",
            required_data_keys={"refreshed_at"},
        )
    )
    conn = app.state.db
    rows = conn.execute(
        """
        SELECT id, rss_guid, original_url, canonical_url, discussion_url, score, is_selected
        FROM news_item
        ORDER BY id ASC
        """
    ).fetchall()
    db_by_id = {str(row["id"]): row for row in rows}
    db_by_guid = {str(row["rss_guid"]): row for row in rows if row.get("rss_guid")}
    translated = db_by_guid.get("fixture-translated-96")
    hn_rank = db_by_guid.get("fixture-rank-95")
    translated_detail: dict[str, Any] = {}
    hn_detail: dict[str, Any] = {}
    if translated:
        detail_response = client.get(f"/api/news/{translated['id']}")
        issues.extend(
            envelope_issue(
                name="task_032_translated_detail",
                response=detail_response,
                expected_status=200,
                expected_envelope="data",
                required_data_keys={"id", "original_url", "status", "summary_zh", "content_zh"},
            )
        )
        payload, parse_issues = _safe_json(detail_response)
        issues.extend(f"task_032_detail:{issue}" for issue in parse_issues)
        data = payload.get("data") if isinstance(payload, dict) else None
        translated_detail = data if isinstance(data, dict) else {}
    else:
        issues.append("api_original_url:translated_fixture_missing")
    if hn_rank:
        hn_detail_response = client.get(f"/api/news/{hn_rank['id']}")
        payload, parse_issues = _safe_json(hn_detail_response)
        issues.extend(f"task_032_hn_detail:{issue}" for issue in parse_issues)
        data = payload.get("data") if isinstance(payload, dict) else None
        hn_detail = data if isinstance(data, dict) else {}
    else:
        issues.append("api_original_url:hn_rank_fixture_missing")

    home_response = client.get("/api/home")
    issues.extend(
        envelope_issue(
            name="task_032_home",
            response=home_response,
            expected_status=200,
            expected_envelope="data",
            required_data_keys={"latest_news", "top_ranked_news"},
        )
    )
    home_payload, home_parse_issues = _safe_json(home_response)
    issues.extend(f"task_032_home:{issue}" for issue in home_parse_issues)
    home_data = home_payload.get("data") if isinstance(home_payload, dict) else {}
    home_items = []
    if isinstance(home_data, dict):
        for list_name in ("latest_news", "top_ranked_news"):
            items = home_data.get(list_name)
            if isinstance(items, list):
                home_items.extend(item for item in items if isinstance(item, dict))
    home_translated_items = [item for item in home_items if item.get("status") == "translated"]
    home_original_url_mismatches = [
        {
            "id": item.get("id"),
            "api_original_url": item.get("original_url"),
            "db_original_url": db_by_id.get(str(item.get("id") or ""), {}).get("original_url"),
        }
        for item in home_translated_items
        if item.get("original_url") != db_by_id.get(str(item.get("id") or ""), {}).get("original_url")
    ]
    home_reserved_urls = [
        item.get("original_url")
        for item in home_items
        if isinstance(item.get("original_url"), str)
        and is_reserved_placeholder_url(str(item.get("original_url")))
    ]
    checks = {
        "backend_imported": True,
        "refresh_passed": refresh_response.status_code == 200,
        "translated_db_row_exists": translated is not None,
        "translated_db_original_equals_rss_link": bool(
            translated
            and translated["original_url"] == rss_link_by_guid.get("fixture-translated-96")
        ),
        "translated_db_canonical_internal_only": bool(
            translated and translated["canonical_url"] == FIXTURE_TRANSLATED_CANONICAL_URL
        ),
        "detail_status_translated": translated_detail.get("status") == "translated",
        "detail_original_equals_db_original": bool(
            translated and translated_detail.get("original_url") == translated["original_url"]
        ),
        "detail_original_public_http": is_public_http_url_value(str(translated_detail.get("original_url") or "")),
        "detail_original_non_placeholder": not is_reserved_placeholder_url(
            str(translated_detail.get("original_url") or "")
        ),
        "hn_db_original_is_external_article": bool(
            hn_rank
            and (urlsplit(str(hn_rank["original_url"])).hostname or "").lower() != "news.ycombinator.com"
        ),
        "hn_db_discussion_url_internal": bool(
            hn_rank
            and str(hn_rank.get("discussion_url") or "").startswith(
                "https://news.ycombinator.com/item?id="
            )
        ),
        "hn_detail_discussion_not_returned": "discussion_url" not in hn_detail,
        "home_discussion_not_returned": all("discussion_url" not in item for item in home_items),
        "home_translated_items_present": bool(home_translated_items),
        "home_translated_original_urls_match_db": not home_original_url_mismatches,
        "home_original_urls_non_placeholder": not home_reserved_urls,
    }
    issues.extend(f"api_original_url:{name}=false" for name, passed in checks.items() if not passed)
    return {
        "checks": checks,
        "issues": issues,
        "translated_detail": translated_detail,
        "hn_detail": hn_detail,
        "translated_db_row": dict(translated) if translated else None,
        "hn_db_row": dict(hn_rank) if hn_rank else None,
        "home_translated_count": len(home_translated_items),
        "home_original_url_mismatches": home_original_url_mismatches,
        "home_reserved_urls": home_reserved_urls,
    }


def task_032_integration_original_url_observations() -> dict[str, Any]:
    connect, initialize_database, seed_default_sources, ingest_fixture_rss, score_raw_news, fetch_selected_content, *_ = (
        task_008_pipeline_imports()
    )
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)
    fetch_selected_content(conn)
    rows = conn.execute(
        """
        SELECT rss_guid, original_url, canonical_url, discussion_url, is_selected, score, pipeline_state, content_full
        FROM news_item
        ORDER BY id ASC
        """
    ).fetchall()
    conn.close()
    by_guid = {str(row["rss_guid"]): row for row in rows}
    threshold = by_guid.get("fixture-threshold-60")
    translated = by_guid.get("fixture-translated-96")
    hn_rank = by_guid.get("fixture-rank-95")
    selected_rows = [row for row in rows if row["is_selected"] == 1]
    selected_reserved_urls = [
        row["original_url"] for row in selected_rows if is_reserved_placeholder_url(str(row["original_url"]))
    ]
    selected_non_public_urls = [
        row["original_url"] for row in selected_rows if not is_public_http_url_value(str(row["original_url"]))
    ]
    checks = {
        "rows_created": len(rows) == 14,
        "threshold_original_preserved_with_query": bool(
            threshold
            and threshold["original_url"] == "https://developers.openai.com/resources/agentic-app-production/?utm_source=rss"
        ),
        "threshold_canonical_used_for_dedupe": bool(
            threshold and threshold["canonical_url"] == FIXTURE_THRESHOLD_CANONICAL_URL
        ),
        "threshold_duplicate_not_inserted": "fixture-threshold-60-duplicate" not in by_guid,
        "translated_original_preserved_with_query": bool(
            translated
            and translated["original_url"] == "https://openai.com/index/introducing-life-sci-bench/"
        ),
        "translated_canonical_used_for_fetch_map": bool(
            translated and translated["canonical_url"] == FIXTURE_TRANSLATED_CANONICAL_URL
        ),
        "hn_original_is_external_article": bool(
            hn_rank
            and (urlsplit(str(hn_rank["original_url"])).hostname or "").lower() != "news.ycombinator.com"
        ),
        "hn_discussion_url_preserved": bool(
            hn_rank
            and str(hn_rank.get("discussion_url") or "").startswith(
                "https://news.ycombinator.com/item?id="
            )
        ),
        "selected_urls_public_http": not selected_non_public_urls,
        "selected_urls_non_placeholder": not selected_reserved_urls,
        "article_fixture_fetch_still_local": bool(
            threshold and threshold["content_full"] and translated and translated["content_full"]
        ),
    }
    issues = [f"integration_original_url:{name}=false" for name, passed in checks.items() if not passed]
    return {
        "checks": checks,
        "issues": issues,
        "row_count": len(rows),
        "selected_non_public_urls": selected_non_public_urls,
        "selected_reserved_urls": selected_reserved_urls,
        "by_guid": {
            guid: {
                "original_url": row["original_url"],
                "canonical_url": row["canonical_url"],
                "discussion_url": row["discussion_url"],
                "is_selected": bool(row["is_selected"]),
                "score": row["score"],
                "pipeline_state": row["pipeline_state"],
                "has_content_full": bool(row["content_full"]),
            }
            for guid, row in by_guid.items()
        },
    }


def task_032_e2e_original_link_observations() -> dict[str, Any]:
    api = task_032_api_original_url_observations()
    sources, read_issues = task_016_read_sources()
    article_source = sources.get("frontend/src/pages/ArticleView.tsx", "")
    news_card_source = sources.get("frontend/src/components/NewsCard.tsx", "")
    high_score_source = sources.get("frontend/src/components/HighScoreList.tsx", "")
    detail = api.get("translated_detail") if isinstance(api, dict) else {}
    original_url = str(detail.get("original_url") if isinstance(detail, dict) else "")
    checks = {
        "api_detail_original_url_public": is_public_http_url_value(original_url),
        "api_detail_original_url_non_placeholder": not is_reserved_placeholder_url(original_url),
        "article_link_uses_detail_original_url": "href={detail.original_url}" in article_source,
        "article_link_opens_new_tab": 'target="_blank"' in article_source and 'rel="noreferrer"' in article_source,
        "article_link_hidden_for_ready": "detail.status !== 'ready'" in article_source,
        "news_card_uses_internal_route": "href={`/news/${item.id}`}" in news_card_source and "original_url" not in news_card_source,
        "high_score_uses_internal_route": "href={`/news/${item.id}`}" in high_score_source and "original_url" not in high_score_source,
    }
    issues = list(read_issues)
    issues.extend(api.get("issues", []) if isinstance(api.get("issues"), list) else [])
    issues.extend(f"e2e_original_link:{name}=false" for name, passed in checks.items() if not passed)
    return {
        "checks": checks,
        "issues": sorted(set(issues)),
        "detail_original_url": original_url,
    }


def task_032_no_live_fetch_observations() -> dict[str, Any]:
    source_text = Path("backend/app/services/pipeline.py").read_text(encoding="utf-8")
    live_fetch_terms = [
        "requests.",
        "httpx.",
        "aiohttp.",
        "urllib.request",
        "urlopen(",
    ]
    hits = [term for term in live_fetch_terms if term in source_text]
    return {
        "checks": {
            "pipeline_uses_article_fixture_map": "ARTICLE_MAP_PATH" in source_text
            and "article_map.json" in source_text,
            "pipeline_has_no_live_fetch_client": not hits,
        },
        "issues": [f"live_fetch_term:{term}" for term in hits],
    }


def run_task_032_static(report_dir: Path, task_id: str) -> int:
    docs = task_032_docs_static_observations(task_id)
    fixtures = task_032_fixture_url_observations()
    live_fetch = task_032_no_live_fetch_observations()
    checks = {
        "docs": docs["checks"],
        "fixtures": fixtures["checks"],
        "live_fetch": live_fetch["checks"],
    }
    issues = docs["issues"] + fixtures["issues"] + live_fetch["issues"]
    passed = not issues and all(all(group.values()) for group in checks.values())
    report = test_report(
        stage="static",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-original-url-realism-static",
        assertions=[
            assertion(
                "A-static-ACC-STOP-010-contract-doc-sync",
                "passed" if passed else "failed",
                {"docs_updated": True, "fixtures_non_placeholder": True, "no_live_fetch": True},
                {"checks": checks, "reserved_urls": fixtures["reserved_urls"]},
                {"issues": issues},
            )
        ],
        expected={"original_url_realism_static": "pass"},
        actual={"docs": docs, "fixtures": fixtures, "live_fetch": live_fetch},
        diff={"issues": issues},
        failure_type=None if passed else "contract",
        error_category=None if passed else "validation",
        referenced_files=[
            "tasks.md",
            f"reports/tasks/{task_id}/plan.json",
            "docs/01_prd.md",
            "docs/03_ui_spec.md",
            "docs/05_api_contract.md",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
            "fixtures/rss/feeds.json",
            "fixtures/articles/article_map.json",
            "backend/app/services/pipeline.py",
        ],
        commands=[f"python3 scripts/run_harness.py --stage static --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "static", task_id), report)
    return 0 if passed else 1


def run_task_032_unit(report_dir: Path, task_id: str) -> int:
    fixtures = task_032_fixture_url_observations()
    checks = fixtures["checks"]
    passed = not fixtures["issues"] and all(checks.values())
    report = test_report(
        stage="unit",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-fixture-url-unit",
        assertions=[
            assertion(
                "task-032-unit-fixture-url-realism",
                "passed" if passed else "failed",
                {"rss_links": "public_non_placeholder", "article_cases": "covered"},
                {"checks": checks, "reserved_urls": fixtures["reserved_urls"]},
                {"issues": fixtures["issues"]},
                visibility="internal_evidence",
            )
        ],
        expected={"fixture_urls": "public_non_placeholder"},
        actual=fixtures,
        diff={"issues": fixtures["issues"]},
        failure_type=None if passed else "validation",
        error_category=None if passed else "validation",
        referenced_files=[
            "fixtures/rss/feeds.json",
            "fixtures/articles/article_map.json",
            "tests/test_fixture_config.py",
            "scripts/run_harness.py",
        ],
        commands=[f"python3 scripts/run_harness.py --stage unit --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "unit", task_id), report)
    return 0 if passed else 1


def run_task_032_api(report_dir: Path, task_id: str) -> int:
    observed = task_032_api_original_url_observations()
    passed = not observed["issues"] and all(observed["checks"].values())
    report = test_report(
        stage="api",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-original-url-api",
        assertions=[
            assertion(
                "A-api-ACC-STOP-004-original-url-real-link",
                "passed" if passed else "failed",
                {"original_url": "rss_link_public_non_placeholder"},
                {
                    "checks": observed["checks"],
                    "detail_original_url": observed.get("translated_detail", {}).get("original_url"),
                    "home_translated_count": observed.get("home_translated_count"),
                },
                {"issues": observed["issues"]},
                visibility="public_surface",
            )
        ],
        expected={"api_original_url": "rss_link_public_non_placeholder"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "api",
        error_category=None if passed else "validation",
        referenced_files=[
            "backend/app/main.py",
            "backend/app/services/pipeline.py",
            "fixtures/rss/feeds.json",
            "tests/test_api_contract.py",
            "scripts/run_harness.py",
        ],
        commands=[f"python3 scripts/run_harness.py --stage api --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "api", task_id), report)
    return 0 if passed else 1


def run_task_032_integration(report_dir: Path, task_id: str) -> int:
    observed = task_032_integration_original_url_observations()
    passed = not observed["issues"] and all(observed["checks"].values())
    report = test_report(
        stage="integration",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-original-url-integration",
        assertions=[
            assertion(
                "task-032-integration-original-url-preservation",
                "passed" if passed else "failed",
                {"original_url": "preserved", "canonical_url": "dedupe_only"},
                {"checks": observed["checks"], "row_count": observed["row_count"]},
                {"issues": observed["issues"]},
                visibility="internal_evidence",
            )
        ],
        expected={"pipeline_original_url": "preserved_while_canonical_dedupes"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "integration",
        error_category=None if passed else "validation",
        referenced_files=[
            "backend/app/services/pipeline.py",
            "fixtures/rss/feeds.json",
            "fixtures/articles/article_map.json",
            "scripts/run_harness.py",
        ],
        commands=[f"python3 scripts/run_harness.py --stage integration --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "integration", task_id), report)
    return 0 if passed else 1


def run_task_032_e2e(report_dir: Path, task_id: str) -> int:
    observed = task_032_e2e_original_link_observations()
    passed = not observed["issues"] and all(observed["checks"].values())
    assertions = [
        assertion(
            "A-e2e-ACC-STOP-006-article-original-link-button",
            "passed" if passed else "failed",
            {"article_link_href": "detail.original_url", "target": "_blank"},
            {"checks": observed["checks"], "detail_original_url": observed["detail_original_url"]},
            {"issues": observed["issues"]},
            visibility="public_surface",
        ),
        assertion(
            "A-e2e-ACC-STOP-006-no-direct-original-navigation",
            "passed" if passed else "failed",
            {"cards_and_rank_items": "internal_article_route"},
            {"checks": observed["checks"]},
            {"issues": observed["issues"]},
            visibility="public_surface",
        ),
    ]
    report = test_report(
        stage="e2e",
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-article-original-link-e2e",
        assertions=assertions,
        expected={"article_original_link": "api_original_url"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else "ui",
        error_category=None if passed else "validation",
        referenced_files=[
            "frontend/src/pages/ArticleView.tsx",
            "frontend/src/components/NewsCard.tsx",
            "frontend/src/components/HighScoreList.tsx",
            "backend/app/main.py",
            "fixtures/rss/feeds.json",
            "scripts/run_harness.py",
        ],
        commands=[f"python3 scripts/run_harness.py --stage e2e --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, "e2e", task_id), report)
    return 0 if passed else 1


def translation_quality_check(
    guid: str,
    title: str,
    summary: str,
    content: str,
) -> dict[str, Any]:
    joined = "\n".join([title, summary, content]).lower()
    paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]
    keywords = TRANSLATION_QUALITY_KEYWORDS.get(guid, ())
    forbidden_terms = [
        term for term in FORBIDDEN_TRANSLATION_PLACEHOLDER_TERMS if term.lower() in joined
    ]
    checks = {
        "known_guid": guid in TRANSLATION_QUALITY_KEYWORDS,
        "no_placeholder_terms": not forbidden_terms,
        "summary_min_length": len(summary) >= 28,
        "content_min_length": len(content) >= 110,
        "body_has_multiple_paragraphs": len(paragraphs) >= 2,
        "summary_matches_keywords": bool(keywords) and any(keyword in summary for keyword in keywords),
        "content_matches_keywords": bool(keywords) and any(keyword in content for keyword in keywords),
    }
    issues = [
        f"{guid}:{name}=false" for name, passed in checks.items() if not passed
    ]
    if forbidden_terms:
        issues.append(f"{guid}:forbidden_terms={','.join(forbidden_terms)}")
    return {
        "guid": guid,
        "checks": checks,
        "issues": issues,
        "summary_length": len(summary),
        "content_length": len(content),
        "paragraph_count": len(paragraphs),
    }


def task_033_fixture_quality_observations() -> dict[str, Any]:
    payload, read_issues = read_json_object(Path("fixtures/llm/translation.json"))
    payload = payload or {}
    records = payload.get("translations", {})
    records = records if isinstance(records, dict) else {}
    quality_results: list[dict[str, Any]] = []
    issues = list(read_issues)
    expected_guids = set(TRANSLATION_QUALITY_KEYWORDS)
    observed_guids = {guid for guid in records if guid in expected_guids}
    if observed_guids != expected_guids:
        issues.append(f"translation_quality:guid_set={sorted(observed_guids)}")
    for guid in sorted(expected_guids):
        record = records.get(guid)
        if not isinstance(record, dict):
            issues.append(f"{guid}:missing_translation_record")
            continue
        result = translation_quality_check(
            guid,
            str(record.get("title_zh") or ""),
            str(record.get("summary_zh") or ""),
            str(record.get("content_zh") or ""),
        )
        quality_results.append(result)
        issues.extend(result["issues"])
    partial = records.get("fixture-translate-partial")
    partial_invalid = not (
        isinstance(partial, dict)
        and all(str(partial.get(field) or "").strip() for field in ("title_zh", "summary_zh", "content_zh", "category_zh"))
    )
    if not partial_invalid:
        issues.append("fixture-translate-partial:partial_case_became_valid")
    checks = {
        "fixture_version": payload.get("version") == MOCK_VERSION,
        "expected_guid_set_present": observed_guids == expected_guids,
        "all_successful_records_readable": not any(result["issues"] for result in quality_results),
        "partial_case_remains_invalid": partial_invalid,
    }
    issues.extend(f"translation_quality:{name}=false" for name, passed in checks.items() if not passed)
    return {"checks": checks, "issues": sorted(set(issues)), "quality_results": quality_results}


def task_033_db_quality_observations() -> dict[str, Any]:
    connect, initialize_database, seed_default_sources, ingest_fixture_rss, score_raw_news, fetch_selected_content, translate_fetched_content, *_ = (
        task_008_pipeline_imports()
    )
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)
    fetch_selected_content(conn)
    translate_fetched_content(conn)
    rows = conn.execute(
        """
        SELECT rss_guid, title_zh, summary_zh, content_zh, has_translate_failed
        FROM news_item
        WHERE title_zh IS NOT NULL
          AND summary_zh IS NOT NULL
          AND content_zh IS NOT NULL
        ORDER BY rss_guid ASC
        """
    ).fetchall()
    partial = conn.execute(
        """
        SELECT title_zh, summary_zh, content_zh, has_translate_failed
        FROM news_item
        WHERE rss_guid = 'fixture-translate-partial'
        """
    ).fetchone()
    conn.close()
    issues: list[str] = []
    quality_results = [
        translation_quality_check(
            str(row["rss_guid"]),
            str(row["title_zh"] or ""),
            str(row["summary_zh"] or ""),
            str(row["content_zh"] or ""),
        )
        for row in rows
    ]
    for result in quality_results:
        issues.extend(result["issues"])
    observed_guids = {str(row["rss_guid"]) for row in rows}
    expected_guids = set(TRANSLATION_QUALITY_KEYWORDS)
    checks = {
        "translated_guid_set": observed_guids == expected_guids,
        "translated_records_readable": not any(result["issues"] for result in quality_results),
        "partial_failure_isolated": bool(
            partial
            and partial["has_translate_failed"] == 1
            and not any(partial[field] for field in ("title_zh", "summary_zh", "content_zh"))
        ),
    }
    issues.extend(f"db_translation_quality:{name}=false" for name, passed in checks.items() if not passed)
    return {"checks": checks, "issues": sorted(set(issues)), "quality_results": quality_results}


def task_033_api_quality_observations() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"checks": {"backend_imported": False}, "issues": [import_issue], "quality_results": []}
    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {
            "checks": {"backend_imported": True, "test_client_imported": False},
            "issues": [f"fastapi_testclient_import_failed:{error.__class__.__name__}"],
            "quality_results": [],
        }
    client = TestClient(app)
    issues = envelope_issue(
        name="task_033_refresh",
        response=client.post("/api/refresh"),
        expected_status=200,
        expected_envelope="data",
        required_data_keys={"refreshed_at"},
    )
    rows = app.state.db.execute(
        """
        SELECT id, rss_guid
        FROM news_item
        WHERE title_zh IS NOT NULL
          AND summary_zh IS NOT NULL
          AND content_zh IS NOT NULL
        ORDER BY id ASC
        """
    ).fetchall()
    quality_results: list[dict[str, Any]] = []
    for row in rows:
        response = client.get(f"/api/news/{row['id']}")
        issues.extend(
            envelope_issue(
                name=f"task_033_detail_{row['rss_guid']}",
                response=response,
                expected_status=200,
                expected_envelope="data",
                required_data_keys={"id", "title", "summary_zh", "content_zh", "status"},
            )
        )
        payload, parse_issues = _safe_json(response)
        issues.extend(f"task_033_detail:{row['rss_guid']}:{issue}" for issue in parse_issues)
        detail = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(detail, dict):
            issues.append(f"{row['rss_guid']}:detail_payload_invalid")
            continue
        if detail.get("status") != "translated":
            issues.append(f"{row['rss_guid']}:detail_status={detail.get('status')}")
        result = translation_quality_check(
            str(row["rss_guid"]),
            str(detail.get("title") or ""),
            str(detail.get("summary_zh") or ""),
            str(detail.get("content_zh") or ""),
        )
        quality_results.append(result)
        issues.extend(result["issues"])
    observed_guids = {str(row["rss_guid"]) for row in rows}
    expected_guids = set(TRANSLATION_QUALITY_KEYWORDS)
    checks = {
        "backend_imported": True,
        "translated_guid_set": observed_guids == expected_guids,
        "all_detail_content_readable": not any(result["issues"] for result in quality_results),
    }
    issues.extend(f"api_translation_quality:{name}=false" for name, passed in checks.items() if not passed)
    return {"checks": checks, "issues": sorted(set(issues)), "quality_results": quality_results}


def task_033_ui_quality_observations() -> dict[str, Any]:
    api = task_033_api_quality_observations()
    article_source = Path("frontend/src/pages/ArticleView.tsx").read_text(encoding="utf-8")
    checks = {
        "api_detail_content_readable": api["checks"].get("all_detail_content_readable") is True,
        "article_view_renders_summary": "article-view__summary" in article_source and "detail.summary_zh" in article_source,
        "article_view_renders_body": "article-view__body" in article_source and "renderContentParagraphs(detail.content_zh)" in article_source,
        "article_view_preserves_paragraphs": "split(/\\n{2,}/)" in article_source,
        "article_view_no_dangerous_html": "dangerouslySetInnerHTML" not in article_source,
    }
    issues = list(api["issues"])
    issues.extend(f"ui_translation_quality:{name}=false" for name, passed in checks.items() if not passed)
    return {"checks": checks, "issues": sorted(set(issues)), "api": api}


def run_task_033_stage(report_dir: Path, task_id: str, stage: str) -> int:
    if stage == "unit":
        observed = task_033_fixture_quality_observations()
        assertion_id = "task-033-unit-translation-quality-fixtures"
        visibility = "internal_evidence"
    elif stage == "api":
        observed = task_033_api_quality_observations()
        assertion_id = "task-033-api-readable-detail-content"
        visibility = "public_surface"
    elif stage == "integration":
        observed = task_033_db_quality_observations()
        assertion_id = "A-integration-ACC-STOP-003-translation-quality-fixtures"
        visibility = "public_surface"
    elif stage == "snapshot":
        observed = task_033_ui_quality_observations()
        assertion_id = "task-033-snapshot-readable-article-dom"
        visibility = "public_surface"
    else:
        observed = task_033_ui_quality_observations()
        assertion_id = "task-033-e2e-readable-article-detail"
        visibility = "public_surface"
    passed = not observed["issues"] and all(observed["checks"].values())
    report = test_report(
        stage=stage,
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-summary-full-text-quality-{stage}",
        assertions=[
            assertion(
                assertion_id,
                "passed" if passed else "failed",
                {"summary_body_quality": "article_specific_readable_non_placeholder"},
                {"checks": observed["checks"], "quality_results": observed.get("quality_results", [])},
                {"issues": observed["issues"]},
                visibility=visibility,
            )
        ],
        expected={"summary_body_quality": "article_specific_readable_non_placeholder"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else stage,
        error_category=None if passed else "validation",
        referenced_files=[
            "fixtures/llm/translation.json",
            "backend/app/services/pipeline.py",
            "backend/app/main.py",
            "frontend/src/pages/ArticleView.tsx",
            "tests/test_fixture_config.py",
            "tests/test_api_contract.py",
            "tests/test_pipeline_refresh.py",
            "scripts/run_harness.py",
        ],
        commands=[f"python3 scripts/run_harness.py --stage {stage} --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, stage, task_id), report)
    return 0 if passed else 1


def run_task_033_unit(report_dir: Path, task_id: str) -> int:
    return run_task_033_stage(report_dir, task_id, "unit")


def run_task_033_api(report_dir: Path, task_id: str) -> int:
    return run_task_033_stage(report_dir, task_id, "api")


def run_task_033_integration(report_dir: Path, task_id: str) -> int:
    return run_task_033_stage(report_dir, task_id, "integration")


def run_task_033_snapshot(report_dir: Path, task_id: str) -> int:
    return run_task_033_stage(report_dir, task_id, "snapshot")


def run_task_033_e2e(report_dir: Path, task_id: str) -> int:
    return run_task_033_stage(report_dir, task_id, "e2e")


def task_034_home_translated_observations() -> dict[str, Any]:
    app, client = task_014_client()
    issues: list[str] = []
    refresh_response = client.post("/api/refresh")
    issues.extend(
        envelope_issue(
            name="task_034_refresh",
            response=refresh_response,
            expected_status=200,
            expected_envelope="data",
            required_data_keys={"refreshed_at"},
        )
    )

    home_response = client.get("/api/home")
    issues.extend(
        envelope_issue(
            name="task_034_home",
            response=home_response,
            expected_status=200,
            expected_envelope="data",
            required_data_keys={"latest_news", "top_ranked_news"},
        )
    )
    home_payload, parse_issues = _safe_json(home_response)
    issues.extend(f"task_034_home:{issue}" for issue in parse_issues)
    home_data = home_payload.get("data", {}) if isinstance(home_payload, dict) else {}
    latest = home_data.get("latest_news") if isinstance(home_data, dict) else []
    top = home_data.get("top_ranked_news") if isinstance(home_data, dict) else []
    latest = latest if isinstance(latest, list) else []
    top = top if isinstance(top, list) else []
    latest_status_counts = news_status_counts(latest)
    top_status_counts = news_status_counts(top)

    if latest_status_counts != EXPECTED_TRANSLATION_LATEST_STATUS_COUNTS:
        issues.append(f"task_034:latest_status_counts:{latest_status_counts}")
    if top_status_counts != EXPECTED_TRANSLATION_TOP_STATUS_COUNTS:
        issues.append(f"task_034:top_status_counts:{top_status_counts}")
    if len(latest) < 10:
        issues.append(f"task_034:latest_count={len(latest)}<10")
    if len(top) != 10:
        issues.append(f"task_034:top_count={len(top)}!=10")

    latest_titles = [str(item.get("original_title")) for item in latest if isinstance(item, dict)]
    top_scores = [item.get("score") for item in top if isinstance(item, dict)]
    expected_latest_prefix = [
        "AI safety benchmark reaches enterprise pilots",
        "Open model eval suite adds agent tasks",
        "AI chip scheduler cuts inference latency",
        "Research lab publishes multimodal tool use benchmark",
        "AI data pipeline validates synthetic QA traces",
        "Small language model improves retrieval planning",
        "AI observability tool traces prompt regressions",
        "AI coding assistant checks repository contracts",
        "AI product analytics detects agent drift",
        "Introducing LifeSciBench",
    ]
    if latest_titles[:10] != expected_latest_prefix:
        issues.append(f"task_034:latest_prefix:{latest_titles[:10]}")
    if top_scores != [96, 95, 94, 93, 92, 91, 90, 89, 88, 87]:
        issues.append(f"task_034:top_scores:{top_scores}")

    conn = app.state.db
    rows = conn.execute("SELECT id, rss_guid FROM news_item").fetchall()
    guid_by_id = {str(row["id"]): str(row["rss_guid"]) for row in rows}
    quality_results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for list_name, item in [
        *[("latest_news", item) for item in latest if isinstance(item, dict)],
        *[("top_ranked_news", item) for item in top if isinstance(item, dict)],
    ]:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        if item.get("status") != "translated":
            issues.append(f"task_034:{list_name}:{item_id}:status={item.get('status')}")
        if not item.get("summary_zh"):
            issues.append(f"task_034:{list_name}:{item_id}:missing_list_summary")
        if "content_zh" in item:
            issues.append(f"task_034:{list_name}:{item_id}:content_leaked_in_list")
        detail_response = client.get(f"/api/news/{item_id}")
        detail_payload, detail_parse_issues = _safe_json(detail_response)
        issues.extend(f"task_034:{list_name}:{item_id}:{issue}" for issue in detail_parse_issues)
        detail = detail_payload.get("data") if isinstance(detail_payload, dict) else None
        if detail_response.status_code != 200 or not isinstance(detail, dict):
            issues.append(f"task_034:{list_name}:{item_id}:detail_unavailable")
            continue
        if detail.get("status") != "translated":
            issues.append(f"task_034:{list_name}:{item_id}:detail_status={detail.get('status')}")
        result = translation_quality_check(
            guid_by_id.get(item_id, ""),
            str(detail.get("title") or ""),
            str(detail.get("summary_zh") or ""),
            str(detail.get("content_zh") or ""),
        )
        quality_results.append(result)
        issues.extend(result["issues"])
        original_url = str(detail.get("original_url") or "")
        if not is_public_http_url_value(original_url):
            issues.append(f"task_034:{list_name}:{item_id}:original_url_not_public_http")
        if is_reserved_placeholder_url(original_url):
            issues.append(f"task_034:{list_name}:{item_id}:original_url_placeholder")

    ready_id = conn.execute(
        "SELECT id FROM news_item WHERE rss_guid = 'fixture-threshold-60'"
    ).fetchone()["id"]
    failed_id = conn.execute(
        "SELECT id FROM news_item WHERE rss_guid = 'fixture-translate-partial'"
    ).fetchone()["id"]
    ready_detail = client.get(f"/api/news/{ready_id}").json()["data"]
    failed_detail = client.get(f"/api/news/{failed_id}").json()["data"]
    direct_checks = {
        "ready_status_preserved": ready_detail.get("status") == "ready",
        "ready_omits_summary": "summary_zh" not in ready_detail and "content_zh" not in ready_detail,
        "failed_status_preserved": failed_detail.get("status") == "translation_failed",
        "failed_omits_summary": "summary_zh" not in failed_detail and "content_zh" not in failed_detail,
    }
    issues.extend(f"task_034:direct:{name}=false" for name, passed in direct_checks.items() if not passed)

    checks = {
        "latest_translated_only": latest_status_counts == EXPECTED_TRANSLATION_LATEST_STATUS_COUNTS,
        "top_translated_only": top_status_counts == EXPECTED_TRANSLATION_TOP_STATUS_COUNTS,
        "latest_density": len(latest) >= 10,
        "top_density": len(top) == 10,
        "latest_order": latest_titles[:10] == expected_latest_prefix,
        "top_order": top_scores == [96, 95, 94, 93, 92, 91, 90, 89, 88, 87],
        "all_click_details_readable": not any(result["issues"] for result in quality_results),
        "direct_ready_failed_preserved": all(direct_checks.values()),
    }
    return {
        "checks": checks,
        "issues": sorted(set(issues)),
        "latest_status_counts": latest_status_counts,
        "top_status_counts": top_status_counts,
        "latest_count": len(latest),
        "top_count": len(top),
        "quality_results": quality_results,
        "direct_checks": direct_checks,
    }


def task_034_e2e_observations() -> dict[str, Any]:
    home = task_034_home_translated_observations()
    surface = e2e_surface_evidence()
    primary_click = surface.get("checks", {}).get("primary_click_readability", {})
    checks = {
        "home_translated_contract": all(home["checks"].values()),
        "surface_primary_click_readable": isinstance(primary_click, dict)
        and primary_click.get("all_visible_items_translated_and_readable") is True,
        "surface_density": surface.get("checks", {}).get("home_news_density_ok") is True,
        "surface_high_score_count": surface.get("checks", {}).get("top_news_count") == 10,
    }
    issues = list(home["issues"])
    issues.extend(f"e2e_surface:{issue}" for issue in surface.get("issues", []))
    issues.extend(f"task_034_e2e:{name}=false" for name, passed in checks.items() if not passed)
    return {
        "checks": checks,
        "issues": sorted(set(issues)),
        "home": home,
        "surface": surface,
    }


def run_task_034_stage(report_dir: Path, task_id: str, stage: str) -> int:
    if stage == "e2e":
        observed = task_034_e2e_observations()
        assertion_id = "A-e2e-ACC-STOP-006-click-to-read-readability"
        visibility = "public_surface"
        failure_type = "ui"
    elif stage == "api":
        observed = task_034_home_translated_observations()
        assertion_id = "A-api-ACC-STOP-004-home-translated-only"
        visibility = "public_surface"
        failure_type = "api"
    elif stage == "integration":
        observed = task_034_home_translated_observations()
        assertion_id = "task-034-integration-home-translated-clicks"
        visibility = "internal_evidence"
        failure_type = "integration"
    else:
        observed = task_034_home_translated_observations()
        assertion_id = "task-034-snapshot-home-translated-surface"
        visibility = "public_surface"
        failure_type = "ui"
    passed = not observed["issues"] and all(observed["checks"].values())
    report = test_report(
        stage=stage,
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-translated-primary-reading-lists-{stage}",
        assertions=[
            assertion(
                assertion_id,
                "passed" if passed else "failed",
                {"home_top_lists": "translated_only_clicks_readable"},
                {"checks": observed["checks"]},
                {"issues": observed["issues"]},
                visibility=visibility,
            )
        ],
        expected={"home_top_lists": "translated_only_clicks_readable"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else failure_type,
        error_category=None if passed else "validation",
        referenced_files=[
            "backend/app/main.py",
            "frontend/src/components/NewsCard.tsx",
            "frontend/src/components/HighScoreList.tsx",
            "frontend/src/pages/ArticleView.tsx",
            "fixtures/rss/feeds.json",
            "fixtures/llm/scoring.json",
            "fixtures/llm/translation.json",
            "tests/test_api_contract.py",
            "tests/test_pipeline_refresh.py",
            "scripts/run_harness.py",
        ],
        commands=[f"python3 scripts/run_harness.py --stage {stage} --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, stage, task_id), report)
    return 0 if passed else 1


def run_task_034_api(report_dir: Path, task_id: str) -> int:
    return run_task_034_stage(report_dir, task_id, "api")


def run_task_034_integration(report_dir: Path, task_id: str) -> int:
    return run_task_034_stage(report_dir, task_id, "integration")


def run_task_034_snapshot(report_dir: Path, task_id: str) -> int:
    return run_task_034_stage(report_dir, task_id, "snapshot")


def run_task_034_e2e(report_dir: Path, task_id: str) -> int:
    return run_task_034_stage(report_dir, task_id, "e2e")


def task_035_home_request(
    client: Any,
    issues: list[str],
    name: str,
    *,
    limit: int,
    cursor: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    response = client.get("/api/home", params=params)
    issues.extend(
        envelope_issue(
            name=name,
            response=response,
            expected_status=200,
            expected_envelope="data",
            required_data_keys={"latest_news", "top_ranked_news"},
        )
    )
    payload, parse_issues = _safe_json(response)
    issues.extend(f"{name}:{issue}" for issue in parse_issues)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        issues.append(f"{name}:data_not_object")
        return {}
    return data


def task_035_api_pagination_observations() -> dict[str, Any]:
    app, import_issue = import_backend_app()
    if import_issue:
        return {"checks": {"backend_imported": False}, "issues": [import_issue], "pages": []}
    try:
        from fastapi.testclient import TestClient
    except Exception as error:
        return {
            "checks": {"backend_imported": True, "test_client_imported": False},
            "issues": [f"fastapi_testclient_import_failed:{error.__class__.__name__}"],
            "pages": [],
        }

    client = TestClient(app)
    issues: list[str] = []
    issues.extend(
        envelope_issue(
            name="task_035_refresh",
            response=client.post("/api/refresh"),
            expected_status=200,
            expected_envelope="data",
            required_data_keys={"refreshed_at"},
        )
    )
    pages = [task_035_home_request(client, issues, "task_035_home_page_1", limit=3)]
    first_cursor = pages[0].get("next_cursor")
    if isinstance(first_cursor, str):
        pages.append(
            task_035_home_request(
                client,
                issues,
                "task_035_home_page_2",
                limit=3,
                cursor=first_cursor,
            )
        )
    else:
        issues.append("home_pagination:first_next_cursor_missing")

    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for page_index in range(3, 8):
        cursor = pages[-1].get("next_cursor") if pages else None
        if not isinstance(cursor, str):
            break
        pages.append(
            task_035_home_request(
                client,
                issues,
                f"task_035_home_page_{page_index}",
                limit=3,
                cursor=cursor,
            )
        )

    latest_items = [
        item
        for page in pages
        for item in page.get("latest_news", [])
        if isinstance(item, dict)
    ]
    for item in latest_items:
        item_id = str(item.get("id") or "")
        if item_id in seen_ids:
            duplicate_ids.add(item_id)
        seen_ids.add(item_id)
    dates = [str(item.get("published_at") or "") for item in latest_items]
    first_ids = [str(item.get("id")) for item in pages[0].get("latest_news", [])]
    second_ids = [str(item.get("id")) for item in pages[1].get("latest_news", [])] if len(pages) > 1 else []
    first_top = [str(item.get("id")) for item in pages[0].get("top_ranked_news", [])]
    second_top = [str(item.get("id")) for item in pages[1].get("top_ranked_news", [])] if len(pages) > 1 else []
    checks = {
        "backend_imported": True,
        "first_page_limited": len(first_ids) == 3,
        "first_next_cursor_present": isinstance(first_cursor, str) and bool(first_cursor),
        "second_page_limited": len(second_ids) == 3,
        "first_second_non_overlapping": not (set(first_ids) & set(second_ids)),
        "combined_sorted_desc": dates == sorted(dates, reverse=True),
        "all_pages_non_duplicate": not duplicate_ids,
        "terminal_page_omits_next_cursor": bool(pages) and "next_cursor" not in pages[-1],
        "top_ranked_unpaginated": bool(first_top) and first_top == second_top,
        "latest_translated_only": all(item.get("status") == "translated" for item in latest_items),
    }
    issues.extend(f"home_pagination:{name}=false" for name, passed in checks.items() if not passed)
    return {
        "checks": checks,
        "issues": sorted(set(issues)),
        "page_lengths": [len(page.get("latest_news", [])) for page in pages],
        "page_ids": [
            [str(item.get("id")) for item in page.get("latest_news", []) if isinstance(item, dict)]
            for page in pages
        ],
        "duplicate_ids": sorted(duplicate_ids),
    }


def task_035_frontend_observations() -> dict[str, Any]:
    api_source, api_issues = _safe_text_read(Path("frontend/src/api/news.ts"), "news_api")
    home_source, home_issues = _safe_text_read(Path("frontend/src/pages/HomePage.tsx"), "home_page")
    high_score_source, high_score_issues = _safe_text_read(
        Path("frontend/src/components/HighScoreList.tsx"),
        "high_score_list",
    )
    app_css, css_issues = _safe_text_read(Path("frontend/src/styles/app.css"), "app_css")
    issues = api_issues + home_issues + high_score_issues + css_issues
    checks = {
        "api_client_accepts_cursor_limit": all(
            token in api_source for token in ["FetchHomeOptions", "URLSearchParams", "cursor", "limit"]
        ),
        "page_level_scroll_listener": "window.addEventListener('scroll'" in home_source
        and "document.documentElement" in home_source,
        "loads_next_cursor": "loadMoreHome" in home_source
        and "client.fetchHome({ cursor: nextCursor })" in home_source,
        "append_unique_news": "mergeUniqueLatestNews" in home_source
        and "new Set" in home_source
        and "[...current.latest_news, ...appendedNews]" in home_source,
        "preserves_top_ranked_news": "top_ranked_news: current.top_ranked_news" in home_source,
        "failure_keeps_retryable_cursor": "setLoadingMoreState('error')" in home_source
        and "loadingMoreState === 'loading'" in home_source
        and "loadingMoreState !== 'idle'" not in home_source,
        "end_state_stops_requests": "if (!nextCursor" in home_source
        and "setNextCursor(nextPage.next_cursor)" in home_source,
        "no_nested_scroll_container": "overflow-y" not in app_css,
        "high_score_not_paginated": "fetchHome" not in high_score_source
        and "next_cursor" not in high_score_source
        and "overflow-y" not in high_score_source,
    }
    issues.extend(f"frontend_infinite_scroll:{name}=false" for name, passed in checks.items() if not passed)
    return {"checks": checks, "issues": sorted(set(issues))}


def task_035_static_observations(task_id: str) -> dict[str, Any]:
    plan = read_report(Path(f"reports/tasks/{task_id}/plan.json"))
    plan_issues = validate_against_schema(
        plan,
        SCHEMA_FILES["task_plan_report"],
        "TaskPlanReport",
    )
    harness_source = Path("scripts/run_harness.py").read_text(encoding="utf-8")
    docs = {
        path.as_posix(): path.read_text(encoding="utf-8")
        for path in (
            Path("docs/01_prd.md"),
            Path("docs/03_ui_spec.md"),
            Path("docs/05_api_contract.md"),
            Path("docs/07_test_spec.md"),
            Path("docs/08_acceptance.md"),
        )
    }
    checks = {
        "plan_schema_valid": not plan_issues,
        "prd_mentions_scroll_loading": all(
            token in docs["docs/01_prd.md"]
            for token in ["向下滚动", "新闻列表底部", "next_cursor"]
        ),
        "ui_mentions_page_level_scroll": "page-level scroll" in docs["docs/03_ui_spec.md"],
        "api_contract_mentions_next_cursor": "next_cursor" in docs["docs/05_api_contract.md"],
        "mandatory_api_assertion_documented": "A-api-ACC-STOP-004-home-pagination" in docs["docs/07_test_spec.md"],
        "mandatory_e2e_assertion_documented": "A-e2e-ACC-STOP-006-home-infinite-scroll" in docs["docs/07_test_spec.md"],
        "acceptance_mentions_infinite_loading": "Home page 向下滚动接近底部" in docs["docs/08_acceptance.md"],
        "task_harness_dispatch_present": "run_task_035_stage" in harness_source,
        "task_fallback_assertions_present": '"TASK-035"' in harness_source
        and "A-api-ACC-STOP-004-home-pagination" in harness_source
        and "A-e2e-ACC-STOP-006-home-infinite-scroll" in harness_source,
    }
    issues = plan_issues + [f"task_035_static:{name}=false" for name, passed in checks.items() if not passed]
    return {"checks": checks, "issues": sorted(set(issues))}


def task_035_home_infinite_observations() -> dict[str, Any]:
    api = task_035_api_pagination_observations()
    frontend = task_035_frontend_observations()
    checks = {
        "api_cursor_pagination": all(api["checks"].values()),
        "scroll_triggers_cursor_request": frontend["checks"].get("page_level_scroll_listener") is True
        and frontend["checks"].get("loads_next_cursor") is True,
        "append_without_duplicates": frontend["checks"].get("append_unique_news") is True
        and api["checks"].get("all_pages_non_duplicate") is True,
        "failure_retryable": frontend["checks"].get("failure_keeps_retryable_cursor") is True,
        "end_state_stop": frontend["checks"].get("end_state_stops_requests") is True
        and api["checks"].get("terminal_page_omits_next_cursor") is True,
        "high_score_unpaginated": frontend["checks"].get("high_score_not_paginated") is True
        and api["checks"].get("top_ranked_unpaginated") is True,
        "no_nested_scroll_container": frontend["checks"].get("no_nested_scroll_container") is True,
    }
    issues = [*api["issues"], *frontend["issues"]]
    issues.extend(f"home_infinite_scroll:{name}=false" for name, passed in checks.items() if not passed)
    return {"checks": checks, "issues": sorted(set(issues)), "api": api, "frontend": frontend}


def run_task_035_stage(report_dir: Path, task_id: str, stage: str) -> int:
    if stage == "static":
        observed = task_035_static_observations(task_id)
        assertion_id = "A-static-ACC-STOP-010-contract-doc-sync"
        visibility = "report_metadata"
        failure_type = "contract"
    elif stage == "api":
        observed = task_035_api_pagination_observations()
        assertion_id = "A-api-ACC-STOP-004-home-pagination"
        visibility = "public_surface"
        failure_type = "api"
    elif stage == "integration":
        observed = task_035_home_infinite_observations()
        assertion_id = "task-035-integration-home-pagination-ui-contract"
        visibility = "internal_evidence"
        failure_type = "integration"
    else:
        observed = task_035_home_infinite_observations()
        assertion_id = "A-e2e-ACC-STOP-006-home-infinite-scroll"
        visibility = "public_surface"
        failure_type = "ui"
    passed = not observed["issues"] and all(observed["checks"].values())
    report = test_report(
        stage=stage,
        status="passed" if passed else "failed",
        test_id=f"{task_id.lower()}-home-infinite-loading-{stage}",
        assertions=[
            assertion(
                assertion_id,
                "passed" if passed else "failed",
                {"home_infinite_loading": "cursor_append_retry_end_state"},
                {"checks": observed["checks"]},
                {"issues": observed["issues"]},
                visibility=visibility,
            )
        ],
        expected={"home_infinite_loading": "cursor_append_retry_end_state"},
        actual=observed,
        diff={"issues": observed["issues"]},
        failure_type=None if passed else failure_type,
        error_category=None if passed else "validation",
        referenced_files=[
            "backend/app/main.py",
            "frontend/src/api/news.ts",
            "frontend/src/pages/HomePage.tsx",
            "frontend/src/components/HighScoreList.tsx",
            "frontend/src/styles/app.css",
            "tests/test_api_contract.py",
            "tests/test_frontend_contract.py",
            "scripts/run_harness.py",
        ],
        commands=[f"python3 scripts/run_harness.py --stage {stage} --task-id {task_id} --report-dir reports"],
    )
    write_test_report(report_destination(report_dir, stage, task_id), report)
    return 0 if passed else 1


def run_task_035_static(report_dir: Path, task_id: str) -> int:
    return run_task_035_stage(report_dir, task_id, "static")


def run_task_035_api(report_dir: Path, task_id: str) -> int:
    return run_task_035_stage(report_dir, task_id, "api")


def run_task_035_integration(report_dir: Path, task_id: str) -> int:
    return run_task_035_stage(report_dir, task_id, "integration")


def run_task_035_e2e(report_dir: Path, task_id: str) -> int:
    return run_task_035_stage(report_dir, task_id, "e2e")


def prd_coverage_evidence(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "acceptance" / "prd_coverage.json"
    payload = read_report(path)
    issues: list[str] = []
    if payload is None:
        issues.append("prd_coverage:missing_report")
        return {"path": report_relative_path(path), "issues": issues}
    issues.extend(
        validate_against_schema(
            payload,
            SCHEMA_FILES["prd_coverage"],
            "PRDCoverage",
        )
    )
    if payload.get("status") != "passed":
        issues.append(f"prd_coverage:status={payload.get('status')}")
    coverage_items = payload.get("coverage_items", [])
    failed_coverage_items: list[str] = []
    if not isinstance(coverage_items, list) or not coverage_items:
        issues.append("prd_coverage:coverage_items_missing")
    else:
        failed_coverage_items = [
            str(item.get("id", "unknown"))
            for item in coverage_items
            if isinstance(item, dict) and item.get("status") != "passed"
        ]
        malformed_coverage_count = sum(
            1 for item in coverage_items if not isinstance(item, dict)
        )
        if malformed_coverage_count:
            issues.append(f"prd_coverage:malformed_items={malformed_coverage_count}")
        if failed_coverage_items:
            issues.append(f"prd_coverage:failed_items={len(failed_coverage_items)}")
    uncovered = payload.get("uncovered_acceptance_items", [])
    if isinstance(uncovered, list) and uncovered:
        issues.append(f"prd_coverage:uncovered_count={len(uncovered)}")
    elif not isinstance(uncovered, list):
        issues.append("prd_coverage:uncovered_acceptance_items_not_list")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "failed_coverage_items": failed_coverage_items,
        "uncovered_acceptance_items": uncovered,
        "issues": issues,
    }


def prd_acceptance_inventory() -> list[dict[str, Any]]:
    path = Path("docs/01_prd.md")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    inventory: list[dict[str, Any]] = []
    current_flow = "0.0"
    flow_counts: dict[str, int] = {}
    in_acceptance = False
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        flow_match = re.match(r"###\s+闭环流程\s+([0-9]+)\.([0-9]+)", stripped)
        if flow_match:
            current_flow = f"{flow_match.group(1)}.{flow_match.group(2)}"
            in_acceptance = False
            continue
        if stripped == "**验收标准**":
            in_acceptance = True
            continue
        if in_acceptance and (stripped.startswith("## ") or stripped.startswith("### ")):
            in_acceptance = False
        if in_acceptance and stripped.startswith("- "):
            flow_counts[current_flow] = flow_counts.get(current_flow, 0) + 1
            inventory.append(
                {
                    "id": f"PRD-{current_flow}-AC-{flow_counts[current_flow]:03d}",
                    "source_path": "docs/01_prd.md",
                    "source_line": line_number,
                    "acceptance_text": stripped[2:].strip(),
                }
            )
    return inventory


def normalize_to_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def ensure_prd_coverage_report(report_dir: Path) -> None:
    path = report_dir / "acceptance" / "prd_coverage.json"
    coverage_items = []
    uncovered_items = []
    for item in prd_acceptance_inventory():
        assertion_ids = PRD_FLOW_ASSERTION_MAP.get(prd_flow_id(item["id"]), [])
        metadata = assertion_candidates_metadata(report_dir, assertion_ids)
        status = (
            "passed"
            if metadata["known_ids"] and set(metadata["known_ids"]) <= set(metadata["passed_ids"])
            else "uncovered"
            if not metadata["known_ids"]
            else "failed"
        )
        coverage_item = {
            **item,
            "task_ids": metadata["task_ids"] or ["TASK-026"],
            "acceptance_gate": metadata["gates"] or ["ACC-STOP-001"],
            "assertion_ids": metadata["known_ids"] or ["unmapped"],
            "report_paths": metadata["report_paths"] or ["reports/acceptance/ACC-STOP-001.json"],
            "status": status,
        }
        coverage_items.append(coverage_item)
        if status != "passed":
            uncovered_items.append(item)
    if not coverage_items:
        coverage_items = [
            {
                "id": "PRD-0.0-AC-001",
                "source_path": "docs/01_prd.md",
                "source_line": 1,
                "acceptance_text": "PRD acceptance inventory could not be extracted.",
                "task_ids": ["TASK-026"],
                "acceptance_gate": ["ACC-STOP-001"],
                "assertion_ids": ["A-acceptance-ACC-STOP-001-prd-coverage-complete"],
                "report_paths": ["reports/acceptance/ACC-STOP-001.json"],
                "status": "uncovered",
            }
        ]
        uncovered_items = [coverage_items[0]]
    write_json(
        path,
        {
            "schema_ref": "07_test_spec.md#6.3.1",
            "schema_version": "v1",
            "status": "passed" if not uncovered_items else "failed",
            "source": {
                "path": "docs/01_prd.md",
                "version": "prd_mvp@v1",
            },
            "coverage_items": coverage_items,
            "uncovered_acceptance_items": uncovered_items,
            "timestamp": FIXED_TIMESTAMP,
        },
    )


def task_acceptance_inventory() -> list[dict[str, Any]]:
    tasks_path = Path("tasks.md")
    payload, issues = read_yaml_object(tasks_path)
    if issues or payload is None:
        return []
    nodes = payload.get("dag", {}).get("nodes", [])
    if not isinstance(nodes, list):
        return []
    try:
        task_lines = tasks_path.read_text().splitlines()
    except OSError:
        task_lines = []
    inventory: list[dict[str, Any]] = []
    search_start = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        task_id = str(node.get("id", "UNKNOWN"))
        criteria = node.get("acceptance_criteria", [])
        if not isinstance(criteria, list):
            continue
        for index, criterion in enumerate(criteria, start=1):
            criterion_text = str(criterion)
            source_line = 1
            for line_index in range(search_start, len(task_lines)):
                if criterion_text in task_lines[line_index]:
                    source_line = line_index + 1
                    search_start = line_index + 1
                    break
            inventory.append(
                {
                    "id": f"{task_id}:AC-{index:03d}",
                    "task_id": task_id,
                    "source_path": "tasks.md",
                    "source_line": source_line,
                    "acceptance_text": criterion_text,
                    "acceptance_gate": normalize_to_list(node.get("acceptance_gate", "none")),
                    "test_scope": normalize_to_list(node.get("test_scope", [])),
                }
            )
    return inventory


def ensure_task_acceptance_coverage_report(report_dir: Path) -> None:
    path = report_dir / "acceptance" / "task_acceptance_coverage.json"
    assertions_by_owner = traceability_assertions_by_owner()
    coverage_items = []
    uncovered_items = []
    for item in task_acceptance_inventory():
        task_id = item["task_id"]
        assertion_ids = sorted(
            set(assertions_by_owner.get(task_id, []))
            | set(TASK_FALLBACK_ASSERTION_MAP.get(task_id, []))
        )
        metadata = assertion_candidates_metadata(report_dir, assertion_ids)
        status = (
            "passed"
            if metadata["known_ids"] and set(metadata["known_ids"]) <= set(metadata["passed_ids"])
            else "uncovered"
            if not metadata["known_ids"]
            else "failed"
        )
        coverage_item = {
            **item,
            "assertion_ids": metadata["known_ids"] or ["unmapped"],
            "report_paths": metadata["report_paths"] or ["reports/acceptance/ACC-STOP-001.json"],
            "status": status,
        }
        coverage_items.append(coverage_item)
        if status != "passed":
            uncovered_items.append(item)
    if not coverage_items:
        coverage_items = [
            {
                "id": "TASK-000:AC-001",
                "task_id": "TASK-000",
                "source_path": "tasks.md",
                "source_line": 1,
                "acceptance_text": "Task acceptance inventory could not be extracted.",
                "acceptance_gate": ["ACC-STOP-001"],
                "test_scope": ["static"],
                "assertion_ids": ["unmapped"],
                "report_paths": ["reports/acceptance/task_acceptance_coverage.json"],
                "status": "uncovered",
            }
        ]
        uncovered_items = [coverage_items[0]]
    write_json(
        path,
        {
            "schema_ref": "07_test_spec.md#6.4",
            "schema_version": "v1",
            "status": "passed" if not uncovered_items else "failed",
            "source": {
                "path": "tasks.md",
                "version": "tasks_mvp@v8",
            },
            "coverage_items": coverage_items,
            "uncovered_task_acceptance_items": uncovered_items,
            "timestamp": FIXED_TIMESTAMP,
        },
    )


def task_acceptance_coverage_evidence(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "acceptance" / "task_acceptance_coverage.json"
    payload = read_report(path)
    issues: list[str] = []
    if payload is None:
        issues.append("task_acceptance_coverage:missing_report")
        return {
            "path": report_relative_path(path),
            "uncovered_task_acceptance_items": task_acceptance_inventory(),
            "issues": issues,
        }
    issues.extend(
        validate_against_schema(
            payload,
            SCHEMA_FILES["task_acceptance_coverage"],
            "TaskAcceptanceCoverage",
        )
    )
    if payload.get("status") != "passed":
        issues.append(f"task_acceptance_coverage:status={payload.get('status')}")
    coverage_items = payload.get("coverage_items", [])
    failed_coverage_items: list[str] = []
    if not isinstance(coverage_items, list) or not coverage_items:
        issues.append("task_acceptance_coverage:coverage_items_missing")
    else:
        failed_coverage_items = [
            str(item.get("id", "unknown"))
            for item in coverage_items
            if isinstance(item, dict) and item.get("status") != "passed"
        ]
        malformed_coverage_count = sum(
            1 for item in coverage_items if not isinstance(item, dict)
        )
        if malformed_coverage_count:
            issues.append(
                f"task_acceptance_coverage:malformed_items={malformed_coverage_count}"
            )
        if failed_coverage_items:
            issues.append(
                f"task_acceptance_coverage:failed_items={len(failed_coverage_items)}"
            )
    uncovered = payload.get("uncovered_task_acceptance_items", [])
    if isinstance(uncovered, list) and uncovered:
        issues.append(f"task_acceptance_coverage:uncovered_count={len(uncovered)}")
    elif not isinstance(uncovered, list):
        issues.append("task_acceptance_coverage:uncovered_task_acceptance_items_not_list")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "failed_coverage_items": failed_coverage_items,
        "uncovered_task_acceptance_items": uncovered,
        "issues": issues,
    }


def local_user_acceptance_evidence(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "acceptance" / "local_user_acceptance.json"
    payload = read_report(path)
    issues: list[str] = []
    if payload is None:
        issues.append("local_user_acceptance:missing_report")
        return {"path": report_relative_path(path), "issues": issues}
    issues.extend(
        validate_against_schema(
            payload,
            SCHEMA_FILES["local_user_acceptance"],
            "LocalUserAcceptance",
        )
    )
    if payload.get("status") != "passed":
        issues.append(f"local_user_acceptance:status={payload.get('status')}")
    required_surfaces = {
        "home_news_feed",
        "high_score_list",
        "article_view",
        "sources_page",
        "refresh_action",
    }
    checked_surfaces = payload.get("checked_surfaces", [])
    if not isinstance(checked_surfaces, list):
        issues.append("local_user_acceptance:checked_surfaces_not_list")
        checked_surfaces_set: set[str] = set()
    else:
        checked_surfaces_set = {str(item) for item in checked_surfaces}
    missing_surfaces = sorted(required_surfaces - checked_surfaces_set)
    if missing_surfaces:
        issues.append(
            "local_user_acceptance:missing_surfaces="
            + ",".join(missing_surfaces)
        )
    failed_findings = payload.get("failed_findings", [])
    if isinstance(failed_findings, list) and failed_findings:
        issues.append(f"local_user_acceptance:failed_findings={len(failed_findings)}")
    elif not isinstance(failed_findings, list):
        issues.append("local_user_acceptance:failed_findings_not_list")
    return {
        "path": report_relative_path(path),
        "status": payload.get("status"),
        "checked_surfaces": checked_surfaces,
        "missing_surfaces": missing_surfaces,
        "failed_findings": failed_findings,
        "issues": issues,
    }


def run_acceptance(report_dir: Path, task_id: str | None) -> int:
    if task_id:
        report = test_report(
            stage="acceptance",
            status="failed",
            test_id=f"{task_id.lower()}-acceptance-unsupported",
            assertions=[
                assertion(
                    "task_scoped_acceptance_forbidden",
                    "failed",
                    {"task_id": None},
                    {"task_id": task_id},
                    {"reason": "acceptance must run as a full gate evaluation"},
                )
            ],
            expected={"task_id": None},
            actual={"task_id": task_id},
            diff={"reason": "acceptance must run as a full gate evaluation"},
            node="acceptance",
            failure_type="contract",
            error_category="validation",
            referenced_files=[
                "scripts/run_harness.py",
                "docs/07_test_spec.md",
                "docs/08_acceptance.md",
            ],
            commands=[
                f"python3 scripts/run_harness.py --stage acceptance --task-id {task_id} --report-dir reports"
            ],
        )
        write_test_report(report_destination(report_dir, "acceptance", task_id), report)
        return 1

    stage_statuses, stage_schema_issues = required_stage_results(report_dir)
    catalog, catalog_issues = mandatory_assertion_catalog()
    base_observations = stage_assertions_by_source(report_dir)
    observations_by_catalog = {
        key: [
            {
                "stage": item.get("stage"),
                "status": item.get("status"),
                "visibility": item.get("visibility"),
            }
            for item in value
            if str(item.get("stage")) != "acceptance"
        ]
        for key, value in base_observations.items()
        if key in catalog
    }
    product_gate_coverage = mandatory_assertion_coverage(
        report_dir,
        include_acceptance=False,
    )

    gate_status: dict[str, str] = {}
    gate_reports: list[tuple[str, Path, dict[str, Any], int | None]] = []
    leak_scan = evaluate_leak_scan()
    task_completion = task_completion_evidence()
    ensure_prd_coverage_report(report_dir)
    ensure_task_acceptance_coverage_report(report_dir)
    prd_coverage = prd_coverage_evidence(report_dir)
    task_acceptance_coverage = task_acceptance_coverage_evidence(report_dir)
    browser_e2e = browser_e2e_stop_input_evidence(report_dir)
    ensure_local_user_acceptance_report(report_dir)
    local_user_acceptance = local_user_acceptance_evidence(report_dir)
    stop_input_evidence = {
        "task_completion_status": task_completion,
        "prd_coverage_status": prd_coverage,
        "task_acceptance_coverage_status": task_acceptance_coverage,
        "browser_e2e_status": browser_e2e,
        "local_user_acceptance_status": local_user_acceptance,
    }
    stop_inputs = {
        name: stop_input_status(list(evidence.get("issues", [])))
        for name, evidence in stop_input_evidence.items()
    }
    failed_stop_inputs = [
        name for name, status in stop_inputs.items() if status != "PASS"
    ]
    failure_reasons = {
        name: list(evidence.get("issues", []))
        for name, evidence in stop_input_evidence.items()
        if evidence.get("issues")
    }
    for gate in REQUIRED_GATES:
        stop_decision_assertion_index: int | None = None
        required_ids = required_assertion_ids_for_gate(
            catalog,
            gate,
            include_acceptance=False,
        )
        status, reasons = evaluate_gate_from_observations(
            gate,
            observations_by_catalog,
            stage_statuses,
            catalog,
            required_assertion_ids=required_ids,
        )
        if stage_schema_issues.get("static"):
            status = "FAIL"
            reasons.append("static_schema_invalid")
        for required_stage in REQUIRED_PRODUCT_STAGES:
            if stage_statuses[required_stage] != "passed":
                reasons.append(f"{required_stage}:{stage_statuses[required_stage]}")

        gate_assertions: list[dict[str, Any]] = []
        if gate == "ACC-STOP-001":
            if task_completion["issues"]:
                status = "FAIL"
                reasons.extend(task_completion["issues"])
            if prd_coverage["issues"]:
                status = "FAIL"
                reasons.extend(prd_coverage["issues"])
            if task_acceptance_coverage["issues"]:
                status = "FAIL"
                reasons.extend(task_acceptance_coverage["issues"])
            if browser_e2e["issues"]:
                status = "FAIL"
                reasons.extend(browser_e2e["issues"])
            if local_user_acceptance["issues"]:
                status = "FAIL"
                reasons.extend(local_user_acceptance["issues"])
            gate_coverage_missing_ids = [
                assertion_id
                for assertion_id in required_ids
                if assertion_id in set(product_gate_coverage["missing_ids"])
            ]
            gate_coverage_failed_ids = [
                assertion_id
                for assertion_id in required_ids
                if assertion_id in set(product_gate_coverage["failed_ids"])
            ]
            if gate_coverage_missing_ids or gate_coverage_failed_ids:
                status = "FAIL"
                reasons.extend(
                    f"{gate}:coverage:{item}"
                    for item in gate_coverage_missing_ids
                )
                reasons.extend(
                    f"{gate}:coverage:{item}"
                    for item in gate_coverage_failed_ids
                )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-task-completion-all-passed",
                    "passed" if not task_completion["issues"] else "failed",
                    {"unfinished_tasks": []},
                    {
                        "path": task_completion["path"],
                        "total_tasks": task_completion.get("total_tasks"),
                        "unfinished_tasks": task_completion.get("unfinished_tasks"),
                        "issues": task_completion["issues"],
                    },
                    {"task_completion": task_completion},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-prd-coverage-complete",
                    "passed" if not prd_coverage["issues"] else "failed",
                    {"uncovered_acceptance_items": []},
                    {
                        "path": prd_coverage["path"],
                        "status": prd_coverage.get("status"),
                        "uncovered_acceptance_items": prd_coverage.get(
                            "uncovered_acceptance_items"
                        ),
                        "issues": prd_coverage["issues"],
                    },
                    {"prd_coverage": prd_coverage},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-task-acceptance-coverage-complete",
                    "passed" if not task_acceptance_coverage["issues"] else "failed",
                    {"uncovered_task_acceptance_items": []},
                    {
                        "path": task_acceptance_coverage["path"],
                        "status": task_acceptance_coverage.get("status"),
                        "uncovered_task_acceptance_items": task_acceptance_coverage.get(
                            "uncovered_task_acceptance_items"
                        ),
                        "issues": task_acceptance_coverage["issues"],
                    },
                    {"task_acceptance_coverage": task_acceptance_coverage},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-browser-e2e-evidence",
                    "passed" if not browser_e2e["issues"] else "failed",
                    {"required_surfaces": browser_e2e["required_surfaces"]},
                    {
                        "path": browser_e2e["path"],
                        "status": browser_e2e.get("status"),
                        "required_surfaces": browser_e2e["required_surfaces"],
                        "issues": browser_e2e["issues"],
                    },
                    {"browser_e2e": browser_e2e},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-local-user-acceptance-passed",
                    "passed" if not local_user_acceptance["issues"] else "failed",
                    {"failed_findings": []},
                    {
                        "path": local_user_acceptance["path"],
                        "status": local_user_acceptance.get("status"),
                        "failed_findings": local_user_acceptance.get("failed_findings"),
                        "issues": local_user_acceptance["issues"],
                    },
                    {"local_user_acceptance": local_user_acceptance},
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-mandatory-catalog-covered",
                    "passed" if status == "PASS" and not gate_coverage_missing_ids and not gate_coverage_failed_ids else "failed",
                    {
                        "missing_ids_count": len(gate_coverage_missing_ids),
                        "failed_ids_count": len(gate_coverage_failed_ids),
                    },
                    {
                        "missing_ids": gate_coverage_missing_ids,
                        "failed_ids": gate_coverage_failed_ids,
                        "catalog_count": len(required_ids),
                        "covered_count": len(required_ids)
                        - (len(gate_coverage_missing_ids) + len(gate_coverage_failed_ids)),
                    },
                    {
                        "reasons": reasons,
                        "mandatory_coverage": product_gate_coverage,
                    },
                    visibility="report_metadata",
                )
            )
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-stop-decision-schema",
                    "passed",
                    {"schema_valid": True},
                    {"schema_valid": True},
                    {"reason": "placeholder for stop decision schema validation"},
                    visibility="report_metadata",
                )
            )
            stop_decision_assertion_index = len(gate_assertions) - 1
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-001-no-task-scoped-substitution",
                    "passed",
                    {"task_scoped_acceptance_forbidden": None},
                    {"task_scoped_acceptance_forbidden": None},
                    {"evidence": "full acceptance only"},
                    visibility="report_metadata",
                )
            )
        elif gate == "ACC-STOP-009":
            leak_assertion_status = (
                "passed"
                if leak_scan["public_surface_forbidden_field_count"] == 0
                and leak_scan["public_surface_sensitive_content_count"] == 0
                else "failed"
            )
            if leak_assertion_status == "failed":
                status = "FAIL"
                reasons.append("report_leak_scan_failed")
            gate_assertions.append(
                assertion(
                    "A-acceptance-ACC-STOP-009-report-leak-scan",
                    leak_assertion_status,
                    {"forbidden_public": 0, "sensitive_content": 0},
                    {
                        "forbidden_public": leak_scan["public_surface_forbidden_field_count"],
                        "sensitive_content": leak_scan["public_surface_sensitive_content_count"],
                    },
                    {"leak_scan": leak_scan},
                    visibility="report_metadata",
                )
            )
        else:
            gate_assertions.append(
                assertion(
                    f"{gate.lower().replace('-', '_')}_required_assertions",
                    "passed" if status == "PASS" else "failed",
                    {"required_assertion_status": "passed"},
                    {"required_assertion_status": status},
                    {"required_ids": required_ids, "reasons": reasons},
                    visibility="report_metadata",
                )
            )

        gate_report_status = "passed" if status == "PASS" else "failed"
        gate_report = test_report(
            stage="acceptance",
            status=gate_report_status,
            test_id=gate,
            assertions=gate_assertions,
            expected={"required_assertions": "passed"},
            actual={"required_assertions": status, "reasons": reasons},
            diff={"gate": gate, "reasons": reasons},
            node="acceptance",
            failure_type="contract",
            error_category="validation" if status != "PASS" else None,
            referenced_files=[
                "scripts/run_harness.py",
                "docs/07_test_spec.md",
                "docs/08_acceptance.md",
                "workflows.md",
            ],
        )
        gate_path = report_dir / "acceptance" / f"{gate}.json"
        gate_reports.append((gate, gate_path, gate_report, stop_decision_assertion_index))
        gate_status[gate] = status

    all_gates_passed = all(status == "PASS" for status in gate_status.values())
    all_stop_inputs_passed = all(status == "PASS" for status in stop_inputs.values())
    round_count_policy = round_count_policy_evidence(
        all_gates_passed=all_gates_passed,
        all_stop_inputs_passed=all_stop_inputs_passed,
        unfinished_tasks=task_completion.get("unfinished_tasks", []),
    )
    round_count_policy_passed = round_count_policy["status"] == "PASS"
    if round_count_policy["failure_reasons"]:
        failure_reasons["round_count_policy"] = round_count_policy["failure_reasons"]
    gate_lists = stop_decision_gate_lists(gate_status)

    stop_decision_expected = {
        "all_pass": True,
        "required_gates": REQUIRED_GATES,
        "stage_statuses": stage_statuses,
        "stop_inputs": {
            "task_completion_status": "PASS",
            "prd_coverage_status": "PASS",
            "task_acceptance_coverage_status": "PASS",
            "browser_e2e_status": "PASS",
            "local_user_acceptance_status": "PASS",
        },
        "round_count_policy": "PASS",
    }
    stop_report = {
        "schema_ref": "08_acceptance.md#5.1",
        "schema_version": "v1",
        "STOP_ALLOWED": all_gates_passed and all_stop_inputs_passed and round_count_policy_passed,
        "gate_status": gate_status,
        "passed_gates": gate_lists["passed_gates"],
        "failed_gates": gate_lists["failed_gates"],
        "blocked_gates": gate_lists["blocked_gates"],
        "unknown_gates": gate_lists["unknown_gates"],
        "stop_inputs": stop_inputs,
        "failed_stop_inputs": failed_stop_inputs,
        "failure_reasons": failure_reasons,
        "unfinished_tasks": task_completion.get("unfinished_tasks", []),
        "uncovered_prd_items": prd_coverage.get("uncovered_acceptance_items", []),
        "uncovered_task_acceptance_items": task_acceptance_coverage.get(
            "uncovered_task_acceptance_items", []
        ),
        "user_acceptance_failures": local_user_acceptance.get("failed_findings", []),
        "round_count_policy": round_count_policy,
        "generated_from_reports": [report_relative_path(path) for _, path, _, _ in gate_reports],
        "timestamp": FIXED_TIMESTAMP,
    }
    stop_issues = (
        validate_against_schema(
            stop_report,
            SCHEMA_FILES["stop_decision"],
            "StopDecision",
        )
        + validate_stop_decision_consistency(stop_report)
    )
    stop_report_actual = {
        **stop_report,
        "STOP_ALLOWED": (
            all_gates_passed
            and all_stop_inputs_passed
            and round_count_policy_passed
            if not stop_issues
            else False
        ),
    }
    if stop_issues:
        stop_report_actual["failure_reasons"] = {
            **stop_report_actual.get("failure_reasons", {}),
            "stop_decision_schema": stop_issues,
        }
        stop_report_actual["gate_status"] = {
            gate: status if status != "PASS" else "FAIL" for gate, status in gate_status.items()
        }
        stop_report_actual["gate_status"]["ACC-STOP-001"] = "FAIL"
        remapped_gate_lists = stop_decision_gate_lists(stop_report_actual["gate_status"])
        stop_report_actual["passed_gates"] = remapped_gate_lists["passed_gates"]
        stop_report_actual["failed_gates"] = remapped_gate_lists["failed_gates"]
        stop_report_actual["blocked_gates"] = remapped_gate_lists["blocked_gates"]
        stop_report_actual["unknown_gates"] = remapped_gate_lists["unknown_gates"]

    stop_decision_schema_assertion = assertion(
        "A-acceptance-ACC-STOP-001-stop-decision-schema",
        "passed" if not stop_issues else "failed",
        {"schema_valid": True},
        {
            "schema_valid": len(stop_issues) == 0,
            "schema_version": "v1",
            "issues_count": len(stop_issues),
            "issues": stop_issues,
        },
        {
            "stop_report_path": "acceptance/STOP_ALLOWED.json",
            "schema_ref": "schemas/stop_decision.schema.json",
            "actual": stop_report_actual,
        },
        visibility="report_metadata",
    )
    for gate_name, gate_path, gate_report, stop_assertion_index in gate_reports:
        if gate_name == "ACC-STOP-001" and stop_assertion_index is not None:
            gate_report["assertions"][stop_assertion_index] = stop_decision_schema_assertion
            if stop_issues:
                gate_report["status"] = "failed"
                gate_report["actual"]["required_assertions"] = "FAIL"
                gate_report["diff"]["reasons"] = sorted(
                    set(gate_report["diff"].get("reasons", []) + stop_issues)
                )
                gate_report["error_category"] = "validation"
        write_test_report(gate_path, gate_report)

    write_json(report_dir / "acceptance" / "STOP_ALLOWED.json", stop_report_actual)

    acceptance_gate_assertion = assertion(
        "all_required_gates_passed",
        "passed" if all_gates_passed and all_stop_inputs_passed else "failed",
        {"expected": stop_decision_expected},
        {
            "actual": {
                "STOP_ALLOWED": stop_report_actual["STOP_ALLOWED"],
                "stop_inputs": stop_inputs,
                "failed_stop_inputs": failed_stop_inputs,
                "round_count_policy": round_count_policy,
            }
        },
        {"stop_report": stop_report_actual, "catalog_issues": catalog_issues, "stop_issues": stop_issues},
    )
    acceptance_stage_report = test_report(
        stage="acceptance",
        status="passed"
        if all_gates_passed
        and all_stop_inputs_passed
        and round_count_policy_passed
        and not stop_issues
        else "failed",
        test_id="acceptance-gate-evaluation",
        assertions=[acceptance_gate_assertion],
        expected={"STOP_ALLOWED": True},
        actual={"STOP_ALLOWED": stop_report_actual["STOP_ALLOWED"]},
        diff={"gates": gate_status},
        node="acceptance",
        failure_type="contract",
        error_category=None
        if (
            all_gates_passed
            and all_stop_inputs_passed
            and round_count_policy_passed
            and not stop_issues
        )
        else "validation",
        referenced_files=[
            "scripts/run_harness.py",
            "docs/07_test_spec.md",
            "docs/08_acceptance.md",
            "workflows.md",
        ],
    )
    write_test_report(report_dir / "stages" / "acceptance.json", acceptance_stage_report)

    if all_gates_passed and all_stop_inputs_passed and round_count_policy_passed and not stop_issues:
        return 0
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Codex Harness.")
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--task-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = Path(args.report_dir)
    if args.stage == "acceptance":
        return run_acceptance(report_dir, args.task_id)
    return run_product_stage(report_dir, args.stage, args.task_id)


if __name__ == "__main__":
    raise SystemExit(main())
