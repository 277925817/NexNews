import json
from pathlib import Path
import threading

from fastapi.testclient import TestClient

from backend.app.db import connect, initialize_database, seed_default_sources
from backend.app.main import create_app
from backend.app.services.trigger import run_manual_refresh, run_scheduled_refresh
from backend.app.services.pipeline import (
    LIVE_SCORING_SYSTEM_PROMPT,
    build_scoring_request,
    build_translation_request,
    backfill_top_scored_translations,
    fallback_ai_value_record,
    fetch_selected_content,
    has_valid_translation_record,
    ingest_fixture_rss,
    ingest_live_rss,
    parse_rss_feed_text,
    read_json,
    request_live_scoring,
    request_live_translation,
    run_fixture_pipeline_summary,
    run_live_pipeline_summary,
    score_raw_news,
    score_raw_news_live,
    score_request_with_fixture,
    validate_scoring_response,
    selected_fetch_candidates,
    score_is_selected,
    translate_fetched_content,
    top_scored_fetched_news_for_translation,
    translation_records,
)


def make_client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "rss.sqlite3")))


def assert_readable_translation(summary: str, content: str, *keywords: str) -> None:
    paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]
    assert len(summary) >= 28
    assert len(content) >= 110
    assert len(paragraphs) >= 2
    assert any(keyword in summary for keyword in keywords)
    assert any(keyword in content for keyword in keywords)

def test_live_refresh_can_use_rss_summary_without_fetching_article_pages(monkeypatch):
    conn = connect(":memory:")
    initialize_database(conn)
    conn.execute(
        """
        INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES ('Live AI Source', 'https://live.example/rss.xml', 1, 'twice_daily', '2026-07-01T00:00:00Z')
        """
    )
    requested_urls = []

    def fake_fetch_url_text(url, **_kwargs):
        requested_urls.append(url)
        if url == "https://live.example/rss.xml":
            return """
            <rss><channel>
              <item>
                <title>Live AI agents reach useful production workflows</title>
                <link>https://live.example/articles/agents-production</link>
                <guid>live-agents-production</guid>
                <pubDate>Mon, 29 Jun 2026 00:00:00 GMT</pubDate>
                <description>Teams report that AI agents are now handling repeatable production workflows with better evaluation and observability.</description>
              </item>
            </channel></rss>
            """, None
        return None, "article_fetch_should_not_run"

    monkeypatch.setattr("backend.app.services.pipeline.fetch_url_text", fake_fetch_url_text)

    result = run_manual_refresh(
        conn,
        now="2026-07-01T00:00:00Z",
        use_live_data=True,
        allow_live_network=True,
        allow_live_llm=False,
        allow_live_article_fetch=False,
        request_timeout_seconds=1,
        request_retry_count=0,
    )

    row = conn.execute(
        """
        SELECT original_title, title_zh, summary_zh, content_zh, pipeline_state, is_selected
        FROM news_item
        WHERE rss_guid = 'live-agents-production'
        """
    ).fetchone()

    assert result["started"] is True
    assert requested_urls == ["https://live.example/rss.xml"]
    assert row["is_selected"] == 1
    assert row["pipeline_state"] == "fetched"
    assert row["title_zh"] == row["original_title"]
    assert "production workflows" in row["summary_zh"]
    assert "production workflows" in row["content_zh"]

def test_live_pipeline_uses_configured_live_llm_concurrency(monkeypatch):
    from backend.app.services import pipeline

    conn = connect(":memory:")
    initialize_database(conn)
    conn.execute(
        """
        INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES ('Live AI Source', 'https://live.example/rss.xml', 1, 'twice_daily', '2026-07-01T00:00:00Z')
        """
    )
    executor_workers = []
    scoring_requests = []

    class FakeExecutor:
        def __init__(self, *, max_workers):
            executor_workers.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *_exc_info):
            return False

        def map(self, func, rows):
            return [func(row) for row in rows]

    def fake_fetch_url_text(url, **_kwargs):
        if url == "https://live.example/rss.xml":
            return """
            <rss><channel>
              <item>
                <title>Live AI agents improve production workflows one</title>
                <link>https://live.example/articles/agents-production-one</link>
                <guid>live-agents-production-one</guid>
                <pubDate>Tue, 30 Jun 2026 00:00:00 GMT</pubDate>
                <description>AI agents improve production workflows with better evaluation and observability.</description>
              </item>
              <item>
                <title>Live AI agents improve production workflows two</title>
                <link>https://live.example/articles/agents-production-two</link>
                <guid>live-agents-production-two</guid>
                <pubDate>Tue, 30 Jun 2026 00:00:00 GMT</pubDate>
                <description>AI agents improve production workflows with better evaluation and observability.</description>
              </item>
              <item>
                <title>Live AI agents improve production workflows three</title>
                <link>https://live.example/articles/agents-production-three</link>
                <guid>live-agents-production-three</guid>
                <pubDate>Tue, 30 Jun 2026 00:00:00 GMT</pubDate>
                <description>AI agents improve production workflows with better evaluation and observability.</description>
              </item>
            </channel></rss>
            """, None
        return None, "unexpected_url"

    def fake_request_live_translation(request, **_kwargs):
        return {
            "title_zh": f"并发翻译：{request['original_title']}",
            "summary_zh": "并发配置测试返回中文摘要，证明 live LLM 批次可按配置执行。",
            "content_zh": "并发配置测试返回中文正文第一段。\n\n并发配置测试返回中文正文第二段。",
            "category_zh": "产品",
        }, None

    def fake_request_live_scoring(request, **_kwargs):
        scoring_requests.append(request)
        return {
            "is_ai_news": True,
            "ai_relevance_score": 92,
            "score": 92,
            "reason": "Live LLM scoring selected this item.",
        }, None

    monkeypatch.setattr(pipeline, "fetch_url_text", fake_fetch_url_text)
    monkeypatch.setattr(pipeline, "request_live_scoring", fake_request_live_scoring)
    monkeypatch.setattr(pipeline, "request_live_translation", fake_request_live_translation)
    monkeypatch.setattr(pipeline, "ThreadPoolExecutor", FakeExecutor)

    summary = run_live_pipeline_summary(
        conn,
        now="2026-07-01T00:00:00Z",
        allow_live_network=True,
        allow_live_llm=True,
        allow_live_article_fetch=False,
        request_timeout_seconds=1,
        request_retry_count=0,
        live_rss_concurrency=1,
        live_llm_base_url="https://llm.example.test/api/v4",
        live_llm_api_key="secret-token",
        live_llm_model="glm-test",
        live_llm_max_items=3,
        live_llm_concurrency=2,
    )

    assert summary["translated_item_count"] == 3
    assert len(scoring_requests) == 3
    assert executor_workers[-1] == 2

