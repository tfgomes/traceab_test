"""
Sprint 3 configuration-driven report query engine.

Context-aware parameter binding:
- For temporal columns (date/time/datetime), ParameterID is treated as lower bound,
  ParameterIDmax as upper bound.
- For non-temporal columns, ParameterID/ParameterIDmax are treated as value lists
  only if they map to list-type global parameters.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from pydantic import BaseModel, Field


APP_VERSION = "Sprint 3.0.1"
REPORT_SCHEMA = "traceability_poc"
MAX_EXPORT_ROWS = 100000
MAX_PARAMETER_VALUES = 350
MAX_QUERY_NODES = 128

MATERIAL_TYPES = ("HALB", "ROH", "ZFIN", "ZADN", "ZVERP")

SyncMode = Literal["unsync", "sync"]
FlowDirection = Literal["up", "down"]


class ConfigurationError(ValueError):
    pass


class MissingParameters(ValueError):
    pass


class MultiValue(BaseModel):
    values: List[str] = Field(default_factory=list)


class GlobalParams(BaseModel):
    active_profile: str = "cv_prod"
    plant_code_name: Optional[str] = None

    converting_production_line: MultiValue = Field(default_factory=MultiValue)
    zfin_material_id: MultiValue = Field(default_factory=MultiValue)
    zfin_order: MultiValue = Field(default_factory=MultiValue)
    zfin_batch_id: MultiValue = Field(default_factory=MultiValue)
    zfin_sscc: MultiValue = Field(default_factory=MultiValue)
    production_date_from: Optional[date] = None
    production_date_to: Optional[date] = None
    production_time_from: Optional[str] = None
    production_time_to: Optional[str] = None

    consumed_material_type_id: MultiValue = Field(default_factory=MultiValue)
    consumed_material_id: MultiValue = Field(default_factory=MultiValue)
    consumed_batch_id: MultiValue = Field(default_factory=MultiValue)
    consumed_sscc: MultiValue = Field(default_factory=MultiValue)
    consumed_date_from: Optional[date] = None
    consumed_date_to: Optional[date] = None
    consumed_time_from: Optional[str] = None
    consumed_time_to: Optional[str] = None

    cmb_line: MultiValue = Field(default_factory=MultiValue)
    cmb_material_id: MultiValue = Field(default_factory=MultiValue)
    cmb_order_number: MultiValue = Field(default_factory=MultiValue)
    cmb_reel_batch_id: MultiValue = Field(default_factory=MultiValue)
    cmb_produced_sscc: MultiValue = Field(default_factory=MultiValue)
    cmb_prod_date_from: Optional[date] = None
    cmb_prod_date_to: Optional[date] = None
    cmb_prod_time_from: Optional[str] = None
    cmb_prod_time_to: Optional[str] = None

    pm_line: MultiValue = Field(default_factory=MultiValue)
    pm_material_id: MultiValue = Field(default_factory=MultiValue)
    pm_order_number: MultiValue = Field(default_factory=MultiValue)
    pm_reel_batch_id: MultiValue = Field(default_factory=MultiValue)
    pm_produced_sscc: MultiValue = Field(default_factory=MultiValue)
    pm_prod_date_from: Optional[date] = None
    pm_prod_date_to: Optional[date] = None
    pm_prod_time_from: Optional[str] = None
    pm_prod_time_to: Optional[str] = None

    class Config:
        extra = "forbid"


@dataclass(frozen=True)
class ParameterBinding:
    key: str
    label: str
    kind: str
    time_key: Optional[str] = None


# Canonical IDs mapped by semantics where possible.
# Ambiguous IDs are resolved contextually in resolve_binding_for_column().
PARAMETERS = {
    "cv-p1": ParameterBinding("plant_code_name", "Plant", "plant"),
    "cv-p2": ParameterBinding("converting_production_line", "Converting Production Line", "multi"),
    "cv-p3": ParameterBinding("zfin_material_id", "Produced Material ID", "multi"),
    "cv-p4": ParameterBinding("zfin_order", "Order Number", "multi"),
    "cv-p5": ParameterBinding("zfin_batch_id", "Produced Batch ID", "multi"),
    "cv-p6": ParameterBinding("zfin_sscc", "Produced SSCC", "multi"),
    "cv-p7": ParameterBinding("production_date_from", "Production FROM", "from", "production_time_from"),
    "cv-p8": ParameterBinding("production_date_to", "Production TO", "to", "production_time_to"),
    "cv-p9": ParameterBinding("consumed_material_type_id", "Consumed Material Type", "multi"),
    "cv-p10": ParameterBinding("consumed_material_id", "Consumed Material ID", "multi"),
    "cv-p11": ParameterBinding("consumed_batch_id", "Consumed Batch ID", "multi"),

    # cv-p12 is reused in your config (SSCC and date-range lower bound).
    # Keep as SSCC here; temporal usage resolved by column context.
    "cv-p12": ParameterBinding("consumed_sscc", "Consumed SSCC", "multi"),
    "cv-p13": ParameterBinding("consumed_date_from", "Consumption FROM", "from", "consumed_time_from"),
    "cv-p14": ParameterBinding("consumed_date_to", "Consumption TO", "to", "consumed_time_to"),

    "pm-p1": ParameterBinding("plant_code_name", "Plant", "plant"),
    "pm-p2": ParameterBinding("pm_line", "PM Production Line", "multi"),
    "pm-p3": ParameterBinding("pm_material_id", "PM Produced Material ID", "multi"),
    "pm-p4": ParameterBinding("pm_order_number", "PM Order Number", "multi"),
    "pm-p5": ParameterBinding("pm_reel_batch_id", "PM Produced Batch ID", "multi"),
    "pm-p6": ParameterBinding("pm_produced_sscc", "PM Produced SSCC", "multi"),
    "pm-p7": ParameterBinding("pm_prod_date_from", "PM Production FROM", "from", "pm_prod_time_from"),
    "pm-p8": ParameterBinding("pm_prod_date_to", "PM Production TO", "to", "pm_prod_time_to"),

    "pm-p9": ParameterBinding("cmb_line", "Combiner Production Line", "multi"),
    "pm-p10": ParameterBinding("cmb_material_id", "CMB Produced Material ID", "multi"),
    "pm-p11": ParameterBinding("cmb_order_number", "CMB Order Number", "multi"),
    "pm-p12": ParameterBinding("cmb_reel_batch_id", "CMB Produced Batch ID", "multi"),
    "pm-p13": ParameterBinding("cmb_produced_sscc", "CMB Produced SSCC", "multi"),
    "pm-p14": ParameterBinding("cmb_prod_date_from", "CMB Production FROM", "from", "cmb_prod_time_from"),
    "pm-p15": ParameterBinding("cmb_prod_date_to", "CMB Production TO", "to", "cmb_prod_time_to"),
}

REPORT_PRESENTATION = {
    "cv-1": ("summary", "Material consumption summary"),
    "cv-2": ("details", "Material consumption batches and handling units"),
    "cv-3": ("orders", "Production Orders and Batches"),
    "cv-4": ("pallets", "Produced Handling Units"),
    "pm-2": ("pm_report_3", "Paper machine production order summary"),
    "pm-3": ("pm_report_4", "Paper machine production batch details"),
    "pm-4": ("cmb_summary", "Combiner production order summary"),
    "pm-5": ("cmb_report_2", "Combiner production batch details"),
}

DIMENSIONS = {
    "cv": "dim_converting_productionline",
    "pm": "dim_PM_productionline",
    "cmb": "dim_CMB_productionline",
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ConfigurationError(f"Invalid SQL identifier: {value!r}")
    return f"`{value}`"


def qualified_table(value: str, catalog: Optional[str] = None) -> str:
    parts = value.split(".")
    if len(parts) == 1:
        parts = [REPORT_SCHEMA, parts[0]]
    elif len(parts) not in (2, 3):
        raise ConfigurationError(f"Invalid table name: {value!r}")

    if parts[-2].casefold() != REPORT_SCHEMA.casefold():
        raise ConfigurationError(f"Only schema {REPORT_SCHEMA!r} is allowed: {value!r}")

    if catalog:
        identifier(catalog)
        if len(parts) == 2:
            parts.insert(0, catalog)
        elif parts[0].casefold() != catalog.casefold():
            raise ConfigurationError(f"Table {value!r} is outside catalog {catalog!r}.")

    return ".".join(identifier(part) for part in parts)


def selected_values(field: MultiValue) -> List[str]:
    return list(dict.fromkeys(v.strip() for v in field.values if v.strip()))


def normalize_time(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("Time must use HH:MM format.") from exc
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise ValueError("Use local time in HH:MM format.")
    return parsed.strftime("%H:%M")


def validate_params(p: GlobalParams) -> None:
    count = 0
    for binding in PARAMETERS.values():
        if binding.kind == "multi":
            count += len(selected_values(getattr(p, binding.key)))
    if count > MAX_PARAMETER_VALUES:
        raise ValueError(f"Too many selected values. Maximum is {MAX_PARAMETER_VALUES}.")

    invalid_types = set(selected_values(p.consumed_material_type_id)) - set(MATERIAL_TYPES)
    if invalid_types:
        raise ValueError("Consumed Material Type must be one of: " + ", ".join(MATERIAL_TYPES))

    for prefix, label in (
        ("production", "Converting production"),
        ("consumed", "Converting consumption"),
        ("cmb_prod", "Combiner production"),
        ("pm_prod", "Papermaking production"),
    ):
        day_from = getattr(p, f"{prefix}_date_from")
        day_to = getattr(p, f"{prefix}_date_to")
        clock_from = normalize_time(getattr(p, f"{prefix}_time_from"))
        clock_to = normalize_time(getattr(p, f"{prefix}_time_to"))

        if day_from and day_to:
            start = datetime.combine(day_from, time.fromisoformat(clock_from or "00:00"))
            end = datetime.combine(day_to, time.fromisoformat(clock_to or "23:59"))
            if start > end:
                raise ValueError(f"{label}: FROM cannot be after TO.")
        elif not day_from and not day_to:
            if clock_from and clock_to and clock_from > clock_to:
                raise ValueError(f"{label}: FROM cannot be after TO.")


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    result = str(value).strip()
    if not result or result.casefold() in ("null", "none"):
        return None
    return result


def _flag(value: Any) -> bool:
    return (_text(value) or "").casefold() in ("1", "1.0", "true", "yes")


def _links(value: Any) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in (_text(value) or "").split(",") if part.strip()))


def _datatype(value: Any) -> str:
    result = (_text(value) or "string").casefold()
    if result in ("datetim", "timestamp"):
        return "datetime"
    return result


@dataclass(frozen=True)
class ColumnSpec:
    field_id: str
    page: str
    section: str
    table: str
    column: str
    label: str
    datatype: str
    parameter_id: Optional[str]
    parameter_max: Optional[str]
    mandatory: bool
    up: Tuple[str, ...]
    down: Tuple[str, ...]
    show: bool


@dataclass(frozen=True)
class ReportSpec:
    page: str
    section: str
    report_id: str
    title: str
    table: str
    dimension: str
    columns: Tuple[ColumnSpec, ...]


class ReportCatalog:
    def __init__(self, records: List[Mapping[str, Any]], catalog: Optional[str] = None):
        self.catalog = catalog
        self.reports: Dict[str, ReportSpec] = {}
        self.fields: Dict[str, List[ColumnSpec]] = defaultdict(list)
        self.warnings: List[str] = []

        grouped = defaultdict(list)
        required_headers = {
            "fieldid", "page", "section", "table", "column",
            "parameterid", "parameteridmax", "mandatory", "show",
        }

        for record in records:
            normalized = {re.sub(r"[^a-z0-9]", "", str(k).casefold()): value for k, value in record.items()}
            if "fieldid" not in normalized and "fileldid" in normalized:
                normalized["fieldid"] = normalized["fileldid"]

            missing = required_headers - set(normalized)
            if missing:
                raise ConfigurationError("Configuration is missing columns: " + ", ".join(sorted(missing)))

            def get(*names):
                for n in names:
                    if n in normalized:
                        return normalized[n]
                return None

            field_id = _text(get("fieldid"))
            page = (_text(get("page")) or "").casefold()
            section = _text(get("section"))
            table = _text(get("table"))
            column = _text(get("column"))

            if page not in ("converting", "papermaking"):
                raise ConfigurationError(f"Unknown page: {page!r}")
            if not all((field_id, section, table, column)):
                raise ConfigurationError("Each row must have FieldID, Section, Table, Column.")

            identifier(column)
            table_sql = qualified_table(table, catalog)

            spec = ColumnSpec(
                field_id=field_id,
                page=page,
                section=section,
                table=table_sql,
                column=column,
                label=_text(get("name")) or column,
                datatype=_datatype(get("datatype")),
                parameter_id=_text(get("parameterid")),
                parameter_max=_text(get("parameteridmax")),
                mandatory=_flag(get("mandatory")),
                up=_links(get("upsychwith", "upsynchwith", "upsyncwith")),
                down=_links(get("downsynchwith", "downsyncwith")),
                show=_flag(get("show")),
            )
            grouped[section].append(spec)
            self.fields[field_id].append(spec)

        if not grouped:
            raise ConfigurationError("Configuration table is empty.")

        for section, cols in sorted(grouped.items()):
            pages = {c.page for c in cols}
            tables = {c.table for c in cols}
            if len(pages) != 1 or len(tables) != 1:
                raise ConfigurationError(f"{section}: section must map to one page and one table.")
            if not any(c.show for c in cols):
                raise ConfigurationError(f"{section}: no columns with Show=1.")

            duplicates = [n for n, ct in Counter(c.column.casefold() for c in cols).items() if ct > 1]
            if duplicates:
                raise ConfigurationError(f"{section}: duplicate columns: {', '.join(duplicates)}")

            first = cols[0]
            report_id, title = REPORT_PRESENTATION.get(section, (section, section))
            table_name = first.table.casefold()
            family = "cv" if first.page == "converting" else ("cmb" if "cmb" in table_name else "pm")

            self.reports[section] = ReportSpec(
                page=first.page,
                section=section,
                report_id=report_id,
                title=title,
                table=first.table,
                dimension=qualified_table(DIMENSIONS[family], catalog),
                columns=tuple(cols),
            )

        for fid, matches in self.fields.items():
            if len(matches) > 1:
                self.warnings.append(f"Duplicate FieldID {fid!r}; cannot be used safely as sync source.")

    def resolve(self, page: str, report_id: str) -> ReportSpec:
        for report in self.reports.values():
            if report.page == page and report_id in (report.section, report.report_id):
                return report
        raise KeyError(report_id)

    def source(self, field_id: str) -> ColumnSpec:
        matches = self.fields.get(field_id, [])
        if len(matches) != 1:
            raise ConfigurationError(f"Sync source {field_id!r} must map to exactly one row; found {len(matches)}.")
        return matches[0]

    def metadata(self) -> Dict[str, Any]:
        return {
            "version": APP_VERSION,
            "schema": REPORT_SCHEMA,
            "material_types": list(MATERIAL_TYPES),
            "warnings": self.warnings,
            "reports": [
                {
                    "id": r.report_id,
                    "section": r.section,
                    "page": r.page,
                    "title": r.title,
                    "columns": [
                        {
                            "field_id": c.field_id,
                            "column": c.column,
                            "name": c.label,
                            "datatype": c.datatype,
                            "show": c.show,
                            "parameter_id": c.parameter_id,
                            "parameter_id_max": c.parameter_max,
                            "mandatory": c.mandatory,
                            "up": list(c.up),
                            "down": list(c.down),
                        }
                        for c in r.columns
                    ],
                }
                for r in self.reports.values()
            ],
        }


def resolve_binding_for_column(column: ColumnSpec, parameter_id: str, is_max: bool) -> ParameterBinding:
    """
    Context-aware binding resolver:
    - Temporal columns use range semantics regardless of reused parameter IDs.
    - Non-temporal columns use canonical static mapping.
    """
    temporal = column.datatype in ("date", "time", "datetime")

    if temporal:
        # Main heuristic: infer family from parameter prefix + column naming.
        pid = parameter_id.casefold()
        col = column.column.casefold()

        # Converting temporal ranges.
        if pid.startswith("cv-"):
            # Consumption windows
            if ("consum" in col or "firstconsumed" in col or "lastconsumed" in col):
                return ParameterBinding(
                    "consumed_date_to" if is_max else "consumed_date_from",
                    "Consumption TO" if is_max else "Consumption FROM",
                    "to" if is_max else "from",
                    "consumed_time_to" if is_max else "consumed_time_from",
                )
            # Production windows
            return ParameterBinding(
                "production_date_to" if is_max else "production_date_from",
                "Production TO" if is_max else "Production FROM",
                "to" if is_max else "from",
                "production_time_to" if is_max else "production_time_from",
            )

        # Papermaking temporal ranges.
        if pid.startswith("pm-"):
            if "cmb" in col:
                return ParameterBinding(
                    "cmb_prod_date_to" if is_max else "cmb_prod_date_from",
                    "CMB Production TO" if is_max else "CMB Production FROM",
                    "to" if is_max else "from",
                    "cmb_prod_time_to" if is_max else "cmb_prod_time_from",
                )
            return ParameterBinding(
                "pm_prod_date_to" if is_max else "pm_prod_date_from",
                "PM Production TO" if is_max else "PM Production FROM",
                "to" if is_max else "from",
                "pm_prod_time_to" if is_max else "pm_prod_time_from",
            )

    # Non-temporal or fallback.
    binding = PARAMETERS.get(parameter_id)
    if binding is None:
        raise ConfigurationError(f"{column.field_id}: unknown ParameterID {parameter_id!r}.")
    return binding


class QueryBuilder:
    def __init__(self, catalog: ReportCatalog, params: GlobalParams, sync_mode: SyncMode, direction: FlowDirection):
        validate_params(params)
        self.catalog = catalog
        self.params = params
        self.sync_mode = sync_mode
        self.direction = direction
        self.binds: Dict[str, Any] = {}
        self.ctes: List[str] = []
        self.base_names: Dict[str, str] = {}
        self.node_names: Dict[Tuple[str, Tuple[str, ...]], str] = {}
        self.node_count = 0
        self.warnings: List[str] = []
        self.used_sections = set()

    def bind(self, value: Any) -> str:
        key = f"p{len(self.binds)}"
        self.binds[key] = value
        return f":{key}"

    def next_name(self) -> str:
        self.node_count += 1
        if self.node_count > MAX_QUERY_NODES:
            raise ConfigurationError("Synchronization graph is too complex.")
        return f"q{self.node_count}"

    def has_value(self, column: ColumnSpec, binding: ParameterBinding) -> bool:
        if binding.kind == "multi":
            return bool(selected_values(getattr(self.params, binding.key)))
        if binding.kind == "plant":
            return bool((self.params.plant_code_name or "").strip())
        if column.datatype == "time":
            return bool(getattr(self.params, binding.time_key or binding.key))
        return getattr(self.params, binding.key) is not None

    def parameter_condition(self, report: ReportSpec, column: ColumnSpec, binding: ParameterBinding) -> Optional[str]:
        expr = f"t.{identifier(column.column)}"

        if binding.kind == "plant":
            plant = (self.params.plant_code_name or "").strip()
            if not plant:
                return None
            marker = self.bind(plant)
            return (
                f"{expr} IN (SELECT DISTINCT `PlantId` FROM {report.dimension} "
                f"WHERE `PlantCodeName` = {marker})"
            )

        if binding.kind == "multi":
            vals = selected_values(getattr(self.params, binding.key))
            if not vals:
                return None
            marks = ", ".join(self.bind(v) for v in vals)
            return f"CAST({expr} AS STRING) IN ({marks})"

        day = getattr(self.params, binding.key)
        clock = normalize_time(getattr(self.params, binding.time_key) if binding.time_key else None)
        upper = binding.kind == "to"

        if column.datatype == "time":
            if not clock:
                return None
            marker = self.bind(clock)
            op = "<=" if upper else ">="
            return f"substr(CAST({expr} AS STRING), 1, 5) {op} {marker}"

        if day is None:
            if clock and column.datatype == "datetime":
                raise ValueError(f"{binding.label}: date required when time is provided.")
            return None

        if column.datatype == "date":
            marker = self.bind(day.isoformat())
            op = "<=" if upper else ">="
            return f"DATE({expr}) {op} CAST({marker} AS DATE)"

        boundary = datetime.combine(day, time.fromisoformat(clock or "00:00"))
        if upper:
            boundary += timedelta(minutes=1) if clock else timedelta(days=1)

        marker = self.bind(boundary.strftime("%Y-%m-%d %H:%M:%S"))
        op = "<" if upper else ">="
        return f"CAST({expr} AS TIMESTAMP) {op} CAST({marker} AS TIMESTAMP)"

    def base(self, section: str) -> str:
        if section in self.base_names:
            return self.base_names[section]

        report = self.catalog.reports[section]
        self.used_sections.add(section)
        conditions: List[str] = []
        missing: List[str] = []

        for col in report.columns:
            if col.mandatory and not col.parameter_id:
                raise ConfigurationError(f"{col.field_id}: Mandatory=1 requires ParameterID.")

            candidates = []
            if col.parameter_id:
                candidates.append((col.parameter_id, False))
            if col.parameter_max:
                candidates.append((col.parameter_max, True))

            for pid, is_max in candidates:
                binding = resolve_binding_for_column(col, pid, is_max)

                # strict sanity: non-temporal must not receive range binding
                if col.datatype not in ("date", "time", "datetime") and binding.kind in ("from", "to"):
                    raise ConfigurationError(
                        f"{col.field_id}: non-temporal column {col.column} mapped to range parameter {pid}."
                    )

                if self.sync_mode == "unsync" and col.mandatory and (pid == col.parameter_id) and not self.has_value(col, binding):
                    missing.append(binding.label)

                cond = self.parameter_condition(report, col, binding)
                if cond:
                    conditions.append(cond)

        if missing:
            raise MissingParameters(
                f"{report.title}: mandatory parameter(s) missing: "
                + ", ".join(dict.fromkeys(missing))
                + "."
            )

        name = self.next_name()
        where = " AND ".join(conditions) if conditions else "TRUE"
        self.ctes.append(f"{name} AS (SELECT t.* FROM {report.table} AS t WHERE {where})")
        self.base_names[section] = name
        return name

    def synchronized(self, section: str, path: Tuple[str, ...]) -> str:
        if section in path:
            self.warnings.append(f"Cycle at {section}: fallback to base scope.")
            return self.base(section)

        key = (section, path)
        if key in self.node_names:
            return self.node_names[key]

        report = self.catalog.reports[section]
        base_name = self.base(section)
        conditions: List[str] = []

        for col in report.columns:
            links = col.up if self.direction == "up" else col.down
            alternatives = []

            for fid in links:
                source_col = self.catalog.source(fid)
                source_name = self.synchronized(source_col.section, path + (section,))

                texpr = f"t.{identifier(col.column)}"
                sexpr = f"s.{identifier(source_col.column)}"
                alternatives.append(
                    f"CAST({texpr} AS STRING) IN ("
                    f"SELECT CAST({sexpr} AS STRING) FROM {source_name} AS s "
                    f"WHERE {sexpr} IS NOT NULL)"
                )

            if alternatives:
                conditions.append("(" + " OR ".join(alternatives) + ")")

        if not conditions:
            self.node_names[key] = base_name
            return base_name

        name = self.next_name()
        self.ctes.append(
            f"{name} AS (SELECT t.* FROM {base_name} AS t WHERE " + " AND ".join(conditions) + ")"
        )
        self.node_names[key] = name
        return name

    def build(self, report: ReportSpec, limit: int, offset: int, export: bool):
        if not 1 <= limit <= 5000 or offset < 0:
            raise ValueError("Invalid pagination.")

        source = self.synchronized(report.section, ()) if self.sync_mode == "sync" else self.base(report.section)

        visible = [c for c in report.columns if c.show]
        projection = ", ".join(
            f"t.{identifier(c.column)} AS `{c.label.replace('`', '``')}`"
            for c in visible
        )
        ordering = ", ".join(f"t.{identifier(c.column)} ASC NULLS LAST" for c in visible)
        row_limit = MAX_EXPORT_ROWS + 1 if export else limit + 1

        sql = (
            "WITH " + ",\n".join(self.ctes)
            + f"\nSELECT {projection} FROM {source} AS t"
            + f" ORDER BY {ordering} LIMIT {row_limit}"
        )
        if not export:
            sql += f" OFFSET {offset}"

        meta = {
            "section": report.section,
            "sync_mode": self.sync_mode,
            "flow_direction": self.direction,
            "source_sections": sorted(self.used_sections),
            "warnings": list(dict.fromkeys(self.warnings)),
        }
        return sql, self.binds, meta


def build_query(
    catalog: ReportCatalog,
    page: str,
    report_id: str,
    params: GlobalParams,
    limit: int = 10,
    offset: int = 0,
    export: bool = False,
    sync_mode: SyncMode = "unsync",
    flow_direction: FlowDirection = "up",
):
    report = catalog.resolve(page, report_id)
    builder = QueryBuilder(catalog, params, sync_mode, flow_direction)
    return builder.build(report, limit, offset, export)