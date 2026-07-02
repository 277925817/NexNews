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
    allow_live_article_fetch: bool
    request_timeout_seconds: float
    request_retry_count: int
    request_retry_backoff_seconds: float
    live_rss_concurrency: int
    llm_api_key: str | None
    llm_base_url: str | None
    llm_model: str | None
    llm_request_timeout_seconds: float
    live_llm_retry_count: int
    live_llm_max_items: int
    live_llm_concurrency: int
    live_llm_max_score_items: int
    live_llm_score_concurrency: int
    backlog_worker_enabled: bool
    backlog_worker_interval_seconds: int
    backlog_worker_max_score_items: int


def _read_env_file(root: Path) -> dict[str, str]:
    env_path = root / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _config_value(name: str, env_file_values: dict[str, str]) -> str | None:
    value = os.getenv(name)
    if value is not None:
        return value
    return env_file_values.get(name)


def _config_value_any(names: tuple[str, ...], env_file_values: dict[str, str]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    for name in names:
        value = env_file_values.get(name)
        if value is not None:
            return value
    return None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return _value_bool(value, default)


def _value_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return _value_int(value, default)


def _value_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return _value_float(value, default)


def _value_float(value: str | None, default: float) -> float:
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
        mock_set="mvp_mock@v2_ai_value_filter",
        clock_source="fixed_clock_fixture@v1",
        rss_fixture_path=repo_root / "fixtures/rss/feeds.json",
        article_fixture_path=repo_root / "fixtures/articles/article_map.json",
        scoring_mock_path=repo_root / "fixtures/llm/scoring.json",
        translation_mock_path=repo_root / "fixtures/llm/translation.json",
        source_fixture_path=repo_root / "fixtures/sources/default_sources.json",
        clock_fixture_path=repo_root / "fixtures/clock/fixed_times.json",
    )


def get_live_runtime_config(root: Path | None = None) -> LiveRuntimeConfig:
    repo_root = root or Path(__file__).resolve().parents[3]
    env_file_values = _read_env_file(repo_root)
    mode = (_config_value("RSS_RUNTIME_MODE", env_file_values) or "fixture").strip().lower()
    llm_api_key = _config_value("LLM_API_KEY", env_file_values)
    llm_base_url = _config_value_any(("LLM_BASE_URL", "LLM_URL"), env_file_values)
    llm_model = _config_value_any(("LLM_MODEL", "LLM_MODEL_NAME"), env_file_values)
    llm_configured = bool(llm_api_key and llm_base_url and llm_model)
    allow_live_llm_value = _config_value("RSS_ALLOW_LIVE_LLM", env_file_values)
    allow_live_network = mode == "live" and _value_bool(
        _config_value("RSS_ALLOW_LIVE_NETWORK", env_file_values),
        True,
    )
    allow_live_llm = (
        mode == "live"
        and llm_configured
        and _value_bool(allow_live_llm_value, llm_configured)
    )
    allow_live_article_fetch = mode == "live" and _value_bool(
        _config_value("RSS_FETCH_LIVE_ARTICLES", env_file_values),
        True,
    )
    timeout = _value_float(_config_value("RSS_HTTP_TIMEOUT_SECONDS", env_file_values), 2)
    retries = _value_int(_config_value("RSS_HTTP_RETRY_COUNT", env_file_values), 0)
    backoff = _value_float(_config_value("RSS_HTTP_RETRY_BACKOFF_SECONDS", env_file_values), 0.2)
    live_rss_concurrency = _value_int(_config_value("RSS_LIVE_RSS_CONCURRENCY", env_file_values), 33)
    llm_timeout = _value_float(_config_value("RSS_LIVE_LLM_TIMEOUT_SECONDS", env_file_values), 30)
    live_llm_retry_count = max(0, _value_int(_config_value("RSS_LIVE_LLM_RETRY_COUNT", env_file_values), 2))
    live_llm_max_items = _value_int(_config_value("RSS_LIVE_LLM_MAX_ITEMS", env_file_values), 20)
    live_llm_concurrency = max(1, _value_int(_config_value("RSS_LIVE_LLM_CONCURRENCY", env_file_values), 2))
    live_llm_max_score_items = _value_int(
        _config_value("RSS_LIVE_LLM_MAX_SCORE_ITEMS", env_file_values),
        20,
    )
    live_llm_score_concurrency = max(
        1,
        _value_int(_config_value("RSS_LIVE_LLM_SCORE_CONCURRENCY", env_file_values), 2),
    )
    backlog_worker_enabled = (
        mode == "live"
        and allow_live_llm
        and _value_bool(_config_value("RSS_BACKLOG_WORKER_ENABLED", env_file_values), True)
    )
    backlog_worker_interval_seconds = max(
        1,
        _value_int(_config_value("RSS_BACKLOG_WORKER_INTERVAL_SECONDS", env_file_values), 300),
    )
    backlog_worker_max_score_items = max(
        1,
        _value_int(_config_value("RSS_BACKLOG_WORKER_MAX_SCORE_ITEMS", env_file_values), 10),
    )
    return LiveRuntimeConfig(
        mode=mode,
        allow_live_network=allow_live_network,
        allow_live_llm=allow_live_llm,
        allow_live_article_fetch=allow_live_article_fetch,
        request_timeout_seconds=timeout,
        request_retry_count=retries,
        request_retry_backoff_seconds=backoff,
        live_rss_concurrency=live_rss_concurrency,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_request_timeout_seconds=llm_timeout,
        live_llm_retry_count=live_llm_retry_count,
        live_llm_max_items=live_llm_max_items,
        live_llm_concurrency=live_llm_concurrency,
        live_llm_max_score_items=live_llm_max_score_items,
        live_llm_score_concurrency=live_llm_score_concurrency,
        backlog_worker_enabled=backlog_worker_enabled,
        backlog_worker_interval_seconds=backlog_worker_interval_seconds,
        backlog_worker_max_score_items=backlog_worker_max_score_items,
    )
