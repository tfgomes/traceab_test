"""Traceability FastAPI app.

Provides:
- Global Parameters and the four Converting reports
- Server-side cancellation of active Converting queries
- CSV export for each Converting report
- Existing Upstream and Downstream API routes
"""

import csv
import io
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

base_dir = Path(__file__).parent
static_dir = base_dir / "static"
templates_dir = base_dir / "templates"

static_dir.mkdir(exist_ok=True)
templates_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ---------------------------------------------------------------------------
# Views and legacy tables
# ---------------------------------------------------------------------------

PLANT_LINE_VIEW = "traceability_poc.dim_converting_productionline"

UPSTREAM_R1_TABLE = f"{settings.DATABRICKS_SCHEMA}.{settings.DATABRICKS_TABLE}"
UPSTREAM_R2_TABLE = "traceability_poc.order_consumptions"
UPSTREAM_R3_TABLE = "traceability_poc.mr_production"

DOWNSTREAM1_TABLE = "traceability_poc.mr_production"
DOWNSTREAM2_TABLE = "traceability_poc.mr_cons_order"
DOWNSTREAM3_TABLE = "traceability_poc.prod_notif"


# ---------------------------------------------------------------------------
# Converting run registry
#
# This registry is process-local. Run one application process/replica unless
# cancellation is redesigned around shared state and Databricks query IDs.
# ---------------------------------------------------------------------------

COOKIE_NAME = "traceability_run_owner"
RUN_TTL_SECONDS = 3600
MAX_RUNS = 100
MAX_CONVERTING_CSV_ROWS = 100000

run_lock = threading.RLock()
runs = {}
query_slots = threading.BoundedSemaphore(4)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_connection():
    cfg = Config()
    return sql.connect(
        server_hostname=cfg.host,
        http_path=settings.DATABRICKS_HTTP_PATH,
        credentials_provider=lambda: cfg.authenticate,
    )


def close_db(cursor, conn):
    try:
        if cursor is not None:
            cursor.close()
    finally:
        if conn is not None:
            conn.close()


def csv_safe(value):
    """Keep spreadsheet applications from interpreting values as formulas."""
    if value is None:
        return ""

    text = str(value)

    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text

    return text


# ---------------------------------------------------------------------------
# Run ownership and cancellation helpers
# ---------------------------------------------------------------------------

def clean_runs():
    """Remove expired runs that do not have work in progress.

    Call only while holding run_lock.
    """
    now = time.monotonic()

    for key, state in list(runs.items()):
        if (
            not state["active"]
            and not state["inflight"]
            and now - state["created"] > RUN_TTL_SECONDS
        ):
            del runs[key]


def owner_for(request: Request) -> str:
    owner = request.cookies.get(COOKIE_NAME)

    if not owner:
        raise HTTPException(
            status_code=403,
            detail="Open the app home page first, then try again.",
        )

    return owner


def check_origin(request: Request):
    """Reject browser requests explicitly marked as cross-site.

    Do not compare Origin with the internal Host header: Databricks Apps may
    proxy requests and expose a different internal Host. Older browsers may
    omit Sec-Fetch-Site; the SameSite cookie still applies in that case.
    """
    site = request.headers.get("sec-fetch-site", "").lower()

    if site == "cross-site":
        raise HTTPException(
            status_code=403,
            detail="Cross-site requests are not allowed.",
        )


def get_run(run_id: str, owner: str):
    with run_lock:
        clean_runs()
        state = runs.get(run_id)

        if state is None or state["owner"] != owner:
            raise HTTPException(
                status_code=404,
                detail="Run not found or expired. Return to Global Parameters and click Run Query again.",
            )

        return state


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def serve_page(name: str, request: Request):
    page = templates_dir / name

    if not page.is_file():
        return HTMLResponse(
            f"{name} not found",
            status_code=404,
        )

    response = FileResponse(page)

    if (
        name in ("global_params.html", "converting.html")
        and not request.cookies.get(COOKIE_NAME)
    ):
        response.set_cookie(
            COOKIE_NAME,
            secrets.token_urlsafe(32),
            httponly=True,
            samesite="lax",
            # The public Databricks Apps URL uses HTTPS. Proxying may cause
            # request.url.scheme to appear as HTTP inside the app.
            secure=True,
            max_age=RUN_TTL_SECONDS,
        )

    return response


@app.get("/")
def home(request: Request):
    return serve_page("global_params.html", request)


