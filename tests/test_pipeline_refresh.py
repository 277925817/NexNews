from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.db import connect, initialize_database, seed_default_sources
from backend.app.main import create_app
from backend.app.services.trigger import run_manual_refresh, run_scheduled_refresh
from backend.app.services.pipeline import (
    build_scoring_request,
    build_translation_request,
    fetch_selected_content,
    has_valid_translation_record,
    ingest_fixture_rss,
    read_json,
    run_fixture_pipeline_summary,
    score_raw_news,
    score_request_with_fixture,
    selected_fetch_candidates,
    score_is_selected,
    translate_fetched_content,
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


def test_ingest_fixture_rss_stores_raw_items_and_crawl_logs_only():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)

    result = ingest_fixture_rss(conn)
    rows = conn.execute(
        """
        SELECT rss_guid, pipeline_state, score, content_full, title_zh
        FROM news_item
        ORDER BY rss_guid ASC
        """
    ).fetchall()
    crawl_logs = conn.execute(
        """
        SELECT stage, success, source_id, news_item_id, error
        FROM processing_log
        ORDER BY id ASC
        """
    ).fetchall()

    assert result["inserted_count"] == 14
    assert result["source_success_count"] == 6
    assert result["source_failure_count"] == 1
    assert len(rows) == 14
    assert {row["pipeline_state"] for row in rows} == {"raw"}
    assert all(row["score"] is None for row in rows)
    assert all(row["content_full"] is None for row in rows)
    assert all(row["title_zh"] is None for row in rows)
    assert len(crawl_logs) == 7
    assert any(log["success"] == 0 and log["error"] == "parsing" for log in crawl_logs)
    assert all(log["stage"] == "crawl" and log["news_item_id"] is None for log in crawl_logs)


def test_scoring_request_validation_retry_and_missing_summary_penalty():
    scoring_payload = read_json(Path("fixtures/llm/scoring.json"))
    request = build_scoring_request(
        {
            "original_title": "Scoring fixture title",
            "content_raw": "",
            "source_name": "Fixture Source",
            "published_at": "2026-06-28T08:00:00Z",
            "original_url": "https://example.com/scoring",
        }
    )

    valid_result = score_request_with_fixture("fixture-translate-partial", request, scoring_payload)
    invalid_result = score_request_with_fixture("missing_score", request, scoring_payload)
    timeout_result = score_request_with_fixture("score_timeout", request, scoring_payload)
    missing_title_result = score_request_with_fixture(
        "fixture-translated-96",
        {**request, "title": ""},
        scoring_payload,
    )

    assert set(request) == {"title", "summary", "source", "published_at", "original_link"}
    assert request["summary"] == ""
    assert valid_result["score"] == 55
    assert valid_result["error"] is None
    assert invalid_result["score"] is None
    assert invalid_result["error"] == "validation_llm_error"
    assert invalid_result["retry_count"] == 2
    assert timeout_result["score"] is None
    assert timeout_result["error"] == "timeout"
    assert timeout_result["retry_count"] == 2
    assert missing_title_result["score"] == 0
    assert missing_title_result["retry_count"] == 0


def test_score_raw_news_transitions_raw_items_without_fetch_or_translation():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)

    result = score_raw_news(conn)
    rows = conn.execute(
        """
        SELECT rss_guid, score, pipeline_state, is_selected, content_full, title_zh
        FROM news_item
        ORDER BY rss_guid ASC
        """
    ).fetchall()
    logs = conn.execute(
        """
        SELECT stage, success, error, source_id, news_item_id
        FROM processing_log
        WHERE stage = 'score'
        ORDER BY id ASC
        """
    ).fetchall()
    by_guid = {row["rss_guid"]: row for row in rows}

    assert result["scored_count"] == 14
    assert result["failed_count"] == 0
    assert result["selected_count"] == 13
    assert {row["pipeline_state"] for row in rows} == {"scored"}
    assert by_guid["fixture-threshold-60"]["score"] == 60
    assert by_guid["fixture-threshold-60"]["is_selected"] == 1
    assert by_guid["fixture-low-59"]["score"] == 59
    assert by_guid["fixture-low-59"]["is_selected"] == 0
    assert all(row["content_full"] is None for row in rows)
    assert all(row["title_zh"] is None for row in rows)
    assert len(logs) == 14
    assert all(log["success"] == 1 and log["news_item_id"] is not None for log in logs)
    assert all(log["source_id"] is None for log in logs)


def test_score_raw_news_logs_invalid_mock_and_keeps_item_raw():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    source_id = conn.execute("SELECT id FROM source ORDER BY id LIMIT 1").fetchone()["id"]
    for guid in ("missing_score", "score_timeout"):
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

    result = score_raw_news(conn)
    rows = conn.execute("SELECT rss_guid, score, pipeline_state FROM news_item ORDER BY rss_guid").fetchall()
    errors = [
        row["error"]
        for row in conn.execute("SELECT error FROM processing_log WHERE stage = 'score' ORDER BY id").fetchall()
    ]

    assert result["scored_count"] == 0
    assert result["failed_count"] == 2
    assert {row["pipeline_state"] for row in rows} == {"raw"}
    assert all(row["score"] is None for row in rows)
    assert errors == ["validation_llm_error", "timeout"]


