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
    assert result["source_success_count"] == 32
    assert result["source_failure_count"] == 1
    assert len(rows) == 15
    assert {row["pipeline_state"] for row in rows} == {"raw"}
    assert all(row["score"] is None for row in rows)
    assert all(row["content_full"] is None for row in rows)
    assert all(row["title_zh"] is None for row in rows)
    assert len(crawl_logs) == 33
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