@app.get("/converting")
def converting_page(request: Request):
    return serve_page("converting.html", request)


@app.get("/product-notification")
def product_notification_page(request: Request):
    return serve_page("product_notification.html", request)


@app.get("/upstream")
def upstream_page(request: Request):
    return serve_page("upstream.html", request)


@app.get("/downstream")
def downstream_page(request: Request):
    return serve_page("downstream.html", request)


@app.get("/index")
def index_legacy(request: Request):
    old_page = static_dir / "index.html"

    if old_page.is_file():
        return FileResponse(old_page)

    return serve_page("product_notification.html", request)


# ---------------------------------------------------------------------------
# Global Parameters dropdowns
# ---------------------------------------------------------------------------

def distinct_values(column: str, plant_id: Optional[str] = None):
    # column is selected by our own route handlers, not supplied by the user.
    where = f"WHERE {column} IS NOT NULL"
    binds = {}

    if plant_id:
        where += " AND PlantId = :plant"
        binds["plant"] = plant_id

    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"SELECT DISTINCT {column} FROM {PLANT_LINE_VIEW} "
            f"{where} ORDER BY {column} LIMIT 500",
            binds or None,
        )

        return sorted(
            {
                str(row[0])
                for row in cursor.fetchall()
                if row[0] is not None
            },
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
def params_production_lines(
    plant_id: Optional[str] = Query(None),
):
    try:
        return {
            "values": distinct_values(
                "ConvertingProductionLine",
                plant_id,
            )
        }
    except Exception as exc:
        return {"values": [], "error": str(exc)}


@app.get("/api/params/suggest")
def params_suggest(
    field: str = Query(...),
    q: str = Query(...),
):
    # Suggestions are not yet configured.
    return {"values": []}


# ---------------------------------------------------------------------------
# Converting reports
# ---------------------------------------------------------------------------

class ReportRequest(BaseModel):
    run_id: str
    params: GlobalParams = Field(default_factory=GlobalParams)
    limit: int = Field(10, ge=1, le=5000)
    offset: int = Field(0, ge=0)


@app.post("/api/converting/runs")
def start_run(request: Request):
    check_origin(request)
    owner = owner_for(request)

    with run_lock:
        clean_runs()

        if len(runs) >= MAX_RUNS:
            raise HTTPException(
                status_code=429,
                detail="Too many runs. Try again shortly.",
            )

        run_id = secrets.token_urlsafe(24)

        runs[run_id] = {
            "owner": owner,
            "created": time.monotonic(),
            "cancelled": False,
            "active": {},    # operation key -> cursor
            "inflight": set(),
        }

    return {"run_id": run_id}


@app.post("/api/converting/runs/{run_id}/cancel")
def stop_run(
    run_id: str,
    request: Request,
):
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


@app.post("/api/converting/{report_id}/data")
def converting_data(
    report_id: str,
    req: ReportRequest,
    request: Request,
):
    check_origin(request)
    owner = owner_for(request)
    state = get_run(req.run_id, owner)

    if report_id not in REPORTS:
        raise HTTPException(
            status_code=404,
            detail="Unknown report.",
        )

    try:
        query, binds = build_report_query(
            report_id,
            req.params,
            req.limit,
            req.offset,
            export=False,
        )
    except (MissingParameters, ValueError) as exc:
        return {
            "success": False,
            "error": str(exc),
            "missing": isinstance(exc, MissingParameters),
        }

    conn = None
    cursor = None
    key = f"data:{report_id}"
    registered = False

    try:
        with run_lock:
            if state["cancelled"]:
                return {
                    "success": False,
                    "cancelled": True,
                    "error": "Run stopped.",
                }

            if key in state["inflight"]:
                return {
                    "success": False,
                    "error": "This report is already loading.",
                }

            state["inflight"].add(key)
            registered = True

        with query_slots:
            with run_lock:
                if state["cancelled"]:
                    return {
                        "success": False,
                        "cancelled": True,
                        "error": "Run stopped.",
                    }

            conn = get_connection()
            cursor = conn.cursor()

            with run_lock:
                if state["cancelled"]:
                    return {
                        "success": False,
                        "cancelled": True,
                        "error": "Run stopped.",
                    }

                state["active"][key] = cursor

            cursor.execute(query, binds or None)
            rows = cursor.fetchmany(req.limit + 1)
            columns = [
                description[0]
                for description in cursor.description
            ]

            with run_lock:
                if state["cancelled"]:
                    return {
                        "success": False,
                        "cancelled": True,
                        "error": "Run stopped.",
                    }

            return {
                "success": True,
                "columns": columns,
                "rows": [
                    list(row)
                    for row in rows[:req.limit]
                ],
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
                state["active"].pop(key, None)
                state["inflight"].discard(key)

        close_db(cursor, conn)


# Register the new unambiguous export URL.
# Keep the old URL as an alias for a previously deployed converting.html.
@app.post("/api/converting/export/{report_id}")
@app.post("/api/converting/{report_id}/export")
def converting_export(
    report_id: str,
    req: ReportRequest,
    request: Request,
):
    check_origin(request)
    owner = owner_for(request)
    state = get_run(req.run_id, owner)

    if report_id not in REPORTS:
        raise HTTPException(
            status_code=404,
            detail="Unknown report.",
        )

    try:
        query, binds = build_report_query(
            report_id,
            req.params,
            req.limit,
            req.offset,
            export=True,
        )
    except (MissingParameters, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    conn = None
    cursor = None
    output = None
    key = f"export:{report_id}"
    registered = False

    try:
        with run_lock:
            if state["cancelled"]:
                raise HTTPException(
                    status_code=409,
                    detail="This run has been stopped.",
                )

            if key in state["inflight"]:
                raise HTTPException(
                    status_code=409,
                    detail="This report is already exporting.",
                )

            state["inflight"].add(key)
            registered = True

        with query_slots:
            with run_lock:
                if state["cancelled"]:
                    raise HTTPException(
                        status_code=409,
                        detail="This run has been stopped.",
                    )

            conn = get_connection()
            cursor = conn.cursor()

            with run_lock:
                if state["cancelled"]:
                    raise HTTPException(
                        status_code=409,
                        detail="This run has been stopped.",
                    )

                state["active"][key] = cursor

            cursor.execute(query, binds or None)

            # Small exports remain in memory; larger exports spill to disk.
            output = tempfile.SpooledTemporaryFile(
                mode="w+t",
                max_size=8 * 1024 * 1024,
                encoding="utf-8",
                newline="",
            )

            writer = csv.writer(output)
            writer.writerow([
                description[0]
                for description in cursor.description
            ])

            total = 0

            while True:
                with run_lock:
                    if state["cancelled"]:
                        raise HTTPException(
                            status_code=409,
                            detail="Export was stopped.",
                        )

                batch = cursor.fetchmany(1000)

                if not batch:
                    break

                for row in batch:
                    total += 1

                    if total > MAX_CONVERTING_CSV_ROWS:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                "More than 100,000 rows match. "
                                "Narrow the filters before exporting."
                            ),
                        )

                    writer.writerow([
                        csv_safe(value)
                        for value in row
                    ])

            with run_lock:
                if state["cancelled"]:
                    raise HTTPException(
                        status_code=409,
                        detail="Export was stopped.",
                    )

        output.seek(0)
        prepared = output
        output = None

        def send_file():
            try:
                while True:
                    chunk = prepared.read(64 * 1024)

                    if not chunk:
                        break

                    yield chunk
            finally:
                prepared.close()

        return StreamingResponse(
            send_file(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="converting_{report_id}.csv"'
                ),
            },
        )

    except HTTPException:
        raise

    except Exception as exc:
        with run_lock:
            stopped = state["cancelled"]

        raise HTTPException(
            status_code=409 if stopped else 500,
            detail=(
                "Export was stopped."
                if stopped
                else f"Export failed: {exc}"
            ),
        ) from exc

    finally:
        if registered:
            with run_lock:
                state["active"].pop(key, None)
                state["inflight"].discard(key)

        close_db(cursor, conn)

        if output is not None:
            output.close()