def test_selected_fetch_candidates_filter_threshold_and_preserve_distinct_items():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)

    candidates = selected_fetch_candidates(conn)
    guids = [row["rss_guid"] for row in candidates]
    canonical_urls = [row["canonical_url"] for row in candidates]

    assert score_is_selected(60) is True
    assert score_is_selected(59) is False
    assert len(candidates) == 13
    assert len(canonical_urls) == len(set(canonical_urls))
    assert "fixture-threshold-60" in guids
    assert "fixture-low-59" not in guids
    assert {"fixture-rank-95", "fixture-rank-94", "fixture-rank-88", "fixture-rank-87"}.issubset(guids)
    assert all(row["pipeline_state"] == "scored" for row in candidates)
    assert all(row["is_selected"] == 1 for row in candidates)
    assert all(row["score"] >= 60 for row in candidates)
    assert all(row["content_full"] is None for row in candidates)


def test_fetch_selected_content_uses_article_fixtures_and_rss_fallback():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)

    result = fetch_selected_content(conn)
    rows = conn.execute(
        """
        SELECT rss_guid, pipeline_state, is_selected, content_raw, content_full
        FROM news_item
        ORDER BY rss_guid ASC
        """
    ).fetchall()
    fetch_logs = conn.execute(
        """
        SELECT success, error, source_id, news_item_id
        FROM processing_log
        WHERE stage = 'fetch'
        ORDER BY id ASC
        """
    ).fetchall()
    by_guid = {row["rss_guid"]: row for row in rows}

    assert result["fetched_count"] == 13
    assert result["content_full_count"] == 2
    assert result["fallback_count"] == 11
    assert result["failed_count"] == 0
    assert by_guid["fixture-threshold-60"]["pipeline_state"] == "fetched"
    assert by_guid["fixture-threshold-60"]["content_full"]
    assert by_guid["fixture-translate-partial"]["pipeline_state"] == "fetched"
    assert by_guid["fixture-translate-partial"]["content_full"] is None
    assert by_guid["fixture-translate-partial"]["content_raw"]
    assert by_guid["fixture-low-59"]["pipeline_state"] == "scored"
    assert by_guid["fixture-low-59"]["content_full"] is None
    assert len(fetch_logs) == 13
    assert sum(log["success"] == 1 for log in fetch_logs) == 2
    assert sum(log["success"] == 0 and log["error"] == "network" for log in fetch_logs) == 11
    assert all(log["source_id"] is None and log["news_item_id"] is not None for log in fetch_logs)


def test_fetch_selected_content_without_fallback_keeps_item_scored():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    source_id = conn.execute("SELECT id FROM source ORDER BY id LIMIT 1").fetchone()["id"]
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

    result = fetch_selected_content(conn)
    row = conn.execute("SELECT score, pipeline_state, content_full FROM news_item").fetchone()
    log = conn.execute("SELECT success, error FROM processing_log WHERE stage = 'fetch'").fetchone()

    assert result["failed_count"] == 1
    assert result["fetched_count"] == 0
    assert row["score"] == 80
    assert row["pipeline_state"] == "scored"
    assert row["content_full"] is None
    assert log == {"success": 0, "error": "network"}


def test_translation_request_validation_and_category_contract():
    translation_payload = read_json(Path("fixtures/llm/translation.json"))
    request = build_translation_request(
        {
            "original_title": "Original title",
            "content_raw": "RSS fallback text",
            "content_full": "",
            "source_name": "Fixture Source",
            "score": 95,
        }
    )
    translations = translation_records(translation_payload)

    assert set(request) == {"original_title", "original_summary", "original_content", "source", "score"}
    assert request["original_content"] == "RSS fallback text"
    assert translations["fixture-translated-96"]["category_zh"] == "产品"
    assert has_valid_translation_record(translations["fixture-translated-96"]) is True
    assert has_valid_translation_record(translations["fixture-translate-partial"]) is False


def test_translate_fetched_content_writes_success_failure_and_fallback_translation():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)
    fetch_selected_content(conn)

    result = translate_fetched_content(conn)
    rows = conn.execute(
        """
        SELECT rss_guid, pipeline_state, content_full, title_zh, summary_zh,
               content_zh, has_translate_failed
        FROM news_item
        ORDER BY rss_guid ASC
        """
    ).fetchall()
    logs = conn.execute(
        """
        SELECT success, error, source_id, news_item_id
        FROM processing_log
        WHERE stage = 'translate'
        ORDER BY id ASC
        """
    ).fetchall()
    by_guid = {row["rss_guid"]: row for row in rows}

    assert result["translated_count"] == 11
    assert result["pending_count"] == 1
    assert result["failed_count"] == 1
    assert by_guid["fixture-translated-96"]["title_zh"] == "新的 AI 模型发布"
    assert by_guid["fixture-translated-96"]["pipeline_state"] == "fetched"
    assert by_guid["fixture-rank-95"]["content_full"] is None
    assert_readable_translation(
        by_guid["fixture-rank-95"]["summary_zh"],
        by_guid["fixture-rank-95"]["content_zh"],
        "安全",
        "基准",
        "企业",
    )
    assert by_guid["fixture-translate-partial"]["title_zh"] is None
    assert by_guid["fixture-translate-partial"]["summary_zh"] is None
    assert by_guid["fixture-translate-partial"]["content_zh"] is None
    assert by_guid["fixture-translate-partial"]["has_translate_failed"] == 1
    assert by_guid["fixture-threshold-60"]["has_translate_failed"] == 0
    assert by_guid["fixture-threshold-60"]["title_zh"] is None
    assert all(row["pipeline_state"] in {"scored", "fetched"} for row in rows)
    assert len(logs) == 12
    assert sum(log["success"] == 1 for log in logs) == 11
    assert sum(log["success"] == 0 and log["error"] == "validation_llm_error" for log in logs) == 1
    assert all(log["source_id"] is None and log["news_item_id"] is not None for log in logs)


def test_fixture_pipeline_run_summary_reports_core_counts_and_failures():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)

    summary = run_fixture_pipeline_summary(conn)

    assert summary["started_at"] == "2026-06-28T09:00:00Z"
    assert summary["finished_at"] == "2026-06-28T09:00:00Z"
    assert summary["source_success_count"] == 6
    assert summary["source_failure_count"] == 1
    assert summary["rss_item_count"] == 15
    assert summary["new_item_count"] == 14
    assert summary["scored_item_count"] == 14
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
          id, rss_guid, canonical_url, score, pipeline_state, is_selected,
          content_raw, content_full, title_zh, summary_zh, content_zh,
          has_translate_failed
        FROM news_item
        ORDER BY canonical_url ASC
        """
    ).fetchall()
    assert len(rows) == 14

    by_guid = {row["rss_guid"]: row for row in rows}
    assert set(by_guid) >= {
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

    threshold = by_guid["fixture-threshold-60"]
    assert threshold["score"] == 60
    assert threshold["is_selected"] == 1
    assert threshold["pipeline_state"] == "fetched"
    assert threshold["content_full"]
    assert threshold["has_translate_failed"] == 0
    assert threshold["title_zh"] is None
    assert threshold["summary_zh"] is None
    assert threshold["content_zh"] is None

    low_score = by_guid["fixture-low-59"]
    assert low_score["score"] == 59
    assert low_score["is_selected"] == 0
    assert low_score["pipeline_state"] == "scored"
    assert low_score["content_full"] is None

    translated = by_guid["fixture-translated-96"]
    assert translated["pipeline_state"] == "fetched"
    assert translated["is_selected"] == 1
    assert translated["title_zh"] == "新的 AI 模型发布"
    assert_readable_translation(translated["summary_zh"], translated["content_zh"], "模型", "API", "发布")
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
    assert sum(1 for row in log_rows if row["stage"] == "score" and row["success"] == 1) == 14
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
    assert repeated_count == 14


def test_pipeline_output_is_projected_through_api_without_internal_leaks(tmp_path):
    client = make_client(tmp_path)
    conn = client.app.state.db

    client.post("/api/refresh")

    home = client.get("/api/home").json()["data"]
    latest_titles = [item["original_title"] for item in home["latest_news"]]
    ranked_scores = [item["score"] for item in home["top_ranked_news"]]

    assert latest_titles[:10] == [
        "New AI model released",
        "AI safety benchmark reaches enterprise pilots",
        "Open model eval suite adds agent tasks",
        "AI chip scheduler cuts inference latency",
        "Research lab publishes multimodal tool use benchmark",
        "AI data pipeline validates synthetic QA traces",
        "Small language model improves retrieval planning",
        "AI observability tool traces prompt regressions",
        "AI coding assistant checks repository contracts",
        "AI product analytics detects agent drift",
    ]
    assert "Low signal AI funding rumor" not in latest_titles
    assert ranked_scores == [96, 95, 94, 93, 92, 91, 90, 89, 88, 87]
    assert "Older AI milestone outside ranking window" not in [
        item["original_title"] for item in home["top_ranked_news"]
    ]

    for item in home["latest_news"] + home["top_ranked_news"]:
        assert "pipeline_state" not in item
        assert "is_selected" not in item
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
        "模型",
        "API",
        "发布",
    )

    failed_id = conn.execute(
        "SELECT id FROM news_item WHERE rss_guid = 'fixture-translate-partial'"
    ).fetchone()["id"]
    failed_detail = client.get(f"/api/news/{failed_id}").json()["data"]
    assert failed_detail["status"] == "translation_failed"
    assert "summary_zh" not in failed_detail
    assert "content_zh" not in failed_detail