def test_fixture_pipeline_run_summary_reports_core_counts_and_failures():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)

    summary = run_fixture_pipeline_summary(conn)

    assert summary["started_at"] == "2026-06-28T09:00:00Z"
    assert summary["finished_at"] == "2026-06-28T09:00:00Z"
    assert summary["source_success_count"] == 32
    assert summary["source_failure_count"] == 1
    assert summary["rss_item_count"] == 16
    assert summary["new_item_count"] == 15
    assert summary["scored_item_count"] == 15
    assert summary["selected_item_count"] == 13
    assert summary["fetched_item_count"] == 13
    assert summary["translated_item_count"] == 11
    assert summary["failure_details"] == {
        "crawl:parsing": 1,
        "fetch:network": 11,
        "translate:validation_llm_error": 1,
    }

def test_refresh_trigger_signal_manual_schedule_and_concurrency():
    manual_conn = connect(":memory:")
    initialize_database(manual_conn)
    seed_default_sources(manual_conn)
    manual_result = run_manual_refresh(manual_conn)

    morning_conn = connect(":memory:")
    initialize_database(morning_conn)
    seed_default_sources(morning_conn)
    morning_result = run_scheduled_refresh(morning_conn, now="2026-06-28T09:00:00Z")

    evening_conn = connect(":memory:")
    initialize_database(evening_conn)
    seed_default_sources(evening_conn)
    evening_result = run_scheduled_refresh(evening_conn, now="2026-06-28T18:00:00Z")

    idle_conn = connect(":memory:")
    initialize_database(idle_conn)
    seed_default_sources(idle_conn)
    idle_result = run_scheduled_refresh(idle_conn, now="2026-06-28T10:00:00Z")

    rejected_conn = connect(":memory:")
    initialize_database(rejected_conn)
    seed_default_sources(rejected_conn)
    rejected_result = run_manual_refresh(rejected_conn, is_running=True)

    assert manual_result["started"] is True
    assert manual_result["summary"]["translated_item_count"] == 11
    assert morning_result["started"] is True
    assert evening_result["started"] is True
    assert idle_result == {"started": False, "reason": "not_scheduled_time", "summary": None}
    assert rejected_result == {"started": False, "reason": "already_running", "summary": None}
    assert rejected_conn.execute("SELECT COUNT(*) AS count FROM processing_log").fetchone()["count"] == 0