# ---------------------------------------------------------------------------
# Legacy helpers used by Upstream and Downstream
# ---------------------------------------------------------------------------

def legacy_where(
    filters,
    allowed,
    date_start=None,
    date_end=None,
    date_column=None,
    exclude=None,
):
    clauses = []
    binds = {}

    for column in allowed:
        value = filters.get(column)

        if value and column != exclude:
            key = f"v_{column}"
            clauses.append(
                f"LOWER(CAST({column} AS STRING)) = LOWER(:{key})"
            )
            binds[key] = value

    if date_column:
        if date_start:
            clauses.append(
                f"DATE({date_column}) >= CAST(:date_from AS DATE)"
            )
            binds["date_from"] = date_start

        if date_end:
            clauses.append(
                f"DATE({date_column}) <= CAST(:date_to AS DATE)"
            )
            binds["date_to"] = date_end

    where = (
        "WHERE " + " AND ".join(clauses)
        if clauses
        else ""
    )

    return where, binds


def legacy_select(
    table,
    where,
    binds,
    limit=100,
    offset=0,
):
    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"SELECT * FROM {table} {where} "
            f"LIMIT {limit + 1} OFFSET {offset}",
            binds or None,
        )

        rows = cursor.fetchmany(limit + 1)

        return {
            "success": True,
            "columns": [
                item[0]
                for item in cursor.description
            ],
            "rows": [
                list(row)
                for row in rows[:limit]
            ],
            "has_more": len(rows) > limit,
            "offset": offset,
            "limit": limit,
        }

    finally:
        close_db(cursor, conn)


