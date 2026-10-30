"""
Traceability APP - Sprint 3 backend.

- Configuration-driven report queries (master_config_report_columns).
- Configuration-driven global parameters (master_config_global_param).
- Run-scoped configuration and parameter snapshots.
- Shared Converting / Papermaking execution.
- Cancellation, bounded database concurrency and a per-query timeout.
- XLSX export with a maximum row count.
- Config tables are pre-loaded at startup so the first page load is faster.

Run registry and configuration caches are process-local.
Run this deployment with ONE Uvicorn worker.
"""

import hashlib
import json
import logging
import math
import os
import secrets
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

import uvicorn
import xlsxwriter
from databricks import sql
from databricks.sdk.core import Config
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from config import settings
from params import (
    APP_VERSION,
    DIMENSIONS,
    MAX_EXPORT_ROWS,
    ConfigurationError,
    FlowDirection,
    GlobalParams,
    MissingParameters,
    ParameterDefinitions,
    ReportCatalog,
    SyncMode,
    build_parameter_definitions,
    build_query,
    identifier,
    qualified_table,
)

logger = logging.getLogger("traceability")

app = FastAPI(title="Traceability APP - DEV", version=APP_VERSION)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Optional catalog avoids dependence on the warehouse's default catalog.
# The schema remains traceability_poc in all cases.
DATABRICKS_CATALOG = (
    os.getenv("TRACEABILITY_CATALOG")
    or getattr(settings, "DATABRICKS_CATALOG", None)
    or None
)

CONFIG_TABLE = qualified_table("master_config_report_columns", DATABRICKS_CATALOG)
PARAM_TABLE = qualified_table("master_config_global_param", DATABRICKS_CATALOG)

COOKIE_NAME = "traceability_run_owner"
RUN_TTL_SECONDS = 3600
MAX_RUNS = 100
QUERY_QUEUE_TIMEOUT_SECONDS = 120

# How long the config tables stay cached. Edits to the config tables show up after this time.
# (60 = faster edits, 300 = fewer slow first loads)
CONFIG_CACHE_SECONDS = 300

# Maximum time a REPORT query may run once it starts (the Databricks query is cancelled after this).
QUERY_TIMEOUT_SECONDS = 20
EXPORT_TIMEOUT_SECONDS = 20     # raise this if large exports need longer

QUERY_SLOTS = threading.BoundedSemaphore(4)
RUN_LOCK = threading.RLock()
RUNS: Dict[str, Dict[str, Any]] = {}

CONFIG_LOCK = threading.Lock()
CONFIG_CACHE: Dict[str, Any] = {"catalog": None, "loaded_at": 0.0}

PARAM_LOCK = threading.Lock()
PARAM_CACHE: Dict[str, Any] = {"defs": None, "loaded_at": 0.0}


class RunStopped(Exception):
    pass


class QueryTimeout(Exception):
    """A report query ran longer than its time limit and was cancelled."""


def timeout_message(seconds: int) -> str:
    return (
        f"Query timed out after {seconds} seconds. "
        "Narrow the filters (for example dates, line or order number) and try again."
    )


class ReportRequest(BaseModel):
    run_id: str = Field(..., min_length=1, max_length=200)
    params: GlobalParams = Field(default_factory=GlobalParams)
    limit: int = Field(10, ge=1, le=5000)
    offset: int = Field(0, ge=0)
    sync_mode: SyncMode = "unsync"
    flow_direction: FlowDirection = "up"

    # Accepted only to produce an explicit migration message.
    apply_order_link_filter: bool = False

    class Config:
        extra = "forbid"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    cfg = Config()
    parsed = urlsplit(
        cfg.host if "://" in cfg.host else f"https://{cfg.host}"
    )
    hostname = parsed.hostname
    if not hostname:
        raise RuntimeError("Databricks workspace hostname is missing.")

    return sql.connect(
        server_hostname=hostname,
        http_path=settings.DATABRICKS_HTTP_PATH,
        credentials_provider=lambda: cfg.authenticate,
    )


