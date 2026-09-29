"""
FastAPI app - Traceability Analytics POC
- Supports pages: Global Parameters (home), Converting, Product Notification, Upstream and Downstream
- Upstream has 3 reports/tables
- Downstream has 3 reports/tables
- Cascading filter values + paginated data + CSV export
- Global query parameters (see params.py) used by the Converting report
"""

from pathlib import Path
from typing import Optional
import io
import csv

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field                      # NEW
from databricks import sql

from config import settings
from params import GlobalParams, build_where                # NEW


app = FastAPI()

# -------------------------
# Paths
# -------------------------
base_dir = Path(__file__).parent
static_dir = base_dir / "static"
templates_dir = base_dir / "templates"

static_dir.mkdir(exist_ok=True)
templates_dir.mkdir(exist_ok=True)

# Serve static assets (css/js/images)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# -------------------------
# Constants
# -------------------------
FILTER_COLUMNS_R1 = ["PlantId", "MaterialId"]
DATE_COLUMN_REPORT1 = "PostingDate"   # upstream report 1
MIN_DATE = "2026-01-01"

# Downstream report 1 uses same date column for start/end
DATE_COLUMN_DOWNSTREAM1_START = "ProductionDate"
DATE_COLUMN_DOWNSTREAM1_END = "ProductionDate"

# Downstream report 3 (Product Complaints) filters
FILTER_COLUMNS_D3 = ["PlantId", "MaterialId", "Notification"]
DATE_COLUMN_DOWNSTREAM3 = "NotificationDate"

# Tables
UPSTREAM_R1_TABLE = f"{settings.DATABRICKS_SCHEMA}.{settings.DATABRICKS_TABLE}"
UPSTREAM_R2_TABLE = "traceability_poc.order_consumptions"
UPSTREAM_R3_TABLE = "traceability_poc.mr_production"

DOWNSTREAM1_TABLE = "traceability_poc.mr_production"
DOWNSTREAM2_TABLE = "traceability_poc.mr_cons_order"
DOWNSTREAM3_TABLE = "traceability_poc.prod_notif"

# -------------------------
# NEW: Global parameters / Converting (fill in when the views are available)
# -------------------------
PRODUCTION_LINE_TABLE = "traceability_poc.dim_converting_productionline"
PRODUCTION_LINE_COLUMN = "ProductionLine"      # TODO confirm real column name

# (table, column) per parameter, used for type-ahead suggestions.
# None = source view not available yet. Example:
#   "zfin_batch_id": ("traceability_poc.dim_zfin_batch", "BatchId"),
SUGGEST_SOURCES = {
    "zfin_base_material_id": None,
    "zfin_material_id": None,
    "zfin_order": None,
    "zfin_batch_id": None,
    "zfin_sscc": None,
}

CONVERTING_VIEW = None                          # TODO e.g. "traceability_poc.converting_raw_material_consumption"
CONVERTING_COLUMNS = {                          # parameter key -> column in the view (TODO confirm; these are guesses)
    "zfin_material_id": "ProductMaterialId",
    "zfin_order": "OrderNumber",
    "zfin_batch_id": "ProductBatchId",
    # "zfin_base_material_id": "...",
    # "zfin_sscc": "...",
    # "production_line": "...",
    # "production_datetime": "...",
}


# -------------------------
# DB helpers
# -------------------------
#05/08/2026
#def get_connection():
#    return sql.connect(
#        server_hostname=settings.DATABRICKS_HOST,
#        http_path=settings.DATABRICKS_HTTP_PATH,
#        access_token=settings.DATABRICKS_TOKEN,
#    )

from databricks.sdk.core import Config

def get_connection():

    cfg = Config()

    return sql.connect(
        server_hostname=cfg.host,
        http_path=settings.DATABRICKS_HTTP_PATH,
        credentials_provider=lambda: cfg.authenticate
    )


def esc(val):
    return str(val).replace("'", "''")


def query_distinct_values(table: str, column: str, where_clause: str = "", limit: int = 500):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        sql_text = (
            f"SELECT DISTINCT {column} FROM {table} {where_clause} "
            f"{'AND' if where_clause else 'WHERE'} {column} IS NOT NULL "
            f"ORDER BY {column} LIMIT {limit}"
        )
        cursor.execute(sql_text)
        return [str(r[0]) for r in cursor.fetchall() if r[0] is not None]
    finally:
        cursor.close()
        conn.close()


