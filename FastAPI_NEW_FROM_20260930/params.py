"""
Sprint 4 configuration-driven query engine + parameter definitions.

Two config tables drive the app (schema traceability_poc):
- master_config_report_columns : which columns each report shows/filters (ReportCatalog)
- master_config_global_param   : which global parameters exist (build_parameter_definitions)

Master / slave execution (Sprint 4):
- Master reports (master_config_report_columns.isMasterTable = 1) are filtered by global parameters;
  in SYNC mode they are additionally chained to other reports through Upsynchwith / Downsynchwith.
- Slave reports are ALWAYS filtered by MasterTableColumn (FieldID of the master column),
  in every mode and direction; Upsynchwith / Downsynchwith are not needed for that.
- UNSYNC: only global parameters reach the masters (mandatory parameters enforced).
- SYNC: global parameters + values of the linked reports; nothing is mandatory.
- A master never receives values from another master except through the sync direction in use.

Context-aware parameter binding:
- For temporal columns (date/time/datetime), ParameterID is treated as lower bound,
  ParameterIDmax as upper bound.
- For non-temporal columns, ParameterID/ParameterIDmax are treated as value lists
  only if they map to list-type global parameters.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from pydantic import BaseModel, Field


APP_VERSION = "Sprint 4.2.0"
REPORT_SCHEMA = "traceability_poc"
MAX_EXPORT_ROWS = 100000
MAX_PARAMETER_VALUES = 350
MAX_QUERY_NODES = 128

# Fallback only: the real list comes from master_config_global_param.PossibleValues
MATERIAL_TYPES = ("HALB", "ROH", "ZFIN", "ZADN", "ZVERP", "ZCRP")

PLANT_KEY = "plant_code_name"   # default plant field that line dropdowns depend on

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
    plant_code_name: Optional[str] = None       # Converting + Paper machine production plant
    cmb_plant_code_name: Optional[str] = None   # Combiner production plant (independent)

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

    # Parameters of newer pages (e.g. fd_p5) are defined only in master_config_global_param,
    # so they are accepted as extra fields and read through param_get().
    class Config:
        extra = "allow"


@dataclass(frozen=True)
class ParameterBinding:
    key: str
    label: str
    kind: str
    time_key: Optional[str] = None
    dimension: Optional[str] = None   # plant parameters: table listing the plants (Query column)


# Parameter ID (master_config_global_param.ID) -> GlobalParams field.
# This is the ONE place where a new parameter must be mapped to a model field.
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

    # cv-p12 is reused in the report config (SSCC and date-range lower bound).
    # Kept as SSCC here; temporal usage is resolved by column context.
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
    "pm-p17": ParameterBinding("cmb_plant_code_name", "Combiner Plant", "plant"),
}

# Section name in master_config_report_columns.Section -> (report id used by the browser, title)
REPORT_PRESENTATION = {
    "c1": ("summary", "Material consumption summary"),
    "c2": ("details", "Material consumption batches and handling units"),
    "c3": ("orders", "Production Orders and Batches"),
    "c4": ("pallets", "Produced Handling Units"),
    "p1": ("pm_report_1", "Raw material consumption"),
    "p2": ("pm_report_3", "Paper machine production order summary"),
    "p3": ("pm_report_4", "Paper machine production batch details"),
    "p4": ("cmb_summary", "Combiner production order summary"),
    "p5": ("cmb_report_2", "Combiner production batch details"),
    "fd1": ("fd1", "Delivery summary"),
    "fd2": ("fd2", "Handling units deliveries"),
}

# Page name in the config tables (casefolded) -> label shown in the global parameter rail.
PAGE_LABELS = {
    "finisheddeliveries": "Finished goods deliveries",
    "zfindeliveries": "Finished goods deliveries",
}

# Alternative page names used in the config tables -> page name used by the app URL.
PAGE_ALIASES = {
    "zfindeliveries": "finisheddeliveries",
}

# Used only while isMasterTable is not populated anywhere in master_config_report_columns.
MASTER_VIEWS = frozenset({
    "v_pm_prodorders_details",
    "v_cmb_prodorders_details",
    "v_salcons_details",
    "v_zfin_hu_cust",
})

# Fallback dimension tables (used only when master_config_global_param is unavailable)
DIMENSIONS = {
    "cv": "dim_converting_productionline",
    "pm": "dim_PM_productionline",
    "cmb": "dim_CMB_productionline",
}

# Default value column of each dimension table, used when master_config_global_param has no QueryColumn.
DIMENSION_COLUMNS = {
    "dim_converting_productionline": "ConvertingProductionLine",
    "dim_pm_productionline": "PM_ProductionLine",
    "dim_cmb_productionline": "CMB_ProductionLine",
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


def selected_values(field_value: MultiValue) -> List[str]:
    return list(dict.fromkeys(v.strip() for v in field_value.values if v.strip()))


def param_get(params: Any, key: Optional[str], kind: str) -> Any:
    """
    Read one global parameter whether it is a declared field or an extra one (new pages).
    multi -> list of strings, plant -> string, from/to -> date, time -> string.
    """
    raw = getattr(params, key, None) if key else None
    if kind == "multi":
        if isinstance(raw, MultiValue):
            return selected_values(raw)
        if isinstance(raw, dict):
            raw = raw.get("values")
        if isinstance(raw, (list, tuple)):
            return list(dict.fromkeys(str(v).strip() for v in raw if v is not None and str(v).strip()))
        return []
    if kind == "plant":
        return (raw or "").strip() if isinstance(raw, str) else ""
    if kind in ("from", "to"):
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw.strip()) if raw.strip() else None
            except ValueError as exc:
                raise ValueError("Dates must use YYYY-MM-DD.") from exc
        return raw if isinstance(raw, date) else None
    return raw if isinstance(raw, str) and raw.strip() else None


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


def validate_params(p: GlobalParams, allowed: Optional[Mapping[str, Any]] = None) -> None:
    count = 0
    for binding in PARAMETERS.values():
        if binding.kind == "multi":
            count += len(param_get(p, binding.key, "multi"))
    if count > MAX_PARAMETER_VALUES:
        raise ValueError(f"Too many selected values. Maximum is {MAX_PARAMETER_VALUES}.")

    # Fixed-value parameters (PossibleValues in the config table); fallback = built-in material types
    rules = {"consumed_material_type_id": MATERIAL_TYPES, **(allowed or {})}
    labels = {b.key: b.label for b in PARAMETERS.values()}
    for key, values in rules.items():
        if not hasattr(p, key):
            continue
        invalid = set(param_get(p, key, "multi")) - set(values)
        if invalid:
            raise ValueError(f"{labels.get(key, key)} must be one of: " + ", ".join(values))

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


def _int(value: Any, default: int) -> int:
    try:
        return int(float(_text(value) or ""))
    except ValueError:
        return default


def _links(value: Any) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in (_text(value) or "").split(",") if part.strip()))


def _datatype(value: Any) -> str:
    result = (_text(value) or "string").casefold()
    if result in ("datetim", "timestamp"):
        return "datetime"
    return result


# ======================================================================
# Global parameter definitions (master_config_global_param -> panel layout)
# ======================================================================

PROFILE_ORDER = {"pm": 0, "cv": 1, "fd": 2}

# Sections that exist in the UI but have no rows in the table yet (shown first, as a reserved note)
PLACEHOLDER_SECTIONS = {
    "pm": [{
        "key": "pm_consumption",
        "title": "Raw material consumption",
        "description": "Reserved for P1 raw material consumption report onboarding.",
    }],
}


@dataclass
class ParameterDefinitions:
    profiles: List[Dict[str, Any]]                                   # sent to the browser
    sources: Dict[str, Dict[str, str]] = field(default_factory=dict)  # param id -> {table, column}
    plant_ids: List[str] = field(default_factory=list)                # ids that list plants
    plant_tables: List[str] = field(default_factory=list)             # all dimension tables holding plants
    plant_sources: Dict[str, str] = field(default_factory=dict)       # plant param id -> its own Query table
    allowed: Dict[str, Tuple[str, ...]] = field(default_factory=dict)  # field key -> fixed values
    warnings: List[str] = field(default_factory=list)


def parse_possible_values(raw: Any) -> List[str]:
    """Parse "( 'A', 'B' )" (or "A, B") into a de-duplicated list."""
    text = _text(raw)
    if not text:
        return []
    quoted = re.findall(r"'([^']*)'|\"([^\"]*)\"", text)
    values = [a or b for a, b in quoted] or [p.strip(" ()") for p in text.split(",")]
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def _split_label(name: str, word: str) -> str:
    """'PM Production DateTime FROM' -> 'PM Production Date FROM' / '... Time FROM'."""
    return name.replace("DateTime", word) if "DateTime" in name else f"{name} ({word.lower()})"


def dynamic_binding(pid: str, name: str, query: Optional[str], datatype: str = "", fmt: str = "") -> ParameterBinding:
    """
    Binding for a parameter that is not listed in PARAMETERS (e.g. the Finished goods deliveries page).
    Date parameters ("... FROM" / "... TO") are range limits; this is decided by DataType/Format,
    never by the name alone (e.g. "Ship To Customer" is a normal list).
    A parameter with "plant" in its Name and a Query is a plant list; anything else = values.
    """
    key = pid.replace("-", "_")
    is_date = datatype.casefold() in ("date", "datetime", "timestamp") or fmt.casefold() == "calendar"
    if is_date and re.search(r"\bFROM\b", name, re.I):
        return ParameterBinding(key, name, "from", f"{key}_time")
    if is_date and re.search(r"\bTO\b", name, re.I):
        return ParameterBinding(key, name, "to", f"{key}_time")
    if query and re.search(r"plant", name, re.I):
        return ParameterBinding(key, name, "plant", None, query)
    return ParameterBinding(key, name, "multi")


def build_parameter_definitions(records: List[Mapping[str, Any]]) -> ParameterDefinitions:
    warnings: List[str] = []
    parsed = []
    sources: Dict[str, Dict[str, str]] = {}
    plant_ids: List[str] = []
    plant_tables: List[str] = []
    plant_sources: Dict[str, str] = {}
    allowed: Dict[str, Tuple[str, ...]] = {}

    for record in records:
        r = {re.sub(r"[^a-z0-9]", "", str(k).casefold()): v for k, v in record.items()}
        pid = _text(r.get("id"))
        if not pid or not re.fullmatch(r"[a-z]+-p\d+", pid):
            warnings.append(f"Parameter row ignored: invalid ID {pid!r}.")
            continue
        binding = PARAMETERS.get(pid)
        if binding is None:
            binding = dynamic_binding(
                pid, _text(r.get("name")) or pid, _text(r.get("query")),
                _text(r.get("datatype")) or "", _text(r.get("format")) or "",
            )
            PARAMETERS[pid] = binding

        name = _text(r.get("name")) or binding.label
        fmt = (_text(r.get("format")) or "").casefold().replace(" ", "-")
        query = _text(r.get("query"))
        query_column = _text(r.get("querycolumn")) or DIMENSION_COLUMNS.get(
            (_text(r.get("query")) or "").split(".")[-1].casefold()
        )
        values = parse_possible_values(r.get("possiblevalues"))
        required = False   # Sprint 4: no mandatory parameters

        profile = pid.split("-")[0]
        # Sprint 4 headers: Page (group) / Section (pill). Old headers: Section (group) / SubSection (pill).
        if "subsection" in r:
            section = _text(r.get("section")) or profile
            sub = _text(r.get("subsection")) or section
        else:
            section = _text(r.get("page")) or profile
            section = PAGE_LABELS.get(section.casefold(), section)
            sub = _text(r.get("section")) or section
        order = _int(r.get("sortorder"), 9999)
        number = _int(pid.split("-p")[-1], 9999)

        if query:
            plant_tables.append(query)
            if binding.kind == "plant":
                plant_sources[pid] = query            # this plant dropdown lists only this table's plants
            elif query_column:
                sources[pid] = {"table": query, "column": query_column}
            else:
                warnings.append(f"{pid}: Query is set but QueryColumn is empty; dropdown has no options.")

        if binding.kind in ("from", "to"):
            # one calendar row -> date input + time input on the same line
            fields = [
                {"key": binding.key, "label": _split_label(name, "Date"), "type": "date", "row": pid, "required": required},
                {"key": binding.time_key, "label": _split_label(name, "Time"), "type": "time", "row": pid},
            ]
        elif binding.kind == "plant":
            plant_ids.append(pid)
            fields = [{"key": binding.key, "label": name, "type": "select", "required": required,
                       "source": f"/api/params/options/{pid}"}]
        else:
            item = {"key": binding.key, "label": name, "required": required}
            if fmt == "dropdown" and query:
                item.update(type="multiselect", source=f"/api/params/options/{pid}", dependsOn=PLANT_KEY)
            elif fmt == "dropdown" and values:
                item.update(type="enum-multi", allowed=values)
                allowed[binding.key] = tuple(values)
            else:
                item["type"] = "multi"                      # free-text, several values
            fields = [item]

        parsed.append((profile, section, sub, order, number, fields))

    if not parsed:
        raise ConfigurationError("master_config_global_param has no usable rows.")

    # group: profile -> sub-section -> fields
    profiles: Dict[str, Dict[str, Any]] = {}
    for profile, section, sub, order, number, fields in parsed:
        group = profiles.setdefault(profile, {"title": f"{section} Parameters", "subs": {}})
        entry = group["subs"].setdefault(sub, {"first": number, "items": []})
        entry["first"] = min(entry["first"], number)          # pill order = lowest parameter number
        entry["items"].append((order, number, fields))

    public = []
    for profile in sorted(profiles, key=lambda p: PROFILE_ORDER.get(p, 99)):
        group = profiles[profile]
        existing = {title.casefold() for title in group["subs"]}
        sections = [
            {**ph, "placeholder": True, "fields": []}
            for ph in PLACEHOLDER_SECTIONS.get(profile, [])
            if ph["title"].casefold() not in existing
        ]
        for title, entry in sorted(group["subs"].items(), key=lambda kv: kv[1]["first"]):
            ordered = sorted(entry["items"], key=lambda item: (item[0], item[1]))
            flat = [f for _, _, fields in ordered for f in fields]

            # each section has its OWN plant: its line dropdowns depend on that plant field
            plant_key = next((f["key"] for f in flat if f["type"] == "select"), None)
            if plant_key:
                for f in flat:
                    if f.get("dependsOn"):
                        f["dependsOn"] = plant_key

            sections.append({
                "key": f"{profile}_" + re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_"),
                "title": title,
                "fields": flat,
            })
        public.append({"profile": profile, "badge": profile.upper(), "title": group["title"], "sections": sections})

    return ParameterDefinitions(
        profiles=public,
        sources=sources,
        plant_ids=plant_ids,
        plant_tables=list(dict.fromkeys(plant_tables)),
        plant_sources=plant_sources,
        allowed=allowed,
        warnings=warnings,
    )


# ======================================================================
# Report catalog (master_config_report_columns)
# ======================================================================

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
    master: bool = False
    master_link: Optional[str] = None   # FieldID of the master report column this column follows


@dataclass(frozen=True)
class ReportSpec:
    page: str
    section: str
    report_id: str
    title: str
    table: str
    dimension: str
    columns: Tuple[ColumnSpec, ...]
    is_master: bool = False
    view: str = ""


class ReportCatalog:
    def __init__(self, records: List[Mapping[str, Any]], catalog: Optional[str] = None):
        self.catalog = catalog
        self.reports: Dict[str, ReportSpec] = {}
        self.fields: Dict[str, List[ColumnSpec]] = defaultdict(list)
        self.warnings: List[str] = []
        # fixed-value parameters from master_config_global_param (set by main.load_catalog)
        self.allowed_values: Dict[str, Tuple[str, ...]] = {}

        grouped = defaultdict(list)
        required_headers = {
            "fieldid", "page", "section", "table", "column",
            "parameterid", "parameteridmax", "show",
        }

        bad: Dict[str, str] = {}     # section -> reason; one broken section must not disable the others

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

            section = _text(get("section"))
            if not section:
                self.warnings.append(f"Row {_text(get('fieldid'))!r} ignored: Section is empty.")
                continue

            try:
                field_id = _text(get("fieldid"))
                page = re.sub(r"\s+", "", _text(get("page")) or "").casefold()
                page = PAGE_ALIASES.get(page, page)
                table = _text(get("table"))
                column = _text(get("column"))

                if not page:
                    raise ConfigurationError(f"{field_id}: Page is empty.")
                if not all((field_id, table, column)):
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
                    mandatory=_flag(get("mandatory", "ismandatory")),
                    up=_links(get("upsychwith", "upsynchwith", "upsyncwith")),
                    down=_links(get("downsynchwith", "downsyncwith")),
                    show=_flag(get("show")),
                    master=_flag(get("ismastertable", "ismaster")),
                    master_link=_text(get("mastertablecolumn")),
                )
            except ConfigurationError as exc:
                bad.setdefault(section, str(exc))
                continue
            grouped[section].append(spec)

        for section, reason in bad.items():
            grouped.pop(section, None)
            self.warnings.append(f"Section {section!r} skipped: {reason}")

        if not grouped:
            raise ConfigurationError("Configuration table has no usable report section.")

        # isMasterTable not filled in yet -> fall back to the known master views
        config_has_masters = any(c.master for cols in grouped.values() for c in cols)

        for section, cols in sorted(grouped.items()):
            try:
                pages = {c.page for c in cols}
                tables = {c.table for c in cols}
                if len(pages) != 1 or len(tables) != 1:
                    raise ConfigurationError(f"{section}: section must map to one page and one table.")
                if not any(c.show for c in cols):
                    raise ConfigurationError(f"{section}: no columns with Show=1.")

                duplicates = [n for n, ct in Counter(c.column.casefold() for c in cols).items() if ct > 1]
                if duplicates:
                    raise ConfigurationError(f"{section}: duplicate columns: {', '.join(duplicates)}")

                masters = {c.master for c in cols}
                if len(masters) != 1:
                    raise ConfigurationError(f"{section}: isMasterTable must be the same on every row.")
            except ConfigurationError as exc:
                self.warnings.append(f"Section {section!r} skipped: {exc}")
                continue

            first = cols[0]
            report_id, title = REPORT_PRESENTATION.get(section, (section, section))
            table_name = first.table.casefold()
            if first.page == "papermaking":
                family = "cmb" if "cmb" in table_name else "pm"
            else:
                family = "cv"   # other pages use the plant table of their own Plant parameter

            view = first.table.split(".")[-1].strip("`")
            is_master = first.master or (not config_has_masters and view.casefold() in MASTER_VIEWS)

            self.reports[section] = ReportSpec(
                page=first.page,
                section=section,
                report_id=report_id,
                title=title,
                table=first.table,
                dimension=qualified_table(DIMENSIONS[family], catalog),
                columns=tuple(cols),
                is_master=is_master,
                view=view,
            )
            for col in cols:
                self.fields[col.field_id].append(col)

        if not self.reports:
            raise ConfigurationError("Configuration table has no valid report section.")

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
            "material_types": list(self.allowed_values.get("consumed_material_type_id", MATERIAL_TYPES)),
            "warnings": self.warnings,
            "reports": [
                {
                    "id": r.report_id,
                    "section": r.section,
                    "page": r.page,
                    "title": r.title,
                    "view": r.view,
                    "is_master": r.is_master,
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
                            "master_link": c.master_link,
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
            if "cmb" in col or pid in ("pm-p14", "pm-p15"):
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
        validate_params(params, catalog.allowed_values)
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
            return bool(param_get(self.params, binding.key, "multi"))
        if binding.kind == "plant":
            return bool(param_get(self.params, binding.key, "plant"))
        if column.datatype == "time":
            return bool(param_get(self.params, binding.time_key or binding.key, "time"))
        return param_get(self.params, binding.key, binding.kind) is not None

    def parameter_condition(self, report: ReportSpec, column: ColumnSpec, binding: ParameterBinding) -> Optional[str]:
        expr = f"t.{identifier(column.column)}"

        if binding.kind == "plant":
            plant = param_get(self.params, binding.key, "plant")
            if not plant:
                return None
            dimension = (
                qualified_table(binding.dimension, self.catalog.catalog) if binding.dimension else report.dimension
            )
            marker = self.bind(plant)
            return (
                f"{expr} IN (SELECT DISTINCT `PlantId` FROM {dimension} "
                f"WHERE `PlantCodeName` = {marker})"
            )

        if binding.kind == "multi":
            vals = param_get(self.params, binding.key, "multi")
            if not vals:
                return None
            marks = ", ".join(self.bind(v) for v in vals)
            return f"CAST({expr} AS STRING) IN ({marks})"

        day = param_get(self.params, binding.key, binding.kind)
        clock = normalize_time(param_get(self.params, binding.time_key, "time") if binding.time_key else None)
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

        enforce_mandatory = False   # Sprint 4: no mandatory parameters

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

                if enforce_mandatory and col.mandatory and (pid == col.parameter_id) and not self.has_value(col, binding):
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

    def links_for(self, report: ReportSpec, col: ColumnSpec) -> List[str]:
        """
        Source FieldIDs that restrict this column.
        - Slave report: always follows MasterTable / MasterTableColumn (any mode or direction).
        - Master report: follows Upsynchwith / Downsynchwith of the active direction, only in SYNC.
        """
        chosen: List[str] = []

        if self.sync_mode == "sync":
            chosen.extend(col.up if self.direction == "up" else col.down)

        if not report.is_master and col.master_link:
            chosen.append(col.master_link)

        return list(dict.fromkeys(chosen))

    @staticmethod
    def key_expr(expr: str) -> str:
        return f"regexp_replace(trim(CAST({expr} AS STRING)), '^0+(?=.)', '')"

    def scope(self, section: str, path: Tuple[str, ...]) -> str:
        """
        CTE holding the rows of a report after global parameters and its sync / master links.
        Masters are resolved before the slaves that depend on them (the slave asks for its master first).
        """
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
            alternatives = []

            for fid in self.links_for(report, col):
                source_col = self.catalog.source(fid)
                source_name = self.scope(source_col.section, path + (section,))

                texpr = f"t.{identifier(col.column)}"
                sexpr = f"s.{identifier(source_col.column)}"
                # Compare as trimmed text without leading zeros: the linked views may store
                # the same key as string in one view and as number in the other.
                alternatives.append(
                    f"{self.key_expr(texpr)} IN ("
                    f"SELECT {self.key_expr(sexpr)} FROM {source_name} AS s "
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

        source = self.scope(report.section, ())

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