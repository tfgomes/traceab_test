"""
Converting report configuration + SQL builder.

Goal
----
- Keep filter/missing-field logic in one place.
- Build safe SQL using named bind parameters.
- Make report behavior easy to change by editing REPORTS + per-report rules.

Important
---------
- Column names in SQL are controlled by this file, not by user input.
- User-entered values are always bound parameters.
"""

from datetime import date
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# Input models
# -----------------------------------------------------------------------------
class MultiValue(BaseModel):
    """
    Multi-value text control (chip-style in UI).
    """
    values: List[str] = Field(default_factory=list)


class GlobalParams(BaseModel):
    """
    All global parameters currently supported by Converting.

    Sections:
    - Existing Converting fields
    - Placeholder sections A and B for future expansion
    """
    # Converting section (active)
    plant_id: Optional[str] = None
    production_line: Optional[str] = None
    zfin_material_id: MultiValue = Field(default_factory=MultiValue)
    zfin_order: MultiValue = Field(default_factory=MultiValue)
    zfin_batch_id: MultiValue = Field(default_factory=MultiValue)
    zfin_sscc: MultiValue = Field(default_factory=MultiValue)
    production_date_from: Optional[date] = None
    production_date_to: Optional[date] = None

    # Optional filters for reports 3 and 4 (kept dormant unless UI sends values)
    consumed_date_from: Optional[date] = None
    consumed_date_to: Optional[date] = None
    order_material_id: MultiValue = Field(default_factory=MultiValue)
    consumed_material_type_id: MultiValue = Field(default_factory=MultiValue)
    consumed_material_id: MultiValue = Field(default_factory=MultiValue)

    # Placeholder section A (future)
    section_a_text_1: Optional[str] = None
    section_a_text_2: Optional[str] = None

    # Placeholder section B (future)
    section_b_text_1: Optional[str] = None
    section_b_text_2: Optional[str] = None


# -----------------------------------------------------------------------------
# Report catalog used by frontend and backend
# -----------------------------------------------------------------------------
REPORTS = {
    "orders": {
        "badge": "1",
        "title": "Production Orders and Batches",
        "view": "traceability_poc.v_ZFIN_ProdOrders",
        "order_by": "ConvertingProductionLine, ProdStartDateTime",
    },
    "pallets": {
        "badge": "2",
        "title": "Produced pallets",
        "view": "traceability_poc.v_ZFIN_SSCC",
        "order_by": None,
    },
    "summary": {
        "badge": "3",
        "title": "Material consumption summary",
        "view": "traceability_poc.v_SALCONS_summary",
        "order_by": "ConsumedMaterialTypeId, ConsumedMaterialText, ConsumedDate",
    },
    "details": {
        "badge": "4",
        "title": "Material consumption batches and handling units",
        "view": "traceability_poc.v_SALCONS_details",
        "order_by": "ConsumedMaterialTypeId, ConsumedMaterialText, ConsumptionEndDate, ConsumptionEndTime",
    },
}


class MissingParameters(ValueError):
    """
    Raised when mandatory fields for a report are not satisfied.
    """
    pass


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------
def values(field: MultiValue, min_length: int = 0) -> List[str]:
    """
    Normalize chip values:
    - trim
    - unique preserving order
    - optional minimum length
    """
    result: List[str] = []
    seen = set()

    for item in field.values:
        if not isinstance(item, str):
            continue
        v = item.strip()
        if len(v) < min_length:
            continue
        if v in seen:
            continue
        seen.add(v)
        result.append(v)

    return result


def validate_dates(p: GlobalParams) -> None:
    """
    Validate FROM/TO pairs used by SQL.
    """
    pairs = [
        (p.production_date_from, p.production_date_to, "Production dates"),
        (p.consumed_date_from, p.consumed_date_to, "Consumed dates"),
    ]
    for start, end, label in pairs:
        if start and end and start > end:
            raise ValueError(f"{label}: FROM cannot be after TO.")