def close_db(cursor, connection):
    # Cleanup must not mask the original query or export error.
    if cursor is not None:
        try:
            cursor.close()
        except Exception:
            logger.warning("Could not close cursor.", exc_info=True)

    if connection is not None:
        try:
            connection.close()
        except Exception:
            logger.warning("Could not close connection.", exc_info=True)


def check_stopped(state):
    if state is None:
        return
    with RUN_LOCK:
        if state["cancelled"]:
            raise RunStopped("Run stopped.")


@contextmanager
def database_cursor(state=None, operation_key=None, timeout=None):
    """
    Acquire a database slot, then register the cursor for cancellation.
    Queued requests periodically check whether their run was stopped.

    timeout (seconds): once the cursor is handed out, a timer cancels the running
    query if it takes longer; the caller then gets QueryTimeout.
    """
    deadline = time.monotonic() + QUERY_QUEUE_TIMEOUT_SECONDS
    acquired = False
    connection = cursor = None
    timer = None
    timed_out = threading.Event()

    try:
        while not acquired:
            check_stopped(state)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(
                    503, "Database queue is busy. Try again shortly."
                )
            acquired = QUERY_SLOTS.acquire(timeout=min(0.25, remaining))

        check_stopped(state)
        connection = get_connection()
        cursor = connection.cursor()

        if state is not None:
            with RUN_LOCK:
                if state["cancelled"]:
                    raise RunStopped("Run stopped.")
                state["active"][operation_key] = cursor

        if timeout:
            def expire():
                timed_out.set()
                try:
                    cursor.cancel()          # stops the query on the warehouse
                except Exception:
                    logger.warning("Timeout cancellation failed.", exc_info=True)

            timer = threading.Timer(timeout, expire)
            timer.daemon = True
            timer.start()

        try:
            yield cursor
        except Exception as exc:
            if timed_out.is_set():
                raise QueryTimeout(timeout_message(timeout)) from exc
            raise
        finally:
            if timer is not None:
                timer.cancel()

        if timed_out.is_set():
            raise QueryTimeout(timeout_message(timeout))
        check_stopped(state)

    finally:
        # Keep the operation registered while closing database resources.
        close_db(cursor, connection)

        if state is not None:
            with RUN_LOCK:
                if state["active"].get(operation_key) is cursor:
                    state["active"].pop(operation_key, None)

        if acquired:
            QUERY_SLOTS.release()


def read_table_records(table_sql: str) -> List[Dict[str, Any]]:
    """Read a (small) config table as a list of dicts."""
    with database_cursor() as cursor:
        cursor.execute(f"SELECT * FROM {table_sql}")
        headers = [column[0] for column in cursor.description]
        return [dict(zip(headers, row)) for row in cursor.fetchall()]


def load_definitions() -> ParameterDefinitions:
    """Global parameter definitions from master_config_global_param (cached)."""
    with PARAM_LOCK:
        cached = PARAM_CACHE["defs"]
        age = time.monotonic() - PARAM_CACHE["loaded_at"]
        if cached is not None and age < CONFIG_CACHE_SECONDS:
            return cached

        defs = build_parameter_definitions(read_table_records(PARAM_TABLE))
        PARAM_CACHE["defs"] = defs
        PARAM_CACHE["loaded_at"] = time.monotonic()
        return defs


