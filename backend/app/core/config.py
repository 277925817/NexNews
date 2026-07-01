"""Local deterministic runtime configuration for tests and harness runs."""

from __future__ import annotations

from dataclasses import dataclass
import os
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


@dataclass(frozen=True)
class LiveRuntimeConfig:
    mode: str
    allow_live_network: bool
    allow_live_llm: bool
    request_timeout_seconds: float
    request_retry_count: int
    request_retry_backoff_seconds: float


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


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


def get_live_runtime_config() -> LiveRuntimeConfig:
    mode = (os.getenv("RSS_RUNTIME_MODE", "fixture") or "fixture").strip().lower()
    allow_live_network = mode == "live" and _env_bool("RSS_ALLOW_LIVE_NETWORK", True)
    allow_live_llm = mode == "live" and _env_bool("RSS_ALLOW_LIVE_LLM", False)
    timeout = _env_float("RSS_HTTP_TIMEOUT_SECONDS", 12)
    retries = _env_int("RSS_HTTP_RETRY_COUNT", 3)
    backoff = _env_float("RSS_HTTP_RETRY_BACKOFF_SECONDS", 0.5)
    return LiveRuntimeConfig(
        mode=mode,
        allow_live_network=allow_live_network,
        allow_live_llm=allow_live_llm,
        request_timeout_seconds=timeout,
        request_retry_count=retries,
        request_retry_backoff_seconds=backoff,
    )
