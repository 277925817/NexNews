from pathlib import Path


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
