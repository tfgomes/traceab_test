# Databricks notebook source
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Traceability APP - Global Parameters</title>
  <link rel="stylesheet" href="/static/common.css">
</head>
<body>
  <div class="container">
    <header>
      <img src="/static/logo.png" alt="Company Logo"
           style="position:absolute;left:40px;top:50%;transform:translateY(-50%);height:60px;background:rgba(255,255,255,0.95);padding:8px 12px;border-radius:8px;">
      <div>
        <h1>Traceability APP</h1>
        <p>Global Query Parameters</p>
      </div>
    </header>

    <div class="page-nav-wrap">
      <div class="page-nav">
        <a href="/" class="active">Global Parameters</a>
        <a href="/converting">Converting</a>
        <a href="/product-notification">Product Notification</a>
        <a href="/upstream">Upstream tracing</a>
        <a href="/downstream">Downstream tracing</a>
      </div>
    </div>

    <div class="table-section">
      <div id="sections"></div>

      <div class="filter-actions" style="margin-bottom:14px;">
        <button class="btn btn-primary" id="runBtn" onclick="runQuery()">Run Query</button>
        <button class="btn btn-secondary" onclick="clearAll()">Clear All</button>
      </div>

      <div id="runStatus" class="status"></div>
      <div id="reportStatusList" class="report-status-list"></div>
    </div>
  </div>

  <script src="/static/common.js"></script>
  <script>
    const controls = {};   // field.key -> { get(), set(value), reset(), commit() }

    function el(tag, cls, text) {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    }
    const numOrNull = v => (v === '' || v === null || isNaN(v)) ? null : Math.max(0, parseInt(v, 10));

    /* ---------- field builders ---------- */
    function buildMulti(field, row) {
      let values = [];
      const box = el('div', 'chip-box');
      const input = el('input');
      input.type = 'text';
      input.placeholder = 'Type a value + Enter (or paste a comma-separated list)';
      const dl = el('datalist'); dl.id = `dl_${field.key}`;
      input.setAttribute('list', dl.id);
      box.appendChild(input);

      const lower = el('input'); lower.type = 'number'; lower.min = 0; lower.placeholder = 'Lower';
      const upper = el('input'); upper.type = 'number'; upper.min = 0; upper.placeholder = 'Upper';

      function renderChips() {
        box.querySelectorAll('.chip').forEach(c => c.remove());
        values.forEach(v => {
          const chip = el('span', 'chip', v);
          const x = el('button', 'chip-x', '×'); x.type = 'button';
          x.onclick = () => { values = values.filter(a => a !== v); renderChips(); };
          chip.appendChild(x);
          box.insertBefore(chip, input);
        });
      }
      function addFromText(text) {
        text.split(/[,;\n]/).map(s => s.trim()).filter(Boolean)
            .forEach(v => { if (!values.includes(v)) values.push(v); });
        input.value = '';
        renderChips();
      }

      let timer;
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); addFromText(input.value); }
        if (e.key === 'Backspace' && !input.value && values.length) { values.pop(); renderChips(); }
      });
      input.addEventListener('blur', () => addFromText(input.value));
      input.addEventListener('input', () => {
        if (/[,;\n]/.test(input.value)) { addFromText(input.value); return; }
        clearTimeout(timer);
        const q = input.value.trim();
        if (q.length < 2) return;
        timer = setTimeout(async () => {
          try {
            const r = await fetch(`/api/params/suggest?field=${field.key}&q=${encodeURIComponent(q)}`);
            const data = await r.json();
            dl.replaceChildren(...(data.values || []).map(v => { const o = el('option'); o.value = v; return o; }));
          } catch (e) { /* suggestions are optional */ }
        }, 300);
      });

      row.append(box, lower, upper, dl);
      controls[field.key] = {
        commit: () => addFromText(input.value),
        get: () => ({ values: [...values], lower: numOrNull(lower.value), upper: numOrNull(upper.value) }),
        set: v => { values = [...((v && v.values) || [])]; lower.value = (v && v.lower) ?? ''; upper.value = (v && v.upper) ?? ''; renderChips(); },
        reset: () => { values = []; input.value = ''; lower.value = ''; upper.value = ''; renderChips(); },
      };
    }

    function buildSelect(field, row) {
      const sel = el('select');
      sel.appendChild(new Option('Any', ''));
      row.classList.add('wide'); row.appendChild(sel);
      let pending = null;
      fetch(field.source).then(r => r.json()).then(data => {
        (data.values || []).forEach(v => sel.appendChild(new Option(v, v)));
        if (pending) sel.value = pending;
        if (data.error) console.warn('Production lines:', data.error);
      }).catch(e => console.warn('Could not load options for', field.key, e));
      controls[field.key] = {
        commit: () => {},
        get: () => sel.value || null,
        set: v => { pending = v; sel.value = v || ''; },
        reset: () => { pending = null; sel.value = ''; },
      };
    }

    function buildDatetimeRange(field, row) {
      const from = el('input'); from.type = 'datetime-local';
      const to = el('input'); to.type = 'datetime-local';
      row.classList.add('wide'); row.append(from, to);
      controls[field.key] = {
        commit: () => {},
        get: () => ({ from: from.value || null, to: to.value || null }),
        set: (p) => { from.value = p.from || ''; to.value = p.to || ''; },
        reset: () => { from.value = ''; to.value = ''; },
      };
    }

    /* ---------- page rendering ---------- */
    function renderSections() {
      const host = document.getElementById('sections');
      SECTIONS.forEach(section => {
        const card = el('div', 'report-card');
        const title = el('div', 'report-title');
        title.append(el('span', 'report-badge', section.badge), el('h2', '', section.title));
        card.appendChild(title);

        const head = el('div', 'param-head');
        head.append(el('span', '', 'Parameter'), el('span', '', 'Value(s)'), el('span', '', 'Lower range'), el('span', '', 'Upper range'));
        card.appendChild(head);

        section.fields.forEach(field => {
          const row = el('div', 'param-row');
          row.appendChild(el('label', 'param-label', field.label));
          if (field.type === 'multi') buildMulti(field, row);
          else if (field.type === 'select') buildSelect(field, row);
          else if (field.type === 'datetime-range') buildDatetimeRange(field, row);
          card.appendChild(row);
        });
        host.appendChild(card);
      });
    }

    function collectParams() {
      const p = {};
      SECTIONS.forEach(s => s.fields.forEach(f => {
        controls[f.key].commit();
        const v = controls[f.key].get();
        if (f.type === 'datetime-range') { p[f.key + '_from'] = v.from; p[f.key + '_to'] = v.to; }
        else p[f.key] = v;
      }));
      return p;
    }

    function applyParams(p) {
      if (!p) return;
      SECTIONS.forEach(s => s.fields.forEach(f => {
        if (f.type === 'datetime-range') controls[f.key].set({ from: p[f.key + '_from'], to: p[f.key + '_to'] });
        else controls[f.key].set(p[f.key]);
      }));
    }

    function validate(p) {
      const a = p.production_datetime_from, b = p.production_datetime_to;
      if (a && b && a > b) { alert('Production date/time "from" cannot be after "to".'); return false; }
      return true;
    }

    /* ---------- actions ---------- */
    async function runQuery() {
      const p = collectParams();
      if (!validate(p)) return;

      saveGlobalParams(p);                       // stores params and clears stale results
      const runBtn = document.getElementById('runBtn'); runBtn.disabled = true;
      setStatus('runStatus', 'loading', 'Running reports...');

      const list = document.getElementById('reportStatusList'); list.innerHTML = '';
      const lines = REPORTS.map(r => { const d = el('div', 'report-status-item', `${r.label}: loading...`); list.appendChild(d); return d; });

      await Promise.all(REPORTS.map(async (r, i) => {
        const data = await fetchReport(r, p, 10, 0);
        if (data.success) {
          saveReportCache(r.id, { columns: data.columns, rows: data.rows, hasMore: data.has_more, offset: 0, limit: 10 });
          lines[i].textContent = `✔ ${r.label}: ${data.rows.length}${data.has_more ? '+' : ''} rows loaded`;
        } else {
          lines[i].textContent = `✖ ${r.label}: ${data.error}`;
        }
      }));

      setStatus('runStatus', 'ok', 'Parameters applied. Open any report tab to see the results.');
      runBtn.disabled = false;
    }

    function clearAll() {
      SECTIONS.forEach(s => s.fields.forEach(f => controls[f.key].reset()));
      clearGlobalParams();
      document.getElementById('reportStatusList').innerHTML = '';
      setStatus('runStatus', 'ok', 'Parameters and results cleared.');
    }

    /* ---------- init ---------- */
    renderSections();
    applyParams(getGlobalParams());              // restore when coming back to this page
    setStatus('runStatus', 'ok', 'Set the parameters and click "Run Query".');
    document.addEventListener('keydown', e => { if (e.key === 'Enter' && e.ctrlKey) runQuery(); });
  </script>
</body>
</html>