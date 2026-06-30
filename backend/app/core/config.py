"""Local deterministic runtime configuration for tests and harness runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalRuntimeConfig:
    database_path: Path
    fixture_set: str
    mock_set: str
    clock_source: str
    rss_fixture_path: Path
    article_fixture_path: Path
    scoring_mock_path: Path
    translation_mock_path: Path
    source_fixture_path: Path
    clock_fixture_path: Path
    allow_live_network: bool = False
    allow_live_llm: bool = False


def get_local_runtime_config(root: Path | None = None) -> LocalRuntimeConfig:
    repo_root = root or Path(__file__).resolve().parents[3]
    return LocalRuntimeConfig(
        database_path=repo_root / "rss.sqlite3",
        fixture_set="mvp_acceptance_fixture@v1",
        mock_set="mvp_mock@v1",
        clock_source="fixed_clock_fixture@v1",
        rss_fixture_path=repo_root / "fixtures/rss/feeds.json",
        article_fixture_path=repo_root / "fixtures/articles/article_map.json",
        scoring_mock_path=repo_root / "fixtures/llm/scoring.json",
        translation_mock_path=repo_root / "fixtures/llm/translation.json",
        source_fixture_path=repo_root / "fixtures/sources/default_sources.json",
        clock_fixture_path=repo_root / "fixtures/clock/fixed_times.json",
    )