def query_data(table: str, where_clause: str, limit: int, offset: int):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        sql_text = f"SELECT * FROM {table} {where_clause} LIMIT {limit + 1} OFFSET {offset}"
        cursor.execute(sql_text)

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        has_more = len(rows) > limit
        rows = rows[:limit]

        return {
            "success": True,
            "columns": columns,
            "rows": [list(row) for row in rows],
            "has_more": has_more,
            "offset": offset,
            "limit": limit,
        }
    finally:
        cursor.close()
        conn.close()


# NEW: same as query_data, but values are bound parameters (no string concatenation)
def query_data_bound(table: str, where_clause: str, binds: dict, limit: int, offset: int):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT * FROM {table} {where_clause} LIMIT {limit + 1} OFFSET {offset}",
            binds or None,
        )
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        return {
            "success": True,
            "columns": columns,
            "rows": [list(row) for row in rows[:limit]],
            "has_more": len(rows) > limit,
            "offset": offset,
            "limit": limit,
        }
    finally:
        cursor.close()
        conn.close()


def query_export_csv(table: str, where_clause: str, filename: str):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        sql_text = f"SELECT * FROM {table} {where_clause}"
        cursor.execute(sql_text)

        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([v if v is not None else "" for v in row])
        output.seek(0)

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    finally:
        cursor.close()
        conn.close()


# -------------------------
# WHERE builders
# -------------------------
def build_where_upstream_r1(filters: dict, exclude: Optional[str] = None):
    conditions = []

    for col, val in filters.items():
        if col in ("ProductionDateStart", "ProductionDateEnd"):
            continue
        if col == exclude:
            continue
        if col not in FILTER_COLUMNS_R1:
            continue
        if val:
            conditions.append(f"LOWER({col}) = LOWER('{esc(val)}')")

    date_start = filters.get("ProductionDateStart")
    date_end = filters.get("ProductionDateEnd")

    if date_start:
        ds = esc(date_start)
        if ds < MIN_DATE:
            ds = MIN_DATE
        conditions.append(f"DATE({DATE_COLUMN_REPORT1}) >= DATE('{ds}')")

    if date_end:
        de = esc(date_end)
        if de < MIN_DATE:
            de = MIN_DATE
        conditions.append(f"DATE({DATE_COLUMN_REPORT1}) <= DATE('{de}')")

    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


def build_where_downstream1(filters: dict, exclude: Optional[str] = None):
    conditions = []

    for col, val in filters.items():
        if col in ("ProductionDateStart", "ProductionDateEnd"):
            continue
        if col == exclude:
            continue
        if col not in FILTER_COLUMNS_R1:
            continue
        if val:
            conditions.append(f"LOWER({col}) = LOWER('{esc(val)}')")

    date_start = filters.get("ProductionDateStart")
    date_end = filters.get("ProductionDateEnd")

    if date_start:
        ds = esc(date_start)
        if ds < MIN_DATE:
            ds = MIN_DATE
        conditions.append(f"DATE({DATE_COLUMN_DOWNSTREAM1_START}) >= DATE('{ds}')")

    if date_end:
        de = esc(date_end)
        if de < MIN_DATE:
            de = MIN_DATE
        conditions.append(f"DATE({DATE_COLUMN_DOWNSTREAM1_END}) <= DATE('{de}')")

    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


def build_where_report2(order_number: Optional[str] = None, component_materialtype: Optional[str] = None):
    conditions = []
    if order_number:
        conditions.append(f"LOWER(OrderNumber) = LOWER('{esc(order_number)}')")
    if component_materialtype:
        conditions.append(f"LOWER(Component_materialtype) = LOWER('{esc(component_materialtype)}')")
    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


def build_where_report3(batch_id: Optional[str] = None):
    conditions = []
    if batch_id:
        conditions.append(f"LOWER(BatchId) = LOWER('{esc(batch_id)}')")
    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


def build_where_downstream3(filters: dict, exclude: Optional[str] = None):
    conditions = []

    for col, val in filters.items():
        if col in ("NotificationDateStart", "NotificationDateEnd"):
            continue
        if col == exclude:
            continue
        if col not in FILTER_COLUMNS_D3:
            continue
        if val:
            conditions.append(f"LOWER({col}) = LOWER('{esc(val)}')")

    date_start = filters.get("NotificationDateStart")
    date_end = filters.get("NotificationDateEnd")

    if date_start:
        ds = esc(date_start)
        if ds < MIN_DATE:
            ds = MIN_DATE
        conditions.append(f"DATE({DATE_COLUMN_DOWNSTREAM3}) >= DATE('{ds}')")

    if date_end:
        de = esc(date_end)
        if de < MIN_DATE:
            de = MIN_DATE
        conditions.append(f"DATE({DATE_COLUMN_DOWNSTREAM3}) <= DATE('{de}')")

    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


