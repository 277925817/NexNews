from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright


FIXED_TIMESTAMP = "2026-06-28T09:00:00Z"
DEFAULT_URL = "http://127.0.0.1:8010"
EXPECTED_LIGHT_GRAY_RGB = "rgb(243, 244, 246)"
EXPECTED_SURFACE_RGB = "rgb(255, 255, 255)"
EXPECTED_BORDER_RGB = "rgb(216, 222, 230)"
UNREADABLE_DETAIL_TITLE = "摘要和正文暂不可用"
READY_UNREADABLE_COPY = "翻译完成后将自动显示中文摘要和正文。"
FAILED_UNREADABLE_COPY = "翻译失败，当前无法显示中文摘要和正文。"
REQUIRED_SURFACES = [
    "home_news_feed",
    "high_score_list",
    "article_view",
    "sources_page",
    "refresh_action",
]
RESERVED_PLACEHOLDER_HOSTS = {"example.com", "example.org", "example.net"}
RESERVED_PLACEHOLDER_SUFFIXES = (".test", ".invalid")


def build_finding(surface: str, summary: str, evidence: str = "") -> dict[str, str]:
    finding = {
        "id": f"deployed-browser-{surface}",
        "surface": surface,
        "severity": "blocker",
        "summary": summary,
    }
    if evidence:
        finding["evidence"] = evidence
    return finding


def is_public_original_url(value: str) -> bool:
    parsed = urlparse(value)
    hostname = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False
    if hostname in RESERVED_PLACEHOLDER_HOSTS:
        return False
    return not hostname.endswith(RESERVED_PLACEHOLDER_SUFFIXES)


def read_home_metrics(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const root = document.querySelector('#root');
          const app = document.querySelector('.app-shell');
          const highScore = document.querySelector('.high-score-list');
          const highScoreStyle = highScore ? getComputedStyle(highScore) : null;
          return {
            root_child_count: root ? root.children.length : 0,
            body_text_length: document.body.innerText.length,
            app_shell_exists: Boolean(app),
            high_score_card_exists: Boolean(highScore),
            news_card_count: document.querySelectorAll('[data-news-card]').length,
            rank_item_count: document.querySelectorAll('[data-rank-item]').length,
            body_background: getComputedStyle(document.body).backgroundColor,
            app_shell_background: app ? getComputedStyle(app).backgroundColor : null,
            high_score_card_background: highScoreStyle ? highScoreStyle.backgroundColor : null,
            high_score_card_border_color: highScoreStyle ? highScoreStyle.borderTopColor : null,
            high_score_card_border_width: highScoreStyle ? highScoreStyle.borderTopWidth : null,
            high_score_card_border_radius: highScoreStyle ? highScoreStyle.borderTopLeftRadius : null
          };
        }
        """
    )


def append_home_findings(metrics: dict[str, Any], findings: list[dict[str, str]]) -> None:
    if int(metrics.get("root_child_count") or 0) <= 0:
        findings.append(build_finding("home_news_feed", "root_child_count <= 0"))
    if int(metrics.get("body_text_length") or 0) <= 0:
        findings.append(build_finding("home_news_feed", "body_text_length <= 0"))
    if metrics.get("app_shell_exists") is not True:
        findings.append(build_finding("home_news_feed", "app shell missing"))
    if int(metrics.get("news_card_count") or 0) <= 0:
        findings.append(build_finding("home_news_feed", "news cards missing"))
    if int(metrics.get("rank_item_count") or 0) <= 0:
        findings.append(build_finding("high_score_list", "rank items missing"))
    if metrics.get("high_score_card_exists") is not True:
        findings.append(build_finding("high_score_list", "high score card missing"))
    if metrics.get("body_background") != EXPECTED_LIGHT_GRAY_RGB:
        findings.append(build_finding("home_news_feed", "body background is not light gray"))
    if metrics.get("app_shell_background") != EXPECTED_LIGHT_GRAY_RGB:
        findings.append(build_finding("home_news_feed", "app shell background is not light gray"))
    if metrics.get("high_score_card_background") != EXPECTED_SURFACE_RGB:
        findings.append(build_finding("high_score_list", "high score card background is not white"))
    if metrics.get("high_score_card_border_color") != EXPECTED_BORDER_RGB:
        findings.append(build_finding("high_score_list", "high score card border color is not documented"))
    if metrics.get("high_score_card_border_width") != "1px":
        findings.append(build_finding("high_score_list", "high score card border width is not 1px"))
    if metrics.get("high_score_card_border_radius") != "8px":
        findings.append(build_finding("high_score_list", "high score card radius is not 8px"))


def check_refresh(page: Page, findings: list[dict[str, str]], timeout_ms: int) -> int | None:
    try:
        with page.expect_response(
            lambda response: response.url.endswith("/api/refresh"),
            timeout=timeout_ms,
        ) as refresh_response:
            page.get_by_role("button", name="刷新").click()
        status = refresh_response.value.status
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception as error:
        findings.append(build_finding("refresh_action", f"refresh failed: {error.__class__.__name__}"))
        return None
    if status != 200:
        findings.append(build_finding("refresh_action", f"refresh status {status}"))
    return status


def read_home_payload(page: Page, base_url: str, findings: list[dict[str, str]], timeout_ms: int) -> dict[str, Any]:
    try:
        response = page.request.get(f"{base_url}/api/home", timeout=timeout_ms)
        if response.status != 200:
            findings.append(build_finding("article_view", f"home API status {response.status}"))
            return {}
        payload = response.json()
    except Exception as error:
        findings.append(build_finding("article_view", f"home API read failed: {error.__class__.__name__}"))
        return {}
    return payload.get("data", {}) if isinstance(payload, dict) else {}


def article_metrics(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => ({
          article_exists: Boolean(document.querySelector('.article-view')),
          content_exists: Boolean(document.querySelector('.article-view__content')),
          summary_text: document.querySelector('.article-view__summary')?.textContent || '',
          body_text: document.querySelector('.article-view__body')?.textContent || '',
          original_href: document.querySelector('.article-view__original-link')?.getAttribute('href') || '',
          visible_text: document.body.innerText,
          text_length: document.body.innerText.length
        })
        """
    )


