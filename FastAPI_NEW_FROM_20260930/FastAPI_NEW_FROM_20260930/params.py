"""
Global query parameters: validation model + WHERE-clause builder.
Pure functions (no DB access), so every report reuses the same filter logic.

Range rule: value V with lower L / upper U  ->  (V - L) <= column <= (V + U)
"""

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class RangeField(BaseModel):
    values: List[str] = []
    lower: Optional[int] = Field(None, ge=0)
    upper: Optional[int] = Field(None, ge=0)


class GlobalParams(BaseModel):
    plant_id: Optional[str] = None                      # NEW
    zfin_base_material_id: RangeField = RangeField()
    zfin_material_id: RangeField = RangeField()
    zfin_order: RangeField = RangeField()
    zfin_batch_id: RangeField = RangeField()
    zfin_sscc: RangeField = RangeField()
    production_line: Optional[str] = None
    production_datetime_from: Optional[str] = None   # e.g. 2026-09-21T15:00
    production_datetime_to: Optional[str] = None


RANGE_FIELDS = [
    "zfin_base_material_id",
    "zfin_material_id",
    "zfin_order",
    "zfin_batch_id",
    "zfin_sscc",
]


def build_where(params: GlobalParams, column_map: Dict[str, str]) -> Tuple[str, Dict[str, Any]]:
    """
    column_map: parameter key -> column name of THIS report's view.
    Parameters missing from the map are ignored, so a report can use a subset.
    Column names come from code constants (never from the user); all values are bound.
    Returns (where_sql, binds) for cursor.execute(sql, binds).
    """
    conditions: List[str] = []
    binds: Dict[str, Any] = {}
    n = 0

    col = column_map.get("plant_id")                    # NEW
    if col and params.plant_id:
        binds["plant"] = params.plant_id
        conditions.append(f"{col} = :plant")

    for key in RANGE_FIELDS:
        col = column_map.get(key)
        field: RangeField = getattr(params, key)
        if not col or not field.values:
            continue

        ors = []
        for raw in field.values:
            v = raw.strip()
            if not v:
                continue
            n += 1
            if (field.lower or field.upper) and v.isdigit():
                binds[f"lo{n}"] = int(v) - (field.lower or 0)
                binds[f"hi{n}"] = int(v) + (field.upper or 0)
                ors.append(f"TRY_CAST({col} AS BIGINT) BETWEEN :lo{n} AND :hi{n}")
            else:  # exact match (also the fallback for non-numeric values)
                binds[f"v{n}"] = v
                ors.append(f"CAST({col} AS STRING) = :v{n}")
        if ors:
            conditions.append("(" + " OR ".join(ors) + ")")

    col = column_map.get("production_line")
    if col and params.production_line:
        binds["line"] = params.production_line
        conditions.append(f"{col} = :line")

    col = column_map.get("production_datetime")
    if col and params.production_datetime_from:
        binds["dt_from"] = params.production_datetime_from
        conditions.append(f"{col} >= CAST(:dt_from AS TIMESTAMP)")
    if col and params.production_datetime_to:
        binds["dt_to"] = params.production_datetime_to
        conditions.append(f"{col} <= CAST(:dt_to AS TIMESTAMP)")

    return ("WHERE " + " AND ".join(conditions)) if conditions else "", binds