def load_catalog() -> ReportCatalog:
    """
    Refresh the shared cache when stale.
    Existing runs retain their original ReportCatalog snapshot.
    """
    with CONFIG_LOCK:
        cached = CONFIG_CACHE["catalog"]
        age = time.monotonic() - CONFIG_CACHE["loaded_at"]
        if cached is not None and age < CONFIG_CACHE_SECONDS:
            return cached

        records = read_table_records(CONFIG_TABLE)

        def sort_key(record):
            lower = {str(k).casefold(): v for k, v in record.items()}
            return (
                str(lower.get("page") or ""),
                str(lower.get("section") or ""),
                str(lower.get("fieldid") or lower.get("fileldid") or ""),
                str(lower.get("column") or ""),
            )

        records.sort(key=sort_key)
        catalog = ReportCatalog(records, DATABRICKS_CATALOG)

        # Fixed-value parameters (e.g. material types) are validated against the parameter table.
        # Best effort: if that table is unavailable the built-in defaults are used.
        try:
            catalog.allowed_values = load_definitions().allowed
        except Exception:
            logger.warning("Parameter definitions unavailable; using built-in allowed values.", exc_info=True)

        CONFIG_CACHE["catalog"] = catalog
        CONFIG_CACHE["loaded_at"] = time.monotonic()
        return catalog


@app.on_event("startup")
def warm_up():
    """Read the config tables in the background so the first page load is faster."""
    def run():
        for loader in (load_definitions, load_catalog):
            try:
                loader()
            except Exception:
                logger.warning("Warm-up failed for %s.", loader.__name__, exc_info=True)

    threading.Thread(target=run, daemon=True).start()


def clean_runs():
    now = time.monotonic()
    with RUN_LOCK:
        for run_id, state in list(RUNS.items()):
            idle = not state["active"] and not state["inflight"]
            expired = now - state["touched"] > RUN_TTL_SECONDS
            if idle and expired:
                del RUNS[run_id]


def owner_for(request: Request) -> str:
    owner = request.cookies.get(COOKIE_NAME)
    if not owner:
        raise HTTPException(403, "Open an app page first.")
    return owner


def check_origin(request: Request):
    """
    CSRF guard: reject cross-site requests.
    Behind the Databricks Apps proxy, request.url.netloc is the internal host,
    so the browser's Origin (public host) is also compared with the forwarded
    host headers. Sec-Fetch-Site is set by the browser and cannot be forged by page scripts.
    """
    site = request.headers.get("sec-fetch-site", "").casefold()
    if site == "cross-site":
        raise HTTPException(403, "Cross-site requests are not allowed.")

    origin = request.headers.get("origin")
    if not origin or site == "same-origin":
        return

    origin_host = urlsplit(origin).netloc.casefold()
    allowed = {request.url.netloc.casefold()}
    for header in ("host", "x-forwarded-host"):
        value = request.headers.get(header)
        if value:
            # x-forwarded-host can be a comma-separated list
            allowed.update(part.strip().casefold() for part in value.split(","))

    if origin_host not in allowed:
        logger.warning("Origin %s not in %s", origin_host, sorted(allowed))
        raise HTTPException(403, "Request origin does not match the app.")


def get_run(run_id: str, owner: str):
    with RUN_LOCK:
        clean_runs()
        state = RUNS.get(run_id)
        if state is None or not secrets.compare_digest(state["owner"], owner):
            raise HTTPException(
                404,
                "Run not found or expired. Click Run Query again.",
            )
        state["touched"] = time.monotonic()
        return state


def request_fingerprint(req: ReportRequest) -> str:
    encoded = jsonable_encoder(req.params)
    encoded.pop("active_profile", None)

    payload = {
        "params": encoded,
        "sync_mode": req.sync_mode,
        "flow_direction": req.flow_direction,
    }
    serialized = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@contextmanager
def run_operation(state, operation_key: str, req: ReportRequest):
    """
    Pin parameters/mode on the first request.
    Subsequent pagination/export requests must use the same snapshot.
    """
    fingerprint = request_fingerprint(req)

    with RUN_LOCK:
        if state["cancelled"]:
            raise RunStopped("Run stopped.")

        if operation_key in state["inflight"]:
            raise HTTPException(
                409, "This report operation is already running."
            )

        if state["fingerprint"] is None:
            state["fingerprint"] = fingerprint
            state["mode"] = {
                "sync_mode": req.sync_mode,
                "flow_direction": req.flow_direction,
            }
        elif state["fingerprint"] != fingerprint:
            raise HTTPException(
                409,
                "Parameters or synchronization mode changed. "
                "Click Run Query to start a new run.",
            )

        state["inflight"].add(operation_key)
        state["touched"] = time.monotonic()

    try:
        yield
        check_stopped(state)
    finally:
        with RUN_LOCK:
            state["inflight"].discard(operation_key)
            state["touched"] = time.monotonic()


