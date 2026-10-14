"""
Traceability APP (Converting + placeholder tabs) - FastAPI entrypoint.

Goal
----
Serve:
1) Global Parameters page
2) Converting report page
3) Placeholder pages TAB1/TAB2/TAB3
4) Converting APIs (run / cancel / data / export)
5) Parameter dropdown APIs (Plant and Production line)

Design notes
------------
- Keep logic simple and explicit.
- Query cancellation and run ownership are server-side.
- Frontend stores report caches in sessionStorage per browser tab.
- CSV export uses exactly the same filter logic as table data.
"""

import csv
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import uvicorn
from databricks import sql
from databricks.sdk.core import Config
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import settings
from params import (
    GlobalParams,
    MissingParameters,
    REPORTS,
    build_report_query,
)

app = FastAPI()

# -----------------------------------------------------------------------------
# Paths / static assets
# -----------------------------------------------------------------------------
base_dir = Path(__file__).parent
static_dir = base_dir / "static"
templates_dir = base_dir / "templates"

static_dir.mkdir(exist_ok=True)
templates_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# -----------------------------------------------------------------------------
# Databricks source used by parameter dropdowns
# -----------------------------------------------------------------------------
PLANT_LINE_VIEW = "traceability_poc.dim_converting_productionline"

# -----------------------------------------------------------------------------
# Runtime limits / session ownership
# -----------------------------------------------------------------------------
COOKIE_NAME = "traceability_run_owner"
RUN_TTL_SECONDS = 3600
MAX_RUNS = 100
MAX_CONVERTING_CSV_ROWS = 100000

# Limits concurrent Databricks queries in this app process.
QUERY_SLOTS = threading.BoundedSemaphore(4)

# In-memory run registry (single-process scope).
# runs[run_id] = {
#   owner: str,
#   created: monotonic_time,
#   cancelled: bool,
#   active: {key: cursor},
#   inflight: set(keys)
# }
run_lock = threading.RLock()
runs = {}


# -----------------------------------------------------------------------------
# Databricks helpers
# -----------------------------------------------------------------------------
def get_connection():
    """Open a Databricks SQL connection using app credentials."""
    cfg = Config()
    return sql.connect(
        server_hostname=cfg.host,
        http_path=settings.DATABRICKS_HTTP_PATH,
        credentials_provider=lambda: cfg.authenticate,
    )


def close_db(cursor, conn):
    """Best-effort close for cursor + connection."""
    try:
        if cursor is not None:
            cursor.close()
    finally:
        if conn is not None:
            conn.close()


def csv_safe(value):
    """
    Protect CSV consumers (Excel, etc.) from formula injection by prefixing
    suspicious leading characters.
    """
    if value is None:
        return ""
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


# -----------------------------------------------------------------------------
# Run ownership / lifecycle helpers
# -----------------------------------------------------------------------------
def clean_runs():
    """Remove old finished runs to keep in-memory state small."""
    now = time.monotonic()
    with run_lock:
        for key, state in list(runs.items()):
            idle = not state["active"] and not state["inflight"]
            expired = (now - state["created"]) > RUN_TTL_SECONDS
            if idle and expired:
                del runs[key]


def owner_for(request: Request) -> str:
    """Return per-browser owner token used to isolate runs."""
    owner = request.cookies.get(COOKIE_NAME)
    if not owner:
        raise HTTPException(403, "Open the app home page first.")
    return owner


def check_origin(request: Request):
    """
    Reject explicit cross-site browser requests.
    We avoid strict Origin-vs-Host comparison due to proxy behavior.
    """
    site = (request.headers.get("sec-fetch-site") or "").lower()
    if site == "cross-site":
        raise HTTPException(403, "Cross-site requests are not allowed.")


def get_run(run_id: str, owner: str):
    """Resolve run and validate ownership."""
    with run_lock:
        clean_runs()
        state = runs.get(run_id)
        if state is None or state["owner"] != owner:
            raise HTTPException(
                404,
                "Run not found or expired. Return to Global Parameters and click Run Query again.",
            )
        return state


# -----------------------------------------------------------------------------
# Page serving
# -----------------------------------------------------------------------------
def serve_page(name: str, request: Request):
    """
    Serve HTML template and initialize owner cookie for key pages.
    """
    page = templates_dir / name
    if not page.is_file():
        return HTMLResponse(f"{name} not found", status_code=404)

    response = FileResponse(page)

    # Initialize owner cookie when opening pages that start/consume runs.
    if name in ("global_params.html", "converting.html") and not request.cookies.get(COOKIE_NAME):
        response.set_cookie(
            COOKIE_NAME,
            secrets.token_urlsafe(32),
            httponly=True,
            samesite="lax",
            secure=True,  # Databricks App runs on HTTPS
            max_age=RUN_TTL_SECONDS,
        )
    return response


