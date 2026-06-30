from fastapi.testclient import TestClient

from backend.app.main import create_app


def make_client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "rss.sqlite3")))


def assert_json_response(response, status_code: int):
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/json")
    return response.json()


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

    assert conn.execute("SELECT COUNT(*) AS count FROM news_item").fetchone()["count"] == 4
    assert conn.execute("SELECT COUNT(*) AS count FROM processing_log").fetchone()["count"] >= 16

    home = assert_json_response(client.get("/api/home"), 200)["data"]
    assert [item["id"] for item in home["latest_news"]] == ["1", "3", "4"]
    assert [item["id"] for item in home["top_ranked_news"]] == ["3", "4", "1"]

    ready_detail = assert_json_response(client.get("/api/news/1"), 200)["data"]
    assert ready_detail["status"] == "ready"
    assert "summary_zh" not in ready_detail
    assert "content_zh" not in ready_detail

    translated_detail = assert_json_response(client.get("/api/news/3"), 200)["data"]
    assert translated_detail["status"] == "translated"
    assert translated_detail["summary_zh"]
    assert translated_detail["content_zh"]