def remove_file(path: str):
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        logger.warning("Could not remove export file.", exc_info=True)


def xlsx_write_cell(sheet, row, column, value, text_fmt, dt_fmt, date_fmt):
    if value is None:
        return

    if isinstance(value, datetime):
        sheet.write_datetime(
            row, column, value.replace(tzinfo=None), dt_fmt
        )
    elif isinstance(value, date):
        sheet.write_datetime(row, column, value, date_fmt)
    elif isinstance(value, bool):
        sheet.write_boolean(row, column, value)
    elif isinstance(value, int):
        # Preserve long numeric identifiers exactly.
        if len(str(abs(value))) > 15:
            sheet.write_string(row, column, str(value), text_fmt)
        else:
            sheet.write_number(row, column, value)
    elif isinstance(value, Decimal):
        if value.is_finite() and len(value.as_tuple().digits) <= 15:
            sheet.write_number(row, column, float(value))
        else:
            sheet.write_string(row, column, str(value), text_fmt)
    elif isinstance(value, float):
        if math.isfinite(value):
            sheet.write_number(row, column, value)
        else:
            sheet.write_string(row, column, str(value), text_fmt)
    else:
        sheet.write_string(row, column, str(value), text_fmt)


def build_xlsx_from_cursor(cursor, output_path: str, state) -> int:
    workbook = xlsxwriter.Workbook(
        output_path,
        {
            "constant_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )

    total = 0
    try:
        sheet = workbook.add_worksheet("Data")
        header_fmt = workbook.add_format(
            {"bold": True, "bg_color": "#DCE6F1"}
        )
        text_fmt = workbook.add_format({"num_format": "@"})
        dt_fmt = workbook.add_format(
            {"num_format": "yyyy-mm-dd hh:mm"}
        )
        date_fmt = workbook.add_format({"num_format": "yyyy-mm-dd"})

        columns = [item[0] for item in cursor.description]
        for index, name in enumerate(columns):
            sheet.write_string(0, index, name, header_fmt)

        sheet.freeze_panes(1, 0)
        if columns:
            sheet.set_column(0, len(columns) - 1, 22)

        while True:
            check_stopped(state)
            batch = cursor.fetchmany(1000)
            if not batch:
                break

            for row in batch:
                check_stopped(state)
                total += 1
                if total > MAX_EXPORT_ROWS:
                    raise HTTPException(
                        413,
                        f"More than {MAX_EXPORT_ROWS:,} rows match. "
                        "Narrow the filters before exporting.",
                    )

                for index, value in enumerate(row):
                    xlsx_write_cell(
                        sheet,
                        total,
                        index,
                        value,
                        text_fmt,
                        dt_fmt,
                        date_fmt,
                    )

        if columns:
            sheet.autofilter(0, 0, total, len(columns) - 1)

    finally:
        workbook.close()

    return total


def serve_page(name: str, request: Request):
    path = TEMPLATES_DIR / name
    if not path.is_file():
        return HTMLResponse(f"{name} not found", status_code=404)

    response = FileResponse(
        path,
        headers={"Cache-Control": "no-store"},
    )

    # Refresh ownership cookie on every page visit.
    response.set_cookie(
        COOKIE_NAME,
        request.cookies.get(COOKIE_NAME) or secrets.token_urlsafe(32),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=RUN_TTL_SECONDS,
        path="/",
    )
    return response


@app.get("/")
def home():
    # Global parameters live on every report page; no separate landing page.
    return RedirectResponse("/papermaking")


@app.get("/converting")
def converting_page(request: Request):
    return serve_page("converting.html", request)


@app.get("/papermaking")
def papermaking_page(request: Request):
    return serve_page("papermaking.html", request)


@app.get("/tab{number}")
def placeholder_page(number: int, request: Request):
    if number not in range(1, 6):
        raise HTTPException(404, "Unknown tab.")
    return serve_page(f"tab{number}.html", request)


@app.get("/api/config")
def report_configuration():
    try:
        return load_catalog().metadata()
    except Exception as exc:
        logger.exception("Could not load report configuration.")
        raise HTTPException(
            503, f"Report configuration unavailable: {exc}"
        ) from exc


@app.get("/api/version")
def version():
    return {
        "version": APP_VERSION,
        "environment": "DEV",
        "schema": "traceability_poc",
    }


# ---------------------------------------------------------------------
# Global parameters: definitions + dropdown options (table driven)
# ---------------------------------------------------------------------

def distinct_values(
    dimension_name: str,
    column: str,
    plant_code_name: Optional[str] = None,
):
    table = qualified_table(dimension_name, DATABRICKS_CATALOG)
    selected_column = identifier(column)
    conditions = [f"{selected_column} IS NOT NULL"]
    binds = {}

    if plant_code_name:
        conditions.append("`PlantCodeName` = :plant")
        binds["plant"] = plant_code_name

    query = (
        f"SELECT DISTINCT {selected_column} FROM {table} WHERE "
        + " AND ".join(conditions)
        + f" ORDER BY {selected_column} LIMIT 500"
    )

    with database_cursor() as cursor:
        cursor.execute(query, binds or None)
        return [
            str(row[0])
            for row in cursor.fetchall()
            if row[0] is not None
        ]


def plant_values(dimension_tables) -> List[str]:
    """Distinct plants available in any of the given dimension tables."""
    parts = [
        "SELECT `PlantCodeName` FROM "
        + qualified_table(table, DATABRICKS_CATALOG)
        + " WHERE `PlantCodeName` IS NOT NULL"
        for table in dict.fromkeys(dimension_tables)
    ]
    query = (
        "SELECT DISTINCT `PlantCodeName` FROM ("
        + " UNION ALL ".join(parts)
        + ") AS plants ORDER BY `PlantCodeName` LIMIT 500"
    )
    with database_cursor() as cursor:
        cursor.execute(query)
        return [str(row[0]) for row in cursor.fetchall()]


@app.get("/api/params/definitions")
def params_definitions():
    """Panel layout built from master_config_global_param (the browser falls back to defaults on error)."""
    try:
        defs = load_definitions()
    except Exception as exc:
        logger.exception("Could not load parameter definitions.")
        raise HTTPException(503, f"Parameter configuration unavailable: {exc}") from exc
    return {"profiles": defs.profiles, "warnings": defs.warnings}


@app.get("/api/params/options/{param_id}")
def params_options(param_id: str, plant_code_name: Optional[str] = Query(None)):
    """Options for any dropdown parameter, resolved from the config table (never from user input)."""
    try:
        defs = load_definitions()
    except Exception as exc:
        return {"values": [], "error": str(exc)}

    try:
        if param_id in defs.sources:
            source = defs.sources[param_id]
            return {"values": distinct_values(source["table"], source["column"], plant_code_name)}
        if param_id in defs.plant_ids:
            # a plant parameter with its own Query lists only that table's plants (e.g. combiner plants)
            tables = (
                [defs.plant_sources[param_id]] if param_id in defs.plant_sources
                else (defs.plant_tables or list(DIMENSIONS.values()))
            )
            return {"values": plant_values(tables)}
    except Exception as exc:
        logger.exception("Could not load options for %s.", param_id)
        return {"values": [], "error": str(exc)}

    raise HTTPException(404, "Unknown parameter.")


# Legacy endpoints: only used by the built-in fallback definitions in common.js.
@app.get("/api/params/plants")
def params_plants():
    try:
        return {"values": plant_values(DIMENSIONS.values())}
    except Exception as exc:
        logger.exception("Could not load plants.")
        return {"values": [], "error": str(exc)}


def _legacy_lines(key: str, column: str, plant_code_name: Optional[str]):
    try:
        return {"values": distinct_values(DIMENSIONS[key], column, plant_code_name)}
    except Exception as exc:
        return {"values": [], "error": str(exc)}


@app.get("/api/params/cv-lines")
def params_cv_lines(plant_code_name: Optional[str] = Query(None)):
    return _legacy_lines("cv", "ConvertingProductionLine", plant_code_name)


@app.get("/api/params/cmb-lines")
def params_cmb_lines(plant_code_name: Optional[str] = Query(None)):
    return _legacy_lines("cmb", "CMB_ProductionLine", plant_code_name)


@app.get("/api/params/pm-lines")
def params_pm_lines(plant_code_name: Optional[str] = Query(None)):
    return _legacy_lines("pm", "PM_ProductionLine", plant_code_name)


@app.get("/api/params/suggest")
def params_suggest(field: str = Query(...), q: str = Query(...)):
    # Autocomplete has no configured source metadata yet.
    return {"values": []}


# ---------------------------------------------------------------------
# Runs and reports
# ---------------------------------------------------------------------

@app.post("/api/runs")
@app.post("/api/converting/runs")
def start_run(request: Request):
    check_origin(request)
    owner = owner_for(request)

    try:
        catalog = load_catalog()
    except Exception as exc:
        logger.exception("Could not start run.")
        raise HTTPException(
            503, f"Cannot load report configuration: {exc}"
        ) from exc

    with RUN_LOCK:
        clean_runs()
        if len(RUNS) >= MAX_RUNS:
            raise HTTPException(429, "Too many runs. Try again shortly.")

        run_id = secrets.token_urlsafe(24)
        started_at = utc_now()
        RUNS[run_id] = {
            "owner": owner,
            "created_at": started_at,
            "touched": time.monotonic(),
            "cancelled": False,
            "active": {},
            "inflight": set(),
            "catalog": catalog,
            "fingerprint": None,
            "mode": None,
        }

    return {
        "run_id": run_id,
        "started_at": started_at,
        "version": APP_VERSION,
    }


@app.post("/api/runs/{run_id}/cancel")
@app.post("/api/converting/runs/{run_id}/cancel")
def stop_run(run_id: str, request: Request):
    check_origin(request)
    state = get_run(run_id, owner_for(request))

    with RUN_LOCK:
        state["cancelled"] = True
        cursors = list(state["active"].values())

    errors = []
    for cursor in cursors:
        try:
            cursor.cancel()
        except Exception as exc:
            logger.warning("Cursor cancellation failed.", exc_info=True)
            errors.append(str(exc))

    return {
        "status": "cancellation_requested",
        "active_queries": len(cursors),
        "errors": errors,
    }


def prepare_query(
    page: str,
    report_id: str,
    req: ReportRequest,
    state,
    export: bool,
):
    if req.apply_order_link_filter:
        raise ValueError(
            "The legacy per-report synchronization flag is no longer used. "
            "Use sync_mode and flow_direction."
        )

    try:
        return build_query(
            catalog=state["catalog"],
            page=page,
            report_id=report_id,
            params=req.params,
            limit=req.limit,
            offset=req.offset,
            export=export,
            sync_mode=req.sync_mode,
            flow_direction=req.flow_direction,
        )
    except KeyError as exc:
        raise HTTPException(404, "Unknown report.") from exc


@app.post("/api/{page}/{report_id}/data")
def report_data(
    page: Literal["converting", "papermaking"],
    report_id: str,
    req: ReportRequest,
    request: Request,
):
    check_origin(request)
    state = get_run(req.run_id, owner_for(request))
    operation_key = f"{page}:data:{report_id}"

    try:
        with run_operation(state, operation_key, req):
            query, binds, metadata = prepare_query(
                page, report_id, req, state, export=False
            )

            with database_cursor(state, operation_key, timeout=QUERY_TIMEOUT_SECONDS) as cursor:
                check_stopped(state)
                cursor.execute(query, binds or None)
                rows = cursor.fetchmany(req.limit + 1)
                columns = [item[0] for item in cursor.description]
                check_stopped(state)

        return {
            "success": True,
            "columns": columns,
            "rows": [list(row) for row in rows[:req.limit]],
            "has_more": len(rows) > req.limit,
            "offset": req.offset,
            "limit": req.limit,
            "run_id": req.run_id,
            "run_started_at": state["created_at"],
            "metadata": metadata,
        }

    except RunStopped:
        return {
            "success": False,
            "cancelled": True,
            "error": "Run stopped.",
        }
    except QueryTimeout as exc:
        logger.warning("Report timed out: %s/%s", page, report_id)
        return {
            "success": False,
            "timeout": True,
            "error": str(exc),
        }
    except MissingParameters as exc:
        return {
            "success": False,
            "missing": True,
            "error": str(exc),
        }
    except ConfigurationError as exc:
        return {
            "success": False,
            "configuration_error": True,
            "error": str(exc),
        }
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    except HTTPException:
        raise
    except Exception as exc:
        with RUN_LOCK:
            stopped = state["cancelled"]

        if not stopped:
            logger.exception("Report failed: %s/%s", page, report_id)

        return {
            "success": False,
            "cancelled": stopped,
            "error": "Run stopped." if stopped else str(exc),
        }


@app.post("/api/{page}/export/{report_id}")
@app.post("/api/{page}/{report_id}/export")
def report_export(
    page: Literal["converting", "papermaking"],
    report_id: str,
    req: ReportRequest,
    request: Request,
):
    check_origin(request)
    state = get_run(req.run_id, owner_for(request))
    operation_key = f"{page}:export:{report_id}"
    temp_path = None

    try:
        with run_operation(state, operation_key, req):
            query, binds, _ = prepare_query(
                page, report_id, req, state, export=True
            )

            with database_cursor(state, operation_key, timeout=EXPORT_TIMEOUT_SECONDS) as cursor:
                check_stopped(state)
                cursor.execute(query, binds or None)
                check_stopped(state)

                with tempfile.NamedTemporaryFile(
                    suffix=".xlsx", delete=False
                ) as temporary:
                    temp_path = temporary.name

                build_xlsx_from_cursor(cursor, temp_path, state)
                check_stopped(state)

        return FileResponse(
            temp_path,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            filename=f"{page}_{report_id}.xlsx",
            background=BackgroundTask(remove_file, temp_path),
            headers={"Cache-Control": "no-store"},
        )

    except RunStopped as exc:
        if temp_path:
            remove_file(temp_path)
        raise HTTPException(409, "Export was stopped.") from exc
    except QueryTimeout as exc:
        if temp_path:
            remove_file(temp_path)
        logger.warning("Export timed out: %s/%s", page, report_id)
        raise HTTPException(408, str(exc)) from exc
    except (ConfigurationError, MissingParameters, ValueError) as exc:
        if temp_path:
            remove_file(temp_path)
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        if temp_path:
            remove_file(temp_path)
        raise
    except Exception as exc:
        if temp_path:
            remove_file(temp_path)

        with RUN_LOCK:
            stopped = state["cancelled"]

        if not stopped:
            logger.exception("Export failed: %s/%s", page, report_id)

        raise HTTPException(
            409 if stopped else 500,
            "Export was stopped." if stopped else f"Export failed: {exc}",
        ) from exc


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=1,
    )