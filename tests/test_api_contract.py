import json
from pathlib import Path
from fastapi.testclient import TestClient
from urllib.parse import urlsplit

from backend.app.main import create_app


FIXTURE_ROOT = Path(__file__).resolve().parents[1]
TRANSLATION_FIXTURE_PATH = FIXTURE_ROOT / "fixtures" / "llm" / "translation.json"


def make_client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "rss.sqlite3")))


def assert_json_response(response, status_code: int):
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/json")
    return response.json()


def is_reserved_placeholder_url(value: str) -> bool:
    host = (urlsplit(value).hostname or "").lower()
    return host in {"example.com", "example.org", "example.net"} or host.endswith(
        (".test", ".invalid")
    )


FORBIDDEN_TRANSLATION_TERMS = (
    "fixture",
    "mock",
    "模拟",
    "占位",
    "这是一条",
    "这是一篇",
)


def hidden_api_guid_rows() -> set[str]:
    payload = json.loads(TRANSLATION_FIXTURE_PATH.read_text())
    translations = payload.get("translations", {})
    return {
        guid
        for guid, record in translations.items()
        if isinstance(record, dict) and record.get("display_in_api") is False
    }


def test_contract_endpoints_return_data_envelopes(tmp_path):
    client = make_client(tmp_path)
    home = assert_json_response(client.get("/api/home"), 200)
    assert set(home["data"]) >= {"latest_news", "top_ranked_news"}
    assert isinstance(home["data"]["latest_news"], list)
    assert isinstance(home["data"]["top_ranked_news"], list)
    for item in home["data"]["latest_news"] + home["data"]["top_ranked_news"]:
        assert "summary_zh" not in item
        assert "content_zh" not in item

    refresh = assert_json_response(client.post("/api/refresh"), 200)
    assert set(refresh["data"]) == {"refreshed_at"}

    sources = assert_json_response(client.get("/api/sources"), 200)
    assert isinstance(sources["data"], list)


def test_contract_errors_use_error_envelope(tmp_path):
    client = make_client(tmp_path)
    missing = assert_json_response(client.get("/api/news/missing"), 404)
    assert set(missing["error"]) >= {"code", "message"}

    invalid = assert_json_response(
        client.post("/api/sources", json={"name": "", "rss_url": "not-a-url"}),
        400,
    )
    assert set(invalid["error"]) >= {"code", "message"}

    unknown = assert_json_response(client.get("/api/unknown"), 404)
    assert set(unknown["error"]) >= {"code", "message"}
    assert "detail" not in unknown


def test_contract_source_mutation_endpoints(tmp_path):
    client = make_client(tmp_path)
    created = assert_json_response(
        client.post(
            "/api/sources",
            json={"name": "Example AI Feed", "rss_url": "https://example.com/rss.xml"},
        ),
        201,
    )["data"]
    assert created["is_enabled"] is True
    assert created["fetch_frequency"] == "twice_daily"

    updated = assert_json_response(
        client.patch(f"/api/sources/{created['id']}", json={"is_enabled": False}),
        200,
    )["data"]
    assert updated["is_enabled"] is False

    deleted = client.delete(f"/api/sources/{created['id']}")
    assert deleted.status_code == 204
    assert deleted.content == b""

    sources = assert_json_response(client.get("/api/sources"), 200)["data"]
    assert all(source["id"] != created["id"] for source in sources)


def test_refresh_populates_news_items_idempotently_and_home_reads_database(tmp_path):
    client = make_client(tmp_path)
    conn = client.app.state.db

    assert conn.execute("SELECT COUNT(*) AS count FROM news_item").fetchone()["count"] == 0

    assert_json_response(client.post("/api/refresh"), 200)
    assert_json_response(client.post("/api/refresh"), 200)

    assert conn.execute("SELECT COUNT(*) AS count FROM news_item").fetchone()["count"] == 14
    assert conn.execute("SELECT COUNT(*) AS count FROM processing_log").fetchone()["count"] >= 16

    home = assert_json_response(client.get("/api/home"), 200)["data"]
    assert [item["id"] for item in home["latest_news"][:10]] == [
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
        "3",
    ]
    assert [item["id"] for item in home["top_ranked_news"]] == [
        "3",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
    ]
    assert "14" not in [item["id"] for item in home["top_ranked_news"]]
    assert all(item["status"] == "translated" for item in home["latest_news"])
    assert all(item["status"] == "translated" for item in home["top_ranked_news"])
    assert all(item.get("summary_zh") for item in home["latest_news"] + home["top_ranked_news"])
    assert all("content_zh" not in item for item in home["latest_news"] + home["top_ranked_news"])

    ready_detail = assert_json_response(client.get("/api/news/1"), 200)["data"]
    assert ready_detail["status"] == "ready"
    assert "summary_zh" not in ready_detail
    assert "content_zh" not in ready_detail

    translated_detail = assert_json_response(client.get("/api/news/3"), 200)["data"]
    assert translated_detail["status"] == "translated"
    assert translated_detail["summary_zh"]
    assert translated_detail["content_zh"]


