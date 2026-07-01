"""FastAPI application entrypoint for the MVP RSS reader."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.core.config import get_live_runtime_config
from backend.app.db import connect, initialize_database, seed_default_sources
from backend.app.services.trigger import run_manual_refresh


FIXED_NOW = "2026-06-28T09:00:00Z"
TOP_RANKED_WINDOW_START = "2026-05-29T09:00:00Z"
ROOT_DIR = Path(__file__).resolve().parents[2]
RUNTIME_SHELL_INDEX_HTML = ROOT_DIR / "index.html"
FRONTEND_DIST_DIR = ROOT_DIR / "frontend" / "dist"
FRONTEND_DIST_INDEX_HTML = FRONTEND_DIST_DIR / "index.html"
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"
DEFAULT_DB_PATH = ROOT_DIR / "rss.sqlite3"
TRANSLATION_FIXTURE_PATH = ROOT_DIR / "fixtures" / "llm" / "translation.json"


class CreateSourceRequest(BaseModel):
    name: str
    rss_url: str


class UpdateSourceRequest(BaseModel):
    is_enabled: bool


def data_response(data: object, status_code: int = 200) -> JSONResponse:
    return JSONResponse({"data": data}, status_code=status_code)


def error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status_code,
    )


def api_not_found() -> JSONResponse:
    return error_response("NOT_FOUND", "Resource not found", 404)


def frontend_index_path() -> Path:
    if FRONTEND_DIST_INDEX_HTML.exists():
        return FRONTEND_DIST_INDEX_HTML
    return RUNTIME_SHELL_INDEX_HTML


def mount_frontend_static_assets(app: FastAPI) -> None:
    if FRONTEND_ASSETS_DIR.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=FRONTEND_ASSETS_DIR),
            name="frontend-assets",
        )


def is_public_http_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname in {"localhost"} or hostname.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback or address.is_link_local)


def hidden_api_rss_guids(translation_fixture_path: Path = TRANSLATION_FIXTURE_PATH) -> tuple[str, ...]:
    try:
        payload = json.loads(translation_fixture_path.read_text())
    except (OSError, json.JSONDecodeError):
        return tuple()

    translations = payload.get("translations") if isinstance(payload, dict) else None
    if not isinstance(translations, dict):
        return tuple()

    hidden = []
    for guid, record in translations.items():
        if isinstance(record, dict) and record.get("display_in_api") is False:
            hidden.append(str(guid))
    return tuple(hidden)


def public_source(source: dict[str, object]) -> dict[str, object]:
    return {
        "id": str(source["id"]),
        "name": source["name"],
        "rss_url": source["rss_url"],
        "is_enabled": bool(source["is_enabled"]),
        "fetch_frequency": source["fetch_frequency"],
        "created_at": source["created_at"],
    }


def display_status(row: dict[str, object]) -> str:
    if row["title_zh"] and row["summary_zh"] and row["content_zh"]:
        fallback_content = row["content_full"] or row["content_raw"]
        if (
            row["title_zh"] == row["original_title"]
            and row["summary_zh"] == row["content_raw"]
            and row["content_zh"] == fallback_content
        ):
            return "untranslated"
        return "translated"
    if bool(row["has_translate_failed"]):
        return "translation_failed"
    return "ready"


def list_item(row: dict[str, object]) -> dict[str, object]:
    status = display_status(row)
    item = {
        "id": str(row["id"]),
        "title": row["title_zh"] or row["original_title"],
        "original_title": row["original_title"],
        "source_name": row["source_name"],
        "original_url": row["original_url"],
        "published_at": row["published_at"],
        "score": row["score"],
        "status": status,
    }
    if status == "translated" and row["summary_zh"]:
        item["summary_zh"] = row["summary_zh"]
    return item


def detail_item(row: dict[str, object]) -> dict[str, object]:
    item = list_item(row)
    if display_status(row) == "translated":
        item["summary_zh"] = row["summary_zh"]
        item["content_zh"] = row["content_zh"]
    return item


def encode_home_cursor(row: dict[str, object]) -> str:
    payload = json.dumps(
        {"published_at": row["published_at"], "id": int(row["id"])},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_home_cursor(value: str) -> tuple[str, int] | None:
    try:
        padded = value + ("=" * (-len(value) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(raw)
        published_at = str(payload["published_at"])
        item_id = int(payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error):
        return None
    if not published_at or item_id < 1:
        return None
    return published_at, item_id


def displayable_news_query(
    extra_where: str = "",
    order_by: str = "news_item.published_at DESC",
    translated_only: bool = False,
    excluded_rss_guids: tuple[str, ...] = (),
) -> tuple[str, list[object]]:
    translated_clause = """
          AND news_item.title_zh IS NOT NULL
          AND news_item.summary_zh IS NOT NULL
          AND news_item.content_zh IS NOT NULL
    """ if translated_only else ""
    exclusion_clause = ""
    params: list[object] = []
    if excluded_rss_guids:
        placeholders = ", ".join("?" for _ in excluded_rss_guids)
        exclusion_clause = f"\n          AND news_item.rss_guid NOT IN ({placeholders})"
        params.extend(excluded_rss_guids)

    query = f"""
        SELECT
          news_item.id,
          news_item.original_title,
          news_item.original_url,
          news_item.published_at,
          news_item.score,
          news_item.title_zh,
          news_item.summary_zh,
          news_item.content_zh,
          news_item.content_raw,
          news_item.content_full,
          news_item.has_translate_failed,
          source.name AS source_name
        FROM news_item
        JOIN source ON source.id = news_item.source_id
        WHERE news_item.is_selected = 1
          AND (news_item.content_full IS NOT NULL OR news_item.content_raw IS NOT NULL)
          {translated_clause}
          {exclusion_clause}
          {extra_where}
        ORDER BY {order_by}
    """
    return query, params


def visible_sources(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return conn.execute(
        """
        SELECT id, name, rss_url, is_enabled, fetch_frequency, created_at, deleted_at
        FROM source
        WHERE deleted_at IS NULL
        ORDER BY created_at ASC
        """
    ).fetchall()


def active_source_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM source
        WHERE deleted_at IS NULL AND is_enabled = 1
        """
    ).fetchone()
    return int(row["count"])


