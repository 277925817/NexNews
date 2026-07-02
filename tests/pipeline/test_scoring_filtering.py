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
    raw_news_for_live_scoring,
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
    assert valid_result["is_ai_news"] is True
    assert valid_result["ai_relevance_score"] == 88
    assert valid_result["error"] is None
    assert invalid_result["score"] is None
    assert invalid_result["error"] == "validation_llm_error"
    assert invalid_result["retry_count"] == 2
    assert timeout_result["score"] is None
    assert timeout_result["error"] == "timeout"
    assert timeout_result["retry_count"] == 2
    assert missing_title_result["score"] == 0
    assert missing_title_result["retry_count"] == 0

def test_scoring_response_requires_ai_value_filter_contract():
    valid_record, valid_error = validate_scoring_response(
        {
            "is_ai_news": True,
            "ai_relevance_score": 86,
            "score": 91,
            "reason": "High-signal AI infrastructure update.",
        }
    )
    missing_ai_flag, missing_ai_flag_error = validate_scoring_response(
        {
            "ai_relevance_score": 86,
            "score": 91,
            "reason": "Missing AI flag.",
        }
    )
    missing_relevance, missing_relevance_error = validate_scoring_response(
        {
            "is_ai_news": True,
            "score": 91,
            "reason": "Missing relevance score.",
        }
    )
    non_boolean_ai_flag, non_boolean_ai_flag_error = validate_scoring_response(
        {
            "is_ai_news": "yes",
            "ai_relevance_score": 86,
            "score": 91,
            "reason": "AI flag must be boolean.",
        }
    )
    boolean_score, boolean_score_error = validate_scoring_response(
        {
            "is_ai_news": True,
            "ai_relevance_score": 86,
            "score": True,
            "reason": "Score must be an integer, not a boolean.",
        }
    )

    assert valid_error is None
    assert valid_record == {
        "is_ai_news": True,
        "ai_relevance_score": 86,
        "score": 91,
        "reason": "High-signal AI infrastructure update.",
    }
    assert missing_ai_flag is None
    assert missing_ai_flag_error == "validation_llm_error"
    assert missing_relevance is None
    assert missing_relevance_error == "validation_llm_error"
    assert non_boolean_ai_flag is None
    assert non_boolean_ai_flag_error == "validation_llm_error"
    assert boolean_score is None
    assert boolean_score_error == "validation_llm_error"

def test_score_selection_requires_ai_relevance_and_value_thresholds():
    assert score_is_selected(81, is_ai_news=True, ai_relevance_score=70) is True
    assert score_is_selected(80, is_ai_news=True, ai_relevance_score=90) is False
    assert score_is_selected(95, is_ai_news=True, ai_relevance_score=69) is False
    assert score_is_selected(95, is_ai_news=False, ai_relevance_score=95) is False

def test_live_scoring_prompt_encodes_ai_value_rubric_and_caps():
    prompt = LIVE_SCORING_SYSTEM_PROMPT

    assert "影响范围 30%" in prompt
    assert "原创性/信息增量 20%" in prompt
    assert "来源权威性与证据可信度 20%" in prompt
    assert "技术/产品/政策具体性 20%" in prompt
    assert "时效性 10%" in prompt
    assert "非 AI 新闻 score 最高不得超过 20" in prompt
    assert "AI 相关但没有具体新信息最高不得超过 45" in prompt
    assert "重复转述、二手汇总或缺少清晰来源最高不得超过 70" in prompt

def test_local_fallback_scoring_applies_ai_value_rubric():
    high_value_ai = fallback_ai_value_record(
        {
            "original_title": "OpenAI releases multimodal AI benchmark for production agents",
            "content_raw": (
                "The release includes model evaluations, latency traces, safety results "
                "and infrastructure evidence for enterprise AI agent workflows."
            ),
            "source_name": "OpenAI News",
            "published_at": "2026-07-01T00:00:00Z",
            "original_url": "https://openai.com/index/agent-eval-benchmark/",
        }
    )
    non_ai = fallback_ai_value_record(
        {
            "original_title": "Developer conference travel discounts surge",
            "content_raw": "Organizers announced ticket and hotel discounts for attendees.",
            "source_name": "Example News",
            "published_at": "2026-07-01T00:00:00Z",
            "original_url": "https://example.com/conference-discounts",
        }
    )
    low_value_ai = fallback_ai_value_record(
        {
            "original_title": "Low signal AI funding rumor spreads online",
            "content_raw": "A marketing startup may raise money, according to unconfirmed rumors.",
            "source_name": "Example News",
            "published_at": "2026-07-01T00:00:00Z",
            "original_url": "https://example.com/ai-funding-rumor",
        }
    )

    assert score_is_selected(
        int(high_value_ai["score"]),
        is_ai_news=bool(high_value_ai["is_ai_news"]),
        ai_relevance_score=int(high_value_ai["ai_relevance_score"]),
    )
    assert non_ai["is_ai_news"] is False
    assert int(non_ai["ai_relevance_score"]) == 0
    assert int(non_ai["score"]) <= 20
    assert low_value_ai["is_ai_news"] is True
    assert int(low_value_ai["score"]) <= 55
    assert score_is_selected(
        int(low_value_ai["score"]),
        is_ai_news=bool(low_value_ai["is_ai_news"]),
        ai_relevance_score=int(low_value_ai["ai_relevance_score"]),
    ) is False