def append_article_readability_findings(
    metrics: dict[str, Any],
    status: str,
    surface: str,
    findings: list[dict[str, str]],
) -> None:
    if metrics.get("article_exists") is not True:
        findings.append(build_finding(surface, "article view missing"))
    if metrics.get("content_exists") is not True:
        findings.append(build_finding(surface, "article content missing"))
    if int(metrics.get("text_length") or 0) <= 0:
        findings.append(build_finding(surface, "article text missing"))
    if status == "translated":
        if not str(metrics.get("summary_text") or "").strip():
            findings.append(build_finding(surface, "translated summary missing"))
        if not str(metrics.get("body_text") or "").strip():
            findings.append(build_finding(surface, "translated body missing"))
        if not is_public_original_url(str(metrics.get("original_href") or "")):
            findings.append(build_finding(surface, "translated original link is not public article URL"))
        return
    visible_text = str(metrics.get("visible_text") or "")
    expected_copy = READY_UNREADABLE_COPY if status == "ready" else FAILED_UNREADABLE_COPY
    if UNREADABLE_DETAIL_TITLE not in visible_text:
        findings.append(build_finding(surface, "unreadable detail title missing"))
    if expected_copy not in visible_text:
        findings.append(build_finding(surface, "unreadable detail reason missing"))
    if status == "translation_failed" and not is_public_original_url(str(metrics.get("original_href") or "")):
        findings.append(build_finding(surface, "failed detail original link is not public article URL"))