# -------------------------
# Page routes
# -------------------------
# NEW: small helper used by the page routes below
def serve_page(name: str):
    page = templates_dir / name
    if page.exists():
        return FileResponse(page)
    return HTMLResponse(f"<html><body><h1>{name} not found</h1></body></html>", status_code=404)


# CHANGED: home page is now the Global Parameters page
@app.get("/", response_class=HTMLResponse)
async def root():
    return serve_page("global_params.html")


# NEW
@app.get("/converting", response_class=HTMLResponse)
async def converting_page():
    return serve_page("converting.html")


@app.get("/product-notification", response_class=HTMLResponse)
async def product_notification_page():
    page = templates_dir / "product_notification.html"
    if page.exists():
        return FileResponse(page)
    return HTMLResponse("<html><body><h1>product_notification.html not found</h1></body></html>", status_code=404)


@app.get("/upstream", response_class=HTMLResponse)
async def upstream_page():
    page = templates_dir / "upstream.html"
    if page.exists():
        return FileResponse(page)
    return HTMLResponse("<html><body><h1>upstream.html not found</h1></body></html>", status_code=404)


@app.get("/downstream", response_class=HTMLResponse)
async def downstream_page():
    page = templates_dir / "downstream.html"
    if page.exists():
        return FileResponse(page)
    return HTMLResponse("<html><body><h1>downstream.html not found</h1></body></html>", status_code=404)


@app.get("/index", response_class=HTMLResponse)
async def index_legacy():
    old = static_dir / "index.html"
    if old.exists():
        return FileResponse(old)
    fallback = templates_dir / "product_notification.html"
    if fallback.exists():
        return FileResponse(fallback)
    return HTMLResponse("<html><body><h1>No page found</h1></body></html>", status_code=404)


# =========================================================
# NEW: GLOBAL PARAMETERS
# =========================================================
@app.get("/api/params/production-lines")
async def params_production_lines():
    try:
        vals = query_distinct_values(PRODUCTION_LINE_TABLE, PRODUCTION_LINE_COLUMN, "", limit=500)
        return {"values": vals}
    except Exception as e:
        return {"values": [], "error": str(e)}


@app.get("/api/params/suggest")
async def params_suggest(field: str = Query(...), q: str = Query(..., min_length=2, max_length=50)):
    source = SUGGEST_SOURCES.get(field)
    if not source:
        return {"values": []}          # source view not available yet
    table, column = source
    like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"SELECT DISTINCT CAST({column} AS STRING) AS v FROM {table} "
                f"WHERE CAST({column} AS STRING) LIKE :q ORDER BY v LIMIT 50",
                {"q": like},
            )
            return {"values": [r[0] for r in cursor.fetchall() if r[0] is not None]}
        finally:
            cursor.close()
            conn.close()
    except Exception as e:
        return {"values": [], "error": str(e)}


# =========================================================
# NEW: CONVERTING
# =========================================================
class ReportRequest(BaseModel):
    params: GlobalParams = GlobalParams()
    limit: int = Field(10, ge=1, le=5000)
    offset: int = Field(0, ge=0)


@app.post("/api/converting/data")
async def converting_data(req: ReportRequest):
    if not CONVERTING_VIEW:
        return {"success": False, "error": "Converting view not configured yet (set CONVERTING_VIEW in main.py)."}
    try:
        where, binds = build_where(req.params, CONVERTING_COLUMNS)
        return query_data_bound(CONVERTING_VIEW, where, binds, req.limit, req.offset)
    except Exception as e:
        return {"success": False, "error": str(e)}