def test_score_raw_news_transitions_raw_items_without_fetch_or_translation():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)

    result = score_raw_news(conn)
    rows = conn.execute(
        """
        SELECT
          rss_guid, score, is_ai_news, ai_relevance_score, pipeline_state,
          is_selected, content_full, title_zh
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

    assert result["scored_count"] == 15
    assert result["failed_count"] == 0
    assert result["selected_count"] == 11
    assert {row["pipeline_state"] for row in rows} == {"scored"}
    assert by_guid["fixture-threshold-60"]["score"] == 75
    assert by_guid["fixture-threshold-60"]["is_ai_news"] == 1
    assert by_guid["fixture-threshold-60"]["ai_relevance_score"] == 70
    assert by_guid["fixture-threshold-60"]["is_selected"] == 0
    assert by_guid["fixture-low-59"]["score"] == 59
    assert by_guid["fixture-low-59"]["is_selected"] == 0
    assert by_guid["fixture-non-ai-high-score"]["score"] == 96
    assert by_guid["fixture-non-ai-high-score"]["is_ai_news"] == 0
    assert by_guid["fixture-non-ai-high-score"]["is_selected"] == 0
    assert all(row["content_full"] is None for row in rows)
    assert all(row["title_zh"] is None for row in rows)
    assert len(logs) == 15
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

def test_score_raw_news_live_without_llm_uses_ai_value_fallback():
    conn = connect(":memory:")
    initialize_database(conn)
    conn.execute(
        """
        INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES ('Fallback Source', 'https://fallback.example/rss.xml', 1, 'twice_daily', '2026-07-01T00:00:00Z')
        """
    )
    source_id = conn.execute("SELECT id FROM source").fetchone()["id"]
    rows = [
        (
            "fallback-high-ai",
            "https://fallback.example/high-ai",
            "OpenAI releases multimodal AI benchmark for production agents",
            "The release includes model evaluations, latency traces, safety results and infrastructure evidence.",
        ),
        (
            "fallback-low-ai",
            "https://fallback.example/ai-funding-rumor",
            "Low signal AI funding rumor spreads online",
            "A marketing startup may raise money, according to unconfirmed rumors.",
        ),
        (
            "fallback-non-ai",
            "https://fallback.example/conference-discounts",
            "Developer conference travel discounts surge",
            "Organizers announced ticket and hotel discounts for attendees.",
        ),
    ]
    for guid, url, title, summary in rows:
        conn.execute(
            """
            INSERT INTO news_item (
              source_id, rss_guid, original_url, canonical_url, original_title,
              published_at, pipeline_state, is_selected, content_raw, created_at,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, '2026-07-01T00:00:00Z', 'raw', 0, ?, ?, ?)
            """,
            (
                source_id,
                guid,
                url,
                url,
                title,
                summary,
                "2026-07-01T00:00:00Z",
                "2026-07-01T00:00:00Z",
            ),
        )

    result = score_raw_news_live(conn, use_live_llm=False)
    scored_rows = conn.execute(
        """
        SELECT rss_guid, is_ai_news, ai_relevance_score, score, is_selected, pipeline_state
        FROM news_item
        ORDER BY rss_guid ASC
        """
    ).fetchall()
    by_guid = {row["rss_guid"]: row for row in scored_rows}

    assert result["scored_count"] == 3
    assert result["selected_count"] == 1
    assert by_guid["fallback-high-ai"]["is_ai_news"] == 1
    assert by_guid["fallback-high-ai"]["ai_relevance_score"] >= 70
    assert by_guid["fallback-high-ai"]["score"] >= 81
    assert by_guid["fallback-high-ai"]["is_selected"] == 1
    assert by_guid["fallback-low-ai"]["is_ai_news"] == 1
    assert by_guid["fallback-low-ai"]["score"] <= 55
    assert by_guid["fallback-low-ai"]["is_selected"] == 0
    assert by_guid["fallback-non-ai"]["is_ai_news"] == 0
    assert by_guid["fallback-non-ai"]["score"] <= 20
    assert by_guid["fallback-non-ai"]["is_selected"] == 0
    assert {row["pipeline_state"] for row in scored_rows} == {"scored"}

def test_selected_fetch_candidates_filter_threshold_and_preserve_distinct_items():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)

    candidates = selected_fetch_candidates(conn)
    guids = [row["rss_guid"] for row in candidates]
    canonical_urls = [row["canonical_url"] for row in candidates]

    assert score_is_selected(81, is_ai_news=True, ai_relevance_score=70) is True
    assert score_is_selected(80, is_ai_news=True, ai_relevance_score=90) is False
    assert len(candidates) == 11
    assert len(canonical_urls) == len(set(canonical_urls))
    assert "fixture-threshold-60" not in guids
    assert "fixture-low-59" not in guids
    assert "fixture-non-ai-high-score" not in guids
    assert {"fixture-rank-95", "fixture-rank-94", "fixture-rank-88", "fixture-rank-87"}.issubset(guids)
    assert all(row["pipeline_state"] == "scored" for row in candidates)
    assert all(row["is_selected"] == 1 for row in candidates)
    assert all(row["is_ai_news"] == 1 for row in candidates)
    assert all(row["ai_relevance_score"] >= 70 for row in candidates)
    assert all(row["score"] >= 81 for row in candidates)
    assert all(row["content_full"] is None for row in candidates)

def test_request_live_scoring_posts_chat_completion_and_parses_score(monkeypatch):
    from backend.app.services import pipeline

    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "is_ai_news": True,
                                    "ai_relevance_score": 87,
                                    "score": 87,
                                    "reason": "High-signal AI infrastructure update.",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            calls.append({"init": kwargs})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, headers=None, json=None):
            calls.append({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(pipeline.httpx, "Client", FakeClient)

    record, error = request_live_scoring(
        {
            "title": "Live AI scoring check",
            "summary": "English summary",
            "source": "Live Source",
            "published_at": "2026-07-01T00:00:00Z",
            "original_link": "https://live.example/scoring",
        },
        base_url="https://llm.example.test/api/v4",
        api_key="secret-token",
        model="glm-test",
        timeout_seconds=3,
    )

    post_call = calls[1]
    assert error is None
    assert record == {
        "is_ai_news": True,
        "ai_relevance_score": 87,
        "score": 87,
        "reason": "High-signal AI infrastructure update.",
    }
    assert post_call["url"] == "https://llm.example.test/api/v4/chat/completions"
    assert post_call["headers"]["Authorization"] == "Bearer secret-token"
    assert post_call["json"]["model"] == "glm-test"
    assert post_call["json"]["messages"][0]["role"] == "system"
    assert "original_link" in post_call["json"]["messages"][1]["content"]

def test_live_scoring_limits_batch_and_prioritizes_newest_raw_items(monkeypatch):
    from backend.app.services import pipeline

    conn = connect(":memory:")
    initialize_database(conn)
    conn.execute(
        """
        INSERT INTO source (id, name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES (1, 'Live AI Source', 'https://live.example/rss.xml', 1, 'twice_daily', '2026-07-01T00:00:00Z')
        """
    )
    rows = [
        ("old-raw", "https://live.example/old", "Old raw item", "2026-06-01T00:00:00Z"),
        ("new-raw", "https://live.example/new", "New raw item", "2026-07-01T08:00:00Z"),
    ]
    for guid, url, title, published_at in rows:
        conn.execute(
            """
            INSERT INTO news_item (
              source_id, rss_guid, original_url, canonical_url, original_title,
              published_at, pipeline_state, content_raw, created_at, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, 'raw', 'summary', '2026-07-01T08:00:00Z', '2026-07-01T08:00:00Z')
            """,
            (guid, url, url, title, published_at),
        )
    scoring_requests = []

    def fake_request_live_scoring(request, **_kwargs):
        scoring_requests.append(request)
        return {
            "is_ai_news": True,
            "ai_relevance_score": 90,
            "score": 90,
            "reason": "Selected by live LLM scoring.",
        }, None

    monkeypatch.setattr(pipeline, "request_live_scoring", fake_request_live_scoring)

    result = score_raw_news_live(
        conn,
        now="2026-07-01T08:00:00Z",
        use_live_llm=True,
        live_llm_base_url="https://llm.example.test/api/v4",
        live_llm_api_key="secret-token",
        live_llm_model="glm-test",
        live_llm_max_score_items=1,
        live_llm_score_concurrency=1,
    )
    stored_rows = conn.execute(
        """
        SELECT rss_guid, pipeline_state, score
        FROM news_item
        ORDER BY published_at DESC
        """
    ).fetchall()

    assert result["scored_count"] == 1
    assert [request["title"] for request in scoring_requests] == ["New raw item"]
    assert stored_rows == [
        {"rss_guid": "new-raw", "pipeline_state": "scored", "score": 90},
        {"rss_guid": "old-raw", "pipeline_state": "raw", "score": None},
    ]

def test_live_scoring_processes_raw_backlog_in_bounded_batches(monkeypatch):
    from backend.app.services import pipeline

    conn = connect(":memory:")
    initialize_database(conn)
    conn.execute(
        """
        INSERT INTO source (id, name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES (1, 'Backlog Source', 'https://backlog.example/rss.xml', 1, 'twice_daily', '2026-07-01T00:00:00Z')
        """
    )
    for index in range(12):
        published_at = f"2026-07-01T00:{index:02d}:00Z"
        conn.execute(
            """
            INSERT INTO news_item (
              source_id, rss_guid, original_url, canonical_url, original_title,
              published_at, pipeline_state, content_raw, created_at, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, 'raw', 'summary', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')
            """,
            (
                f"raw-{index:02d}",
                f"https://backlog.example/{index}",
                f"https://backlog.example/{index}",
                f"Backlog item {index:02d}",
                published_at,
            ),
        )

    def fake_request_live_scoring(_request, **_kwargs):
        return {
            "is_ai_news": True,
            "ai_relevance_score": 91,
            "score": 91,
            "reason": "Backlog item selected by live scoring.",
        }, None

    monkeypatch.setattr(pipeline, "request_live_scoring", fake_request_live_scoring)

    first_batch = raw_news_for_live_scoring(conn, max_items=10)
    result = score_raw_news_live(
        conn,
        now="2026-07-01T00:20:00Z",
        use_live_llm=True,
        live_llm_base_url="https://llm.example.test/api/v4",
        live_llm_api_key="secret-token",
        live_llm_model="glm-test",
        live_llm_max_score_items=10,
        live_llm_score_concurrency=1,
    )
    remaining_raw = conn.execute(
        """
        SELECT rss_guid
        FROM news_item
        WHERE pipeline_state = 'raw'
        ORDER BY published_at DESC
        """
    ).fetchall()

    assert [row["rss_guid"] for row in first_batch] == [f"raw-{index:02d}" for index in range(11, 1, -1)]
    assert result["scored_count"] == 10
    assert result["selected_count"] == 10
    assert [row["rss_guid"] for row in remaining_raw] == ["raw-01", "raw-00"]

def test_live_pipeline_skips_translation_when_live_llm_is_rate_limited_during_scoring(monkeypatch):
    from backend.app.services import pipeline

    conn = connect(":memory:")
    initialize_database(conn)
    conn.execute(
        """
        INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES ('Live AI Source', 'https://live.example/rss.xml', 1, 'twice_daily', '2026-07-01T00:00:00Z')
        """
    )
    translation_requests = []

    def fake_fetch_url_text(url, **_kwargs):
        if url == "https://live.example/rss.xml":
            return """
            <rss><channel>
              <item>
                <title>Live AI rate limit should not trigger translation</title>
                <link>https://live.example/articles/rate-limit</link>
                <guid>live-rate-limit-skip-translation</guid>
                <pubDate>Tue, 30 Jun 2026 00:00:00 GMT</pubDate>
                <description>AI news item used to verify rate limit handling.</description>
              </item>
            </channel></rss>
            """, None
        return None, "unexpected_url"

    def fake_request_live_scoring(request, **_kwargs):
        return None, "llm_rate_limited"

    def fake_request_live_translation(request, **_kwargs):
        translation_requests.append(request)
        return None, "llm_rate_limited"

    monkeypatch.setattr(pipeline, "fetch_url_text", fake_fetch_url_text)
    monkeypatch.setattr(pipeline, "request_live_scoring", fake_request_live_scoring)
    monkeypatch.setattr(pipeline, "request_live_translation", fake_request_live_translation)

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
    )
    score_log = conn.execute(
        """
        SELECT success, error
        FROM processing_log
        WHERE stage = 'score'
          AND news_item_id = (
            SELECT id FROM news_item WHERE rss_guid = 'live-rate-limit-skip-translation'
          )
        """
    ).fetchone()

    assert summary["translated_item_count"] == 0
    assert summary["llm_unavailable_count"] == 1
    assert score_log == {"success": 0, "error": "llm_rate_limited"}
    assert translation_requests == []