def missing_for(report_id: str, p: GlobalParams) -> List[str]:
    """
    Mandatory field rules by report.
    """
    if report_id not in REPORTS:
        raise KeyError(report_id)

    missing = []

    if not (p.plant_id or "").strip():
        missing.append("Plant ID")

    # 1,3,4 require production line
    if report_id in ("orders", "summary", "details") and not (p.production_line or "").strip():
        missing.append("Production line")

    # 2 requires (SSCC >=3 chars OR order)
    if report_id == "pallets":
        has_sscc = bool(values(p.zfin_sscc, 3))
        has_order = bool(values(p.zfin_order, 1))
        if not (has_sscc or has_order):
            missing.append("Produced SSCC (at least 3 characters) or Order number")

    # 4 requires order
    if report_id == "details" and not values(p.zfin_order, 1):
        missing.append("Order number")

    return missing


# -----------------------------------------------------------------------------
# SQL builder
# -----------------------------------------------------------------------------
def build_report_query(
    report_id: str,
    p: GlobalParams,
    limit: int,
    offset: int,
    export: bool = False,
) -> Tuple[str, Dict[str, object]]:
    """
    Build SQL + bind parameters for one report.
    """
    if report_id not in REPORTS:
        raise KeyError(report_id)

    if not 1 <= limit <= 5000:
        raise ValueError("Invalid page size.")
    if offset < 0:
        raise ValueError("Invalid offset.")

    validate_dates(p)

    missing = missing_for(report_id, p)
    if missing:
        raise MissingParameters(
            "Mandatory field(s) missing: "
            + ", ".join(missing)
            + ". Please fill them in on the Global Parameters page."
        )

    spec = REPORTS[report_id]
    conditions = []
    binds: Dict[str, object] = {}

    # Shared base mandatory filter
    conditions.append("PlantId = :plant")
    binds["plant"] = p.plant_id.strip()

    # Shared line filter for reports 1,3,4
    if report_id in ("orders", "summary", "details"):
        conditions.append("ConvertingProductionLine = :line")
        binds["line"] = p.production_line.strip()

    def add_values(column: str, field: MultiValue, key: str, minimum: int = 0) -> bool:
        selected = values(field, minimum)
        if not selected:
            return False

        marks = []
        for idx, val in enumerate(selected):
            name = f"{key}_{idx}"
            binds[name] = val
            marks.append(f":{name}")

        conditions.append(f"{column} IN ({', '.join(marks)})")
        return True

    def add_date(column: str, day: Optional[date], operator: str, key: str) -> None:
        if day is None:
            return
        binds[key] = day.isoformat()
        conditions.append(f"DATE({column}) {operator} CAST(:{key} AS DATE)")

    # Report-specific optional filters
    if report_id == "orders":
        # Keep previous threshold behavior
        add_values("MaterialId", p.zfin_material_id, "material", 4)
        add_values("OrderNumber", p.zfin_order, "order", 4)
        add_values("BatchId", p.zfin_batch_id, "batch", 4)
        add_date("ProdStartDate", p.production_date_from, ">=", "prod_from")
        add_date("ProdEndDate", p.production_date_to, "<=", "prod_to")

    elif report_id == "pallets":
        # SSCC has precedence if present with min length 3
        if not add_values("SSCC", p.zfin_sscc, "sscc", 3):
            add_values("OrderNumber", p.zfin_order, "order", 1)

    elif report_id in ("summary", "details"):
        add_date("ConsumptionEndDate", p.consumed_date_from, ">=", "cons_from")
        add_date("ConsumptionEndDate", p.consumed_date_to, "<=", "cons_to")

        if report_id == "details":
            # mandatory already validated; include exact values
            add_values("OrderNumber", p.zfin_order, "order", 1)
        else:
            # optional threshold for summary
            add_values("OrderNumber", p.zfin_order, "order", 3)

        # dormant optional filters (only applied if UI sends values)
        add_values("OrderMaterialId", p.order_material_id, "order_material", 3)
        add_values("ConsumedMaterialTypeId", p.consumed_material_type_id, "cons_type", 3)
        add_values("ConsumedMaterialId", p.consumed_material_id, "cons_mat", 3)

    if len(binds) > 250:
        raise ValueError("Too many selected values; reduce filters and retry.")

    query = f"SELECT * FROM {spec['view']} WHERE " + " AND ".join(conditions)

    if spec["order_by"]:
        query += f" ORDER BY {spec['order_by']}"

    if export:
        # fetch one extra row to detect oversized export
        query += " LIMIT 100001"
    else:
        query += f" LIMIT {limit + 1} OFFSET {offset}"

    return query, binds