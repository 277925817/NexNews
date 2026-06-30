"""FastAPI application entrypoint for the MVP RSS reader."""

from __future__ import annotations

import ipaddress
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.db import connect, initialize_database, seed_default_sources
from backend.app.services.pipeline import run_fixture_refresh


FIXED_NOW = "2026-06-28T09:00:00Z"
ROOT_DIR = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT_DIR / "index.html"
DEFAULT_DB_PATH = ROOT_DIR / "rss.sqlite3"


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
        return "translated"
    if bool(row["has_translate_failed"]):
        return "translation_failed"
    return "ready"


def list_item(row: dict[str, object]) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "title": row["title_zh"] or row["original_title"],
        "original_title": row["original_title"],
        "source_name": row["source_name"],
        "original_url": row["original_url"],
        "published_at": row["published_at"],
        "score": row["score"],
        "status": display_status(row),
    }


def detail_item(row: dict[str, object]) -> dict[str, object]:
    item = list_item(row)
    if display_status(row) == "translated":
        item["summary_zh"] = row["summary_zh"]
        item["content_zh"] = row["content_zh"]
    return item


def displayable_news_query(
    extra_where: str = "",
    order_by: str = "news_item.published_at DESC",
) -> str:
    return f"""
        SELECT
          news_item.id,
          news_item.original_title,
          news_item.original_url,
          news_item.published_at,
          news_item.score,
          news_item.title_zh,
          news_item.summary_zh,
          news_item.content_zh,
          news_item.has_translate_failed,
          source.name AS source_name
        FROM news_item
        JOIN source ON source.id = news_item.source_id
        WHERE news_item.is_selected = 1
          AND (news_item.content_full IS NOT NULL OR news_item.content_raw IS NOT NULL)
          {extra_where}
        ORDER BY {order_by}
    """


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
    conn = connect(db_path or str(DEFAULT_DB_PATH))
    initialize_database(conn)
    seed_default_sources(conn)
    app.state.db = conn
    app.state.last_successful_refresh_at = FIXED_NOW
    app.state.refresh_running = False

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
        latest_rows = db().execute(
            displayable_news_query(order_by="news_item.published_at DESC") + " LIMIT ?",
            (bounded_limit,),
        ).fetchall()
        top_rows = db().execute(
            displayable_news_query(
                order_by="news_item.score DESC, news_item.published_at DESC"
            )
            + " LIMIT 10"
        ).fetchall()
        return data_response(
            {
                "latest_news": [list_item(row) for row in latest_rows],
                "top_ranked_news": [list_item(row) for row in top_rows],
            }
        )

    @app.get("/api/news/{id}")
    def get_news(id: str) -> JSONResponse:
        row = db().execute(
            displayable_news_query(
                extra_where="AND news_item.id = ?",
                order_by="news_item.published_at DESC",
            ),
            (id,),
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
            run_fixture_refresh(db())
        finally:
            app.state.refresh_running = False
        app.state.last_successful_refresh_at = FIXED_NOW
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
        return FileResponse(INDEX_HTML)

    @app.get("/{path:path}", include_in_schema=False)
    def spa_or_api_404(path: str) -> Response:
        if path.startswith("api/"):
            return api_not_found()
        return FileResponse(INDEX_HTML)

    return app


app = create_app()