@app.get("/")
def home(request: Request):
    return serve_page("global_params.html", request)


@app.get("/converting")
def converting_page(request: Request):
    return serve_page("converting.html", request)


@app.get("/tab1")
def tab1_page(request: Request):
    return serve_page("tab1.html", request)


@app.get("/tab2")
def tab2_page(request: Request):
    return serve_page("tab2.html", request)


@app.get("/tab3")
def tab3_page(request: Request):
    return serve_page("tab3.html", request)


# -----------------------------------------------------------------------------
# Global parameter dropdown APIs
# -----------------------------------------------------------------------------
def distinct_values(column: str, plant_id: Optional[str] = None):
    """
    Read distinct values for a known-safe column from PLANT_LINE_VIEW.
    """
    where = f"WHERE {column} IS NOT NULL"
    binds = {}
    if plant_id:
        where += " AND PlantId = :plant"
        binds["plant"] = plant_id

    conn = cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT DISTINCT {column} FROM {PLANT_LINE_VIEW} {where} ORDER BY {column} LIMIT 500",
            binds or None,
        )
        return sorted(
            {str(row[0]) for row in cursor.fetchall() if row[0] is not None},
            key=str.casefold,
        )
    finally:
        close_db(cursor, conn)


@app.get("/api/params/plants")
def params_plants():
    try:
        return {"values": distinct_values("PlantId")}
    except Exception as exc:
        return {"values": [], "error": str(exc)}


@app.get("/api/params/production-lines")
def params_production_lines(plant_id: Optional[str] = Query(None)):
    try:
        return {"values": distinct_values("ConvertingProductionLine", plant_id)}
    except Exception as exc:
        return {"values": [], "error": str(exc)}


@app.get("/api/params/suggest")
def params_suggest(field: str = Query(...), q: str = Query(...)):
    """
    Placeholder endpoint for future type-ahead suggestions.
    Kept for frontend compatibility; currently unused.
    """
    return {"values": []}


# -----------------------------------------------------------------------------
# Converting API models
# -----------------------------------------------------------------------------
class ReportRequest(BaseModel):
    run_id: str
    params: GlobalParams = Field(default_factory=GlobalParams)
    limit: int = Field(10, ge=1, le=5000)
    offset: int = Field(0, ge=0)


# -----------------------------------------------------------------------------
# Converting run lifecycle APIs
# -----------------------------------------------------------------------------
@app.post("/api/converting/runs")
def start_run(request: Request):
    """
    Start a new logical run:
    - creates run_id
    - frontend stores params + run_id and report caches
    """
    check_origin(request)
    owner = owner_for(request)

    with run_lock:
        clean_runs()
        if len(runs) >= MAX_RUNS:
            raise HTTPException(429, "Too many runs. Try again shortly.")

        run_id = secrets.token_urlsafe(24)
        runs[run_id] = {
            "owner": owner,
            "created": time.monotonic(),
            "cancelled": False,
            "active": {},
            "inflight": set(),
        }

    return {"run_id": run_id}


@app.post("/api/converting/runs/{run_id}/cancel")
def stop_run(run_id: str, request: Request):
    """
    Request cancellation for every active query in this run.
    """
    check_origin(request)
    owner = owner_for(request)
    state = get_run(run_id, owner)

    with run_lock:
        state["cancelled"] = True
        active_cursors = list(state["active"].values())

    errors = []
    for cursor in active_cursors:
        try:
            cursor.cancel()
        except Exception as exc:
            errors.append(str(exc))

    return {
        "status": "cancellation_requested",
        "active_queries": len(active_cursors),
        "errors": errors,
    }


