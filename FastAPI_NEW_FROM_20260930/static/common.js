const PARAMS_KEY = 'traceability.globalParams';
const RUN_KEY = 'traceability.convertingRun';
const CACHE_PREFIX = 'traceability.convertingReport.';

const SECTIONS = [
  {
    badge: 'CV', title: 'Converting finished goods',
    fields: [
      { key: 'plant_id', label: 'Plant ID', type: 'select', source: '/api/params/plants' },
      { key: 'production_line', label: 'Production line', type: 'select',
        source: '/api/params/production-lines', dependsOn: 'plant_id' },
      { key: 'zfin_material_id', label: 'Produced material ID', type: 'multi', newRow: true },
      { key: 'zfin_order', label: 'Order number', type: 'multi' },
      { key: 'zfin_batch_id', label: 'ZFIN batch', type: 'multi' },
      { key: 'production_date_from', label: 'Production date FROM', type: 'date', newRow: true },
      { key: 'production_date_to', label: 'Production date TO', type: 'date' },
      { key: 'zfin_sscc', label: 'Produced SSCC', type: 'multi', newRow: true },
    ],
  },
];

const REPORTS = [
  { id: 'orders', badge: '1', label: 'Production Orders and Batches' },
  { id: 'pallets', badge: '2', label: 'Produced pallets' },
  { id: 'summary', badge: '3', label: 'Material consumption summary' },
  { id: 'details', badge: '4', label: 'Material consumption batches and handling units' },
];

function getGlobalParams() {
  try { return JSON.parse(sessionStorage.getItem(PARAMS_KEY)); }
  catch (_) { return null; }
}

function getRunId() { return sessionStorage.getItem(RUN_KEY); }

function clearReportCaches() {
  Object.keys(sessionStorage).filter(k => k.startsWith(CACHE_PREFIX))
    .forEach(k => sessionStorage.removeItem(k));
}

function saveGlobalParams(params, runId) {
  sessionStorage.setItem(PARAMS_KEY, JSON.stringify(params));
  sessionStorage.setItem(RUN_KEY, runId);
  clearReportCaches();
}

function clearGlobalParams() {
  sessionStorage.removeItem(PARAMS_KEY);
  sessionStorage.removeItem(RUN_KEY);
  clearReportCaches();
}

function loadReportCache(id) {
  try { return JSON.parse(sessionStorage.getItem(CACHE_PREFIX + id)); }
  catch (_) { return null; }
}

function saveReportCache(id, value) {
  try { sessionStorage.setItem(CACHE_PREFIX + id, JSON.stringify(value)); }
  catch (_) { console.warn('Cache full: results were not stored.'); }
}

async function startRun() {
  const response = await fetch('/api/converting/runs', { method: 'POST' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Could not start run (HTTP ${response.status}).`);
  return data.run_id;
}

async function stopRun(runId) {
  if (!runId) return null;
  const response = await fetch(
    `/api/converting/runs/${encodeURIComponent(runId)}/cancel`,
    { method: 'POST' }
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Cancellation failed.');
  return data;
}

async function fetchReport(report, params, runId, limit, offset) {
  try {
    const response = await fetch(`/api/converting/${report.id}/data`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId, params, limit, offset }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return { success: false, error: data.detail || `HTTP ${response.status}` };
    return data;
  } catch (error) {
    return { success: false, error: String(error) };
  }
}

function setStatus(id, kind, message) {
  const node = document.getElementById(id);
  node.className = `status ${kind}`;
  node.textContent = message;
}

function renderTable(head, body, columns, rows) {
  head.replaceChildren();
  body.replaceChildren();
  const heading = document.createElement('tr');
  columns.forEach(column => {
    const th = document.createElement('th');
    th.textContent = column;
    heading.appendChild(th);
  });
  head.appendChild(heading);
  if (!rows.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = Math.max(1, columns.length);
    cell.textContent = 'No records found';
    row.appendChild(cell);
    body.appendChild(row);
  }
  rows.forEach(values => {
    const row = document.createElement('tr');
    values.forEach(value => {
      const cell = document.createElement('td');
      cell.textContent = value == null ? '' : String(value);
      row.appendChild(cell);
    });
    body.appendChild(row);
  });
}

function describeParams(params) {
  if (!params) return 'No parameters applied.';
  const parts = [];
  SECTIONS.forEach(section => section.fields.forEach(field => {
    if (['plant_id', 'production_line'].includes(field.key)) return;   // shown in the small table
    const value = params[field.key];
    if (field.type === 'multi' && value?.values?.length)
      parts.push(`${field.label}: ${value.values.join(', ')}`);
    else if (field.type !== 'multi' && value)
      parts.push(`${field.label}: ${value}`);
  }));
  return parts.join(' | ');
}