def legacy_options(
    table,
    column,
    where,
    binds,
    limit=500,
):
    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        joiner = "AND" if where else "WHERE"

        cursor.execute(
            f"SELECT DISTINCT {column} "
            f"FROM {table} {where} "
            f"{joiner} {column} IS NOT NULL "
            f"ORDER BY {column} LIMIT {limit}",
            binds or None,
        )

        return [
            str(row[0])
            for row in cursor.fetchall()
            if row[0] is not None
        ]

    finally:
        close_db(cursor, conn)


def legacy_export(
    table,
    where,
    binds,
    filename,
):
    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"SELECT * FROM {table} {where}",
            binds or None,
        )

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            item[0]
            for item in cursor.description
        ])

        for row in cursor.fetchall():
            writer.writerow([
                "" if value is None else value
                for value in row
            ])

        output.seek(0)

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f"attachment; filename={filename}"
                ),
            },
        )

    finally:
        close_db(cursor, conn)


def legacy_result(
    table,
    where,
    binds,
    limit,
    offset,
):
    try:
        return legacy_select(
            table,
            where,
            binds,
            limit,
            offset,
        )
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }


def legacy_csv(
    table,
    where,
    binds,
    filename,
):
    try:
        return legacy_export(
            table,
            where,
            binds,
            filename,
        )
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Upstream report 1
# ---------------------------------------------------------------------------

@app.get("/api/filter-values")
def upstream_r1_filters(
    PlantId: Optional[str] = None,
    MaterialId: Optional[str] = None,
    ProductionDateStart: Optional[str] = None,
    ProductionDateEnd: Optional[str] = None,
):
    result = {}

    args = {
        "PlantId": PlantId,
        "MaterialId": MaterialId,
    }

    for column in ("PlantId", "MaterialId"):
        where, binds = legacy_where(
            args,
            ("PlantId", "MaterialId"),
            ProductionDateStart,
            ProductionDateEnd,
            "PostingDate",
            exclude=column,
        )

        try:
            result[column] = legacy_options(
                UPSTREAM_R1_TABLE,
                column,
                where,
                binds,
                200,
            )
        except Exception:
            result[column] = []

    return result


def upstream_r1_where(
    PlantId,
    MaterialId,
    ProductionDateStart,
    ProductionDateEnd,
):
    return legacy_where(
        {
            "PlantId": PlantId,
            "MaterialId": MaterialId,
        },
        ("PlantId", "MaterialId"),
        ProductionDateStart,
        ProductionDateEnd,
        "PostingDate",
    )