# =========================================================
# UPSTREAM - REPORT 1
# =========================================================
@app.get("/api/filter-values")
async def upstream_r1_filter_values(
    PlantId: Optional[str] = Query(None),
    MaterialId: Optional[str] = Query(None),
    ProductionDateStart: Optional[str] = Query(None),
    ProductionDateEnd: Optional[str] = Query(None),
):
    filters = {}
    if PlantId:
        filters["PlantId"] = PlantId
    if MaterialId:
        filters["MaterialId"] = MaterialId
    if ProductionDateStart:
        filters["ProductionDateStart"] = ProductionDateStart
    if ProductionDateEnd:
        filters["ProductionDateEnd"] = ProductionDateEnd

    result = {}
    table = UPSTREAM_R1_TABLE

    try:
        conn = get_connection()
        cursor = conn.cursor()

        for col in FILTER_COLUMNS_R1:
            where = build_where_upstream_r1(filters, exclude=col)

            if where:
                sql_text = (
                    f"SELECT DISTINCT {col} FROM {table} {where} "
                    f"AND {col} IS NOT NULL ORDER BY {col} LIMIT 200"
                )
            else:
                sql_text = (
                    f"SELECT DISTINCT {col} FROM {table} "
                    f"WHERE {col} IS NOT NULL ORDER BY {col} LIMIT 200"
                )

            try:
                cursor.execute(sql_text)
                result[col] = [str(r[0]) for r in cursor.fetchall() if r[0] is not None]
            except Exception:
                result[col] = []

        cursor.close()
        conn.close()
        return result

    except Exception:
        return {col: [] for col in FILTER_COLUMNS_R1}