def check_article_id(
    page: Page,
    base_url: str,
    news_id: str,
    status: str,
    findings: list[dict[str, str]],
    timeout_ms: int,
) -> dict[str, Any]:
    before_count = len(findings)
    try:
        page.goto(f"{base_url}/news/{news_id}", wait_until="networkidle", timeout=timeout_ms)
        metrics = article_metrics(page)
        append_article_readability_findings(metrics, status, "article_view", findings)
        return {
            "mode": "direct",
            "news_id": news_id,
            "status": status,
            "passed": len(findings) == before_count,
            "summary_length": len(str(metrics.get("summary_text") or "")),
            "body_length": len(str(metrics.get("body_text") or "")),
            "has_unreadable_title": UNREADABLE_DETAIL_TITLE in str(metrics.get("visible_text") or ""),
        }
    except Exception as error:
        findings.append(build_finding("article_view", f"article {news_id} navigation failed: {error.__class__.__name__}"))
        return {"mode": "direct", "news_id": news_id, "status": status, "passed": False}


def check_article_click(
    page: Page,
    base_url: str,
    selector: str,
    status_by_id: dict[str, str],
    findings: list[dict[str, str]],
    timeout_ms: int,
) -> dict[str, Any]:
    before_count = len(findings)
    try:
        page.goto(base_url, wait_until="networkidle", timeout=timeout_ms)
        target = page.locator(selector).first
        href = target.get_attribute("href", timeout=timeout_ms) or ""
        news_id = href.rstrip("/").rsplit("/", 1)[-1]
        target.click(timeout=timeout_ms)
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        status = status_by_id.get(news_id)
        if not status:
            findings.append(build_finding("article_view", f"clicked article {news_id} missing from home payload"))
            return {"mode": "click", "selector": selector, "news_id": news_id, "passed": False}
        metrics = article_metrics(page)
        append_article_readability_findings(metrics, status, "article_view", findings)
        return {
            "mode": "click",
            "selector": selector,
            "news_id": news_id,
            "status": status,
            "passed": len(findings) == before_count,
            "summary_length": len(str(metrics.get("summary_text") or "")),
            "body_length": len(str(metrics.get("body_text") or "")),
            "has_unreadable_title": UNREADABLE_DETAIL_TITLE in str(metrics.get("visible_text") or ""),
        }
    except Exception as error:
        findings.append(build_finding("article_view", f"article click failed: {error.__class__.__name__}", selector))
        return {"mode": "click", "selector": selector, "passed": False}


def append_primary_item_findings(items: list[dict[str, Any]], findings: list[dict[str, str]]) -> None:
    if not items:
        findings.append(build_finding("home_news_feed", "home/top payload has no news items"))
        return
    for item in items:
        item_id = str(item.get("id") or "")
        if item.get("status") != "translated":
            findings.append(build_finding("home_news_feed", f"primary item {item_id} is not translated"))
        if not str(item.get("summary_zh") or "").strip():
            findings.append(build_finding("home_news_feed", f"primary item {item_id} summary missing"))
        if not is_public_original_url(str(item.get("original_url") or "")):
            findings.append(build_finding("article_view", f"primary item {item_id} original_url is not public"))


def find_detail_id_by_status(page: Page, base_url: str, status: str, timeout_ms: int) -> str | None:
    for candidate_id in range(1, 51):
        try:
            response = page.request.get(f"{base_url}/api/news/{candidate_id}", timeout=timeout_ms)
            if response.status != 200:
                continue
            payload = response.json()
        except Exception:
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and data.get("status") == status:
            return str(candidate_id)
    return None


def check_article(page: Page, base_url: str, findings: list[dict[str, str]], timeout_ms: int) -> list[dict[str, Any]]:
    home_data = read_home_payload(page, base_url, findings, timeout_ms)
    latest = home_data.get("latest_news") if isinstance(home_data, dict) else []
    ranked = home_data.get("top_ranked_news") if isinstance(home_data, dict) else []
    items = [item for item in [*(latest if isinstance(latest, list) else []), *(ranked if isinstance(ranked, list) else [])] if isinstance(item, dict)]
    append_primary_item_findings(items, findings)
    status_by_id = {str(item.get("id")): str(item.get("status")) for item in items}
    results = []
    results.append(check_article_click(page, base_url, "[data-news-card]", status_by_id, findings, timeout_ms))
    results.append(check_article_click(page, base_url, "[data-rank-item]", status_by_id, findings, timeout_ms))
    for status in ("translated", "ready", "translation_failed"):
        matching_item = next((item for item in items if item.get("status") == status), None)
        if status != "translated":
            detail_id = find_detail_id_by_status(page, base_url, status, timeout_ms)
            if detail_id:
                results.append(check_article_id(page, base_url, detail_id, status, findings, timeout_ms))
                continue
        if not matching_item:
            findings.append(build_finding("article_view", f"{status} detail sample missing"))
            continue
        results.append(check_article_id(page, base_url, str(matching_item.get("id")), status, findings, timeout_ms))
    return results


