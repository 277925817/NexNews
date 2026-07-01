import json
from pathlib import Path
import threading

from fastapi.testclient import TestClient

from backend.app.db import connect, initialize_database, seed_default_sources
from backend.app.main import create_app
from backend.app.services.trigger import run_manual_refresh, run_scheduled_refresh
from backend.app.services.pipeline import (
    build_scoring_request,
    build_translation_request,
    backfill_top_scored_translations,
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


def test_parse_rss_feed_text_separates_article_link_from_discussion_url():
    payload = """
    <rss><channel>
      <item>
        <title>HN fixture story</title>
        <link>https://example-news.com/article</link>
        <guid isPermaLink="false">https://news.ycombinator.com/item?id=123</guid>
        <comments>https://news.ycombinator.com/item?id=123</comments>
        <pubDate>Sun, 28 Jun 2026 06:00:00 +0000</pubDate>
        <description>Article summary</description>
      </item>
    </channel></rss>
    """

    items = parse_rss_feed_text(payload)

    assert items == [
        {
            "guid": "https://news.ycombinator.com/item?id=123",
            "title": "HN fixture story",
            "link": "https://example-news.com/article",
            "discussion_url": "https://news.ycombinator.com/item?id=123",
            "published_at": "2026-06-28T06:00:00Z",
            "summary": "Article summary",
        }
    ]


def test_parse_rss_feed_text_supports_atom_entries():
    payload = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Live AI research update</title>
        <id>https://research.example.com/posts/live-ai-research-update</id>
        <link href="https://research.example.com/posts/live-ai-research-update?utm_source=feed" />
        <updated>2026-06-30T10:00:00Z</updated>
        <summary>Researchers describe a new AI evaluation method.</summary>
      </entry>
    </feed>
    """

    items = parse_rss_feed_text(payload)

    assert items == [
        {
            "guid": "https://research.example.com/posts/live-ai-research-update",
            "title": "Live AI research update",
            "link": "https://research.example.com/posts/live-ai-research-update?utm_source=feed",
            "discussion_url": None,
            "published_at": "2026-06-30T10:00:00Z",
            "summary": "Researchers describe a new AI evaluation method.",
        }
    ]


def test_fetch_url_text_retries_without_env_proxy_when_proxy_scheme_is_unsupported(monkeypatch):
    from backend.app.services import pipeline

    calls = []

    class FakeResponse:
        status_code = 200
        text = "<rss><channel></channel></rss>"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs.get("trust_env"))
            if kwargs.get("trust_env") is not False:
                raise ValueError("Unknown scheme for proxy URL URL('socks://127.0.0.1:7897/')")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(pipeline.httpx, "Client", FakeClient)

    text, error = pipeline.fetch_url_text("https://example.com/rss.xml", retry_count=0)

    assert text == "<rss><channel></channel></rss>"
    assert error is None
    assert calls == [True, False]


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

    assert result["inserted_count"] == 15
    assert result["source_success_count"] == 22
    assert result["source_failure_count"] == 1
    assert len(rows) == 15
    assert {row["pipeline_state"] for row in rows} == {"raw"}
    assert all(row["score"] is None for row in rows)
    assert all(row["content_full"] is None for row in rows)
    assert all(row["title_zh"] is None for row in rows)
    assert len(crawl_logs) == 23
    assert any(log["success"] == 0 and log["error"] == "parsing" for log in crawl_logs)
    assert all(log["stage"] == "crawl" and log["news_item_id"] is None for log in crawl_logs)


def test_ingest_fixture_rss_preserves_hn_article_and_discussion_urls():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)

    ingest_fixture_rss(conn)

    rows = conn.execute(
        """
        SELECT rss_guid, original_url, canonical_url, discussion_url
        FROM news_item
        WHERE rss_guid LIKE 'fixture-rank-%'
           OR rss_guid = 'fixture-old-high-99'
        ORDER BY rss_guid ASC
        """
    ).fetchall()

    assert rows
    assert all(row["original_url"] == row["canonical_url"] for row in rows)
    assert all("news.ycombinator.com/item" not in row["original_url"] for row in rows)
    assert all(row["discussion_url"].startswith("https://news.ycombinator.com/item?id=") for row in rows)


def test_live_rss_ingest_filters_archival_items_by_published_at(monkeypatch):
    conn = connect(":memory:")
    initialize_database(conn)
    conn.execute(
        """
        INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES ('OpenAI', 'https://openai.com/news/rss.xml', 1, 'twice_daily', '2026-07-01T00:00:00Z')
        """
    )
    payload = """
    <rss><channel>
      <item>
        <title>Introducing GPT-4.1 in the API</title>
        <link>https://openai.com/index/gpt-4-1/</link>
        <guid>https://openai.com/index/gpt-4-1/</guid>
        <pubDate>Mon, 14 Apr 2025 10:00:00 GMT</pubDate>
        <description>Historical model release.</description>
      </item>
      <item>
        <title>Introducing GeneBench-Pro</title>
        <link>https://openai.com/index/introducing-genebench-pro/</link>
        <guid>https://openai.com/index/introducing-genebench-pro/</guid>
        <pubDate>Tue, 30 Jun 2026 00:00:00 GMT</pubDate>
        <description>Current benchmark release.</description>
      </item>
    </channel></rss>
    """

    def fake_fetch_url_text(*_args, **_kwargs):
        return payload, None

    monkeypatch.setattr("backend.app.services.pipeline.fetch_url_text", fake_fetch_url_text)

    result = ingest_live_rss(conn, now="2026-07-01T00:00:00Z")
    rows = conn.execute(
        """
        SELECT original_title, original_url, published_at
        FROM news_item
        ORDER BY id ASC
        """
    ).fetchall()

    assert result["inserted_count"] == 1
    assert rows == [
            {
                "original_title": "Introducing GeneBench-Pro",
                "original_url": "https://openai.com/index/introducing-genebench-pro/",
                "published_at": "2026-06-30T00:00:00Z",
            }
        ]


def test_live_rss_ingest_fetches_sources_concurrently(monkeypatch):
    conn = connect(":memory:")
    initialize_database(conn)
    for name, rss_url in (
        ("Source A", "https://source-a.example/rss.xml"),
        ("Source B", "https://source-b.example/rss.xml"),
    ):
        conn.execute(
            """
            INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at)
            VALUES (?, ?, 1, 'twice_daily', '2026-07-01T00:00:00Z')
            """,
            (name, rss_url),
        )

    started_urls = set()
    lock = threading.Lock()
    both_started = threading.Event()

    def fake_fetch_url_text(url, **_kwargs):
        with lock:
            started_urls.add(url)
            if len(started_urls) == 2:
                both_started.set()
        if not both_started.wait(timeout=0.5):
            return None, "not_concurrent"
        source_name = "a" if "source-a" in url else "b"
        return f"""
        <rss><channel>
          <item>
            <title>Live AI update from source {source_name}</title>
            <link>https://articles.example/{source_name}</link>
            <guid>live-{source_name}</guid>
            <pubDate>Tue, 30 Jun 2026 00:00:00 GMT</pubDate>
            <description>Current AI news summary from source {source_name}.</description>
          </item>
        </channel></rss>
        """, None

    monkeypatch.setattr("backend.app.services.pipeline.fetch_url_text", fake_fetch_url_text)

    result = ingest_live_rss(
        conn,
        now="2026-07-01T00:00:00Z",
        timeout=1,
        retry_count=0,
        max_workers=2,
    )

    assert result["inserted_count"] == 2
    assert result["source_success_count"] == 2
    assert result["source_failure_count"] == 0


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
    assert score_is_selected(75, is_ai_news=True, ai_relevance_score=70) is True
    assert score_is_selected(74, is_ai_news=True, ai_relevance_score=90) is False
    assert score_is_selected(95, is_ai_news=True, ai_relevance_score=69) is False
    assert score_is_selected(95, is_ai_news=False, ai_relevance_score=95) is False


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
    assert result["selected_count"] == 13
    assert {row["pipeline_state"] for row in rows} == {"scored"}
    assert by_guid["fixture-threshold-60"]["score"] == 75
    assert by_guid["fixture-threshold-60"]["is_ai_news"] == 1
    assert by_guid["fixture-threshold-60"]["ai_relevance_score"] == 70
    assert by_guid["fixture-threshold-60"]["is_selected"] == 1
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


def test_selected_fetch_candidates_filter_threshold_and_preserve_distinct_items():
    conn = connect(":memory:")
    initialize_database(conn)
    seed_default_sources(conn)
    ingest_fixture_rss(conn)
    score_raw_news(conn)

    candidates = selected_fetch_candidates(conn)
    guids = [row["rss_guid"] for row in candidates]
    canonical_urls = [row["canonical_url"] for row in candidates]

    assert score_is_selected(75, is_ai_news=True, ai_relevance_score=70) is True
    assert score_is_selected(74, is_ai_news=True, ai_relevance_score=90) is False
    assert len(candidates) == 13
    assert len(canonical_urls) == len(set(canonical_urls))
    assert "fixture-threshold-60" in guids
    assert "fixture-low-59" not in guids
    assert "fixture-non-ai-high-score" not in guids
    assert {"fixture-rank-95", "fixture-rank-94", "fixture-rank-88", "fixture-rank-87"}.issubset(guids)
    assert all(row["pipeline_state"] == "scored" for row in candidates)
    assert all(row["is_selected"] == 1 for row in candidates)
    assert all(row["is_ai_news"] == 1 for row in candidates)
    assert all(row["ai_relevance_score"] >= 70 for row in candidates)
    assert all(row["score"] >= 75 for row in candidates)
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
          published_at, score, is_ai_news, ai_relevance_score, pipeline_state,
          is_selected, content_raw, created_at, updated_at
        )
        VALUES (?, 'fetch-no-fallback', ?, ?, 'No fallback', ?, 80, 1, 90, 'scored', 1, '', ?, ?)
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
    assert translations["fixture-translated-96"]["category_zh"] == "研究"
    assert has_valid_translation_record(translations["fixture-translated-96"]) is True
    assert has_valid_translation_record(translations["fixture-translate-partial"]) is False


def test_request_live_translation_posts_chat_completion_and_parses_json(monkeypatch):
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
                                    "title_zh": "现场 LLM 翻译标题",
                                    "summary_zh": "现场 LLM 返回的中文摘要，能够概括同一条新闻的核心信息。",
                                    "content_zh": "现场 LLM 返回的中文正文第一段，说明新闻背景和主要事实。\n\n第二段继续解释影响和后续观察点。",
                                    "category_zh": "产品",
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

    record, error = request_live_translation(
        {
            "original_title": "Live AI translation check",
            "original_summary": "English summary",
            "original_content": "English content",
            "source": "Live Source",
            "score": 92,
        },
        base_url="https://llm.example.test/api/v4",
        api_key="secret-token",
        model="glm-test",
        timeout_seconds=3,
    )

    post_call = calls[1]
    assert error is None
    assert record["title_zh"] == "现场 LLM 翻译标题"
    assert post_call["url"] == "https://llm.example.test/api/v4/chat/completions"
    assert post_call["headers"]["Authorization"] == "Bearer secret-token"
    assert post_call["json"]["model"] == "glm-test"
    assert post_call["json"]["messages"][0]["role"] == "system"
    assert "original_title" in post_call["json"]["messages"][1]["content"]


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


def test_request_live_llm_supports_anthropic_messages_format(monkeypatch):
    from backend.app.services import pipeline

    calls = []

    class FakeTranslationResponse:
        status_code = 200

        def json(self):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "title_zh": "Anthropic 格式中文标题",
                                "summary_zh": "Anthropic 格式返回中文摘要，证明 DeepSeek 兼容端点可用。",
                                "content_zh": "Anthropic 格式返回中文正文第一段。\n\nAnthropic 格式返回中文正文第二段。",
                                "category_zh": "产品",
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            }

    class FakeScoringResponse:
        status_code = 200

        def json(self):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "is_ai_news": True,
                                "ai_relevance_score": 91,
                                "score": 91,
                                "reason": "Anthropic-compatible scoring response.",
                            },
                            ensure_ascii=False,
                        ),
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
            if "original_title" in json["messages"][0]["content"]:
                return FakeTranslationResponse()
            return FakeScoringResponse()

    monkeypatch.setattr(pipeline.httpx, "Client", FakeClient)

    translation_record, translation_error = request_live_translation(
        {
            "original_title": "Anthropic translation check",
            "original_summary": "English summary",
            "original_content": "English content",
            "source": "Live Source",
            "score": 92,
        },
        base_url="https://api.deepseek.com/anthropic",
        api_key="secret-token",
        model="deepseek-v4-flash",
        timeout_seconds=3,
    )
    scoring_record, scoring_error = request_live_scoring(
        {
            "title": "Anthropic scoring check",
            "summary": "English summary",
            "source": "Live Source",
            "published_at": "2026-07-01T00:00:00Z",
            "original_link": "https://live.example/scoring",
        },
        base_url="https://api.deepseek.com/anthropic",
        api_key="secret-token",
        model="deepseek-v4-flash",
        timeout_seconds=3,
    )

    translation_call = calls[1]
    scoring_call = calls[3]
    assert translation_error is None
    assert translation_record["title_zh"] == "Anthropic 格式中文标题"
    assert scoring_error is None
    assert scoring_record == {
        "is_ai_news": True,
        "ai_relevance_score": 91,
        "score": 91,
        "reason": "Anthropic-compatible scoring response.",
    }
    assert translation_call["url"] == "https://api.deepseek.com/anthropic/messages"
    assert scoring_call["url"] == "https://api.deepseek.com/anthropic/messages"
    assert translation_call["headers"]["x-api-key"] == "secret-token"
    assert "Authorization" not in translation_call["headers"]
    assert translation_call["json"]["system"].startswith("你是 AI 新闻聚合系统的中文翻译器")
    assert translation_call["json"]["messages"][0]["role"] == "user"
    assert translation_call["json"]["max_tokens"] == 4096


def test_request_live_translation_uses_direct_network_before_env_proxy(monkeypatch):
    from backend.app.services import pipeline

    trust_env_values = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title_zh": "无代理后的中文标题",
                                    "summary_zh": "无代理重试后返回中文摘要，说明请求成功。",
                                    "content_zh": "无代理重试后返回中文正文第一段。\n\n无代理重试后返回中文正文第二段。",
                                    "category_zh": "产品",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.trust_env = kwargs.get("trust_env")
            trust_env_values.append(self.trust_env)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(pipeline.httpx, "Client", FakeClient)

    record, error = request_live_translation(
        {
            "original_title": "Proxy timeout check",
            "original_summary": "Summary",
            "original_content": "Content",
            "source": "Live Source",
            "score": 80,
        },
        base_url="https://llm.example.test/api/v4",
        api_key="secret-token",
        model="glm-test",
        timeout_seconds=3,
    )

    assert error is None
    assert record["title_zh"] == "无代理后的中文标题"
    assert trust_env_values == [False]


def test_request_live_translation_retries_transient_llm_failures(monkeypatch):
    from backend.app.services import pipeline

    status_codes = [500, 502, 200]
    post_count = 0

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title_zh": "重试后的中文标题",
                                    "summary_zh": "重试后的中文摘要，说明瞬时失败可以恢复。",
                                    "content_zh": "重试后的中文正文第一段。\n\n重试后的中文正文第二段。",
                                    "category_zh": "产品",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            nonlocal post_count
            status_code = status_codes[post_count]
            post_count += 1
            return FakeResponse(status_code)

    monkeypatch.setattr(pipeline.httpx, "Client", FakeClient)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_args, **_kwargs: None)

    record, error = request_live_translation(
        {
            "original_title": "Transient retry check",
            "original_summary": "Summary",
            "original_content": "Content",
            "source": "Live Source",
            "score": 80,
        },
        base_url="https://llm.example.test/api/v4",
        api_key="secret-token",
        model="glm-test",
        timeout_seconds=3,
    )

    assert error is None
    assert record["title_zh"] == "重试后的中文标题"
    assert post_count == 3


def test_request_live_llm_rate_limit_fails_fast_without_retry(monkeypatch):
    from backend.app.services import pipeline

    post_count = 0
    sleep_calls = []

    class FakeResponse:
        status_code = 429

        def json(self):
            return {"error": {"code": "1302", "message": "rate limited"}}

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            nonlocal post_count
            post_count += 1
            return FakeResponse()

    monkeypatch.setattr(pipeline.httpx, "Client", FakeClient)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *args, **_kwargs: sleep_calls.append(args))

    translation_record, translation_error = request_live_translation(
        {
            "original_title": "Rate limit check",
            "original_summary": "Summary",
            "original_content": "Content",
            "source": "Live Source",
            "score": 80,
        },
        base_url="https://llm.example.test/api/v4",
        api_key="secret-token",
        model="glm-test",
        timeout_seconds=3,
    )
    scoring_record, scoring_error = request_live_scoring(
        {
            "title": "Rate limit scoring check",
            "summary": "Summary",
            "source": "Live Source",
            "published_at": "2026-07-01T00:00:00Z",
            "original_link": "https://live.example/rate-limit",
        },
        base_url="https://llm.example.test/api/v4",
        api_key="secret-token",
        model="glm-test",
        timeout_seconds=3,
    )

    assert translation_record is None
    assert translation_error == "llm_rate_limited"
    assert scoring_record is None
    assert scoring_error == "llm_rate_limited"
    assert post_count == 2
    assert sleep_calls == []


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


def test_live_pipeline_uses_live_llm_translation_when_enabled(monkeypatch):
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
    scoring_requests = []

    def fake_fetch_url_text(url, **_kwargs):
        if url == "https://live.example/rss.xml":
            return """
            <rss><channel>
              <item>
                <title>Live AI agents improve production workflows</title>
                <link>https://live.example/articles/agents-production-translation</link>
                <guid>live-agents-production-translation</guid>
                <pubDate>Mon, 29 Jun 2026 00:00:00 GMT</pubDate>
                <description>AI agents are improving production workflows with better evaluation and observability.</description>
              </item>
            </channel></rss>
            """, None
        return None, "unexpected_url"

    def fake_request_live_translation(request, **kwargs):
        translation_requests.append({"request": request, "kwargs": kwargs})
        return {
            "title_zh": "现场 AI 智能体改进生产工作流",
            "summary_zh": "现场 LLM 将 AI 智能体生产工作流新闻翻译成中文摘要，保留评估和可观测性重点。",
            "content_zh": "现场 LLM 将这条 AI 智能体新闻翻译成中文正文，说明团队正在用更好的评估和可观测性改进生产工作流。\n\n第二段说明这一变化会影响上线质量、监控方式和后续产品迭代。",
            "category_zh": "产品",
        }, None

    def fake_request_live_scoring(request, **kwargs):
        scoring_requests.append({"request": request, "kwargs": kwargs})
        return {
            "is_ai_news": True,
            "ai_relevance_score": 92,
            "score": 92,
            "reason": "Live LLM scoring selected this item.",
        }, None

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
    row = conn.execute(
        """
        SELECT original_title, title_zh, summary_zh, content_zh, has_translate_failed
        FROM news_item
        WHERE rss_guid = 'live-agents-production-translation'
        """
    ).fetchone()
    translate_log = conn.execute(
        """
        SELECT success, error
        FROM processing_log
        WHERE stage = 'translate'
          AND news_item_id = (
            SELECT id FROM news_item WHERE rss_guid = 'live-agents-production-translation'
          )
        """
    ).fetchone()

    assert summary["translated_item_count"] == 1
    assert len(scoring_requests) == 1
    assert scoring_requests[0]["kwargs"]["base_url"] == "https://llm.example.test/api/v4"
    assert len(translation_requests) == 1
    assert translation_requests[0]["kwargs"]["base_url"] == "https://llm.example.test/api/v4"
    assert translation_requests[0]["kwargs"]["model"] == "glm-test"
    assert row["title_zh"] == "现场 AI 智能体改进生产工作流"
    assert row["title_zh"] != row["original_title"]
    assert "生产工作流" in row["summary_zh"]
    assert "第二段" in row["content_zh"]
    assert row["has_translate_failed"] == 0
    assert translate_log == {"success": 1, "error": None}


def test_live_pipeline_limits_live_llm_translation_batch(monkeypatch):
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
    scoring_requests = []

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
            </channel></rss>
            """, None
        return None, "unexpected_url"

    def fake_request_live_translation(request, **_kwargs):
        translation_requests.append(request)
        return {
            "title_zh": f"批量上限翻译 {len(translation_requests)}",
            "summary_zh": "批量上限测试返回中文摘要，证明只翻译允许数量的新闻。",
            "content_zh": "批量上限测试返回中文正文第一段。\n\n批量上限测试返回中文正文第二段。",
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
        live_llm_max_items=1,
    )
    translated_rows = conn.execute(
        """
        SELECT rss_guid
        FROM news_item
        WHERE title_zh IS NOT NULL AND title_zh != original_title
        ORDER BY rss_guid ASC
        """
    ).fetchall()

    assert summary["translated_item_count"] == 1
    assert len(scoring_requests) == 2
    assert len(translation_requests) == 1
    assert [row["rss_guid"] for row in translated_rows] == ["live-agents-production-two"]