def test_refresh_runs_fixture_pipeline_with_dedupe_threshold_fetch_and_translation(tmp_path):
    client = make_client(tmp_path)
    conn = client.app.state.db

    response = client.post("/api/refresh")

    assert response.status_code == 200
    assert response.json() == {"data": {"refreshed_at": "2026-06-28T09:00:00Z"}}

    rows = conn.execute(
        """
        SELECT
          id, rss_guid, canonical_url, score, is_ai_news,
          ai_relevance_score, pipeline_state, is_selected, content_raw,
          content_full, title_zh, summary_zh, content_zh, has_translate_failed
        FROM news_item
        ORDER BY canonical_url ASC
        """
    ).fetchall()
    assert len(rows) == 15

    by_guid = {row["rss_guid"]: row for row in rows}
    assert set(by_guid) >= {
        "fixture-low-59",
        "fixture-threshold-60",
        "fixture-translated-96",
        "fixture-translate-partial",
        "fixture-rank-95",
        "fixture-rank-94",
        "fixture-non-ai-high-score",
        "fixture-rank-93",
        "fixture-rank-92",
        "fixture-rank-91",
        "fixture-rank-90",
        "fixture-rank-89",
        "fixture-rank-88",
        "fixture-rank-87",
        "fixture-old-high-99",
    }

    threshold = by_guid["fixture-threshold-60"]
    assert threshold["score"] == 75
    assert threshold["is_ai_news"] == 1
    assert threshold["ai_relevance_score"] == 70
    assert threshold["is_selected"] == 1
    assert threshold["pipeline_state"] == "fetched"
    assert threshold["content_full"]
    assert threshold["has_translate_failed"] == 0
    assert threshold["title_zh"] is None
    assert threshold["summary_zh"] is None
    assert threshold["content_zh"] is None

    low_score = by_guid["fixture-low-59"]
    assert low_score["score"] == 59
    assert low_score["is_ai_news"] == 1
    assert low_score["ai_relevance_score"] == 82
    assert low_score["is_selected"] == 0
    assert low_score["pipeline_state"] == "scored"
    assert low_score["content_full"] is None

    non_ai_high_score = by_guid["fixture-non-ai-high-score"]
    assert non_ai_high_score["score"] == 96
    assert non_ai_high_score["is_ai_news"] == 0
    assert non_ai_high_score["ai_relevance_score"] == 15
    assert non_ai_high_score["is_selected"] == 0
    assert non_ai_high_score["pipeline_state"] == "scored"
    assert non_ai_high_score["content_full"] is None

    translated = by_guid["fixture-translated-96"]
    assert translated["pipeline_state"] == "fetched"
    assert translated["is_selected"] == 1
    assert translated["title_zh"] == "OpenAI 发布 LifeSciBench 生命科学基准"
    assert_readable_translation(translated["summary_zh"], translated["content_zh"], "LifeSciBench", "生命科学", "基准")
    assert translated["has_translate_failed"] == 0

    failed_translation = by_guid["fixture-translate-partial"]
    assert failed_translation["pipeline_state"] == "fetched"
    assert failed_translation["is_selected"] == 1
    assert failed_translation["content_full"] is None
    assert failed_translation["content_raw"]
    assert failed_translation["title_zh"] is None
    assert failed_translation["summary_zh"] is None
    assert failed_translation["content_zh"] is None
    assert failed_translation["has_translate_failed"] == 1

    log_rows = conn.execute(
        """
        SELECT stage, success, source_id, news_item_id, error
        FROM processing_log
        ORDER BY id ASC
        """
    ).fetchall()
    assert any(row["stage"] == "crawl" and row["success"] == 0 for row in log_rows)
    assert sum(1 for row in log_rows if row["stage"] == "score" and row["success"] == 1) == 15
    assert sum(1 for row in log_rows if row["stage"] == "fetch") == 13
    assert any(
        row["stage"] == "translate"
        and row["success"] == 0
        and row["error"] == "validation_llm_error"
        for row in log_rows
    )

    client.post("/api/refresh")
    repeated_count = conn.execute("SELECT COUNT(*) AS count FROM news_item").fetchone()[
        "count"
    ]
    assert repeated_count == 15

def test_pipeline_output_is_projected_through_api_without_internal_leaks(tmp_path):
    client = make_client(tmp_path)
    conn = client.app.state.db

    client.post("/api/refresh")

    home = client.get("/api/home").json()["data"]
    latest_titles = [item["original_title"] for item in home["latest_news"]]
    ranked_scores = [item["score"] for item in home["top_ranked_news"]]

    assert latest_titles[:10] == [
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
    assert "Low signal AI funding rumor" not in latest_titles
    assert "Developer conference travel discounts surge" not in latest_titles
    assert ranked_scores == [96, 95, 94, 93, 92, 91, 90, 89, 88, 87]
    assert "Older AI milestone outside ranking window" not in [
        item["original_title"] for item in home["top_ranked_news"]
    ]

    for item in home["latest_news"] + home["top_ranked_news"]:
        assert "pipeline_state" not in item
        assert "is_selected" not in item
        assert "is_ai_news" not in item
        assert "ai_relevance_score" not in item
        assert "content_raw" not in item
        assert "content_full" not in item
        assert "has_translate_failed" not in item
        assert "content_zh" not in item
        assert item["status"] == "translated"
        assert item["summary_zh"]

    translated_id = conn.execute(
        "SELECT id FROM news_item WHERE rss_guid = 'fixture-translated-96'"
    ).fetchone()["id"]
    translated_detail = client.get(f"/api/news/{translated_id}").json()["data"]
    assert translated_detail["status"] == "translated"
    assert_readable_translation(
        translated_detail["summary_zh"],
        translated_detail["content_zh"],
        "LifeSciBench",
        "生命科学",
        "基准",
    )

    failed_id = conn.execute(
        "SELECT id FROM news_item WHERE rss_guid = 'fixture-translate-partial'"
    ).fetchone()["id"]
    failed_detail = client.get(f"/api/news/{failed_id}").json()["data"]
    assert failed_detail["status"] == "translation_failed"
    assert "summary_zh" not in failed_detail
    assert "content_zh" not in failed_detail
