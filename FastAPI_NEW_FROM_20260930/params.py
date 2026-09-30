"""Validated Converting parameters and report-specific SQL builders.

Only identifiers defined in REPORTS are interpolated into SQL. All user values
are passed separately as Databricks SQL Connector named parameters.
"""

from datetime import date
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


class MultiValue(BaseModel):
    # Old sessionStorage objects may contain lower/upper. Pydantic ignores them;
    # ranges are never used to build SQL.
    values: List[str] = Field(default_factory=list)


class GlobalParams(BaseModel):
    plant_id: Optional[str] = None
    production_line: Optional[str] = None
    zfin_material_id: MultiValue = Field(default_factory=MultiValue)
    zfin_order: MultiValue = Field(default_factory=MultiValue)
    zfin_batch_id: MultiValue = Field(default_factory=MultiValue)
    zfin_sscc: MultiValue = Field(default_factory=MultiValue)

    production_date_from: Optional[date] = None
    production_date_to: Optional[date] = None

    # Optional filters for reports 3 and 4.
    # These can stay unset if the Global Parameters UI does not expose them.
    consumed_date_from: Optional[date] = None
    consumed_date_to: Optional[date] = None
    order_material_id: MultiValue = Field(default_factory=MultiValue)
    consumed_material_type_id: MultiValue = Field(default_factory=MultiValue)
    consumed_material_id: MultiValue = Field(default_factory=MultiValue)


REPORTS = {
    "orders": {
        "title": "Production Orders and Batches",
        "view": "traceability_poc.v_ZFIN_ProdOrders",
        "order_by": "ConvertingProductionLine, ProdStartDateTime",
    },
    "pallets": {
        "title": "Produced pallets",
        "view": "traceability_poc.v_ZFIN_SSCC",
        "order_by": None,
    },
    "summary": {
        "title": "Material consumption summary",
        "view": "traceability_poc.v_SALCONS_summary",
        "order_by": "ConsumedMaterialTypeId, ConsumedMaterialText, ConsumedDate",
    },
    "details": {
        "title": "Material consumption batches and handling units",
        "view": "traceability_poc.v_SALCONS_details",
        "order_by": "ConsumedMaterialTypeId, ConsumedMaterialText, ConsumedDate, ConsumedTime",
    },
}


class MissingParameters(ValueError):
    pass


def values(field: MultiValue, min_length: int = 0) -> List[str]:
    """Exact string values, preserving leading zeroes; no numeric ranges."""
    return list(dict.fromkeys(
        item.strip()
        for item in field.values
        if isinstance(item, str) and len(item.strip()) >= min_length
    ))


def validate_dates(p: GlobalParams) -> None:
    for start, end, label in (
        (p.production_date_from, p.production_date_to, "Production dates"),
        (p.consumed_date_from, p.consumed_date_to, "Consumed dates"),
    ):
        if start and end and start > end:
            raise ValueError(f"{label}: FROM cannot be after TO.")


def missing_for(report_id: str, p: GlobalParams) -> List[str]:
    if report_id not in REPORTS:
        raise KeyError(report_id)

    missing = []

    if not (p.plant_id or "").strip():
        missing.append("Plant ID")

    if report_id in ("orders", "summary", "details") and not (p.production_line or "").strip():
        missing.append("Production line")

    # Report 2 requires Plant + (valid SSCC OR order number)
    if report_id == "pallets" and not values(p.zfin_sscc, 3) and not values(p.zfin_order):
        missing.append("Produced SSCC (at least 3 characters) or Order number")

    # Report 4 requires Plant + Line + OrderNumber
    if report_id == "details" and not values(p.zfin_order):
        missing.append("Order number")

    return missing


def build_report_query(
    report_id: str,
    p: GlobalParams,
    limit: int,
    offset: int,
    export: bool = False,
) -> Tuple[str, Dict[str, object]]:
    """Return (SQL, bound parameters). Validate BEFORE connecting to Databricks."""
    if report_id not in REPORTS:
        raise KeyError(report_id)

    if not 1 <= limit <= 5000 or offset < 0:
        raise ValueError("Invalid page size or offset.")

    validate_dates(p)

    missing = missing_for(report_id, p)
    if missing:
        raise MissingParameters(
            "Mandatory field(s) missing: " + ", ".join(missing)
            + ". Please fill them in on the Global Parameters page."
        )

    spec = REPORTS[report_id]
    conditions = ["PlantId = :plant"]
    binds: Dict[str, object] = {"plant": p.plant_id.strip()}

    if report_id in ("orders", "summary", "details"):
        conditions.append("ConvertingProductionLine = :line")
        binds["line"] = p.production_line.strip()

    def add_values(column: str, field: MultiValue, key: str, minimum: int = 0) -> bool:
        selected = values(field, minimum)
        if not selected:
            return False

        names = []
        for index, value in enumerate(selected):
            name = f"{key}_{index}"
            binds[name] = value
            names.append(f":{name}")

        conditions.append(f"{column} IN ({', '.join(names)})")
        return True

    def add_date(column: str, day: Optional[date], operator: str, key: str) -> None:
        if day is not None:
            binds[key] = day.isoformat()
            conditions.append(f"DATE({column}) {operator} CAST(:{key} AS DATE)")

    if report_id == "orders":
        # len(value) > 3 rules from your SQL
        add_values("MaterialId", p.zfin_material_id, "material", 4)
        add_values("OrderNumber", p.zfin_order, "order", 4)
        add_values("BatchId", p.zfin_batch_id, "batch", 4)
        add_date("ProdStartDate", p.production_date_from, ">=", "prod_from")
        add_date("ProdEndDate", p.production_date_to, "<=", "prod_to")

    elif report_id == "pallets":
        # SSCC precedence: if valid SSCC exists, ignore order fallback.
        if not add_values("SSCC", p.zfin_sscc, "sscc", 3):
            add_values("OrderNumber", p.zfin_order, "order")

    elif report_id in ("summary", "details"):
        add_date("ConsumedDate", p.consumed_date_from, ">=", "cons_from")
        add_date("ConsumedDate", p.consumed_date_to, "<=", "cons_to")

        if report_id == "details":
            # Mandatory order for details: any nonblank value qualifies.
            add_values("OrderNumber", p.zfin_order, "order")
        else:
            # Optional for summary, len(value) > 2
            add_values("OrderNumber", p.zfin_order, "order", 3)

        add_values("OrderMaterialId", p.order_material_id, "order_material", 3)
        add_values("ConsumedMaterialTypeId", p.consumed_material_type_id, "type", 3)
        add_values("ConsumedMaterialId", p.consumed_material_id, "cons_material", 3)

    # Keep marker count comfortably below connector constraint.
    if len(binds) > 250:
        raise ValueError("Too many selected values; reduce the filters and retry.")

    query = f"SELECT * FROM {spec['view']} WHERE " + " AND ".join(conditions)

    if spec["order_by"]:
        query += f" ORDER BY {spec['order_by']}"

    if export:
        # One extra row so API can reject oversize export cleanly.
        query += " LIMIT 100001"
    else:
        query += f" LIMIT {limit + 1} OFFSET {offset}"

    return query, binds