def test_backfill_top_scored_translations_prioritizes_top_target(monkeypatch, tmp_path):
    from backend.app.services import pipeline

    db_path = tmp_path / "rss.sqlite3"
    conn = connect(str(db_path))
    initialize_database(conn)
    conn.execute(
        """
        INSERT INTO source (name, rss_url, is_enabled, fetch_frequency, created_at)
        VALUES ('Live AI Source', 'https://live.example/rss.xml', 1, 'twice_daily', '2026-07-01T00:00:00Z')
        """
    )
    source_id = conn.execute("SELECT id FROM source").fetchone()["id"]

    def insert_fetched_item(
        guid: str,
        title: str,
        score: int,
        published_at: str,
        *,
        translated: bool = False,
        fallback: bool = False,
    ) -> None:
        content_raw = f"{title} raw RSS summary"
        content_full = f"{title} full article text"
        if translated:
            title_zh = f"{title} 中文标题"
            summary_zh = f"{title} 中文摘要"
            content_zh = f"{title} 中文正文第一段。\n\n{title} 中文正文第二段。"
        elif fallback:
            title_zh = title
            summary_zh = content_raw
            content_zh = content_full
        else:
            title_zh = None
            summary_zh = None
            content_zh = None
        conn.execute(
            """
            INSERT INTO news_item (
              source_id, rss_guid, original_url, canonical_url,
              original_title, published_at, score, pipeline_state,
              is_selected, content_raw, content_full, title_zh,
              summary_zh, content_zh, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'fetched', 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                guid,
                f"https://live.example/{guid}",
                f"https://live.example/{guid}",
                title,
                published_at,
                score,
                content_raw,
                content_full,
                title_zh,
                summary_zh,
                content_zh,
                "2026-07-01T00:00:00Z",
                "2026-07-01T00:00:00Z",
            ),
        )

    insert_fetched_item("score-100-fallback", "Top fallback", 100, "2026-07-01T00:03:00Z", fallback=True)
    insert_fetched_item("score-95-translated", "Already translated", 95, "2026-07-01T00:02:00Z", translated=True)
    insert_fetched_item("score-90-missing", "Missing translation", 90, "2026-07-01T00:01:00Z")
    insert_fetched_item("score-80-outside", "Outside top target", 80, "2026-07-01T00:00:00Z", fallback=True)
    conn.commit()

    translation_requests = []

    def fake_request_live_translation(request, **_kwargs):
        translation_requests.append(request)
        return {
            "title_zh": f"{request['original_title']} 中文补翻",
            "summary_zh": "评分优先补翻返回中文摘要，证明 Top 目标内的未翻译项会被处理。",
            "content_zh": "评分优先补翻返回中文正文第一段。\n\n评分优先补翻返回中文正文第二段。",
            "category_zh": "产品",
        }, None

    monkeypatch.setattr(pipeline, "request_live_translation", fake_request_live_translation)

    before_rows = top_scored_fetched_news_for_translation(conn, target_count=3)
    result = backfill_top_scored_translations(
        conn,
        target_count=3,
        now="2026-07-01T00:10:00Z",
        live_llm_base_url="https://llm.example.test/api/v4",
        live_llm_api_key="secret-token",
        live_llm_model="glm-test",
        live_llm_timeout_seconds=1,
        live_llm_retry_count=0,
        live_llm_concurrency=2,
    )
    rows = conn.execute(
        """
        SELECT rss_guid, title_zh, summary_zh, content_zh
        FROM news_item
        ORDER BY score DESC
        """
    ).fetchall()

    assert [row["rss_guid"] for row in before_rows] == [
        "score-100-fallback",
        "score-95-translated",
        "score-90-missing",
    ]
    assert [request["original_title"] for request in translation_requests] == [
        "Top fallback",
        "Missing translation",
    ]
    assert result["requested_count"] == 2
    assert result["translated_count"] == 2
    assert result["top_translated_count"] == 3
    assert result["top_untranslated_count"] == 0
    assert rows[1]["title_zh"] == "Already translated 中文标题"
    assert rows[3]["title_zh"] == "Outside top target"
    assert rows[3]["summary_zh"] == "Outside top target raw RSS summary"
    assert rows[3]["content_zh"] == "Outside top target full article text"
    conn.close()

    persisted_conn = connect(str(db_path))
    persisted_top_rows = top_scored_fetched_news_for_translation(persisted_conn, target_count=3)
    try:
        assert all(
            row["title_zh"]
            and row["summary_zh"]
            and row["content_zh"]
            and not (
                row["title_zh"] == row["original_title"]
                and row["summary_zh"] == row["content_raw"]
                and row["content_zh"] == (row["content_full"] or row["content_raw"])
            )
            for row in persisted_top_rows
        )
    finally:
        persisted_conn.close()


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
    assert by_guid["fixture-translated-96"]["title_zh"] == "OpenAI 发布 LifeSciBench 生命科学基准"
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
    assert summary["source_success_count"] == 22
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