# -----------------------------------------------------------------------------
# Converting data API
# -----------------------------------------------------------------------------
@app.post("/api/converting/{report_id}/data")
def converting_data(report_id: str, req: ReportRequest, request: Request):
    """
    Fetch paginated data for one report table.
    SQL and validation are built by params.py.
    """
    check_origin(request)
    owner = owner_for(request)
    state = get_run(req.run_id, owner)

    if report_id not in REPORTS:
        raise HTTPException(404, "Unknown report.")

    try:
        query, binds = build_report_query(
            report_id=report_id,
            p=req.params,
            limit=req.limit,
            offset=req.offset,
            export=False,
        )
    except (MissingParameters, ValueError) as exc:
        return {
            "success": False,
            "error": str(exc),
            "missing": isinstance(exc, MissingParameters),
        }

    conn = cursor = None
    op_key = f"data:{report_id}"
    registered = False

    try:
        with run_lock:
            if state["cancelled"]:
                return {"success": False, "cancelled": True, "error": "Run stopped."}
            if op_key in state["inflight"]:
                return {"success": False, "error": "This report is already loading."}
            state["inflight"].add(op_key)
            registered = True

        with QUERY_SLOTS:
            with run_lock:
                if state["cancelled"]:
                    return {"success": False, "cancelled": True, "error": "Run stopped."}

            conn = get_connection()
            cursor = conn.cursor()

            with run_lock:
                if state["cancelled"]:
                    return {"success": False, "cancelled": True, "error": "Run stopped."}
                state["active"][op_key] = cursor

            cursor.execute(query, binds or None)
            rows = cursor.fetchmany(req.limit + 1)
            columns = [item[0] for item in cursor.description]

            with run_lock:
                if state["cancelled"]:
                    return {"success": False, "cancelled": True, "error": "Run stopped."}

            return {
                "success": True,
                "columns": columns,
                "rows": [list(row) for row in rows[:req.limit]],
                "has_more": len(rows) > req.limit,
                "offset": req.offset,
                "limit": req.limit,
            }

    except Exception as exc:
        with run_lock:
            stopped = state["cancelled"]
        return {
            "success": False,
            "cancelled": stopped,
            "error": "Run stopped." if stopped else str(exc),
        }

    finally:
        if registered:
            with run_lock:
                state["active"].pop(op_key, None)
                state["inflight"].discard(op_key)
        close_db(cursor, conn)


# -----------------------------------------------------------------------------
# Converting CSV export API
# -----------------------------------------------------------------------------
# Keep BOTH routes for compatibility with existing frontend variants.
@app.post("/api/converting/export/{report_id}")
@app.post("/api/converting/{report_id}/export")
def converting_export(report_id: str, req: ReportRequest, request: Request):
    """
    Export one report as CSV using same filters as table API.
    Safety rules:
    - hard row cap: MAX_CONVERTING_CSV_ROWS
    - stop/cancel aware
    """
    check_origin(request)
    owner = owner_for(request)
    state = get_run(req.run_id, owner)

    if report_id not in REPORTS:
        raise HTTPException(404, "Unknown report.")

    try:
        query, binds = build_report_query(
            report_id=report_id,
            p=req.params,
            limit=req.limit,
            offset=req.offset,
            export=True,
        )
    except (MissingParameters, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

    conn = cursor = output = None
    op_key = f"export:{report_id}"
    registered = False

    try:
        with run_lock:
            if state["cancelled"]:
                raise HTTPException(409, "This run has been stopped.")
            if op_key in state["inflight"]:
                raise HTTPException(409, "This report is already exporting.")
            state["inflight"].add(op_key)
            registered = True

        with QUERY_SLOTS:
            with run_lock:
                if state["cancelled"]:
                    raise HTTPException(409, "This run has been stopped.")

            conn = get_connection()
            cursor = conn.cursor()

            with run_lock:
                if state["cancelled"]:
                    raise HTTPException(409, "This run has been stopped.")
                state["active"][op_key] = cursor

            cursor.execute(query, binds or None)

            # Spill to disk if buffer grows >8MB.
            output = tempfile.SpooledTemporaryFile(
                mode="w+t",
                max_size=8 * 1024 * 1024,
                encoding="utf-8",
                newline="",
            )
            writer = csv.writer(output)
            writer.writerow([item[0] for item in cursor.description])

            total = 0
            while True:
                with run_lock:
                    if state["cancelled"]:
                        raise HTTPException(409, "Export was stopped.")

                batch = cursor.fetchmany(1000)
                if not batch:
                    break

                for row in batch:
                    total += 1
                    if total > MAX_CONVERTING_CSV_ROWS:
                        raise HTTPException(
                            413,
                            "More than 100,000 rows match. Narrow the filters before exporting.",
                        )
                    writer.writerow([csv_safe(v) for v in row])

            with run_lock:
                if state["cancelled"]:
                    raise HTTPException(409, "Export was stopped.")

        output.seek(0)
        prepared = output
        output = None  # response generator now owns the file handle

        def stream_file():
            try:
                while True:
                    chunk = prepared.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                prepared.close()

        return StreamingResponse(
            stream_file(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="converting_{report_id}.csv"',
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        with run_lock:
            stopped = state["cancelled"]
        raise HTTPException(
            409 if stopped else 500,
            "Export was stopped." if stopped else f"Export failed: {exc}",
        ) from exc
    finally:
        if registered:
            with run_lock:
                state["active"].pop(op_key, None)
                state["inflight"].discard(op_key)
        close_db(cursor, conn)
        if output is not None:
            output.close()


# -----------------------------------------------------------------------------
# Local development entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )