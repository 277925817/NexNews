from fastapi.testclient import TestClient

from backend.app.main import create_app


def make_client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "rss.sqlite3")))


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
    assert len(rows) == 12

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
    assert translated["summary_zh"] == "这是一条来自 fixture 的中文摘要。"
    assert translated["content_zh"] == "这是一篇来自 fixture 的中文正文。"
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
    assert sum(1 for row in log_rows if row["stage"] == "score" and row["success"] == 1) == 12
    assert sum(1 for row in log_rows if row["stage"] == "fetch") == 11
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
    assert repeated_count == 12


def test_pipeline_output_is_projected_through_api_without_internal_leaks(tmp_path):
    client = make_client(tmp_path)
    conn = client.app.state.db

    client.post("/api/refresh")

    home = client.get("/api/home").json()["data"]
    latest_titles = [item["original_title"] for item in home["latest_news"]]
    ranked_scores = [item["score"] for item in home["top_ranked_news"]]

    assert latest_titles[:10] == [
        "Threshold AI agent reaches production",
        "New AI model released",
        "AI translation mock emits partial output",
        "AI safety benchmark reaches enterprise pilots",
        "Open model eval suite adds agent tasks",
        "AI chip scheduler cuts inference latency",
        "Research lab publishes multimodal tool use benchmark",
        "AI data pipeline validates synthetic QA traces",
        "Small language model improves retrieval planning",
        "AI observability tool traces prompt regressions",
    ]
    assert "Low signal AI funding rumor" not in latest_titles
    assert ranked_scores == [96, 95, 94, 93, 92, 91, 90, 89, 75, 60]
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

    translated_id = conn.execute(
        "SELECT id FROM news_item WHERE rss_guid = 'fixture-translated-96'"
    ).fetchone()["id"]
    translated_detail = client.get(f"/api/news/{translated_id}").json()["data"]
    assert translated_detail["status"] == "translated"
    assert translated_detail["summary_zh"] == "这是一条来自 fixture 的中文摘要。"
    assert translated_detail["content_zh"] == "这是一篇来自 fixture 的中文正文。"

    failed_id = conn.execute(
        "SELECT id FROM news_item WHERE rss_guid = 'fixture-translate-partial'"
    ).fetchone()["id"]
    failed_detail = client.get(f"/api/news/{failed_id}").json()["data"]
    assert failed_detail["status"] == "translation_failed"
    assert "summary_zh" not in failed_detail
    assert "content_zh" not in failed_detail
