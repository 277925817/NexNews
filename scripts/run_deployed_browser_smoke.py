from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright


FIXED_TIMESTAMP = "2026-06-28T09:00:00Z"
DEFAULT_URL = "http://127.0.0.1:8010"
REQUIRED_SURFACES = [
    "home_news_feed",
    "high_score_list",
    "article_view",
    "sources_page",
    "refresh_action",
]


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


def read_home_metrics(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const root = document.querySelector('#root');
          const app = document.querySelector('.app-shell');
          return {
            root_child_count: root ? root.children.length : 0,
            body_text_length: document.body.innerText.length,
            app_shell_exists: Boolean(app),
            news_card_count: document.querySelectorAll('[data-news-card]').length,
            rank_item_count: document.querySelectorAll('[data-rank-item]').length
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


def check_article(page: Page, base_url: str, findings: list[dict[str, str]], timeout_ms: int) -> None:
    try:
        page.goto(f"{base_url}/news/3", wait_until="networkidle", timeout=timeout_ms)
        article_metrics = page.evaluate(
            """
            () => ({
              article_exists: Boolean(document.querySelector('.article-view')),
              content_exists: Boolean(document.querySelector('.article-view__content')),
              text_length: document.body.innerText.length
            })
            """
        )
    except Exception as error:
        findings.append(build_finding("article_view", f"article navigation failed: {error.__class__.__name__}"))
        return
    if article_metrics.get("article_exists") is not True:
        findings.append(build_finding("article_view", "article view missing"))
    if article_metrics.get("content_exists") is not True:
        findings.append(build_finding("article_view", "article content missing"))
    if int(article_metrics.get("text_length") or 0) <= 0:
        findings.append(build_finding("article_view", "article text missing"))


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
            check_article(page, url, findings, timeout_ms)
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
