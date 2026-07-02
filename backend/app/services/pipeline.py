"""News refresh pipeline with fixture and live network execution paths."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import httpx


FIXED_NOW = "2026-06-28T09:00:00Z"
ROOT_DIR = Path(__file__).resolve().parents[3]
RSS_FIXTURE_PATH = ROOT_DIR / "fixtures" / "rss" / "feeds.json"
ARTICLE_MAP_PATH = ROOT_DIR / "fixtures" / "articles" / "article_map.json"
SCORING_FIXTURE_PATH = ROOT_DIR / "fixtures" / "llm" / "scoring.json"
TRANSLATION_FIXTURE_PATH = ROOT_DIR / "fixtures" / "llm" / "translation.json"
TRACE_ID = "refresh-fixture-20260628T090000Z"
AI_VALUE_SCORE_THRESHOLD = 81
AI_RELEVANCE_THRESHOLD = 70
SCORING_RETRY_MAX = 2
LIVE_LLM_RETRY_MAX = 2
LIVE_LLM_RETRY_BACKOFF_SECONDS = 0.5
TERMINAL_LIVE_LLM_STATUS_CODES = {400, 401, 403, 404, 429}
LIVE_LLM_AVAILABILITY_ERRORS = {
    "llm_bad_request",
    "llm_auth",
    "llm_endpoint",
    "llm_rate_limited",
}
LIVE_RSS_MAX_AGE_DAYS = 30
LIVE_TRANSLATION_SYSTEM_PROMPT = (
    "你是 AI 新闻聚合系统的中文翻译器。"
    "请根据用户提供的 JSON，将同一条新闻翻译并改写为可阅读中文。"
    "只返回 JSON 对象，不要 Markdown，不要解释。"
    "必须包含非空字段：title_zh、summary_zh、content_zh、category_zh。"
    "summary_zh 用一到两句话概括同一条新闻；content_zh 至少两段，保留事实，不编造。"
)
LIVE_SCORING_SYSTEM_PROMPT = (
    "你是 AI 新闻聚合系统的新闻价值评分器。"
    "请根据用户提供的 JSON 判断该新闻是否是高价值 AI 新闻，score 是最终 AI 价值分，不是热度分。"
    "先判定 is_ai_news：只有直接涉及 AI 模型、研究、评测、芯片/基础设施、开发者平台、"
    "安全治理、监管政策或重要产业采用的新信息，才可为 true。"
    "ai_relevance_score 只衡量 AI 相关性；非 AI 或只是泛科技背景提到 AI 时应低于 70。"
    "score 按以下维度综合评分：影响范围 30%，原创性/信息增量 20%，来源权威性与证据可信度 20%，"
    "技术/产品/政策具体性 20%，时效性 10%。"
    "优先给高分：一手发布、权威研究、重要模型或能力发布、可靠 benchmark/eval、"
    "关键 AI 基础设施、安全治理或监管变化、明确改变开发者/企业决策的信息。"
    "必须执行分数上限：非 AI 新闻 score 最高不得超过 20；AI 相关但没有具体新信息最高不得超过 45；"
    "SEO 软文、广告导流、标题党、普通工具清单、会议/折扣/招聘、加密或财经噪声最高不得超过 50；"
    "只有融资、合作、营销或传闻而没有实质技术/产品/政策变化最高不得超过 60；"
    "重复转述、二手汇总或缺少清晰来源最高不得超过 70。"
    "90-100 只给重大且可信的一手 AI 进展；81-89 给具体且有决策价值的 AI 新闻；"
    "60-80 是相关但增量有限，不应被选入后续流程。"
    "只返回 JSON 对象，不要 Markdown，不要解释。"
    "必须包含字段：is_ai_news、ai_relevance_score、score、reason。"
    "is_ai_news 必须是布尔值；ai_relevance_score 和 score 必须是 0 到 100 的整数；"
    "reason 必须用一句话说明评分依据。"
)

AI_KEYWORD_PATTERNS = (
    r"\bai\b",
    r"\bartificial intelligence\b",
    r"\bgenerative ai\b",
    r"\bgenai\b",
    r"\bllms?\b",
    r"\blarge language models?\b",
    r"\blanguage models?\b",
    r"\bfoundation models?\b",
    r"\bmultimodal\b",
    r"\bmachine learning\b",
    r"\bdeep learning\b",
    r"\bneural networks?\b",
    r"\btransformers?\b",
    r"\bagents?\b",
    r"\bagentic\b",
    r"\binference\b",
    r"\btraining\b",
    r"\bbenchmarks?\b",
    r"\bevals?\b",
    r"\bevaluations?\b",
    r"\brag\b",
    r"\bopenai\b",
    r"\banthropic\b",
    r"\bdeepmind\b",
    r"\bhugging face\b",
    r"\bnvidia\b",
    r"\bgpus?\b",
)

AI_HIGH_VALUE_PATTERNS = (
    r"\breleases?\b",
    r"\blaunches?\b",
    r"\bpublishes?\b",
    r"\bintroduces?\b",
    r"\bbenchmarks?\b",
    r"\bevals?\b",
    r"\bevaluations?\b",
    r"\bresearch\b",
    r"\bmodel\b",
    r"\bmodels\b",
    r"\binfrastructure\b",
    r"\binference\b",
    r"\blatency\b",
    r"\bchip\b",
    r"\bchips\b",
    r"\bsafety\b",
    r"\bpolicy\b",
    r"\bregulation\b",
    r"\bgovernance\b",
    r"\bproduction\b",
    r"\bworkflows?\b",
    r"\bobservability\b",
)

AI_LOW_VALUE_PATTERNS = (
    r"\brumou?rs?\b",
    r"\bfunding\b",
    r"\bpartnership\b",
    r"\bmarketing\b",
    r"\bseo\b",
    r"\bsponsored\b",
    r"\badvertis(e|ing|ement)\b",
    r"\baffiliate\b",
    r"\bdiscounts?\b",
    r"\btickets?\b",
    r"\btravel\b",
    r"\bwebinar\b",
    r"\blisticles?\b",
    r"\broundups?\b",
    r"\btop\s+\d+\b",
    r"\bcrypto\b",
    r"\bstocks?\b",
)

LIVE_REQUEST_HEADERS = {
    "Accept": "text/html,application/xml,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "User-Agent": "Mozilla/5.0 (compatible; rss-aggregator/1.0; +https://example.com/bot)",
}


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            urlencode(query),
            "",
        )
    )


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_url_text(
    url: str,
    *,
    timeout: float = 12,
    retry_count: int = 3,
    retry_backoff_seconds: float = 0.5,
    headers: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    request_headers = dict(LIVE_REQUEST_HEADERS)
    if headers:
        request_headers.update(headers)
    last_error = "network"
    attempts = max(0, int(retry_count)) + 1
    for _ in range(attempts):
        for trust_env in (True, False):
            try:
                with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=trust_env) as client:
                    response = client.get(url, headers=request_headers)
                if 200 <= response.status_code < 300:
                    if response.text:
                        return response.text, None
                    last_error = "empty_body"
                else:
                    last_error = f"status_{response.status_code}"
                break
            except ValueError:
                last_error = "proxy_config"
                if trust_env:
                    continue
                break
            except httpx.HTTPError:
                last_error = "http_error"
                break
        if _ < attempts - 1:
            if retry_backoff_seconds > 0:
                time.sleep(retry_backoff_seconds)
    return None, last_error


def log_processing(
    conn: sqlite3.Connection,
    *,
    source_id: int | None = None,
    news_item_id: int | None = None,
    stage: str,
    success: int,
    error: str | None = None,
    now: str = FIXED_NOW,
    trace_id: str = TRACE_ID,
) -> None:
    conn.execute(
        """
        INSERT INTO processing_log (
          source_id, news_item_id, stage, success, error, trace_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, news_item_id, stage, success, error, trace_id, now),
    )