def find_source(conn: sqlite3.Connection, id: str) -> dict[str, object] | None:
    return conn.execute(
        """
        SELECT id, name, rss_url, is_enabled, fetch_frequency, created_at, deleted_at
        FROM source
        WHERE id = ? AND deleted_at IS NULL
        """,
        (id,),
    ).fetchone()


def validate_source_request(
    conn: sqlite3.Connection,
    name: str,
    rss_url: str,
) -> JSONResponse | None:
    if not name.strip():
        return error_response("VALIDATION_ERROR", "name is required", 400)
    if not is_public_http_url(rss_url):
        return error_response("VALIDATION_ERROR", "rss_url must be a public URL", 400)
    duplicate = conn.execute(
        "SELECT id FROM source WHERE rss_url = ?",
        (rss_url,),
    ).fetchone()
    if duplicate is not None:
        return error_response("DUPLICATE_SOURCE", "rss_url already exists", 409)
    return None


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="rss-aggregator")
    mount_frontend_static_assets(app)
    conn = connect(db_path or str(DEFAULT_DB_PATH))
    initialize_database(conn)
    seed_default_sources(conn)
    app.state.db = conn
    app.state.last_successful_refresh_at = None
    app.state.refresh_running = False
    app.state.live_runtime = get_live_runtime_config()
    app.state.hidden_api_rss_guids = hidden_api_rss_guids()

    def db() -> sqlite3.Connection:
        return app.state.db

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return error_response("VALIDATION_ERROR", "Invalid request", 400)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if request.url.path.startswith("/api/"):
            if exc.status_code == 404:
                return api_not_found()
            return error_response("HTTP_ERROR", str(exc.detail), exc.status_code)
        return error_response("NOT_FOUND", "Resource not found", exc.status_code)

    @app.get("/api/home")
    def get_home(limit: int = 50, cursor: str | None = None) -> JSONResponse:
        bounded_limit = min(max(limit, 1), 100)
        hidden_guids = app.state.hidden_api_rss_guids
        cursor_where = ""
        cursor_params: list[object] = []
        if cursor:
            decoded_cursor = decode_home_cursor(cursor)
            if decoded_cursor is None:
                return error_response("VALIDATION_ERROR", "Invalid cursor", 400)
            published_at, item_id = decoded_cursor
            cursor_where = """
          AND (
            news_item.published_at < ?
            OR (news_item.published_at = ? AND news_item.id < ?)
          )
            """
            cursor_params = [published_at, published_at, item_id]

        latest_query, latest_params = displayable_news_query(
            extra_where=cursor_where,
            order_by="news_item.published_at DESC, news_item.id DESC",
            translated_only=True,
            excluded_rss_guids=hidden_guids,
        )
        latest_rows = db().execute(
            f"{latest_query} LIMIT ?",
            tuple(latest_params + cursor_params + [bounded_limit + 1]),
        ).fetchall()
        latest_page = latest_rows[:bounded_limit]

        # Keep top list bounded as before; top ranking is already
        # filtered by translation completeness + 30-day window + hidden samples.
        top_query, top_params = displayable_news_query(
            extra_where="AND news_item.published_at >= ?",
            order_by="news_item.score DESC, news_item.published_at DESC",
            translated_only=True,
            excluded_rss_guids=hidden_guids,
        )
        top_rows = db().execute(
            f"{top_query} LIMIT 10",
            tuple(top_params + [TOP_RANKED_WINDOW_START]),
        ).fetchall()
        home_data = {
            "latest_news": [list_item(row) for row in latest_page],
            "top_ranked_news": [list_item(row) for row in top_rows],
        }
        if len(latest_rows) > bounded_limit and latest_page:
            home_data["next_cursor"] = encode_home_cursor(latest_page[-1])
        return data_response(home_data)

    @app.get("/api/news/{id}")
    def get_news(id: str) -> JSONResponse:
        hidden_guids = app.state.hidden_api_rss_guids
        query, query_params = displayable_news_query(
            extra_where="AND news_item.id = ?",
            order_by="news_item.published_at DESC",
            excluded_rss_guids=hidden_guids,
        )
        row = db().execute(
            query,
            tuple(query_params + [id]),
        ).fetchone()
        if row is not None:
            return data_response(detail_item(row))
        return error_response("NEWS_NOT_FOUND", "新闻不存在或不可展示", 404)

    @app.post("/api/refresh")
    def refresh() -> JSONResponse:
        if app.state.refresh_running:
            return data_response({"refreshed_at": app.state.last_successful_refresh_at})
        app.state.refresh_running = True
        try:
            runtime = app.state.live_runtime
            now = (
                datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                if runtime.mode == "live"
                else FIXED_NOW
            )
            result = run_manual_refresh(
                db(),
                now=now,
                use_live_data=runtime.mode == "live",
                allow_live_network=runtime.allow_live_network,
                allow_live_llm=runtime.allow_live_llm,
                allow_live_article_fetch=runtime.allow_live_article_fetch,
                request_timeout_seconds=runtime.request_timeout_seconds,
                request_retry_count=runtime.request_retry_count,
                request_retry_backoff_seconds=runtime.request_retry_backoff_seconds,
                live_rss_concurrency=runtime.live_rss_concurrency,
                live_llm_base_url=runtime.llm_base_url,
                live_llm_api_key=runtime.llm_api_key,
                live_llm_model=runtime.llm_model,
                live_llm_timeout_seconds=runtime.llm_request_timeout_seconds,
                live_llm_retry_count=runtime.live_llm_retry_count,
                live_llm_max_items=runtime.live_llm_max_items,
                live_llm_concurrency=runtime.live_llm_concurrency,
                live_llm_max_score_items=runtime.live_llm_max_score_items,
                live_llm_score_concurrency=runtime.live_llm_score_concurrency,
            )
            if result["started"]:
                app.state.last_successful_refresh_at = result["summary"]["finished_at"]
        finally:
            app.state.refresh_running = False
        return data_response({"refreshed_at": app.state.last_successful_refresh_at})

    @app.get("/api/sources")
    def get_sources() -> JSONResponse:
        return data_response([public_source(source) for source in visible_sources(db())])

    @app.post("/api/sources")
    def create_source(payload: CreateSourceRequest) -> JSONResponse:
        validation_error = validate_source_request(db(), payload.name, payload.rss_url)
        if validation_error is not None:
            return validation_error
        try:
            cursor = db().execute(
                """
                INSERT INTO source (
                  name, rss_url, is_enabled, deleted_at, fetch_frequency, created_at
                )
                VALUES (?, ?, 1, NULL, 'twice_daily', ?)
                """,
                (payload.name.strip(), payload.rss_url, FIXED_NOW),
            )
            db().commit()
        except sqlite3.IntegrityError:
            return error_response("DUPLICATE_SOURCE", "rss_url already exists", 409)
        source = find_source(db(), str(cursor.lastrowid))
        return data_response(public_source(source), status_code=201)

    @app.patch("/api/sources/{id}")
    def update_source(id: str, payload: UpdateSourceRequest) -> JSONResponse:
        source = find_source(db(), id)
        if source is None:
            return api_not_found()
        if (
            payload.is_enabled is False
            and bool(source["is_enabled"]) is True
            and active_source_count(db()) == 1
        ):
            return error_response(
                "LAST_SOURCE_CONFLICT",
                "At least one source must remain enabled",
                409,
            )
        db().execute(
            "UPDATE source SET is_enabled = ? WHERE id = ?",
            (1 if payload.is_enabled else 0, id),
        )
        db().commit()
        return data_response(public_source(find_source(db(), id)))

    @app.delete("/api/sources/{id}")
    def delete_source(id: str) -> Response:
        source = find_source(db(), id)
        if source is None:
            return api_not_found()
        if bool(source["is_enabled"]) is True and active_source_count(db()) == 1:
            return error_response(
                "LAST_SOURCE_CONFLICT",
                "At least one source must remain enabled",
                409,
            )
        db().execute(
            "UPDATE source SET is_enabled = 0, deleted_at = ? WHERE id = ?",
            (FIXED_NOW, id),
        )
        db().commit()
        return Response(status_code=204)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(frontend_index_path())

    @app.get("/{path:path}", include_in_schema=False)
    def spa_or_api_404(path: str) -> Response:
        if path.startswith("api/"):
            return api_not_found()
        return FileResponse(frontend_index_path())

    return app


def create_runtime_shell_app() -> FastAPI:
    runtime_app = FastAPI(title="rss-aggregator")
    mount_frontend_static_assets(runtime_app)

    @runtime_app.get("/", include_in_schema=False)
    def runtime_index() -> FileResponse:
        return FileResponse(frontend_index_path())

    @runtime_app.get("/{path:path}", include_in_schema=False)
    def runtime_spa(path: str) -> FileResponse:
        return FileResponse(frontend_index_path())

    return runtime_app


app = create_runtime_shell_app()