@app.get("/api/data")
def upstream_r1_data(
    PlantId: Optional[str] = None,
    MaterialId: Optional[str] = None,
    ProductionDateStart: Optional[str] = None,
    ProductionDateEnd: Optional[str] = None,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    where, binds = upstream_r1_where(
        PlantId,
        MaterialId,
        ProductionDateStart,
        ProductionDateEnd,
    )

    return legacy_result(
        UPSTREAM_R1_TABLE,
        where,
        binds,
        limit,
        offset,
    )


@app.get("/api/export")
def upstream_r1_export(
    PlantId: Optional[str] = None,
    MaterialId: Optional[str] = None,
    ProductionDateStart: Optional[str] = None,
    ProductionDateEnd: Optional[str] = None,
):
    where, binds = upstream_r1_where(
        PlantId,
        MaterialId,
        ProductionDateStart,
        ProductionDateEnd,
    )

    return legacy_csv(
        UPSTREAM_R1_TABLE,
        where,
        binds,
        "equipment_model.csv",
    )


# ---------------------------------------------------------------------------
# Upstream report 2
# ---------------------------------------------------------------------------

def order_where(
    OrderNumber,
    Component_materialtype,
):
    return legacy_where(
        {
            "OrderNumber": OrderNumber,
            "Component_materialtype": Component_materialtype,
        },
        ("OrderNumber", "Component_materialtype"),
    )


@app.get("/api/order-consumptions/filter-values")
def upstream_r2_filters(
    OrderNumber: Optional[str] = None,
    Component_materialtype: Optional[str] = None,
):
    try:
        w1, b1 = order_where(
            None,
            Component_materialtype,
        )

        w2, b2 = order_where(
            OrderNumber,
            None,
        )

        return {
            "OrderNumber": legacy_options(
                UPSTREAM_R2_TABLE,
                "OrderNumber",
                w1,
                b1,
            ),
            "Component_materialtype": legacy_options(
                UPSTREAM_R2_TABLE,
                "Component_materialtype",
                w2,
                b2,
            ),
        }

    except Exception:
        return {
            "OrderNumber": [],
            "Component_materialtype": [],
        }


@app.get("/api/data-order-consumptions")
def upstream_r2_data(
    OrderNumber: Optional[str] = None,
    Component_materialtype: Optional[str] = None,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    where, binds = order_where(
        OrderNumber,
        Component_materialtype,
    )

    return legacy_result(
        UPSTREAM_R2_TABLE,
        where,
        binds,
        limit,
        offset,
    )


@app.get("/api/export-order-consumptions")
def upstream_r2_export(
    OrderNumber: Optional[str] = None,
    Component_materialtype: Optional[str] = None,
):
    where, binds = order_where(
        OrderNumber,
        Component_materialtype,
    )

    return legacy_csv(
        UPSTREAM_R2_TABLE,
        where,
        binds,
        "order_consumptions.csv",
    )


# ---------------------------------------------------------------------------
# Upstream report 3
# ---------------------------------------------------------------------------

def batch_where(BatchId):
    return legacy_where(
        {"BatchId": BatchId},
        ("BatchId",),
    )


@app.get("/api/mr-production/filter-values")
def upstream_r3_filters(
    BatchId: Optional[str] = None,
):
    try:
        return {
            "BatchId": legacy_options(
                UPSTREAM_R3_TABLE,
                "BatchId",
                "",
                {},
            )
        }
    except Exception:
        return {"BatchId": []}


@app.get("/api/data-mr-production")
def upstream_r3_data(
    BatchId: Optional[str] = None,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    where, binds = batch_where(BatchId)

    return legacy_result(
        UPSTREAM_R3_TABLE,
        where,
        binds,
        limit,
        offset,
    )


@app.get("/api/export-mr-production")
def upstream_r3_export(
    BatchId: Optional[str] = None,
):
    where, binds = batch_where(BatchId)

    return legacy_csv(
        UPSTREAM_R3_TABLE,
        where,
        binds,
        "mr_production.csv",
    )


# ---------------------------------------------------------------------------
# Downstream report 1
# ---------------------------------------------------------------------------

def downstream1_where(
    PlantId,
    MaterialId,
    ProductionDateStart,
    ProductionDateEnd,
    exclude=None,
):
    return legacy_where(
        {
            "PlantId": PlantId,
            "MaterialId": MaterialId,
        },
        ("PlantId", "MaterialId"),
        ProductionDateStart,
        ProductionDateEnd,
        "ProductionDate",
        exclude,
    )


@app.get("/api/downstream1/filter-values")
def downstream1_filters(
    PlantId: Optional[str] = None,
    MaterialId: Optional[str] = None,
    ProductionDateStart: Optional[str] = None,
    ProductionDateEnd: Optional[str] = None,
):
    result = {}

    for column in ("PlantId", "MaterialId"):
        where, binds = downstream1_where(
            PlantId,
            MaterialId,
            ProductionDateStart,
            ProductionDateEnd,
            column,
        )

        try:
            result[column] = legacy_options(
                DOWNSTREAM1_TABLE,
                column,
                where,
                binds,
                200,
            )
        except Exception:
            result[column] = []

    return result


@app.get("/api/downstream1/data")
def downstream1_data(
    PlantId: Optional[str] = None,
    MaterialId: Optional[str] = None,
    ProductionDateStart: Optional[str] = None,
    ProductionDateEnd: Optional[str] = None,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    where, binds = downstream1_where(
        PlantId,
        MaterialId,
        ProductionDateStart,
        ProductionDateEnd,
    )

    return legacy_result(
        DOWNSTREAM1_TABLE,
        where,
        binds,
        limit,
        offset,
    )


@app.get("/api/downstream1/export")
def downstream1_export(
    PlantId: Optional[str] = None,
    MaterialId: Optional[str] = None,
    ProductionDateStart: Optional[str] = None,
    ProductionDateEnd: Optional[str] = None,
):
    where, binds = downstream1_where(
        PlantId,
        MaterialId,
        ProductionDateStart,
        ProductionDateEnd,
    )

    return legacy_csv(
        DOWNSTREAM1_TABLE,
        where,
        binds,
        "downstream1.csv",
    )


# ---------------------------------------------------------------------------
# Downstream report 2
# ---------------------------------------------------------------------------

def downstream2_where(RawMaterialBatchId):
    return legacy_where(
        {
            "RawMaterialBatchId": RawMaterialBatchId,
        },
        ("RawMaterialBatchId",),
    )


@app.get("/api/downstream2/filter-values")
def downstream2_filters(
    RawMaterialBatchId: Optional[str] = None,
):
    try:
        return {
            "RawMaterialBatchId": legacy_options(
                DOWNSTREAM2_TABLE,
                "RawMaterialBatchId",
                "",
                {},
            )
        }
    except Exception:
        return {"RawMaterialBatchId": []}


@app.get("/api/downstream2/data")
def downstream2_data(
    RawMaterialBatchId: Optional[str] = None,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    where, binds = downstream2_where(
        RawMaterialBatchId
    )

    return legacy_result(
        DOWNSTREAM2_TABLE,
        where,
        binds,
        limit,
        offset,
    )


@app.get("/api/downstream2/export")
def downstream2_export(
    RawMaterialBatchId: Optional[str] = None,
):
    where, binds = downstream2_where(
        RawMaterialBatchId
    )

    return legacy_csv(
        DOWNSTREAM2_TABLE,
        where,
        binds,
        "downstream2.csv",
    )


# ---------------------------------------------------------------------------
# Downstream report 3
# ---------------------------------------------------------------------------

def downstream3_where(
    PlantId,
    MaterialId,
    Notification,
    NotificationDateStart,
    NotificationDateEnd,
    exclude=None,
):
    return legacy_where(
        {
            "PlantId": PlantId,
            "MaterialId": MaterialId,
            "Notification": Notification,
        },
        ("PlantId", "MaterialId", "Notification"),
        NotificationDateStart,
        NotificationDateEnd,
        "NotificationDate",
        exclude,
    )


@app.get("/api/downstream3/filter-values")
def downstream3_filters(
    PlantId: Optional[str] = None,
    MaterialId: Optional[str] = None,
    Notification: Optional[str] = None,
    NotificationDateStart: Optional[str] = None,
    NotificationDateEnd: Optional[str] = None,
):
    result = {}

    for column in (
        "PlantId",
        "MaterialId",
        "Notification",
    ):
        where, binds = downstream3_where(
            PlantId,
            MaterialId,
            Notification,
            NotificationDateStart,
            NotificationDateEnd,
            column,
        )

        try:
            result[column] = legacy_options(
                DOWNSTREAM3_TABLE,
                column,
                where,
                binds,
                200,
            )
        except Exception:
            result[column] = []

    return result


@app.get("/api/downstream3/data")
def downstream3_data(
    PlantId: Optional[str] = None,
    MaterialId: Optional[str] = None,
    Notification: Optional[str] = None,
    NotificationDateStart: Optional[str] = None,
    NotificationDateEnd: Optional[str] = None,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    where, binds = downstream3_where(
        PlantId,
        MaterialId,
        Notification,
        NotificationDateStart,
        NotificationDateEnd,
    )

    return legacy_result(
        DOWNSTREAM3_TABLE,
        where,
        binds,
        limit,
        offset,
    )


@app.get("/api/downstream3/export")
def downstream3_export(
    PlantId: Optional[str] = None,
    MaterialId: Optional[str] = None,
    Notification: Optional[str] = None,
    NotificationDateStart: Optional[str] = None,
    NotificationDateEnd: Optional[str] = None,
):
    where, binds = downstream3_where(
        PlantId,
        MaterialId,
        Notification,
        NotificationDateStart,
        NotificationDateEnd,
    )

    return legacy_csv(
        DOWNSTREAM3_TABLE,
        where,
        binds,
        "downstream3.csv",
    )


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )