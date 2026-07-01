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


def test_live_llm_endpoint_keeps_full_chat_completions_url():
    from backend.app.services.pipeline import live_llm_endpoint

    endpoint = "https://apihub.agnes-ai.com/v1/chat/completions"

    assert live_llm_endpoint(endpoint, anthropic_format=False) == endpoint


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
