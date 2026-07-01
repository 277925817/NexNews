import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
RESERVED_HOSTS = {"example.com", "example.org", "example.net"}
RESERVED_SUFFIXES = (".test", ".invalid")
FORBIDDEN_TRANSLATION_TERMS = (
    "fixture",
    "mock",
    "模拟",
    "占位",
    "这是一条",
    "这是一篇",
)
TRANSLATED_GUID_KEYWORDS = {
    "fixture-translated-96": ("LifeSciBench", "生命科学", "基准"),
    "fixture-rank-95": ("安全", "基准", "企业"),
    "fixture-rank-94": ("评测", "智能体", "任务"),
    "fixture-rank-93": ("芯片", "调度", "延迟"),
    "fixture-rank-92": ("多模态", "工具", "基准"),
    "fixture-rank-91": ("数据", "合成", "问答"),
    "fixture-rank-90": ("检索", "规划", "小型"),
    "fixture-rank-89": ("可观测", "提示词", "回归"),
    "fixture-rank-88": ("编码", "仓库", "契约"),
    "fixture-rank-87": ("产品", "漂移", "智能体"),
    "fixture-old-high-99": ("里程碑", "窗口", "榜单"),
}


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query), ""))


def read_fixture(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def is_reserved_placeholder_url(value: str) -> bool:
    host = (urlsplit(value).hostname or "").lower()
    return host in RESERVED_HOSTS or host.endswith(RESERVED_SUFFIXES)


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


def test_displayable_rss_fixture_links_are_public_non_placeholder_urls():
    rss = read_fixture("fixtures/rss/feeds.json")
    articles = read_fixture("fixtures/articles/article_map.json")

    displayable_items = [
        item
        for feed in rss["feeds"]
        for item in feed.get("items", [])
        if item["guid"] != "fixture-low-59"
    ]
    item_links = [item["link"] for item in displayable_items]
    assert item_links
    assert all(urlsplit(link).scheme in {"http", "https"} for link in item_links)
    assert not [link for link in item_links if is_reserved_placeholder_url(link)]

    hn_items = [
        item
        for item in displayable_items
        if str(item["guid"]).startswith("fixture-rank-")
        or item["guid"] == "fixture-old-high-99"
    ]
    assert hn_items
    assert not [
        item["link"]
        for item in hn_items
        if (urlsplit(item["link"]).hostname or "").lower() == "news.ycombinator.com"
        and urlsplit(item["link"]).path == "/item"
    ]
    assert all(
        str(item.get("comments_url", "")).startswith("https://news.ycombinator.com/item?id=")
        for item in hn_items
    )

    article_urls = set(articles["articles"])
    case_urls = {item["url"] for item in articles["cases"]}
    assert not [url for url in article_urls | case_urls if is_reserved_placeholder_url(url)]
    assert case_urls.issubset(article_urls)
    assert canonical_url(
        "https://developers.openai.com/resources/agentic-app-production/?utm_source=rss"
    ) in article_urls
    assert canonical_url("https://openai.com/index/introducing-life-sci-bench/") in article_urls
    assert (
        canonical_url("https://openai.com/index/introducing-gpt-4-1-in-the-api/?utm_medium=rss")
        not in article_urls
    )


def test_displayable_openai_fixture_does_not_use_archival_gpt_4_1_release():
    rss = read_fixture("fixtures/rss/feeds.json")
    archival_urls = {
        canonical_url("https://openai.com/index/gpt-4-1"),
        canonical_url("https://openai.com/index/gpt-4-1/"),
        canonical_url("https://openai.com/index/introducing-gpt-4-1-in-the-api/?utm_medium=rss"),
    }
    displayable_openai_urls = {
        canonical_url(item["link"])
        for feed in rss["feeds"]
        if feed.get("rss_url") == "https://openai.com/news/rss.xml"
        for item in feed.get("items", [])
        if item["guid"] != "fixture-low-59"
    }

    assert displayable_openai_urls
    assert not (displayable_openai_urls & archival_urls)


def test_successful_translation_fixtures_are_article_specific_and_readable():
    translation = read_fixture("fixtures/llm/translation.json")
    translations = translation["translations"]

    successful_records = {
        guid: record
        for guid, record in translations.items()
        if guid in TRANSLATED_GUID_KEYWORDS
    }
    assert set(successful_records) == set(TRANSLATED_GUID_KEYWORDS)

    for guid, record in successful_records.items():
        title = record["title_zh"]
        summary = record["summary_zh"]
        content = record["content_zh"]
        joined = "\n".join([title, summary, content]).lower()
        paragraphs = [part.strip() for part in content.split("\n\n") if part.strip()]
        keywords = TRANSLATED_GUID_KEYWORDS[guid]

        assert not [term for term in FORBIDDEN_TRANSLATION_TERMS if term.lower() in joined], guid
        assert len(summary) >= 28, guid
        assert len(content) >= 110, guid
        assert len(paragraphs) >= 2, guid
        assert any(keyword in summary for keyword in keywords), guid
        assert any(keyword in content for keyword in keywords), guid