def check_sources(page: Page, base_url: str, findings: list[dict[str, str]], timeout_ms: int) -> None:
    try:
        page.goto(f"{base_url}/sources", wait_until="networkidle", timeout=timeout_ms)
        source_metrics = page.evaluate(
            """
            () => ({
              page_exists: Boolean(document.querySelector('.sources-page')),
              source_count: document.querySelectorAll('.source-list li').length,
              form_exists: Boolean(document.querySelector('form'))
            })
            """
        )
    except Exception as error:
        findings.append(build_finding("sources_page", f"sources navigation failed: {error.__class__.__name__}"))
        return
    if source_metrics.get("page_exists") is not True:
        findings.append(build_finding("sources_page", "sources page missing"))
    if int(source_metrics.get("source_count") or 0) <= 0:
        findings.append(build_finding("sources_page", "source rows missing"))
    if source_metrics.get("form_exists") is not True:
        findings.append(build_finding("sources_page", "source form missing"))


def run_smoke(url: str, report_dir: Path, timeout_ms: int) -> dict[str, Any]:
    report_path = report_dir / "acceptance" / "deployed_browser_smoke.json"
    screenshot_path = report_dir / "acceptance" / "deployed_browser_smoke.png"
    findings: list[dict[str, str]] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    response_statuses: dict[str, int] = {}
    browser_metrics: dict[str, Any] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("response", lambda response: response_statuses.update({response.url: response.status}))
        try:
            response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            browser_metrics["http_status"] = response.status if response else None
            api_response = page.request.get(f"{url}/api/home", timeout=timeout_ms)
            browser_metrics["api_home_status"] = api_response.status
            home_metrics = read_home_metrics(page)
            browser_metrics.update(home_metrics)
            append_home_findings(home_metrics, findings)
            browser_metrics["refresh_status"] = check_refresh(page, findings, timeout_ms)
            browser_metrics["article_readability"] = check_article(page, url, findings, timeout_ms)
            check_sources(page, url, findings, timeout_ms)
            page.screenshot(path=screenshot_path, full_page=True)
        except Exception as error:
            findings.append(build_finding("home_news_feed", f"browser smoke failed: {error.__class__.__name__}"))
        finally:
            browser.close()
    browser_metrics["console_error_count"] = len(console_errors)
    browser_metrics["page_error_count"] = len(page_errors)
    browser_metrics["screenshot_path"] = screenshot_path.as_posix()
    if console_errors:
        findings.append(build_finding("home_news_feed", "console errors present"))
    if page_errors:
        findings.append(build_finding("home_news_feed", "page errors present"))
    return {
        "schema_ref": "workflows.md#DeployedBrowserSmokeReport",
        "schema_version": "v1",
        "status": "failed" if findings else "passed",
        "local_url": url,
        "port": int(url.rsplit(":", 1)[-1].rstrip("/")),
        "checked_surfaces": REQUIRED_SURFACES,
        "browser": browser_metrics,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "response_statuses": response_statuses,
        "failed_findings": findings,
        "timestamp": FIXED_TIMESTAMP,
        "artifact_paths": [screenshot_path.as_posix(), report_path.as_posix()],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deployed browser smoke acceptance.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--timeout-ms", type=int, default=10000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report_dir = Path(args.report_dir)
    report_path = report_dir / "acceptance" / "deployed_browser_smoke.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = run_smoke(args.url.rstrip("/"), report_dir, args.timeout_ms)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
