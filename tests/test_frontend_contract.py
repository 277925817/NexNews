from pathlib import Path


def test_frontend_uses_documented_api_contract_only():
    text = Path("index.html").read_text()

    for legacy_endpoint in ("/rss", "/api/sync", "/api/feeds", "/api/items"):
        assert legacy_endpoint not in text

    for contract_endpoint in (
        "/api/home",
        "/api/refresh",
        "/api/sources",
        "/api/news/",
    ):
        assert contract_endpoint in text


def test_frontend_checks_json_content_type_before_parsing():
    text = Path("index.html").read_text()

    assert "content-type" in text
    assert "application/json" in text
    assert "response.json()" in text