def active_sources(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return conn.execute(
        """
        SELECT id, name, rss_url
        FROM source
        WHERE deleted_at IS NULL AND is_enabled = 1
        ORDER BY created_at ASC
        """
    ).fetchall()


def fixture_feeds(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    feeds = payload.get("feeds", [])
    if not isinstance(feeds, list):
        return {}
    return {
        str(feed.get("rss_url")): feed
        for feed in feeds
        if isinstance(feed, dict) and feed.get("rss_url")
    }


def is_valid_rss_item(item: object) -> bool:
    return (
        isinstance(item, dict)
        and bool(item.get("guid"))
        and bool(item.get("link"))
        and bool(item.get("title"))
    )


def rss_item_discussion_url(item: dict[str, object]) -> str | None:
    value = item.get("discussion_url") or item.get("comments_url")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def xml_item_text(item: object, tag_name: str) -> str | None:
    node = item.find(tag_name)
    if node is None:
        return None
    text = node.get_text("", strip=True)
    return text or None


def atom_entry_link(entry: object) -> str | None:
    alternate = entry.find("link", attrs={"rel": "alternate"}) if entry else None
    node = alternate or entry.find("link") if entry else None
    if node is None:
        return None
    href = node.get("href")
    if isinstance(href, str) and href.strip():
        return href.strip()
    text = node.get_text("", strip=True)
    return text or None


def _rss_published_at(item: dict[str, object]) -> str:
    raw_value = str(
        item.get("published_at")
        or item.get("pubDate")
        or item.get("published")
        or item.get("updated")
        or item.get("date")
        or FIXED_NOW
    )
    parsed = parse_rss_datetime(raw_value)
    if parsed is None:
        return raw_value
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_rss_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def rss_item_within_live_window(
    item: dict[str, object],
    *,
    now: str,
    max_age_days: int = LIVE_RSS_MAX_AGE_DAYS,
) -> bool:
    published_at = parse_rss_datetime(item.get("published_at"))
    now_at = parse_rss_datetime(now)
    if published_at is None or now_at is None:
        return True
    return published_at >= now_at - timedelta(days=max_age_days)


def filter_live_rss_items_by_window(
    items: list[dict[str, object]],
    *,
    now: str,
    max_age_days: int = LIVE_RSS_MAX_AGE_DAYS,
) -> list[dict[str, object]]:
    return [
        item
        for item in items
        if rss_item_within_live_window(item, now=now, max_age_days=max_age_days)
    ]


def parse_rss_feed_text(payload: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(payload, "xml")
    parsed_items: list[dict[str, object]] = []
    for item in soup.find_all("item"):
        title = xml_item_text(item, "title")
        link = xml_item_text(item, "link")
        guid = xml_item_text(item, "guid") or link
        if not title or not link:
            continue
        parsed_items.append(
            {
                "guid": str(guid),
                "title": str(title),
                "link": str(link),
                "discussion_url": xml_item_text(item, "comments"),
                "published_at": _rss_published_at(
                    {
                        "pubDate": xml_item_text(item, "pubDate"),
                        "published": xml_item_text(item, "published"),
                        "updated": xml_item_text(item, "updated"),
                        "date": xml_item_text(item, "date"),
                    }
                ),
                "summary": str(xml_item_text(item, "description") or xml_item_text(item, "content") or ""),
            }
        )
    for entry in soup.find_all("entry"):
        title = xml_item_text(entry, "title")
        link = atom_entry_link(entry)
        guid = xml_item_text(entry, "id") or link
        if not title or not link:
            continue
        parsed_items.append(
            {
                "guid": str(guid),
                "title": str(title),
                "link": str(link),
                "discussion_url": None,
                "published_at": _rss_published_at(
                    {
                        "published": xml_item_text(entry, "published"),
                        "updated": xml_item_text(entry, "updated"),
                    }
                ),
                "summary": str(xml_item_text(entry, "summary") or xml_item_text(entry, "content") or ""),
            }
        )
    return parsed_items


def news_item_exists(conn: sqlite3.Connection, canonical_url: str) -> bool:
    row = conn.execute(
        "SELECT id FROM news_item WHERE canonical_url = ?",
        (canonical_url,),
    ).fetchone()
    return row is not None


def ingest_feed_items(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    feed: dict[str, object],
    seen_canonical_urls: set[str],
    now: str,
) -> int:
    inserted_count = 0
    items = feed.get("items", [])
    if not isinstance(items, list):
        return inserted_count

    for item in items:
        if not is_valid_rss_item(item):
            continue
        canonical_url = canonicalize_url(str(item["link"]))
        if canonical_url in seen_canonical_urls or news_item_exists(conn, canonical_url):
            continue
        seen_canonical_urls.add(canonical_url)
        insert_raw_item(conn, source_id=source_id, item=item, canonical_url=canonical_url, now=now)
        inserted_count += 1
    return inserted_count


def ingest_fixture_rss(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> dict[str, int]:
    rss_payload = read_json(fixture_root / "fixtures" / "rss" / "feeds.json")
    feeds_by_url = fixture_feeds(rss_payload)
    seen_canonical_urls: set[str] = set()
    result = {"source_success_count": 0, "source_failure_count": 0, "inserted_count": 0}

    for source in active_sources(conn):
        source_id = int(source["id"])
        feed = feeds_by_url.get(str(source["rss_url"]))
        if not feed or feed.get("status") != "success":
            result["source_failure_count"] += 1
            error = str(feed.get("error") if feed else "missing_fixture")
            log_processing(conn, source_id=source_id, stage="crawl", success=0, error=error, now=now)
            continue
        result["source_success_count"] += 1
        log_processing(conn, source_id=source_id, stage="crawl", success=1, now=now)
        result["inserted_count"] += ingest_feed_items(
            conn, source_id=source_id, feed=feed, seen_canonical_urls=seen_canonical_urls, now=now
        )
    conn.commit()
    return result


def ingest_live_rss(
    conn: sqlite3.Connection,
    *,
    now: str = FIXED_NOW,
    timeout: float = 12,
    retry_count: int = 3,
    retry_backoff_seconds: float = 0.5,
    max_workers: int = 8,
) -> dict[str, int]:
    result = {"source_success_count": 0, "source_failure_count": 0, "inserted_count": 0}
    seen_canonical_urls: set[str] = set()

    sources = active_sources(conn)

    def fetch_source(source: dict[str, object]) -> tuple[dict[str, object], str | None, str | None]:
        feed_text, error = fetch_url_text(
            str(source["rss_url"]),
            timeout=timeout,
            retry_count=retry_count,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        return source, feed_text, error

    worker_count = max(1, min(max_workers, len(sources) or 1))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        fetched_sources = list(executor.map(fetch_source, sources))

    for source, feed_text, error in fetched_sources:
        source_id = int(source["id"])
        if not feed_text:
            result["source_failure_count"] += 1
            log_processing(
                conn,
                source_id=source_id,
                stage="crawl",
                success=0,
                error=error,
                now=now,
            )
            continue
        parsed_items = parse_rss_feed_text(feed_text)
        if not parsed_items:
            result["source_failure_count"] += 1
            log_processing(
                conn,
                source_id=source_id,
                stage="crawl",
                success=0,
                error="empty_feed",
                now=now,
            )
            continue
        feed = {"items": filter_live_rss_items_by_window(parsed_items, now=now)}
        result["source_success_count"] += 1
        log_processing(
            conn,
            source_id=source_id,
            stage="crawl",
            success=1,
            now=now,
        )
        result["inserted_count"] += ingest_feed_items(
            conn, source_id=source_id, feed=feed, seen_canonical_urls=seen_canonical_urls, now=now
        )
    conn.commit()
    return result


def insert_raw_item(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    item: dict[str, object],
    canonical_url: str,
    now: str,
) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO news_item (
          source_id, rss_guid, original_url, canonical_url, original_title,
          discussion_url, published_at, pipeline_state, is_selected,
          content_raw, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'raw', 0, ?, ?, ?)
        """,
        (
            source_id,
            str(item["guid"]),
            str(item["link"]),
            canonical_url,
            str(item["title"]),
            rss_item_discussion_url(item),
            str(item["published_at"]),
            str(item.get("summary") or ""),
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM news_item WHERE canonical_url = ?",
        (canonical_url,),
    ).fetchone()
    return int(row["id"])


def build_scoring_request(record: dict[str, object]) -> dict[str, str]:
    return {
        "title": str(record.get("original_title") or ""),
        "summary": str(record.get("content_raw") or ""),
        "source": str(record.get("source_name") or ""),
        "published_at": str(record.get("published_at") or ""),
        "original_link": str(record.get("original_url") or ""),
    }


def validate_scoring_response(response: object) -> tuple[dict[str, object] | None, str | None]:
    if not isinstance(response, dict):
        return None, "validation_llm_error"
    is_ai_news = response.get("is_ai_news")
    ai_relevance_score = response.get("ai_relevance_score")
    score = response.get("score")
    reason = response.get("reason")
    if not isinstance(is_ai_news, bool):
        return None, "validation_llm_error"
    if (
        not isinstance(ai_relevance_score, int)
        or isinstance(ai_relevance_score, bool)
        or ai_relevance_score < 0
        or ai_relevance_score > 100
    ):
        return None, "validation_llm_error"
    if not isinstance(score, int) or isinstance(score, bool) or score < 0 or score > 100:
        return None, "validation_llm_error"
    if not isinstance(reason, str) or not reason.strip():
        return None, "validation_llm_error"
    return {
        "is_ai_news": is_ai_news,
        "ai_relevance_score": ai_relevance_score,
        "score": score,
        "reason": reason,
    }, None


def live_score_for_record(record: dict[str, object]) -> int:
    title = str(record.get("original_title") or "")
    source_name = str(record.get("source_name") or "")
    summary = str(record.get("content_raw") or "")
    score = 55
    score += min(30, len(title) // 2)
    score += min(20, len(summary) // 50)
    score += min(10, len(source_name))
    return min(100, score)


def count_pattern_matches(text: str, patterns: tuple[str, ...]) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE))


def clamp_score(value: int) -> int:
    return max(0, min(100, int(value)))


def fallback_ai_value_record(record: dict[str, object]) -> dict[str, object]:
    request = build_scoring_request(record)
    if not request["title"].strip() or not request["original_link"].strip():
        return {
            "is_ai_news": False,
            "ai_relevance_score": 0,
            "score": 0,
            "reason": "Missing title or original link.",
        }

    text = " ".join(
        [
            request["title"],
            request["summary"],
            request["source"],
            request["original_link"],
        ]
    ).lower()
    ai_hits = count_pattern_matches(text, AI_KEYWORD_PATTERNS)
    high_value_hits = count_pattern_matches(text, AI_HIGH_VALUE_PATTERNS)
    low_value_hits = count_pattern_matches(text, AI_LOW_VALUE_PATTERNS)
    if ai_hits == 0:
        return {
            "is_ai_news": False,
            "ai_relevance_score": 0,
            "score": min(20, max(1, live_score_for_record(record) // 4)),
            "reason": "Local fallback rejected non-AI news.",
        }

    relevance = clamp_score(68 + min(24, ai_hits * 6) + min(8, high_value_hits * 2))
    score = 50 + min(25, high_value_hits * 5) + min(15, ai_hits * 3)
    if request["summary"].strip():
        score += min(10, len(request["summary"]) // 120)
    if high_value_hits == 0:
        score = min(score, 65)
    if low_value_hits and high_value_hits < 2:
        score = min(score, 55)
    return {
        "is_ai_news": relevance >= AI_RELEVANCE_THRESHOLD,
        "ai_relevance_score": relevance,
        "score": clamp_score(score),
        "reason": "Local fallback AI value heuristic.",
    }


def apply_missing_summary_penalty(score: int, request: dict[str, str]) -> int:
    if request["summary"].strip():
        return score
    return max(0, score - 20)


def apply_scoring_penalties(record: dict[str, object], request: dict[str, str]) -> dict[str, object]:
    return {**record, "score": apply_missing_summary_penalty(int(record["score"]), request)}


def scoring_timeout_error(guid: str, scoring_payload: dict[str, object]) -> str | None:
    timeout_cases = scoring_payload.get("timeout_cases", {})
    if not isinstance(timeout_cases, dict) or guid not in timeout_cases:
        return None
    case = timeout_cases.get(guid)
    if isinstance(case, dict):
        return str(case.get("error") or "timeout")
    return "timeout"


def scoring_response_for_guid(guid: str, scoring_payload: dict[str, object]) -> object:
    scores = scoring_payload.get("scores", {})
    if isinstance(scores, dict) and guid in scores:
        return scores[guid]
    invalid_cases = scoring_payload.get("invalid_cases", {})
    if isinstance(invalid_cases, dict) and guid in invalid_cases:
        case = invalid_cases[guid]
        return case.get("response") if isinstance(case, dict) else None
    return None


def score_request_with_fixture(
    guid: str,
    request: dict[str, str],
    scoring_payload: dict[str, object],
) -> dict[str, object]:
    if not request["title"].strip() or not request["original_link"].strip():
        return {
            "is_ai_news": False,
            "ai_relevance_score": 0,
            "score": 0,
            "reason": "Missing title or original link.",
            "error": None,
            "retry_count": 0,
        }

    timeout_error = scoring_timeout_error(guid, scoring_payload)
    if timeout_error:
        return {"score": None, "error": timeout_error, "retry_count": SCORING_RETRY_MAX}

    record, error = validate_scoring_response(scoring_response_for_guid(guid, scoring_payload))
    if error:
        return {"score": None, "error": error, "retry_count": SCORING_RETRY_MAX}
    assert record is not None
    return {
        **apply_scoring_penalties(record, request),
        "error": None,
        "retry_count": 0,
    }


def score_rss_item(
    item: dict[str, object],
    *,
    source_name: str,
    scoring_payload: dict[str, object],
) -> dict[str, object]:
    request = build_scoring_request(
        {
            "original_title": item.get("title"),
            "content_raw": item.get("summary") or "",
            "source_name": source_name,
            "published_at": item.get("published_at"),
            "original_url": item.get("link"),
        }
    )
    return score_request_with_fixture(str(item.get("guid") or ""), request, scoring_payload)


def raw_news_for_scoring(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return conn.execute(
        """
        SELECT
          news_item.id, news_item.rss_guid, news_item.original_title,
          news_item.content_raw, news_item.published_at, news_item.original_url,
          source.name AS source_name
        FROM news_item
        JOIN source ON source.id = news_item.source_id
        WHERE news_item.pipeline_state = 'raw'
        ORDER BY news_item.id ASC
        """
    ).fetchall()


def raw_news_for_live_scoring(
    conn: sqlite3.Connection,
    *,
    max_items: int,
) -> list[dict[str, object]]:
    limit_clause = "LIMIT ?" if max_items > 0 else ""
    params: tuple[object, ...] = (max_items,) if max_items > 0 else ()
    return conn.execute(
        f"""
        SELECT
          news_item.id, news_item.rss_guid, news_item.original_title,
          news_item.content_raw, news_item.published_at, news_item.original_url,
          source.name AS source_name
        FROM news_item
        JOIN source ON source.id = news_item.source_id
        WHERE news_item.pipeline_state = 'raw'
        ORDER BY news_item.published_at DESC, news_item.id DESC
        {limit_clause}
        """,
        params,
    ).fetchall()


def score_raw_news(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> dict[str, int]:
    scoring_payload = read_json(fixture_root / "fixtures" / "llm" / "scoring.json")
    result = {"scored_count": 0, "failed_count": 0, "selected_count": 0}
    for row in raw_news_for_scoring(conn):
        request = build_scoring_request(row)
        score_result = score_request_with_fixture(str(row["rss_guid"] or ""), request, scoring_payload)
        score = score_result["score"]
        if isinstance(score, int):
            selected = apply_score(
                conn,
                news_item_id=int(row["id"]),
                score=score,
                is_ai_news=bool(score_result.get("is_ai_news")),
                ai_relevance_score=int(score_result.get("ai_relevance_score") or 0),
                now=now,
            )
            result["scored_count"] += 1
            result["selected_count"] += 1 if selected else 0
            continue
        result["failed_count"] += 1
        error = str(score_result["error"] or "validation_llm_error")
        log_processing(conn, news_item_id=int(row["id"]), stage="score", success=0, error=error, now=now)
    conn.commit()
    return result


def request_live_scoring_rows(
    rows: list[dict[str, object]],
    *,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    timeout_seconds: float,
    retry_count: int,
    concurrency: int,
) -> list[tuple[dict[str, object], dict[str, object] | None, str | None]]:
    def score_live_row(row: dict[str, object]) -> tuple[dict[str, object], dict[str, object] | None, str | None]:
        record, error = request_live_scoring(
            build_scoring_request(row),
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
        )
        return row, record, error

    max_workers = min(max(1, concurrency), len(rows)) if rows else 1
    if max_workers == 1:
        return [score_live_row(row) for row in rows]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(score_live_row, rows))


def score_raw_news_live(
    conn: sqlite3.Connection,
    *,
    now: str = FIXED_NOW,
    use_live_llm: bool = False,
    live_llm_base_url: str | None = None,
    live_llm_api_key: str | None = None,
    live_llm_model: str | None = None,
    live_llm_timeout_seconds: float = 30,
    live_llm_retry_count: int = LIVE_LLM_RETRY_MAX,
    live_llm_max_score_items: int = 20,
    live_llm_score_concurrency: int = 2,
) -> dict[str, int]:
    result = {"scored_count": 0, "failed_count": 0, "selected_count": 0, "llm_unavailable_count": 0}
    rows = (
        raw_news_for_live_scoring(conn, max_items=live_llm_max_score_items)
        if use_live_llm
        else raw_news_for_scoring(conn)
    )
    live_results = (
        request_live_scoring_rows(
            rows,
            base_url=live_llm_base_url,
            api_key=live_llm_api_key,
            model=live_llm_model,
            timeout_seconds=live_llm_timeout_seconds,
            retry_count=live_llm_retry_count,
            concurrency=live_llm_score_concurrency,
        )
        if use_live_llm
        else []
    )
    live_results_by_id = {int(row["id"]): (record, error) for row, record, error in live_results}
    for row in rows:
        request = build_scoring_request(row)
        if use_live_llm:
            record, error = live_results_by_id[int(row["id"])]
            record = apply_scoring_penalties(record, request) if record else None
            score = int(record["score"]) if record else None
        else:
            record = apply_scoring_penalties(fallback_ai_value_record(row), request)
            score = int(record["score"])
            error = None
        if isinstance(score, int):
            selected = apply_score(
                conn,
                news_item_id=int(row["id"]),
                score=score,
                is_ai_news=bool(record["is_ai_news"]),
                ai_relevance_score=int(record["ai_relevance_score"]),
                now=now,
            )
            result["scored_count"] += 1
            result["selected_count"] += 1 if selected else 0
            continue
        result["failed_count"] += 1
        if use_live_llm and error in LIVE_LLM_AVAILABILITY_ERRORS:
            result["llm_unavailable_count"] += 1
        log_processing(
            conn,
            news_item_id=int(row["id"]),
            stage="score",
            success=0,
            error=error or "live_scoring_error",
            now=now,
        )
    conn.commit()
    return result


def apply_score(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    score: int,
    is_ai_news: bool,
    ai_relevance_score: int,
    now: str,
) -> bool:
    is_selected = score_is_selected(
        score,
        is_ai_news=is_ai_news,
        ai_relevance_score=ai_relevance_score,
    )
    conn.execute(
        """
        UPDATE news_item
        SET score = ?,
            is_ai_news = ?,
            ai_relevance_score = ?,
            pipeline_state = 'scored',
            is_selected = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            score,
            1 if is_ai_news else 0,
            ai_relevance_score,
            1 if is_selected else 0,
            now,
            news_item_id,
        ),
    )
    log_processing(conn, news_item_id=news_item_id, stage="score", success=1, now=now)
    return is_selected


def score_is_selected(
    score: int,
    *,
    is_ai_news: bool = True,
    ai_relevance_score: int = 100,
    score_threshold: int = AI_VALUE_SCORE_THRESHOLD,
    relevance_threshold: int = AI_RELEVANCE_THRESHOLD,
) -> bool:
    return bool(is_ai_news) and ai_relevance_score >= relevance_threshold and score >= score_threshold


def selected_fetch_candidates(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return conn.execute(
        """
        SELECT
          id, rss_guid, canonical_url, score, is_ai_news,
          ai_relevance_score, pipeline_state, is_selected, content_full,
          published_at
        FROM news_item
        WHERE pipeline_state = 'scored'
          AND is_selected = 1
          AND score >= ?
          AND is_ai_news = 1
          AND ai_relevance_score >= ?
        ORDER BY score DESC, published_at DESC, id ASC
        """,
        (AI_VALUE_SCORE_THRESHOLD, AI_RELEVANCE_THRESHOLD),
    ).fetchall()


def article_records(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    articles = payload.get("articles", {})
    if not isinstance(articles, dict):
        return {}
    return {
        str(url): record
        for url, record in articles.items()
        if isinstance(record, dict)
    }


def extract_article_text(path_or_html: Path | str) -> str:
    if isinstance(path_or_html, Path):
        soup = BeautifulSoup(path_or_html.read_text(), "html.parser")
    else:
        soup = BeautifulSoup(str(path_or_html), "html.parser")
    article = soup.find("article") or soup.body
    if article is None:
        return ""
    chunks = [
        node.get_text(" ", strip=True)
        for node in article.find_all(["h1", "h2", "h3", "p"])
        if node.get_text(" ", strip=True)
    ]
    if chunks:
        return "\n".join(chunks)
    return article.get_text(" ", strip=True)


def fetch_content(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    canonical_url: str,
    article_map: dict[str, dict[str, object]],
    fixture_root: Path,
    now: str,
    allow_live_network: bool = False,
    live_timeout_seconds: float = 12,
    live_retry_count: int = 3,
    live_retry_backoff_seconds: float = 0.5,
) -> bool:
    row = conn.execute(
        "SELECT content_raw FROM news_item WHERE id = ?",
        (news_item_id,),
    ).fetchone()
    content_raw = str(row["content_raw"] or "")
    record = article_map.get(canonical_url)
    content_full: str | None = None
    success = 0
    error = "network"

    if record and record.get("status") == "success":
        article_path = fixture_root / "fixtures" / "articles" / str(record.get("path"))
        content_full = extract_article_text(article_path)
        if content_full:
            success = 1
            error = None
        else:
            error = "parsing"
    elif allow_live_network:
        content_full_text, fetch_error = fetch_url_text(
            canonical_url,
            timeout=live_timeout_seconds,
            retry_count=live_retry_count,
            retry_backoff_seconds=live_retry_backoff_seconds,
        )
        if content_full_text is not None:
            content_full = extract_article_text(content_full_text)
            if content_full:
                success = 1
                error = None
            else:
                error = "parsing"
        else:
            error = fetch_error
    elif record and record.get("error"):
        error = str(record["error"])

    if not content_full and not content_raw:
        log_processing(
            conn,
            news_item_id=news_item_id,
            stage="fetch",
            success=0,
            error=error,
            now=now,
        )
        return False

    conn.execute(
        """
        UPDATE news_item
        SET content_full = ?,
            pipeline_state = 'fetched',
            updated_at = ?
        WHERE id = ?
        """,
        (content_full, now, news_item_id),
    )
    log_processing(
        conn,
        news_item_id=news_item_id,
        stage="fetch",
        success=success,
        error=error,
        now=now,
    )
    return True


def fetch_selected_content(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
    allow_live_network: bool = False,
    live_timeout_seconds: float = 12,
    live_retry_count: int = 3,
    live_retry_backoff_seconds: float = 0.5,
) -> dict[str, int]:
    article_payload = read_json(fixture_root / "fixtures" / "articles" / "article_map.json")
    articles_by_url = article_records(article_payload)
    result = {"fetched_count": 0, "content_full_count": 0, "fallback_count": 0, "failed_count": 0}
    for row in selected_fetch_candidates(conn):
        fetched = fetch_content(
            conn,
            news_item_id=int(row["id"]),
            canonical_url=str(row["canonical_url"]),
            article_map=articles_by_url,
            fixture_root=fixture_root,
            now=now,
            allow_live_network=allow_live_network,
            live_timeout_seconds=live_timeout_seconds,
            live_retry_count=live_retry_count,
            live_retry_backoff_seconds=live_retry_backoff_seconds,
        )
        if not fetched:
            result["failed_count"] += 1
            continue
        result["fetched_count"] += 1
        stored = conn.execute("SELECT content_full FROM news_item WHERE id = ?", (row["id"],)).fetchone()
        if stored and stored["content_full"]:
            result["content_full_count"] += 1
        else:
            result["fallback_count"] += 1
    conn.commit()
    return result


def translation_records(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    translations = payload.get("translations", {})
    if not isinstance(translations, dict):
        return {}
    return {
        str(guid): record
        for guid, record in translations.items()
        if isinstance(record, dict)
    }


def pending_translation_guids(payload: dict[str, object]) -> set[str]:
    pending = payload.get("pending_guids", [])
    if not isinstance(pending, list):
        return set()
    return {str(item) for item in pending}


def build_translation_request(record: dict[str, object]) -> dict[str, object]:
    content_full = str(record.get("content_full") or "")
    content_raw = str(record.get("content_raw") or "")
    return {
        "original_title": str(record.get("original_title") or ""),
        "original_summary": content_raw,
        "original_content": content_full or content_raw,
        "source": str(record.get("source_name") or ""),
        "score": int(record.get("score") or 0),
    }


def has_valid_translation_record(record: dict[str, object] | None) -> bool:
    required_fields = ("title_zh", "summary_zh", "content_zh", "category_zh")
    return bool(
        record
        and all(isinstance(record.get(field), str) and record[field].strip() for field in required_fields)
    )


def strip_json_code_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def post_live_llm_response(
    *,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, object],
    timeout_seconds: float,
) -> tuple[httpx.Response | None, str]:
    response = None
    last_request_error = "llm"
    for trust_env in (False, True):
        try:
            with httpx.Client(
                timeout=timeout_seconds,
                follow_redirects=True,
                trust_env=trust_env,
            ) as client:
                response = client.post(endpoint, headers=headers, json=payload)
            break
        except ValueError:
            last_request_error = "llm"
        except httpx.TimeoutException:
            last_request_error = "timeout"
        except httpx.HTTPError:
            last_request_error = "llm"
        if trust_env:
            break
    return response, last_request_error


def live_llm_http_error(response: httpx.Response) -> str:
    if response.status_code == 400:
        return "llm_bad_request"
    if response.status_code in {401, 403}:
        return "llm_auth"
    if response.status_code == 404:
        return "llm_endpoint"
    if response.status_code == 429:
        return "llm_rate_limited"
    if response.status_code >= 500:
        return "llm_server"
    return "llm"


def is_terminal_live_llm_response(response: httpx.Response) -> bool:
    return response.status_code in TERMINAL_LIVE_LLM_STATUS_CODES


def uses_anthropic_llm_format(base_url: str | None) -> bool:
    return "/anthropic" in str(base_url or "").lower().rstrip("/")


def live_llm_endpoint(base_url: str, *, anthropic_format: bool) -> str:
    normalized = base_url.rstrip("/")
    if normalized.lower().endswith(("/chat/completions", "/messages")):
        return normalized
    suffix = "messages" if anthropic_format else "chat/completions"
    return f"{normalized}/{suffix}"


def live_llm_headers(api_key: str, *, anthropic_format: bool) -> dict[str, str]:
    if anthropic_format:
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def live_llm_payload(
    *,
    model: str,
    system_prompt: str,
    request: dict[str, object],
    temperature: float,
    anthropic_format: bool,
) -> dict[str, object]:
    user_content = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    if anthropic_format:
        return {
            "model": model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
            "temperature": temperature,
        }
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
    }


def live_llm_response_content(response: httpx.Response) -> str | None:
    response_payload = response.json()
    choices = response_payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        return str(content)

    anthropic_content = response_payload.get("content")
    if isinstance(anthropic_content, str):
        return anthropic_content
    if isinstance(anthropic_content, list):
        chunks = [
            str(part.get("text"))
            for part in anthropic_content
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
        ]
        return "\n".join(chunks)
    return None


def parse_live_translation_response(response: httpx.Response) -> tuple[dict[str, object] | None, str | None]:
    try:
        content = live_llm_response_content(response)
        if not content:
            return None, "validation_llm_error"
        record = json.loads(strip_json_code_fence(str(content)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "validation_llm_error"
    if isinstance(record, dict) and has_valid_translation_record(record):
        return record, None
    return None, "validation_llm_error"


def parse_live_scoring_response(response: httpx.Response) -> tuple[dict[str, object] | None, str | None]:
    try:
        content = live_llm_response_content(response)
        if not content:
            return None, "validation_llm_error"
        record = json.loads(strip_json_code_fence(str(content)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "validation_llm_error"
    scoring_record, error = validate_scoring_response(record)
    if error:
        return None, error
    assert scoring_record is not None
    return scoring_record, None


def request_live_scoring(
    request: dict[str, object],
    *,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    timeout_seconds: float = 30,
    retry_count: int = SCORING_RETRY_MAX,
) -> tuple[dict[str, object] | None, str | None]:
    if not base_url or not api_key or not model:
        return None, "llm_config_missing"
    anthropic_format = uses_anthropic_llm_format(base_url)
    endpoint = live_llm_endpoint(base_url, anthropic_format=anthropic_format)
    payload = live_llm_payload(
        model=model,
        system_prompt=LIVE_SCORING_SYSTEM_PROMPT,
        request=request,
        temperature=0.1,
        anthropic_format=anthropic_format,
    )
    headers = live_llm_headers(api_key, anthropic_format=anthropic_format)
    last_request_error = "llm"
    retry_max = max(0, int(retry_count))
    for attempt in range(retry_max + 1):
        response, last_request_error = post_live_llm_response(
            endpoint=endpoint,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if response is None:
            if attempt < retry_max:
                time.sleep(LIVE_LLM_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            return None, last_request_error
        if not 200 <= response.status_code < 300:
            last_request_error = live_llm_http_error(response)
            if is_terminal_live_llm_response(response):
                return None, last_request_error
        else:
            record, last_request_error = parse_live_scoring_response(response)
            if record:
                return record, None
        if attempt < retry_max:
            time.sleep(LIVE_LLM_RETRY_BACKOFF_SECONDS * (attempt + 1))
    return None, last_request_error


def request_live_translation(
    request: dict[str, object],
    *,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    timeout_seconds: float = 30,
    retry_count: int = LIVE_LLM_RETRY_MAX,
) -> tuple[dict[str, object] | None, str | None]:
    if not base_url or not api_key or not model:
        return None, "llm_config_missing"
    anthropic_format = uses_anthropic_llm_format(base_url)
    endpoint = live_llm_endpoint(base_url, anthropic_format=anthropic_format)
    payload = live_llm_payload(
        model=model,
        system_prompt=LIVE_TRANSLATION_SYSTEM_PROMPT,
        request=request,
        temperature=0.2,
        anthropic_format=anthropic_format,
    )
    headers = live_llm_headers(api_key, anthropic_format=anthropic_format)
    last_request_error = "llm"
    retry_max = max(0, int(retry_count))
    for attempt in range(retry_max + 1):
        response, last_request_error = post_live_llm_response(
            endpoint=endpoint,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if response is None:
            if attempt < retry_max:
                time.sleep(LIVE_LLM_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            return None, last_request_error
        if not 200 <= response.status_code < 300:
            last_request_error = live_llm_http_error(response)
            if is_terminal_live_llm_response(response):
                return None, last_request_error
            if attempt < retry_max:
                time.sleep(LIVE_LLM_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            return None, last_request_error
        record, last_request_error = parse_live_translation_response(response)
        if record:
            return record, None
        if attempt < retry_max:
            time.sleep(LIVE_LLM_RETRY_BACKOFF_SECONDS * (attempt + 1))
    return None, last_request_error


def fetched_news_for_translation(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return conn.execute(
        """
        SELECT
          news_item.id, news_item.rss_guid, news_item.original_title,
          news_item.content_raw, news_item.content_full, news_item.score,
          news_item.title_zh, news_item.summary_zh, news_item.content_zh,
          news_item.has_translate_failed,
          source.name AS source_name
        FROM news_item
        JOIN source ON source.id = news_item.source_id
        WHERE news_item.pipeline_state = 'fetched'
          AND news_item.is_selected = 1
          AND news_item.is_ai_news = 1
          AND news_item.ai_relevance_score >= ?
          AND news_item.score >= ?
          AND (news_item.content_full IS NOT NULL OR news_item.content_raw IS NOT NULL)
        ORDER BY news_item.published_at DESC, news_item.id DESC
        """,
        (AI_RELEVANCE_THRESHOLD, AI_VALUE_SCORE_THRESHOLD),
    ).fetchall()


def top_scored_fetched_news_for_translation(
    conn: sqlite3.Connection,
    *,
    target_count: int,
) -> list[dict[str, object]]:
    limit_clause = "LIMIT ?" if target_count > 0 else ""
    params: tuple[object, ...] = (target_count,) if target_count > 0 else ()
    params = (
        AI_VALUE_SCORE_THRESHOLD,
        AI_RELEVANCE_THRESHOLD,
        *params,
    )
    return conn.execute(
        f"""
        SELECT
          news_item.id, news_item.rss_guid, news_item.original_title,
          news_item.content_raw, news_item.content_full, news_item.score,
          news_item.title_zh, news_item.summary_zh, news_item.content_zh,
          news_item.has_translate_failed,
          source.name AS source_name,
          news_item.published_at
        FROM news_item
        JOIN source ON source.id = news_item.source_id
        WHERE news_item.pipeline_state = 'fetched'
          AND news_item.is_selected = 1
          AND news_item.score >= ?
          AND news_item.is_ai_news = 1
          AND news_item.ai_relevance_score >= ?
          AND (news_item.content_full IS NOT NULL OR news_item.content_raw IS NOT NULL)
        ORDER BY news_item.score DESC, news_item.published_at DESC, news_item.id DESC
        {limit_clause}
        """,
        params,
    ).fetchall()


def is_original_fallback_translation(row: dict[str, object]) -> bool:
    fallback_content = row["content_full"] or row["content_raw"]
    return bool(
        row["title_zh"]
        and row["summary_zh"]
        and row["content_zh"]
        and row["title_zh"] == row["original_title"]
        and row["summary_zh"] == row["content_raw"]
        and row["content_zh"] == fallback_content
    )


def has_complete_non_fallback_translation(row: dict[str, object]) -> bool:
    return bool(
        row["title_zh"]
        and row["summary_zh"]
        and row["content_zh"]
        and not is_original_fallback_translation(row)
    )


def write_translation_success(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    record: dict[str, object],
    now: str,
) -> None:
    conn.execute(
        """
        UPDATE news_item
        SET title_zh = ?,
            summary_zh = ?,
            content_zh = ?,
            has_translate_failed = 0,
            updated_at = ?
        WHERE id = ?
        """,
        (
            str(record["title_zh"]),
            str(record["summary_zh"]),
            str(record["content_zh"]),
            now,
            news_item_id,
        ),
    )
    log_processing(
        conn,
        news_item_id=news_item_id,
        stage="translate",
        success=1,
        now=now,
    )


def write_translation_failure(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    error: str,
    now: str,
) -> None:
    conn.execute(
        """
        UPDATE news_item
        SET title_zh = NULL,
            summary_zh = NULL,
            content_zh = NULL,
            has_translate_failed = 1,
            updated_at = ?
        WHERE id = ?
        """,
        (now, news_item_id),
    )
    log_processing(
        conn,
        news_item_id=news_item_id,
        stage="translate",
        success=0,
        error=error,
        now=now,
    )


def write_original_fallback_translation(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    fallback_title: str,
    fallback_summary: str,
    fallback_content: str,
    now: str,
) -> None:
    conn.execute(
        """
        UPDATE news_item
        SET title_zh = ?,
            summary_zh = ?,
            content_zh = ?,
            has_translate_failed = 0,
            updated_at = ?
        WHERE id = ?
        """,
        (fallback_title, fallback_summary, fallback_content, now, news_item_id),
    )
    log_processing(
        conn,
        news_item_id=news_item_id,
        stage="translate",
        success=0,
        error="live_llm_disabled_fallback",
        now=now,
    )


def apply_translation(
    conn: sqlite3.Connection,
    *,
    news_item_id: int,
    guid: str,
    translation_payload: dict[str, object],
    now: str,
    fallback_to_original: bool = False,
    fallback_title: str = "",
    fallback_summary: str = "",
    fallback_content: str = "",
) -> str:
    if guid in pending_translation_guids(translation_payload):
        return "pending"

    record = translation_records(translation_payload).get(guid)
    if has_valid_translation_record(record):
        write_translation_success(
            conn,
            news_item_id=news_item_id,
            record=record,
            now=now,
        )
        return "translated"

    if fallback_to_original and fallback_summary and fallback_content:
        write_original_fallback_translation(
            conn,
            news_item_id=news_item_id,
            fallback_title=fallback_title,
            fallback_summary=fallback_summary,
            fallback_content=fallback_content,
            now=now,
        )
        return "fallback"

    write_translation_failure(
        conn,
        news_item_id=news_item_id,
        error="validation_llm_error",
        now=now,
    )
    return "failed"


def select_live_translation_rows(
    rows: list[dict[str, object]],
    *,
    max_items: int,
) -> tuple[list[dict[str, object]], int]:
    live_rows: list[dict[str, object]] = []
    pending_count = 0
    for row in rows:
        if has_complete_non_fallback_translation(row):
            continue
        if max_items > 0 and len(live_rows) >= max_items:
            pending_count += 1
            continue
        live_rows.append(row)
    return live_rows, pending_count


def request_live_translation_rows(
    rows: list[dict[str, object]],
    *,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    timeout_seconds: float,
    retry_count: int,
    concurrency: int,
) -> list[tuple[dict[str, object], dict[str, object] | None, str | None]]:
    def translate_live_row(
        row: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object] | None, str | None]:
        record, error = request_live_translation(
            build_translation_request(row),
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
        )
        return row, record, error

    max_workers = min(max(1, concurrency), len(rows)) if rows else 1
    if max_workers == 1:
        return [translate_live_row(row) for row in rows]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(translate_live_row, rows))


def write_live_translation_results(
    conn: sqlite3.Connection,
    translated_rows: list[tuple[dict[str, object], dict[str, object] | None, str | None]],
    *,
    now: str,
) -> dict[str, int]:
    result = {"translated_count": 0, "failed_count": 0, "pending_count": 0, "fallback_count": 0}
    for row, record, error in translated_rows:
        if record:
            write_translation_success(
                conn,
                news_item_id=int(row["id"]),
                record=record,
                now=now,
            )
            result["translated_count"] += 1
            continue
        write_translation_failure(
            conn,
            news_item_id=int(row["id"]),
            error=error or "validation_llm_error",
            now=now,
        )
        result["failed_count"] += 1
    return result


def translate_live_fetched_content(
    conn: sqlite3.Connection,
    rows: list[dict[str, object]],
    *,
    now: str,
    live_llm_base_url: str | None,
    live_llm_api_key: str | None,
    live_llm_model: str | None,
    live_llm_timeout_seconds: float,
    live_llm_retry_count: int,
    live_llm_max_items: int,
    live_llm_concurrency: int,
) -> dict[str, int]:
    live_rows, pending_count = select_live_translation_rows(rows, max_items=live_llm_max_items)
    translated_rows = request_live_translation_rows(
        live_rows,
        base_url=live_llm_base_url,
        api_key=live_llm_api_key,
        model=live_llm_model,
        timeout_seconds=live_llm_timeout_seconds,
        retry_count=live_llm_retry_count,
        concurrency=live_llm_concurrency,
    )
    result = write_live_translation_results(conn, translated_rows, now=now)
    result["pending_count"] = pending_count
    conn.commit()
    return result


def backfill_top_scored_translations(
    conn: sqlite3.Connection,
    *,
    target_count: int,
    now: str,
    live_llm_base_url: str | None,
    live_llm_api_key: str | None,
    live_llm_model: str | None,
    live_llm_timeout_seconds: float,
    live_llm_retry_count: int,
    live_llm_concurrency: int,
) -> dict[str, int]:
    target_rows = top_scored_fetched_news_for_translation(
        conn,
        target_count=target_count,
    )
    pending_rows = [
        row for row in target_rows if not has_complete_non_fallback_translation(row)
    ]
    translated_rows = request_live_translation_rows(
        pending_rows,
        base_url=live_llm_base_url,
        api_key=live_llm_api_key,
        model=live_llm_model,
        timeout_seconds=live_llm_timeout_seconds,
        retry_count=live_llm_retry_count,
        concurrency=live_llm_concurrency,
    )
    result = write_live_translation_results(conn, translated_rows, now=now)
    conn.commit()
    updated_target_rows = top_scored_fetched_news_for_translation(
        conn,
        target_count=target_count,
    )
    result.update(
        {
            "target_count": target_count,
            "top_item_count": len(updated_target_rows),
            "requested_count": len(pending_rows),
            "top_translated_count": sum(
                1 for row in updated_target_rows if has_complete_non_fallback_translation(row)
            ),
            "top_untranslated_count": sum(
                1 for row in updated_target_rows if not has_complete_non_fallback_translation(row)
            ),
        }
    )
    return result


def translate_mock_fetched_content(
    conn: sqlite3.Connection,
    rows: list[dict[str, object]],
    *,
    translation_payload: dict[str, object],
    now: str,
    fallback_to_original: bool,
) -> dict[str, int]:
    pending_guids = pending_translation_guids(translation_payload)
    result = {"translated_count": 0, "failed_count": 0, "pending_count": 0, "fallback_count": 0}
    for row in rows:
        if has_complete_non_fallback_translation(row):
            continue
        guid = str(row["rss_guid"] or "")
        if guid in pending_guids:
            result["pending_count"] += 1
            continue
        fallback_title = str(row["original_title"] or "")
        fallback_summary = str(row["content_raw"] or "")
        fallback_content = str(row["content_full"] or row["content_raw"] or "")
        status = apply_translation(
            conn,
            news_item_id=int(row["id"]),
            guid=guid,
            translation_payload=translation_payload,
            now=now,
            fallback_to_original=fallback_to_original,
            fallback_title=fallback_title,
            fallback_summary=fallback_summary,
            fallback_content=fallback_content,
        )
        if status == "translated":
            result["translated_count"] += 1
        elif status == "failed":
            result["failed_count"] += 1
        elif status == "fallback":
            result["fallback_count"] += 1
    conn.commit()
    return result


def translate_fetched_content(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
    fallback_to_original: bool = False,
    use_live_llm: bool = False,
    live_llm_base_url: str | None = None,
    live_llm_api_key: str | None = None,
    live_llm_model: str | None = None,
    live_llm_timeout_seconds: float = 30,
    live_llm_retry_count: int = LIVE_LLM_RETRY_MAX,
    live_llm_max_items: int = 20,
    live_llm_concurrency: int = 2,
) -> dict[str, int]:
    translation_payload = (
        {}
        if use_live_llm
        else read_json(fixture_root / "fixtures" / "llm" / "translation.json")
    )
    rows = fetched_news_for_translation(conn)
    if use_live_llm:
        return translate_live_fetched_content(
            conn,
            rows,
            now=now,
            live_llm_base_url=live_llm_base_url,
            live_llm_api_key=live_llm_api_key,
            live_llm_model=live_llm_model,
            live_llm_timeout_seconds=live_llm_timeout_seconds,
            live_llm_retry_count=live_llm_retry_count,
            live_llm_max_items=live_llm_max_items,
            live_llm_concurrency=live_llm_concurrency,
        )
    return translate_mock_fetched_content(
        conn,
        rows,
        translation_payload=translation_payload,
        now=now,
        fallback_to_original=fallback_to_original,
    )


def rss_fixture_item_count(conn: sqlite3.Connection, fixture_root: Path = ROOT_DIR) -> int:
    rss_payload = read_json(fixture_root / "fixtures" / "rss" / "feeds.json")
    feeds_by_url = fixture_feeds(rss_payload)
    item_count = 0
    for source in active_sources(conn):
        feed = feeds_by_url.get(str(source["rss_url"]))
        items = feed.get("items", []) if feed and feed.get("status") == "success" else []
        item_count += len(items) if isinstance(items, list) else 0
    return item_count


def processing_failure_details(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT stage, error, COUNT(*) AS count
        FROM processing_log
        WHERE success = 0
        GROUP BY stage, error
        ORDER BY stage ASC, error ASC
        """
    ).fetchall()
    return {f"{row['stage']}:{row['error']}": int(row["count"]) for row in rows}


def run_fixture_pipeline_summary(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> dict[str, object]:
    started_at = now
    rss_item_count = rss_fixture_item_count(conn, fixture_root)
    ingest_result = ingest_fixture_rss(conn, fixture_root=fixture_root, now=now)
    score_result = score_raw_news(conn, fixture_root=fixture_root, now=now)
    fetch_result = fetch_selected_content(conn, fixture_root=fixture_root, now=now)
    translate_result = translate_fetched_content(conn, fixture_root=fixture_root, now=now)
    return {
        "started_at": started_at,
        "finished_at": now,
        "source_success_count": ingest_result["source_success_count"],
        "source_failure_count": ingest_result["source_failure_count"],
        "rss_item_count": rss_item_count,
        "new_item_count": ingest_result["inserted_count"],
        "scored_item_count": score_result["scored_count"],
        "selected_item_count": score_result["selected_count"],
        "fetched_item_count": fetch_result["fetched_count"],
        "translated_item_count": translate_result["translated_count"],
        "failure_details": processing_failure_details(conn),
    }


def run_live_pipeline_summary(
    conn: sqlite3.Connection,
    *,
    now: str | None = None,
    allow_live_network: bool = True,
    allow_live_llm: bool = False,
    allow_live_article_fetch: bool = False,
    request_timeout_seconds: float = 12,
    request_retry_count: int = 3,
    request_retry_backoff_seconds: float = 0.5,
    live_rss_concurrency: int = 8,
    live_llm_base_url: str | None = None,
    live_llm_api_key: str | None = None,
    live_llm_model: str | None = None,
    live_llm_timeout_seconds: float = 30,
    live_llm_retry_count: int = LIVE_LLM_RETRY_MAX,
    live_llm_max_items: int = 20,
    live_llm_concurrency: int = 2,
    live_llm_max_score_items: int = 20,
    live_llm_score_concurrency: int = 2,
) -> dict[str, object]:
    now_value = now or utcnow_iso()
    started_at = now_value
    if not allow_live_network:
        return run_fixture_pipeline_summary(
            conn,
            now=now_value,
        )
    ingest_result = ingest_live_rss(
        conn,
        now=now_value,
        timeout=request_timeout_seconds,
        retry_count=request_retry_count,
        retry_backoff_seconds=request_retry_backoff_seconds,
        max_workers=live_rss_concurrency,
    )
    score_result = score_raw_news_live(
        conn,
        now=now_value,
        use_live_llm=allow_live_llm,
        live_llm_base_url=live_llm_base_url,
        live_llm_api_key=live_llm_api_key,
        live_llm_model=live_llm_model,
        live_llm_timeout_seconds=live_llm_timeout_seconds,
        live_llm_retry_count=live_llm_retry_count,
        live_llm_max_score_items=live_llm_max_score_items,
        live_llm_score_concurrency=live_llm_score_concurrency,
    )
    fetch_result = fetch_selected_content(
        conn,
        now=now_value,
        allow_live_network=allow_live_article_fetch,
        live_timeout_seconds=request_timeout_seconds,
        live_retry_count=request_retry_count,
        live_retry_backoff_seconds=request_retry_backoff_seconds,
    )
    if allow_live_llm and int(score_result.get("llm_unavailable_count", 0)) > 0:
        translate_result = {"translated_count": 0, "failed_count": 0, "pending_count": 0, "fallback_count": 0}
    else:
        translate_result = translate_fetched_content(
            conn,
            now=now_value,
            use_live_llm=allow_live_llm,
            live_llm_base_url=live_llm_base_url,
            live_llm_api_key=live_llm_api_key,
            live_llm_model=live_llm_model,
            live_llm_timeout_seconds=live_llm_timeout_seconds,
            live_llm_retry_count=live_llm_retry_count,
            live_llm_max_items=live_llm_max_items,
            live_llm_concurrency=live_llm_concurrency,
            fallback_to_original=not allow_live_llm,
        )
    return {
        "started_at": started_at,
        "finished_at": now_value,
        "source_success_count": ingest_result["source_success_count"],
        "source_failure_count": ingest_result["source_failure_count"],
        "rss_item_count": ingest_result["inserted_count"],
        "new_item_count": ingest_result["inserted_count"],
        "scored_item_count": score_result["scored_count"],
        "selected_item_count": score_result["selected_count"],
        "fetched_item_count": fetch_result["fetched_count"],
        "translated_item_count": translate_result["translated_count"],
        "llm_unavailable_count": score_result.get("llm_unavailable_count", 0),
        "failure_details": processing_failure_details(conn),
        "runtime_mode": "live",
    }


def run_live_backlog_pipeline_summary(
    conn: sqlite3.Connection,
    *,
    now: str | None = None,
    allow_live_llm: bool = False,
    allow_live_article_fetch: bool = False,
    request_timeout_seconds: float = 12,
    request_retry_count: int = 3,
    request_retry_backoff_seconds: float = 0.5,
    live_llm_base_url: str | None = None,
    live_llm_api_key: str | None = None,
    live_llm_model: str | None = None,
    live_llm_timeout_seconds: float = 30,
    live_llm_retry_count: int = LIVE_LLM_RETRY_MAX,
    live_llm_max_items: int = 20,
    live_llm_concurrency: int = 2,
    live_llm_max_score_items: int = 10,
    live_llm_score_concurrency: int = 2,
) -> dict[str, object]:
    now_value = now or utcnow_iso()
    score_result = {"scored_count": 0, "failed_count": 0, "selected_count": 0, "llm_unavailable_count": 0}
    fetch_result = {"fetched_count": 0, "content_full_count": 0, "fallback_count": 0, "failed_count": 0}
    translate_result = {"translated_count": 0, "failed_count": 0, "pending_count": 0, "fallback_count": 0}

    if allow_live_llm:
        score_result = score_raw_news_live(
            conn,
            now=now_value,
            use_live_llm=True,
            live_llm_base_url=live_llm_base_url,
            live_llm_api_key=live_llm_api_key,
            live_llm_model=live_llm_model,
            live_llm_timeout_seconds=live_llm_timeout_seconds,
            live_llm_retry_count=live_llm_retry_count,
            live_llm_max_score_items=live_llm_max_score_items,
            live_llm_score_concurrency=live_llm_score_concurrency,
        )
        fetch_result = fetch_selected_content(
            conn,
            now=now_value,
            allow_live_network=allow_live_article_fetch,
            live_timeout_seconds=request_timeout_seconds,
            live_retry_count=request_retry_count,
            live_retry_backoff_seconds=request_retry_backoff_seconds,
        )
        if int(score_result.get("llm_unavailable_count", 0)) == 0:
            translate_result = translate_fetched_content(
                conn,
                now=now_value,
                use_live_llm=True,
                live_llm_base_url=live_llm_base_url,
                live_llm_api_key=live_llm_api_key,
                live_llm_model=live_llm_model,
                live_llm_timeout_seconds=live_llm_timeout_seconds,
                live_llm_retry_count=live_llm_retry_count,
                live_llm_max_items=live_llm_max_items,
                live_llm_concurrency=live_llm_concurrency,
            )

    return {
        "started_at": now_value,
        "finished_at": now_value,
        "source_success_count": 0,
        "source_failure_count": 0,
        "rss_item_count": 0,
        "new_item_count": 0,
        "scored_item_count": score_result["scored_count"],
        "selected_item_count": score_result["selected_count"],
        "fetched_item_count": fetch_result["fetched_count"],
        "translated_item_count": translate_result["translated_count"],
        "llm_unavailable_count": score_result.get("llm_unavailable_count", 0),
        "failure_details": processing_failure_details(conn),
        "runtime_mode": "live_backlog",
    }


def refresh_payloads(fixture_root: Path) -> tuple[
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
    dict[str, object],
    dict[str, object],
]:
    rss_payload = read_json(fixture_root / "fixtures" / "rss" / "feeds.json")
    article_payload = read_json(fixture_root / "fixtures" / "articles" / "article_map.json")
    scoring_payload = read_json(fixture_root / "fixtures" / "llm" / "scoring.json")
    translation_payload = read_json(fixture_root / "fixtures" / "llm" / "translation.json")
    return fixture_feeds(rss_payload), article_records(article_payload), scoring_payload, translation_payload


def process_refresh_item(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    source_name: str,
    item: dict[str, object],
    seen_canonical_urls: set[str],
    articles_by_url: dict[str, dict[str, object]],
    scoring_payload: dict[str, object],
    translation_payload: dict[str, object],
    fixture_root: Path,
    now: str,
) -> None:
    canonical_url = canonicalize_url(str(item["link"]))
    if canonical_url in seen_canonical_urls:
        return
    seen_canonical_urls.add(canonical_url)
    news_item_id = insert_raw_item(conn, source_id=source_id, item=item, canonical_url=canonical_url, now=now)
    score_result = score_rss_item(item, source_name=source_name, scoring_payload=scoring_payload)
    score = score_result["score"]
    if not isinstance(score, int):
        error = str(score_result["error"] or "validation_llm_error")
        log_processing(conn, news_item_id=news_item_id, stage="score", success=0, error=error, now=now)
        return
    if not apply_score(
        conn,
        news_item_id=news_item_id,
        score=score,
        is_ai_news=bool(score_result.get("is_ai_news")),
        ai_relevance_score=int(score_result.get("ai_relevance_score") or 0),
        now=now,
    ):
        return
    fetched = fetch_content(
        conn,
        news_item_id=news_item_id,
        canonical_url=canonical_url,
        article_map=articles_by_url,
        fixture_root=fixture_root,
        now=now,
    )
    if fetched:
        apply_translation(
            conn,
            news_item_id=news_item_id,
            guid=str(item["guid"]),
            translation_payload=translation_payload,
            now=now,
        )


def process_refresh_source(
    conn: sqlite3.Connection,
    *,
    source: dict[str, object],
    feed: dict[str, object] | None,
    seen_canonical_urls: set[str],
    articles_by_url: dict[str, dict[str, object]],
    scoring_payload: dict[str, object],
    translation_payload: dict[str, object],
    fixture_root: Path,
    now: str,
) -> None:
    source_id = int(source["id"])
    source_name = str(source.get("name") or "")
    if not feed or feed.get("status") != "success":
        error = str(feed.get("error") if feed else "missing_fixture")
        log_processing(conn, source_id=source_id, stage="crawl", success=0, error=error, now=now)
        return
    log_processing(conn, source_id=source_id, stage="crawl", success=1, now=now)
    items = feed.get("items", [])
    for item in items if isinstance(items, list) else []:
        if is_valid_rss_item(item):
            process_refresh_item(
                conn,
                source_id=source_id,
                source_name=source_name,
                item=item,
                seen_canonical_urls=seen_canonical_urls,
                articles_by_url=articles_by_url,
                scoring_payload=scoring_payload,
                translation_payload=translation_payload,
                fixture_root=fixture_root,
                now=now,
            )


def run_fixture_refresh(
    conn: sqlite3.Connection,
    *,
    fixture_root: Path = ROOT_DIR,
    now: str = FIXED_NOW,
) -> None:
    feeds_by_url, articles_by_url, scoring_payload, translation_payload = refresh_payloads(fixture_root)
    seen_canonical_urls: set[str] = set()

    for source in active_sources(conn):
        process_refresh_source(
            conn,
            source=source,
            feed=feeds_by_url.get(str(source["rss_url"])),
            seen_canonical_urls=seen_canonical_urls,
            articles_by_url=articles_by_url,
            scoring_payload=scoring_payload,
            translation_payload=translation_payload,
            fixture_root=fixture_root,
            now=now,
        )
    conn.commit()


def run_live_refresh(
    conn: sqlite3.Connection,
    *,
    now: str | None = None,
    allow_live_network: bool = True,
    allow_live_llm: bool = False,
    allow_live_article_fetch: bool = False,
    request_timeout_seconds: float = 12,
    request_retry_count: int = 3,
    request_retry_backoff_seconds: float = 0.5,
    live_rss_concurrency: int = 8,
    live_llm_base_url: str | None = None,
    live_llm_api_key: str | None = None,
    live_llm_model: str | None = None,
    live_llm_timeout_seconds: float = 30,
    live_llm_retry_count: int = LIVE_LLM_RETRY_MAX,
    live_llm_max_items: int = 20,
    live_llm_concurrency: int = 2,
    live_llm_max_score_items: int = 20,
    live_llm_score_concurrency: int = 2,
) -> None:
    run_live_pipeline_summary(
        conn,
        now=now,
        allow_live_network=allow_live_network,
        allow_live_llm=allow_live_llm,
        allow_live_article_fetch=allow_live_article_fetch,
        request_timeout_seconds=request_timeout_seconds,
        request_retry_count=request_retry_count,
        request_retry_backoff_seconds=request_retry_backoff_seconds,
        live_rss_concurrency=live_rss_concurrency,
        live_llm_base_url=live_llm_base_url,
        live_llm_api_key=live_llm_api_key,
        live_llm_model=live_llm_model,
        live_llm_timeout_seconds=live_llm_timeout_seconds,
        live_llm_retry_count=live_llm_retry_count,
        live_llm_max_items=live_llm_max_items,
        live_llm_concurrency=live_llm_concurrency,
        live_llm_max_score_items=live_llm_max_score_items,
        live_llm_score_concurrency=live_llm_score_concurrency,
    )
