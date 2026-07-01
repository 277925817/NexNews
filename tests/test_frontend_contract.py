from pathlib import Path


def css_rule_body(css: str, selector: str) -> str:
    marker = f"{selector} {{"
    start = css.find(marker)
    assert start != -1, selector
    body_start = start + len(marker)
    body_end = css.find("}", body_start)
    assert body_end != -1, selector
    return css[body_start:body_end]


def test_frontend_vite_entrypoint_exists():
    text = Path("frontend/index.html").read_text()

    assert 'id="root"' in text
    assert 'type="module"' in text
    assert "/src/main.tsx" in text


def test_frontend_vite_uses_react_plugin():
    config_path = Path("frontend/vite.config.ts")

    assert config_path.exists()

    text = config_path.read_text()
    assert "@vitejs/plugin-react" in text
    assert "react()" in text


def test_root_static_page_is_only_a_runtime_shell():
    text = Path("index.html").read_text()

    assert "/frontend/src/main.tsx" in text
    assert "<script>" not in text

    for legacy_endpoint in ("/rss", "/api/sync", "/api/feeds", "/api/items"):
        assert legacy_endpoint not in text


def test_frontend_uses_documented_light_gray_theme():
    ui_spec = Path("docs/03_ui_spec.md").read_text()
    app_css = Path("frontend/src/styles/app.css").read_text().lower()
    article_css = Path("frontend/src/styles/article.css").read_text().lower()
    sources_css = Path("frontend/src/styles/sources.css").read_text().lower()
    combined_css = "\n".join([app_css, article_css, sources_css])

    for token in ("#F3F4F6", "#FFFFFF", "#F8FAFC", "#D8DEE6"):
        assert token in ui_spec

    assert "background: #f3f4f6" in app_css
    assert ".app-shell" in app_css
    assert "color-scheme: dark" not in combined_css

    for old_token in ("#0b0f14", "#111820", "#151e28"):
        assert old_token not in combined_css

    for surface_token in ("background: #ffffff", "background: #f8fafc"):
        assert surface_token in combined_css

    assert "#d8dee6" in app_css
    assert "#d8dee6" in article_css
    assert "#d8dee6" in sources_css


def test_top_30_days_uses_one_overall_card():
    ui_spec = Path("docs/03_ui_spec.md").read_text()
    test_spec = Path("docs/07_test_spec.md").read_text()
    acceptance = Path("docs/08_acceptance.md").read_text()
    app_css = Path("frontend/src/styles/app.css").read_text().lower()

    assert "Top 30 Days / HighScoreList must render as one overall card" in ui_spec
    assert "整体卡片" in test_spec
    assert "整体卡片" in acceptance

    container_body = css_rule_body(app_css, ".high-score-list")
    items_body = css_rule_body(app_css, ".high-score-list__items")
    row_link_body = css_rule_body(app_css, ".high-score-list__item a")

    assert "background: #ffffff" in container_body
    assert "border: 1px solid #d8dee6" in container_body
    assert "border-radius: 8px" in container_body
    assert "padding: 16px" in container_body
    assert "gap: 0" in items_body
    assert ".high-score-list__item + .high-score-list__item" in app_css
    assert "border-top: 1px solid #d8dee6" in app_css
    assert "border: 1px solid" not in row_link_body
    assert "background: #ffffff" not in row_link_body


def test_article_view_non_translated_states_explain_unreadable_content():
    ui_spec = Path("docs/03_ui_spec.md").read_text()
    article_source = Path("frontend/src/pages/ArticleView.tsx").read_text()
    article_css = Path("frontend/src/styles/article.css").read_text()

    assert "摘要和正文暂不可用" in ui_spec
    assert "摘要和正文暂不可用" in article_source
    assert "翻译完成后将自动显示中文摘要和正文。" in article_source
    assert "翻译失败，当前无法显示中文摘要和正文。" in article_source
    assert "article-view__state-title" in article_source
    assert "article-view__state-copy" in article_source
    assert ".article-view__state-title" in article_css
    assert ".article-view__state-copy" in article_css


def test_non_translated_news_links_expose_unreadable_state():
    news_card_source = Path("frontend/src/components/NewsCard.tsx").read_text()
    high_score_source = Path("frontend/src/components/HighScoreList.tsx").read_text()
    combined_source = "\n".join([news_card_source, high_score_source])

    assert "摘要和正文暂不可用" in news_card_source
    assert "摘要和正文暂不可用" in high_score_source
    assert "aria-label={linkLabel}" in news_card_source
    assert "aria-label={linkLabel}" in high_score_source
    assert "翻译中，摘要和正文暂不可用" in combined_source
    assert "翻译失败，摘要和正文暂不可用" in combined_source
