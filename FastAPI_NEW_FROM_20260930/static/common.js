/* Shared by every page. */

const PARAMS_KEY = 'traceability.globalParams';
const CACHE_PREFIX = 'traceability.report.';

/* ---- Field definitions: to add a parameter or a section, edit only this ----
   type: 'select' | 'multi' | 'date' | 'time'
   dependsOn: key of another select; its value is sent as ?<key>=<value>
   newRow: start this field on a new grid row
   range: false hides the lower/upper boxes on a multi field                    */
const SECTIONS = [
  {
    id: 'cv_prod', badge: 'CV', title: 'Converting finished goods',
    fields: [
      { key: 'plant_id',        label: 'Plant ID',        type: 'select', source: '/api/params/plants' },
      { key: 'production_line', label: 'Production line', type: 'select', source: '/api/params/production-lines', dependsOn: 'plant_id' },

      { key: 'zfin_material_id', label: 'Produced material ID', type: 'multi', newRow: true },
      { key: 'zfin_order',       label: 'Order number',         type: 'multi' },
      { key: 'zfin_batch_id',    label: 'ZFIN batch',           type: 'multi' },

      { key: 'production_date_from', label: 'Production date FROM', type: 'date', newRow: true },
      { key: 'production_time_from', label: 'Production time FROM', type: 'time' },
      { key: 'production_date_to',   label: 'Production date TO',   type: 'date', newRow: true },
      { key: 'production_time_to',   label: 'Production time TO',   type: 'time' },

      { key: 'zfin_sscc', label: 'Produced SSCC', type: 'multi', newRow: true },
    ],
  },
];

/* ---- Reports that Run Query refreshes: add one line per new report ---- */
const REPORTS = [
  { id: 'converting', label: 'Converting', url: '/api/converting/data' },
];

/* ---- Global parameters (per browser tab, survive page navigation) ---- */
function getGlobalParams() {
  try { return JSON.parse(sessionStorage.getItem(PARAMS_KEY)); } catch (e) { return null; }
}
function saveGlobalParams(p) {
  sessionStorage.setItem(PARAMS_KEY, JSON.stringify(p));
  clearReportCaches();                       // new parameters -> old results are stale
}
function clearGlobalParams() {
  sessionStorage.removeItem(PARAMS_KEY);
  clearReportCaches();
}

/* ---- Report result cache ---- */
function loadReportCache(id) {
  try { return JSON.parse(sessionStorage.getItem(CACHE_PREFIX + id)); } catch (e) { return null; }
}
function saveReportCache(id, data) {
  try { sessionStorage.setItem(CACHE_PREFIX + id, JSON.stringify(data)); }
  catch (e) { console.warn('Result too large to cache; it will be re-queried on demand.'); }
}
function clearReportCaches() {
  Object.keys(sessionStorage)
    .filter(k => k.startsWith(CACHE_PREFIX))
    .forEach(k => sessionStorage.removeItem(k));
}

/* ---- Backend call: params travel in the JSON body ---- */
async function fetchReport(report, params, limit, offset) {
  try {
    const res = await fetch(report.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ params, limit, offset }),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data) return { success: false, error: `HTTP ${res.status}: ${JSON.stringify(data && data.detail || '')}` };
    return data;
  } catch (e) {
    return { success: false, error: e.message };
  }
}

/* ---- Small helpers ---- */
function setStatus(elId, cls, text) {
  const el = document.getElementById(elId);
  el.textContent = text;
  el.className = `status ${cls}`;
}

function renderTable(theadEl, tbodyEl, columns, rows) {
  theadEl.innerHTML = '';
  tbodyEl.innerHTML = '';
  const hr = document.createElement('tr');
  columns.forEach(c => { const th = document.createElement('th'); th.textContent = c; hr.appendChild(th); });
  theadEl.appendChild(hr);

  if (!rows.length) {
    const tr = document.createElement('tr'), td = document.createElement('td');
    td.colSpan = columns.length; td.style.cssText = 'text-align:center;padding:40px;color:#666;';
    td.textContent = 'No records found';
    tr.appendChild(td); tbodyEl.appendChild(tr);
    return;
  }
  rows.forEach(row => {
    const tr = document.createElement('tr');
    row.forEach(v => {
      const td = document.createElement('td');
      td.textContent = (v !== null && v !== undefined) ? v : '';
      td.title = td.textContent;
      tr.appendChild(td);
    });
    tbodyEl.appendChild(tr);
  });
}

/* Human-readable one-line summary of the active parameters */
function describeParams(p) {
  if (!p) return 'No parameters set.';
  const parts = [];
  SECTIONS.forEach(s => s.fields.forEach(f => {
    if (f.type === 'multi') {
      const v = p[f.key];
      if (v && v.values && v.values.length) {
        const range = (v.lower || v.upper) ? ` (-${v.lower || 0} / +${v.upper || 0})` : '';
        parts.push(`${f.label}: ${v.values.join(', ')}${range}`);
      }
    } else if (f.type === 'select') {
      if (p[f.key]) parts.push(`${f.label}: ${p[f.key]}`);
    }
  }));
  const a = p.production_datetime_from, b = p.production_datetime_to;
  if (a || b) parts.push(`Production: ${a || '…'} → ${b || '…'}`);
  return parts.length ? parts.join('  |  ') : 'No filters (all data).';
}