import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))


def read_fixture(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def test_local_runtime_config_points_to_fixture_and_mock_inputs():
    from backend.app.core.config import get_local_runtime_config

    config = get_local_runtime_config(ROOT)

    assert config.database_path == ROOT / "rss.sqlite3"
    assert config.fixture_set == "mvp_acceptance_fixture@v1"
    assert config.mock_set == "mvp_mock@v1"
    assert config.clock_source == "fixed_clock_fixture@v1"
    assert config.rss_fixture_path == ROOT / "fixtures/rss/feeds.json"
    assert config.article_fixture_path == ROOT / "fixtures/articles/article_map.json"
    assert config.scoring_mock_path == ROOT / "fixtures/llm/scoring.json"
    assert config.translation_mock_path == ROOT / "fixtures/llm/translation.json"
    assert config.source_fixture_path == ROOT / "fixtures/sources/default_sources.json"
    assert config.clock_fixture_path == ROOT / "fixtures/clock/fixed_times.json"
    assert config.allow_live_network is False
    assert config.allow_live_llm is False


def test_fixture_and_mock_inputs_are_versioned_and_cover_task_cases():
    rss = read_fixture("fixtures/rss/feeds.json")
    scoring = read_fixture("fixtures/llm/scoring.json")
    translation = read_fixture("fixtures/llm/translation.json")
    clock = read_fixture("fixtures/clock/fixed_times.json")
    sources = read_fixture("fixtures/sources/default_sources.json")
    source_cases = read_fixture("fixtures/sources/source_cases.json")
    articles = read_fixture("fixtures/articles/article_map.json")

    assert rss["version"] == "mvp_acceptance_fixture@v1"
    assert scoring["version"] == "mvp_mock@v1"
    assert translation["version"] == "mvp_mock@v1"
    assert clock["version"] == "fixed_clock_fixture@v1"
    assert sources["version"] == "mvp_acceptance_fixture@v1"
    assert source_cases["version"] == "mvp_acceptance_fixture@v1"
    assert articles["version"] == "mvp_acceptance_fixture@v1"

    feeds = rss["feeds"]
    assert any(feed["status"] == "success" for feed in feeds)
    assert any(feed["status"] == "failure" for feed in feeds)

    item_links = [
        item["link"]
        for feed in feeds
        for item in feed.get("items", [])
    ]
    canonical_counts = {}
    for link in item_links:
        canonical = canonical_url(link)
        canonical_counts[canonical] = canonical_counts.get(canonical, 0) + 1
    assert any(count >= 2 for count in canonical_counts.values())

    assert scoring["scores"]
    assert scoring["invalid_cases"]["missing_score"]["response"]
    assert scoring["invalid_cases"]["out_of_range"]["response"]
    assert scoring["timeout_cases"]

    assert translation["translations"]
    assert translation["invalid_cases"]["invalid_json"]["raw_response"]
    assert translation["timeout_cases"]
    assert translation["partial_cases"]

    clock_kinds = {item["kind"] for item in clock["cases"]}
    assert {"scheduled_09", "scheduled_18", "non_trigger"}.issubset(clock_kinds)

    assert source_cases["valid_public"]
    assert source_cases["duplicate_url"]
    assert source_cases["local_url"]
    assert source_cases["private_url"]
    assert {"success", "extraction_failure", "network_failure", "empty_summary"}.issubset(
        {item["case"] for item in articles["cases"]}
    )