def test_hidden_translation_probes_are_not_retrievable_by_id(tmp_path):
    client = make_client(tmp_path)
    assert_json_response(client.post("/api/refresh"), 200)

    conn = client.app.state.db
    hidden_guids = hidden_api_guid_rows()
    assert hidden_guids
    hidden_news_ids = {
        str(row["id"])
        for row in conn.execute(
            f"""
            SELECT id FROM news_item
            WHERE rss_guid IN ({', '.join(['?'] * len(hidden_guids))})
            """,
            tuple(hidden_guids),
        ).fetchall()
    }
    for guid in hidden_guids:
        row = conn.execute(
            "SELECT id FROM news_item WHERE rss_guid = ?",
            (guid,),
        ).fetchone()
        assert row is not None
        response = assert_json_response(
            client.get(f"/api/news/{row['id']}"),
            404,
        )
        assert response["error"]["code"] == "NEWS_NOT_FOUND"

    home = assert_json_response(client.get("/api/home"), 200)["data"]
    visible_home_ids = {
        item["id"] for item in home["latest_news"] + home["top_ranked_news"]
    }
    assert not (hidden_news_ids & visible_home_ids)


def test_refresh_preserves_real_rss_original_urls_in_api(tmp_path):
    client = make_client(tmp_path)
    conn = client.app.state.db

    assert_json_response(client.post("/api/refresh"), 200)

    translated = conn.execute(
        """
        SELECT id, original_url
        FROM news_item
        WHERE rss_guid = 'fixture-translated-96'
        """
    ).fetchone()
    detail = assert_json_response(client.get(f"/api/news/{translated['id']}"), 200)["data"]

    assert detail["status"] == "translated"
    assert detail["original_url"] == translated["original_url"]
    assert urlsplit(detail["original_url"]).scheme in {"http", "https"}
    assert not is_reserved_placeholder_url(detail["original_url"])

    hn_row = conn.execute(
        """
        SELECT id, original_url, discussion_url
        FROM news_item
        WHERE rss_guid = 'fixture-rank-95'
        """
    ).fetchone()
    hn_detail = assert_json_response(client.get(f"/api/news/{hn_row['id']}"), 200)["data"]

    assert hn_detail["original_url"] == hn_row["original_url"]
    assert (urlsplit(hn_detail["original_url"]).hostname or "").lower() != "news.ycombinator.com"
    assert hn_row["discussion_url"].startswith("https://news.ycombinator.com/item?id=")
    assert "discussion_url" not in hn_detail


def test_translated_news_details_return_readable_article_specific_content(tmp_path):
    client = make_client(tmp_path)
    conn = client.app.state.db

    assert_json_response(client.post("/api/refresh"), 200)

    translated_rows = conn.execute(
        """
        SELECT id, rss_guid
        FROM news_item
        WHERE title_zh IS NOT NULL
          AND summary_zh IS NOT NULL
          AND content_zh IS NOT NULL
        ORDER BY id ASC
        """
    ).fetchall()

    assert translated_rows
    hidden_guids = hidden_api_guid_rows()
    for row in translated_rows:
        if row["rss_guid"] in hidden_guids:
            continue
        detail = assert_json_response(client.get(f"/api/news/{row['id']}"), 200)["data"]
        summary = detail["summary_zh"]
        content = detail["content_zh"]
        joined = "\n".join([detail["title"], summary, content]).lower()
        paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]

        assert detail["status"] == "translated"
        assert not [term for term in FORBIDDEN_TRANSLATION_TERMS if term.lower() in joined], row["rss_guid"]
        assert len(summary) >= 28, row["rss_guid"]
        assert len(content) >= 110, row["rss_guid"]
        assert len(paragraphs) >= 2, row["rss_guid"]


def test_refresh_concurrent_rejection_before_success_returns_null(tmp_path):
    client = make_client(tmp_path)
    conn = client.app.state.db

    client.app.state.refresh_running = True
    response = assert_json_response(client.post("/api/refresh"), 200)
    client.app.state.refresh_running = False

    assert response == {"data": {"refreshed_at": None}}
    assert conn.execute("SELECT COUNT(*) AS count FROM processing_log").fetchone()["count"] == 0
