/*
  Sprint 3 global-parameters panel (shared by every report page).
  - Fields, labels, pills, order, asterisks and fixed values come from SECTIONS, which common.js
    loads from master_config_global_param (fallback: built-in defaults). Nothing is hard-coded here.
  - Left rail: Papermaking / Converting (one active at a time)
  - Section pills: only ONE section of the active profile is visible at a time
  - One shared draft in sessionStorage -> same selection on every page
  - SYNC switch (+ UP/DOWN when ON, DOWN by default), Run / Stop / Clear, last-run timestamp
  - "Pending changes" marker + hints when parameters or mode differ from the last run
  - Mandatory parameters are validated PER REPORT by the backend (master_config_report_columns).
*/
(() => {
  "use strict";

  const api = window.TraceabilityPhaseB;
  const DRAFT_KEY = "traceability.draft";   // live (unsaved) form values
  const UI_KEY = "traceability.ui";         // which tab/section is open, panel hidden
  const SIG_KEY = "traceability.lastSig";   // signature (params + mode) of the last run
  const UI_VERSION = "sprint3-ui-1";        // must match report_page.js
  const PAGE_SIZE = 10;
  const MULTI_TYPES = new Set(["multi", "multiselect", "enum-multi"]);
  const PLACEHOLDER_TEXT = "-- Select --";   // same text on every dropdown

  // Text shown next to the SYNC switch for each mode
  const MODE_HINTS = {
    unsync: "Global parameters only",
    up: "Following upstream flow",
    down: "Following downstream flow",
  };

  // Shown whenever parameters or mode changed since the last run (queries are never run automatically)
  const PENDING_MESSAGE = "Changes pending. Click Run Query to refresh the reports.";
  const PENDING_SHORT = "Run Query to update";   // reminder shown under the toolbar hint

  // ---------- small helpers ----------
  const readJson = (k, d) => { try { return JSON.parse(sessionStorage.getItem(k)) ?? d; } catch { return d; } };
  const writeJson = (k, v) => sessionStorage.setItem(k, JSON.stringify(v));
  const clone = (v) => JSON.parse(JSON.stringify(v));
  const isEmpty = (v) => v == null || v === "" || (Array.isArray(v) && !v.length);
  function el(tag, cls = "", text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  // ---------- state ----------
  const draft = readJson(DRAFT_KEY, {});
  const ui = readJson(UI_KEY, {});
  ui.active = ui.active || {};          // per page: { profile, section: { pm: key, cv: key } }
  const controls = new Map();           // field key -> [control]  (same key can appear in 2 sections)
  let FIELDS = [];                      // unique fields by key; filled in mount() once definitions are loaded
  let onChange = () => {};              // set by mount(); called after any parameter/mode change

  // Unique fields by key (e.g. plant appears in both profiles).
  function collectFields() {
    FIELDS = [];
    api.SECTIONS.forEach((g) => g.sections.forEach((s) => s.fields.forEach((f) => {
      if (!FIELDS.some((x) => x.key === f.key)) FIELDS.push(f);
    })));
  }

  // Single entry point for every change: store, persist, refresh all views of the key,
  // and reload dependent option lists (e.g. production lines depend on their section's plant).
  function commit(key, value) {
    draft[key] = value;
    writeJson(DRAFT_KEY, draft);
    (controls.get(key) || []).forEach((c) => c.refresh());
    FIELDS.filter((f) => f.dependsOn === key)
      .forEach((f) => (controls.get(f.key) || []).forEach((c) => c.reload && c.reload()));
    onChange();
  }
  function register(key, ctl) {
    if (!controls.has(key)) controls.set(key, []);
    controls.get(key).push(ctl);
  }

  // Options come from the backend. The backend always reads the plant from `plant_code_name`,
  // whichever plant field (PM or Combiner) this dropdown depends on.
  async function loadOptions(f) {
    let url = f.source;
    if (f.dependsOn && draft[f.dependsOn]) url += `?plant_code_name=${encodeURIComponent(draft[f.dependsOn])}`;
    try {
      const data = await (await fetch(url)).json();
      return { values: data.values || [], error: data.error || null };
    } catch (e) {
      return { values: [], error: String(e) };
    }
  }

  // ---------- field builders (each returns a DOM node and registers a control) ----------
  function buildSelect(f) {
    const sel = el("select");
    const ctl = {
      refresh() {
        const v = draft[f.key] || "";
        if (v && ![...sel.options].some((o) => o.value === v)) sel.append(new Option(v, v));
        sel.value = v;
      },
      async reload() {
        const { values } = await loadOptions(f);
        sel.replaceChildren(new Option(PLACEHOLDER_TEXT, ""));
        values.forEach((v) => sel.append(new Option(v, v)));
        ctl.refresh();
      },
    };
    sel.onchange = () => commit(f.key, sel.value);
    register(f.key, ctl);
    ctl.reload();
    return sel;
  }

  // Checkbox dropdown used by both "multiselect" (backend options) and "enum-multi" (fixed options).
  function buildDropdown(f) {
    const wrap = el("div", "dropdown-control");
    const det = el("details", "line-dropdown");
    const sum = el("summary");
    const label = el("span");
    const list = el("div", "line-options");
    const err = el("div", "dropdown-error");
    sum.append(label);
    det.append(sum, list);
    wrap.append(det, err);

    let options = [];
    const selected = () => draft[f.key] || [];

    const ctl = {
      refresh() {
        label.textContent = selected().length ? selected().join(", ") : PLACEHOLDER_TEXT;
        list.replaceChildren();
        if (!options.length) list.append(el("div", "dropdown-note", "No options available"));
        options.forEach((o) => {
          const row = el("label", "line-option");
          const cb = el("input");
          cb.type = "checkbox";
          cb.checked = selected().includes(o);
          cb.onchange = () => {
            const s = new Set(selected());
            cb.checked ? s.add(o) : s.delete(o);
            commit(f.key, [...s]);
          };
          row.append(cb, document.createTextNode(o));
          list.append(row);
        });
      },
      async reload() {
        if (f.type === "enum-multi") {
          options = [...new Set(f.allowed || [])];      // de-duplicated fixed list
        } else {
          const res = await loadOptions(f);
          options = res.values;
          err.textContent = res.error || "";
          // drop selections that no longer exist (e.g. plant changed)
          if (!res.error && options.length) {
            const kept = selected().filter((v) => options.includes(v));
            if (kept.length !== selected().length) { commit(f.key, kept); return; }
          }
        }
        ctl.refresh();
      },
    };
    register(f.key, ctl);
    ctl.reload();
    return wrap;
  }

  // Free-text multi value (chips). Split on comma / space / semicolon / newline.
  function buildChips(f) {
    const box = el("div", "chip-box");
    const input = el("input");
    input.placeholder = "Type and press Enter";
    box.append(input);

    const add = () => {
      const cur = draft[f.key] || [];
      const next = [...new Set([...cur, ...input.value.split(/[\s,;]+/).filter(Boolean)])];
      input.value = "";
      if (next.length !== cur.length) commit(f.key, next);
    };
    input.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
      else if (e.key === "Backspace" && !input.value && (draft[f.key] || []).length) {
        commit(f.key, draft[f.key].slice(0, -1));
      }
    };
    input.onblur = add;
    input.onpaste = () => setTimeout(add, 0);

    const ctl = {
      refresh() {
        box.querySelectorAll(".chip").forEach((c) => c.remove());
        (draft[f.key] || []).forEach((v) => {
          const chip = el("span", "chip", v);
          const x = el("button", "chip-x", "×");
          x.type = "button";
          x.onclick = () => commit(f.key, draft[f.key].filter((i) => i !== v));
          chip.append(x);
          box.insertBefore(chip, input);
        });
      },
    };
    register(f.key, ctl);
    ctl.refresh();
    return box;
  }

  function buildInput(f) {            // date / time
    const input = el("input");
    input.type = f.type;
    input.onchange = () => commit(f.key, input.value);
    register(f.key, { refresh() { input.value = draft[f.key] || ""; } });
    input.value = draft[f.key] || "";
    return input;
  }

  function buildControl(f) {
    if (f.type === "select") return buildSelect(f);
    if (f.type === "multiselect" || f.type === "enum-multi") return buildDropdown(f);
    if (f.type === "multi") return buildChips(f);
    return buildInput(f);
  }

  function buildCell(f) {
    const cell = el("div", "param-cell");
    const label = el("label", "param-label", f.label);
    if (f.required) {
      const star = el("span", "required-star", "*");
      star.title = "Mandatory for some reports in UNSYNC mode";
      label.append(star);
    }
    const ctl = el("div", "param-ctl");
    ctl.append(buildControl(f));
    cell.append(label, ctl);
    return cell;
  }

  // ---------- one section body (title is shown by the section pill) ----------
  function renderSectionBody(section) {
    const body = el("div", "gp-section");
    if (section.placeholder) {
      body.append(el("p", "section-description", section.description || "Reserved."));
      return body;
    }
    const grid = el("div", "param-grid");
    const rows = new Map();                         // fields sharing `row` are shown side by side
    section.fields.forEach((f) => {
      if (f.row) {
        if (!rows.has(f.row)) rows.set(f.row, el("div", "datetime-row"));
        rows.get(f.row).append(buildCell(f));
      } else {
        grid.append(buildCell(f));
      }
    });
    body.append(grid);
    if (rows.size) {
      const wrap = el("div", "datetime-rows");
      rows.forEach((r) => wrap.append(r));
      body.append(wrap);
    }
    return body;
  }

  // ---------- panel: left rail (profiles) + pills (sections) ----------
  function renderPanel(host, pageProfile) {
    host.replaceChildren();

    // Remembered choice per page; default = this page's profile and its first non-placeholder section.
    const state = ui.active[pageProfile] || (ui.active[pageProfile] = { profile: pageProfile, section: {} });
    const save = () => writeJson(UI_KEY, ui);

    const layout = el("div", "gp-layout");
    const rail = el("aside", "profile-tabs");
    const content = el("div", "profile-content");
    layout.append(rail, content);
    host.append(layout);

    const views = [];   // { group, tab, pane, pills: [{section, btn, body}] }

    api.SECTIONS.forEach((group) => {
      const tab = el("button", "profile-tab-btn", group.title.replace(/ Parameters$/, ""));
      tab.type = "button";
      rail.append(tab);

      const pane = el("div", "gp-pane");
      const bar = el("div", "section-pills");
      pane.append(bar);

      const pills = group.sections.map((section) => {
        const btn = el("button", "section-pill", section.title);
        btn.type = "button";
        const body = renderSectionBody(section);
        bar.append(btn);
        pane.append(body);
        return { section, btn, body };
      });

      // Reset a stale choice (e.g. the config table renamed or removed that section)
      if (!group.sections.some((s) => s.key === state.section[group.profile])) {
        const first = group.sections.find((s) => !s.placeholder) || group.sections[0];
        state.section[group.profile] = first.key;
      }

      content.append(pane);
      views.push({ group, tab, pane, pills });
    });

    if (!views.some((v) => v.group.profile === state.profile)) state.profile = pageProfile;

    // Show exactly one profile and one section; everything else stays mounted but hidden.
    function show() {
      views.forEach((v) => {
        const activeProfile = v.group.profile === state.profile;
        v.tab.classList.toggle("active", activeProfile);
        v.pane.hidden = !activeProfile;
        v.pills.forEach((p) => {
          const activeSection = p.section.key === state.section[v.group.profile];
          p.btn.classList.toggle("active", activeSection);
          p.body.hidden = !activeSection;
        });
      });
      save();
    }

    views.forEach((v) => {
      v.tab.onclick = () => { state.profile = v.group.profile; show(); };
      v.pills.forEach((p) => { p.btn.onclick = () => { state.section[v.group.profile] = p.section.key; show(); }; });
    });
    show();

    // Hide/Show the whole panel (button lives next to the "Global Parameters" title).
    const title = document.querySelector(".gp-card-title");
    if (title && !title.querySelector(".collapse-btn")) {
      const btn = el("button", "collapse-btn");
      btn.type = "button";
      const apply = () => { host.hidden = Boolean(ui.panelHidden); btn.textContent = ui.panelHidden ? "Show" : "Hide"; };
      btn.onclick = () => { ui.panelHidden = !ui.panelHidden; save(); apply(); };
      title.append(btn);
      apply();
    }

    // close open dropdowns on outside click
    document.addEventListener("click", (e) => {
      document.querySelectorAll("details.line-dropdown[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
    });
  }

  // ---------- SYNC toolbar: [SYNC (ON/OFF switch)] [UP | DOWN when ON] [hint + pending reminder] ----------
  function renderToolbar(host, onModeChange) {
    const draw = () => {
      const mode = api.getMode();
      host.replaceChildren();   // only the children are rebuilt, so the host's "has-pending" class survives

      // Switch shows the CURRENT state (blue/right = ON, grey/left = OFF)
      host.append(el("span", "sync-title", "SYNC"));
      const sw = el("button", `sync-switch ${mode.sync ? "on" : "off"}`);
      sw.type = "button";
      sw.setAttribute("role", "switch");
      sw.setAttribute("aria-checked", String(mode.sync));
      sw.title = mode.sync ? "SYNC is ON - click to switch OFF (UNSYNC)" : "SYNC is OFF (UNSYNC) - click to switch ON";
      sw.append(el("span", "sync-switch-text", mode.sync ? "ON" : "OFF"), el("span", "sync-knob"));
      // Turning SYNC ON always starts with DOWN selected (UP can be chosen afterwards)
      sw.onclick = () => { api.setMode({ sync: !mode.sync, direction: "down" }); draw(); onModeChange(); };
      host.append(sw);

      // UP/DOWN only appears when SYNC is active
      if (mode.sync) {
        const seg = el("div", "flow-segment");
        [
          { id: "up", tip: "UP - follow the upstream supply chain" },
          { id: "down", tip: "DOWN - follow the downstream supply chain" },
        ].forEach(({ id, tip }) => {
          const b = el("button", mode.direction === id ? "active" : "", id.toUpperCase());
          b.type = "button";
          b.title = tip;
          b.onclick = () => { api.setMode({ sync: true, direction: id }); draw(); onModeChange(); };
          seg.append(b);
        });
        host.append(seg);
      }

      // Hint on the first line; "Run Query to update" reminder below it (visible only while changes are pending)
      const info = el("div", "sync-info");
      info.append(
        el("span", "sync-hint", MODE_HINTS[mode.sync ? mode.direction : "unsync"]),
        el("span", "sync-pending", PENDING_SHORT)
      );
      host.append(info);

      document.body.classList.toggle("sync-on", mode.sync);   // CSS dims mandatory stars
    };
    draw();
  }

  // ---------- run / stop / clear ----------
  const toFlat = () => {
    const out = {};
    FIELDS.forEach((f) => {
      const v = draft[f.key];
      out[f.key] = MULTI_TYPES.has(f.type) ? { values: v || [] } : (isEmpty(v) ? null : v);
    });
    return out;
  };

  // Signature of what a run would use (parameters + mode); compared with the last run's signature.
  const currentSignature = () => JSON.stringify({ params: toFlat(), mode: api.getMode() });

  // report_page.js reads api.getGlobalParams().byProfile[profile]; every profile gets the same values.
  function snapshotFor() {
    const flat = toFlat();
    const byProfile = {};
    api.PROFILE_TABS.forEach((t) => { byProfile[t.id] = { ...clone(flat), active_profile: t.id === "pm" ? "pm_prod" : "cv_prod" }; });
    return { ...flat, byProfile };
  }

  async function mount({ profile, page }) {
    // Visible feedback while the definitions load (the first load can take a while if the warehouse was idle)
    const panelHost = document.getElementById("sections");
    if (panelHost) panelHost.textContent = "Loading parameters...";

    await api.ready;          // parameter definitions (table or fallback) are loaded
    collectFields();

    const $ = (id) => document.getElementById(id);
    const runBtn = $("runBtn"), stopBtn = $("stopBtn"), clearBtn = $("clearBtn");
    const statusNode = $("runStatus");
    const toolbar = $("syncToolbar");
    const status = (kind, msg) => api.setStatus("runStatus", kind, msg);
    const paintLastRun = () => { $("lastRunLabel").textContent = api.formatRunTimestamp(api.getLastRunAt()); };
    let currentRun = null, stopped = false, busy = false;

    const setBusy = (b) => { busy = b; runBtn.disabled = b; clearBtn.disabled = b; stopBtn.disabled = !b; };

    // Amber glow + dot on Run Query, a hint in the panel and a reminder in the toolbar
    // when parameters or mode changed since the last run.
    // Nothing runs automatically: queries are expensive, so the user decides when to refresh.
    const refreshPending = () => {
      const last = sessionStorage.getItem(SIG_KEY);
      const pending = last !== null && last !== currentSignature();
      runBtn.classList.toggle("pending", pending);
      runBtn.textContent = pending ? "Run Query •" : "Run Query";
      runBtn.title = pending ? "Parameters or mode changed since the last run" : "";
      toolbar.classList.toggle("has-pending", pending);
      if (pending) status("ok", PENDING_MESSAGE);
      else if (statusNode.textContent === PENDING_MESSAGE) { statusNode.className = "status"; statusNode.textContent = ""; }
    };
    onChange = refreshPending;

    renderPanel($("sections"), profile);
    renderToolbar(toolbar, refreshPending);
    paintLastRun();
    refreshPending();

    // Tell the user when the config table could not be read and the built-in definitions are used
    if (api.definitionState.error && !statusNode.textContent) {
      status("ok", `Using built-in parameter definitions (config table unavailable): ${api.definitionState.error}`);
    }

    // Version badge comes from the backend (APP_VERSION) so it never gets stale.
    fetch("/api/version").then((r) => r.json()).then((v) => {
      const badge = $("appVersion");
      if (badge && v.version) { badge.textContent = `Version: ${v.version}`; badge.title = `${v.environment} · schema ${v.schema}`; }
    }).catch(() => {});

    runBtn.onclick = async () => {
      if (busy) return;
      const mode = api.getMode();

      // NOTE: no mandatory-field check here. The backend validates the Mandatory flags of each
      // report (master_config_report_columns) and returns a clear message per report card.
      stopped = false;
      setBusy(true);
      status("loading", `Running (${mode.sync ? "SYNC-" + mode.direction.toUpperCase() : "UNSYNC"})...`);
      try {
        const previous = api.getRunId(profile);
        if (previous) api.stopRun(previous).catch(() => {});
        currentRun = await api.startRun();
        api.setRunId(profile, currentRun);
        api.clearReportCaches(page);
        const saved = snapshotFor();
        api.saveGlobalParams(saved);

        // Pre-load page 1 of every report of this page in parallel; report_page.js renders from cache.
        // A report that exceeds the backend time limit comes back as an error message on its own card.
        const reports = profile === "pm" ? api.REPORTS_PAPERMAKING : api.REPORTS_CONVERTING;
        const params = saved.byProfile[profile];
        const results = await Promise.all(reports.map((r) => api.fetchReport(page, r, clone(params), currentRun, PAGE_SIZE, 0, mode)));
        if (stopped) { api.clearReportCaches(page); setBusy(false); return status("err", "Run stopped."); }

        results.forEach((d, i) => {
          const cache = { uiVersion: UI_VERSION, runId: currentRun, profile, offset: 0, limit: PAGE_SIZE, sync: mode.sync, direction: mode.direction };
          if (d.success) Object.assign(cache, { columns: d.columns || [], rows: d.rows || [], hasMore: Boolean(d.has_more) });
          else Object.assign(cache, { error: typeof d.error === "string" ? d.error : "The report could not be generated.", missing: Boolean(d.missing), timeout: Boolean(d.timeout) });
          api.saveReportCache(reports[i].id, cache, page);
        });
        api.setLastRunAt(new Date().toISOString());
        sessionStorage.setItem(SIG_KEY, currentSignature());   // clears the pending marker
        location.reload();                            // report_page.js redraws from cache
      } catch (e) {
        setBusy(false);
        status("err", e.message || String(e));
      }
    };

    stopBtn.onclick = async () => {
      stopped = true;
      try { await api.stopRun(currentRun); } catch (e) { status("err", e.message || String(e)); }
    };

    clearBtn.onclick = () => {
      sessionStorage.removeItem(DRAFT_KEY);
      sessionStorage.removeItem(SIG_KEY);
      api.clearGlobalParams();                        // also clears runs, report caches and last run
      location.reload();
    };
  }

  window.TraceabilityParamsPanel = { mount };
})();