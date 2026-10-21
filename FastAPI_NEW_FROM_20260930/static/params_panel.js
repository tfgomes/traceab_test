/*
  Sprint 3 shared frontend runtime.
  - Shared global parameters on every page
  - Sync mode persisted in session
  - Reusable helpers for report pages
*/

const PARAMS_KEY = "traceability.globalParams";
const RUNS_KEY = "traceability.runsByProfile";
const CACHE_PREFIX = "traceability.report.";
const MODE_KEY = "traceability.mode";
const LAST_RUN_KEY = "traceability.lastRunAt";

const PAGE_CONVERTING = "converting";
const PAGE_PAPERMAKING = "papermaking";

const MATERIAL_TYPES = ["HALB", "ROH", "ZFIN", "ZADN", "ZVERP"];

const PROFILE_TABS = [
  { id: "pm", label: "Papermaking", title: "Papermaking parameters" },
  { id: "cv", label: "Converting", title: "Converting parameters" },
];

const PROFILE_TO_PAGE = {
  pm: PAGE_PAPERMAKING,
  cv: PAGE_CONVERTING,
};

const SECTIONS = [
  {
    profile: "pm",
    badge: "PM",
    title: "Papermaking Parameters",
    sections: [
      {
        key: "pm_consumption",
        title: "Papermaking consumption",
        description: "Reserved for PM-1 consumption report onboarding.",
        placeholder: true,
        fields: [],
      },
      {
        key: "pm_production",
        title: "Papermaking production",
        fields: [
          { key: "plant_code_name", label: "Plant", type: "select", required: true, source: "/api/params/plants" },
          { key: "pm_line", label: "PM Production line", type: "multiselect", required: true, source: "/api/params/pm-lines", dependsOn: "plant_code_name" },
          { key: "pm_material_id", label: "PM Produced Material ID", type: "multi" },
          { key: "pm_order_number", label: "PM Order number", type: "multi" },
          { key: "pm_reel_batch_id", label: "PM Produced Batch ID", type: "multi" },
          { key: "pm_produced_sscc", label: "PM Produced SSCC", type: "multi" },
          { key: "pm_prod_date_from", label: "PM Production Date FROM", type: "date", row: "pm_from" },
          { key: "pm_prod_time_from", label: "PM Production Time FROM", type: "time", row: "pm_from" },
          { key: "pm_prod_date_to", label: "PM Production Date TO", type: "date", row: "pm_to" },
          { key: "pm_prod_time_to", label: "PM Production Time TO", type: "time", row: "pm_to" },
        ],
      },
      {
        key: "cmb_production",
        title: "Combiner production",
        fields: [
          { key: "cmb_line", label: "Combiner Production Line", type: "multiselect", required: true, source: "/api/params/cmb-lines", dependsOn: "plant_code_name" },
          { key: "cmb_material_id", label: "Combiner Produced Material ID", type: "multi" },
          { key: "cmb_order_number", label: "Combiner Order Number", type: "multi" },
          { key: "cmb_reel_batch_id", label: "Combiner Produced Batch ID", type: "multi" },
          { key: "cmb_produced_sscc", label: "Combiner Produced SSCC", type: "multi" },
          { key: "cmb_prod_date_from", label: "CMB Production Date FROM", type: "date", row: "cmb_from" },
          { key: "cmb_prod_time_from", label: "CMB Production Time FROM", type: "time", row: "cmb_from" },
          { key: "cmb_prod_date_to", label: "CMB Production Date TO", type: "date", row: "cmb_to" },
          { key: "cmb_prod_time_to", label: "CMB Production Time TO", type: "time", row: "cmb_to" },
        ],
      },
    ],
  },
  {
    profile: "cv",
    badge: "CV",
    title: "Converting Parameters",
    sections: [
      {
        key: "raw_consumption",
        title: "Raw material consumptions",
        fields: [
          { key: "plant_code_name", label: "Plant", type: "select", required: true, source: "/api/params/plants" },
          { key: "converting_production_line", label: "Production Line", type: "multiselect", required: true, source: "/api/params/cv-lines", dependsOn: "plant_code_name" },
          { key: "consumed_material_type_id", label: "Consumed Material Type", type: "enum-multi", allowed: MATERIAL_TYPES },
          { key: "consumed_material_id", label: "Consumed Material ID", type: "multi" },
          { key: "consumed_batch_id", label: "Consumed Batch ID", type: "multi" },
          { key: "consumed_date_from", label: "Consumption Date FROM", type: "date", row: "cons_from" },
          { key: "consumed_time_from", label: "Consumption Time FROM", type: "time", row: "cons_from" },
          { key: "consumed_date_to", label: "Consumption Date TO", type: "date", row: "cons_to" },
          { key: "consumed_time_to", label: "Consumption Time TO", type: "time", row: "cons_to" },
          { key: "consumed_sscc", label: "Consumed SSCC", type: "multi" },
        ],
      },
      {
        key: "fg_production",
        title: "Finished good production",
        fields: [
          { key: "zfin_material_id", label: "Produced Material ID", type: "multi" },
          { key: "zfin_order", label: "Order Number", type: "multi" },
          { key: "production_date_from", label: "Production Date FROM", type: "date", row: "prod_from" },
          { key: "production_time_from", label: "Production Time FROM", type: "time", row: "prod_from" },
          { key: "production_date_to", label: "Production Date TO", type: "date", row: "prod_to" },
          { key: "production_time_to", label: "Production Time TO", type: "time", row: "prod_to" },
          { key: "zfin_sscc", label: "Produced SSCC", type: "multi" },
          { key: "zfin_batch_id", label: "Produced Batch", type: "multi" },
        ],
      },
    ],
  },
];

const REPORTS_CONVERTING = [
  { id: "summary", badge: "1", label: "Material consumption summary" },
  { id: "details", badge: "2", label: "Material consumption batches and handling units" },
  { id: "orders", badge: "3", label: "Production Orders and Batches" },
  { id: "pallets", badge: "4", label: "Produced pallets" },
];

const REPORTS_PAPERMAKING = [
  { id: "pm_report_3", badge: "2", label: "Paper machine production order summary" },
  { id: "pm_report_4", badge: "3", label: "Paper machine production batch details" },
  { id: "cmb_summary", badge: "4", label: "Combiner production order summary" },
  { id: "cmb_report_2", badge: "5", label: "Combiner production batch details" },
];

const DEFAULT_MODE = { sync: false, direction: "up" };

function readJson(key, fallback) {
  try {
    const v = JSON.parse(sessionStorage.getItem(key));
    return v ?? fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key, value) {
  sessionStorage.setItem(key, JSON.stringify(value));
}

function getGlobalParams() { return readJson(PARAMS_KEY, null); }
function saveGlobalParams(params) { writeJson(PARAMS_KEY, params || {}); }
function clearGlobalParams() {
  sessionStorage.removeItem(PARAMS_KEY);
  clearAllRuns();
  clearReportCaches();
  clearLastRunAt();
}

function getRunsByProfile() { return readJson(RUNS_KEY, {}); }
function getRunId(profile = null) {
  if (!profile) return null;
  return getRunsByProfile()[profile] || null;
}
function setRunId(profile, runId) {
  const runs = getRunsByProfile();
  runs[profile] = runId;
  writeJson(RUNS_KEY, runs);
}
function clearRunId(profile) {
  const runs = getRunsByProfile();
  delete runs[profile];
  writeJson(RUNS_KEY, runs);
}
function clearAllRuns() { sessionStorage.removeItem(RUNS_KEY); }

function getPageKey(pageName) { return `${CACHE_PREFIX}${pageName}.`; }
function clearReportCaches(pageName = null) {
  const prefix = pageName ? getPageKey(pageName) : CACHE_PREFIX;
  Object.keys(sessionStorage).filter(k => k.startsWith(prefix)).forEach(k => sessionStorage.removeItem(k));
}
function loadReportCache(reportId, pageName) { return readJson(getPageKey(pageName) + reportId, null); }
function saveReportCache(reportId, value, pageName) { writeJson(getPageKey(pageName) + reportId, value); }

function getMode() {
  const mode = readJson(MODE_KEY, DEFAULT_MODE);
  return {
    sync: Boolean(mode?.sync),
    direction: (mode?.direction || "up").toLowerCase() === "down" ? "down" : "up",
  };
}
function setMode(next) {
  const mode = {
    sync: Boolean(next?.sync),
    direction: (next?.direction || "up").toLowerCase() === "down" ? "down" : "up",
  };
  writeJson(MODE_KEY, mode);
}
function clearMode() { writeJson(MODE_KEY, DEFAULT_MODE); }

function setLastRunAt(valueIso) {
  if (!valueIso) return;
  sessionStorage.setItem(LAST_RUN_KEY, valueIso);
}
function getLastRunAt() { return sessionStorage.getItem(LAST_RUN_KEY) || null; }
function clearLastRunAt() { sessionStorage.removeItem(LAST_RUN_KEY); }

function formatRunTimestamp(iso) {
  if (!iso) return "Last run: -";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return `Last run: ${iso}`;
  return `Last run: ${d.toLocaleString()}`;
}

async function startRun() {
  const response = await fetch("/api/converting/runs", { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Could not start run (HTTP ${response.status}).`);
  return data.run_id;
}
async function stopRun(runId) {
  if (!runId) return null;
  const response = await fetch(`/api/converting/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Cancellation failed.");
  return data;
}
async function fetchReport(pageName, report, params, runId, limit, offset, mode) {
  const base = pageName === PAGE_PAPERMAKING ? "/api/papermaking" : "/api/converting";
  try {
    const response = await fetch(`${base}/${report.id}/data`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        run_id: runId,
        params,
        limit,
        offset,
        sync_mode: mode?.sync ? "sync" : "unsync",
        flow_direction: mode?.direction || "up",
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return { success: false, error: data.detail || `HTTP ${response.status}` };
    return data;
  } catch (error) {
    return { success: false, error: String(error) };
  }
}
async function exportReport(pageName, report, params, runId, mode) {
  const base = pageName === PAGE_PAPERMAKING ? "/api/papermaking" : "/api/converting";
  return fetch(`${base}/export/${report.id}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      run_id: runId,
      params,
      limit: 10,
      offset: 0,
      sync_mode: mode?.sync ? "sync" : "unsync",
      flow_direction: mode?.direction || "up",
    }),
  });
}

function setStatus(id, kind, message) {
  const node = document.getElementById(id);
  if (!node) return;
  node.className = `status ${kind}`;
  node.textContent = message;
}
function renderTable(head, body, columns, rows) {
  head.replaceChildren();
  body.replaceChildren();

  const tr = document.createElement("tr");
  columns.forEach(col => {
    const th = document.createElement("th");
    th.textContent = col;
    tr.appendChild(th);
  });
  head.appendChild(tr);

  if (!rows.length) {
    const r = document.createElement("tr");
    const c = document.createElement("td");
    c.colSpan = Math.max(1, columns.length);
    c.textContent = "No records found";
    r.appendChild(c);
    body.appendChild(r);
    return;
  }

  rows.forEach(values => {
    const r = document.createElement("tr");
    values.forEach(v => {
      const c = document.createElement("td");
      c.textContent = v == null ? "" : String(v);
      r.appendChild(c);
    });
    body.appendChild(r);
  });
}

window.TraceabilityPhaseB = {
  PAGE_CONVERTING,
  PAGE_PAPERMAKING,
  PROFILE_TABS,
  PROFILE_TO_PAGE,
  SECTIONS,
  REPORTS_CONVERTING,
  REPORTS_PAPERMAKING,
  MATERIAL_TYPES,

  getGlobalParams,
  saveGlobalParams,
  clearGlobalParams,

  getRunId,
  setRunId,
  clearRunId,
  clearAllRuns,

  clearReportCaches,
  loadReportCache,
  saveReportCache,

  getMode,
  setMode,
  clearMode,

  setLastRunAt,
  getLastRunAt,
  clearLastRunAt,
  formatRunTimestamp,

  startRun,
  stopRun,
  fetchReport,
  exportReport,

  setStatus,
  renderTable,
};