@app.get("/api/data")
async def upstream_r1_data(
    PlantId: Optional[str] = Query(None),
    MaterialId: Optional[str] = Query(None),
    ProductionDateStart: Optional[str] = Query(None),
    ProductionDateEnd: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    filters = {}
    if PlantId:
        filters["PlantId"] = PlantId
    if MaterialId:
        filters["MaterialId"] = MaterialId
    if ProductionDateStart:
        filters["ProductionDateStart"] = ProductionDateStart
    if ProductionDateEnd:
        filters["ProductionDateEnd"] = ProductionDateEnd

    try:
        where = build_where_upstream_r1(filters)
        return query_data(UPSTREAM_R1_TABLE, where, limit, offset)
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/export")
async def upstream_r1_export(
    PlantId: Optional[str] = Query(None),
    MaterialId: Optional[str] = Query(None),
    ProductionDateStart: Optional[str] = Query(None),
    ProductionDateEnd: Optional[str] = Query(None),
):
    filters = {}
    if PlantId:
        filters["PlantId"] = PlantId
    if MaterialId:
        filters["MaterialId"] = MaterialId
    if ProductionDateStart:
        filters["ProductionDateStart"] = ProductionDateStart
    if ProductionDateEnd:
        filters["ProductionDateEnd"] = ProductionDateEnd

    try:
        where = build_where_upstream_r1(filters)
        return query_export_csv(UPSTREAM_R1_TABLE, where, "equipment_model.csv")
    except Exception as e:
        return {"success": False, "error": str(e)}


# =========================================================
# UPSTREAM - REPORT 2
# =========================================================
@app.get("/api/order-consumptions/filter-values")
async def upstream_r2_filter_values(
    OrderNumber: Optional[str] = Query(None),
    Component_materialtype: Optional[str] = Query(None),
):
    table = UPSTREAM_R2_TABLE
    try:
        where_order = build_where_report2(order_number=None, component_materialtype=Component_materialtype)
        order_vals = query_distinct_values(table, "OrderNumber", where_order, limit=500)

        where_type = build_where_report2(order_number=OrderNumber, component_materialtype=None)
        type_vals = query_distinct_values(table, "Component_materialtype", where_type, limit=500)

        return {"OrderNumber": order_vals, "Component_materialtype": type_vals}
    except Exception:
        return {"OrderNumber": [], "Component_materialtype": []}


@app.get("/api/data-order-consumptions")
async def upstream_r2_data(
    OrderNumber: Optional[str] = Query(None),
    Component_materialtype: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    try:
        where = build_where_report2(OrderNumber, Component_materialtype)
        return query_data(UPSTREAM_R2_TABLE, where, limit, offset)
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/export-order-consumptions")
async def upstream_r2_export(
    OrderNumber: Optional[str] = Query(None),
    Component_materialtype: Optional[str] = Query(None),
):
    try:
        where = build_where_report2(OrderNumber, Component_materialtype)
        return query_export_csv(UPSTREAM_R2_TABLE, where, "order_consumptions.csv")
    except Exception as e:
        return {"success": False, "error": str(e)}


# =========================================================
# UPSTREAM - REPORT 3
# =========================================================
@app.get("/api/mr-production/filter-values")
async def upstream_r3_filter_values(BatchId: Optional[str] = Query(None)):
    _ = BatchId
    table = UPSTREAM_R3_TABLE
    try:
        batch_vals = query_distinct_values(table, "BatchId", "", limit=500)
        return {"BatchId": batch_vals}
    except Exception:
        return {"BatchId": []}


@app.get("/api/data-mr-production")
async def upstream_r3_data(
    BatchId: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    try:
        where = build_where_report3(BatchId)
        return query_data(UPSTREAM_R3_TABLE, where, limit, offset)
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/export-mr-production")
async def upstream_r3_export(BatchId: Optional[str] = Query(None)):
    try:
        where = build_where_report3(BatchId)
        return query_export_csv(UPSTREAM_R3_TABLE, where, "mr_production.csv")
    except Exception as e:
        return {"success": False, "error": str(e)}


# =========================================================
# DOWNSTREAM - REPORT 1
# =========================================================
@app.get("/api/downstream1/filter-values")
async def downstream1_filter_values(
    PlantId: Optional[str] = Query(None),
    MaterialId: Optional[str] = Query(None),
    ProductionDateStart: Optional[str] = Query(None),
    ProductionDateEnd: Optional[str] = Query(None),
):
    filters = {}
    if PlantId:
        filters["PlantId"] = PlantId
    if MaterialId:
        filters["MaterialId"] = MaterialId
    if ProductionDateStart:
        filters["ProductionDateStart"] = ProductionDateStart
    if ProductionDateEnd:
        filters["ProductionDateEnd"] = ProductionDateEnd

    result = {}
    table = DOWNSTREAM1_TABLE

    try:
        conn = get_connection()
        cursor = conn.cursor()

        for col in FILTER_COLUMNS_R1:
            where = build_where_downstream1(filters, exclude=col)

            if where:
                sql_text = (
                    f"SELECT DISTINCT {col} FROM {table} {where} "
                    f"AND {col} IS NOT NULL ORDER BY {col} LIMIT 200"
                )
            else:
                sql_text = (
                    f"SELECT DISTINCT {col} FROM {table} "
                    f"WHERE {col} IS NOT NULL ORDER BY {col} LIMIT 200"
                )

            try:
                cursor.execute(sql_text)
                result[col] = [str(r[0]) for r in cursor.fetchall() if r[0] is not None]
            except Exception:
                result[col] = []

        cursor.close()
        conn.close()
        return result

    except Exception:
        return {col: [] for col in FILTER_COLUMNS_R1}


@app.get("/api/downstream1/data")
async def downstream1_data(
    PlantId: Optional[str] = Query(None),
    MaterialId: Optional[str] = Query(None),
    ProductionDateStart: Optional[str] = Query(None),
    ProductionDateEnd: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    filters = {}
    if PlantId:
        filters["PlantId"] = PlantId
    if MaterialId:
        filters["MaterialId"] = MaterialId
    if ProductionDateStart:
        filters["ProductionDateStart"] = ProductionDateStart
    if ProductionDateEnd:
        filters["ProductionDateEnd"] = ProductionDateEnd

    try:
        where = build_where_downstream1(filters)
        return query_data(DOWNSTREAM1_TABLE, where, limit, offset)
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/downstream1/export")
async def downstream1_export(
    PlantId: Optional[str] = Query(None),
    MaterialId: Optional[str] = Query(None),
    ProductionDateStart: Optional[str] = Query(None),
    ProductionDateEnd: Optional[str] = Query(None),
):
    filters = {}
    if PlantId:
        filters["PlantId"] = PlantId
    if MaterialId:
        filters["MaterialId"] = MaterialId
    if ProductionDateStart:
        filters["ProductionDateStart"] = ProductionDateStart
    if ProductionDateEnd:
        filters["ProductionDateEnd"] = ProductionDateEnd

    try:
        where = build_where_downstream1(filters)
        return query_export_csv(DOWNSTREAM1_TABLE, where, "downstream1.csv")
    except Exception as e:
        return {"success": False, "error": str(e)}


# =========================================================
# DOWNSTREAM - REPORT 2 (RawMaterialBatchId only)
# =========================================================
@app.get("/api/downstream2/filter-values")
async def downstream2_filter_values(RawMaterialBatchId: Optional[str] = Query(None)):
    table = DOWNSTREAM2_TABLE
    try:
        _ = RawMaterialBatchId
        vals = query_distinct_values(table, "RawMaterialBatchId", "", limit=500)
        return {"RawMaterialBatchId": vals}
    except Exception:
        return {"RawMaterialBatchId": []}


@app.get("/api/downstream2/data")
async def downstream2_data(
    RawMaterialBatchId: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    try:
        conditions = []
        if RawMaterialBatchId:
            conditions.append(f"LOWER(RawMaterialBatchId) = LOWER('{esc(RawMaterialBatchId)}')")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        return query_data(DOWNSTREAM2_TABLE, where, limit, offset)
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/downstream2/export")
async def downstream2_export(RawMaterialBatchId: Optional[str] = Query(None)):
    try:
        conditions = []
        if RawMaterialBatchId:
            conditions.append(f"LOWER(RawMaterialBatchId) = LOWER('{esc(RawMaterialBatchId)}')")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        return query_export_csv(DOWNSTREAM2_TABLE, where, "downstream2.csv")
    except Exception as e:
        return {"success": False, "error": str(e)}


# =========================================================
# DOWNSTREAM - REPORT 3 (Product Complaints / Notifications)
# =========================================================
@app.get("/api/downstream3/filter-values")
async def downstream3_filter_values(
    PlantId: Optional[str] = Query(None),
    MaterialId: Optional[str] = Query(None),
    Notification: Optional[str] = Query(None),
    NotificationDateStart: Optional[str] = Query(None),
    NotificationDateEnd: Optional[str] = Query(None),
):
    filters = {}
    if PlantId:
        filters["PlantId"] = PlantId
    if MaterialId:
        filters["MaterialId"] = MaterialId
    if Notification:
        filters["Notification"] = Notification
    if NotificationDateStart:
        filters["NotificationDateStart"] = NotificationDateStart
    if NotificationDateEnd:
        filters["NotificationDateEnd"] = NotificationDateEnd

    result = {}
    table = DOWNSTREAM3_TABLE

    try:
        conn = get_connection()
        cursor = conn.cursor()

        for col in FILTER_COLUMNS_D3:
            where = build_where_downstream3(filters, exclude=col)

            if where:
                sql_text = (
                    f"SELECT DISTINCT {col} FROM {table} {where} "
                    f"AND {col} IS NOT NULL ORDER BY {col} LIMIT 200"
                )
            else:
                sql_text = (
                    f"SELECT DISTINCT {col} FROM {table} "
                    f"WHERE {col} IS NOT NULL ORDER BY {col} LIMIT 200"
                )

            try:
                cursor.execute(sql_text)
                result[col] = [str(r[0]) for r in cursor.fetchall() if r[0] is not None]
            except Exception:
                result[col] = []

        cursor.close()
        conn.close()
        return result

    except Exception:
        return {col: [] for col in FILTER_COLUMNS_D3}


@app.get("/api/downstream3/data")
async def downstream3_data(
    PlantId: Optional[str] = Query(None),
    MaterialId: Optional[str] = Query(None),
    Notification: Optional[str] = Query(None),
    NotificationDateStart: Optional[str] = Query(None),
    NotificationDateEnd: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    filters = {}
    if PlantId:
        filters["PlantId"] = PlantId
    if MaterialId:
        filters["MaterialId"] = MaterialId
    if Notification:
        filters["Notification"] = Notification
    if NotificationDateStart:
        filters["NotificationDateStart"] = NotificationDateStart
    if NotificationDateEnd:
        filters["NotificationDateEnd"] = NotificationDateEnd

    try:
        where = build_where_downstream3(filters)
        return query_data(DOWNSTREAM3_TABLE, where, limit, offset)
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/downstream3/export")
async def downstream3_export(
    PlantId: Optional[str] = Query(None),
    MaterialId: Optional[str] = Query(None),
    Notification: Optional[str] = Query(None),
    NotificationDateStart: Optional[str] = Query(None),
    NotificationDateEnd: Optional[str] = Query(None),
):
    filters = {}
    if PlantId:
        filters["PlantId"] = PlantId
    if MaterialId:
        filters["MaterialId"] = MaterialId
    if Notification:
        filters["Notification"] = Notification
    if NotificationDateStart:
        filters["NotificationDateStart"] = NotificationDateStart
    if NotificationDateEnd:
        filters["NotificationDateEnd"] = NotificationDateEnd

    try:
        where = build_where_downstream3(filters)
        return query_export_csv(DOWNSTREAM3_TABLE, where, "downstream3.csv")
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)