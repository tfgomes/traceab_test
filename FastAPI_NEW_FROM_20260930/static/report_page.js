/*
  Sprint 3 report-page controller
  - Builds one card per report (list comes from common.js, so there is a single source of truth)
  - Renders first-page results cached by params_panel.js (Run Query)
  - Supports refresh, pagination, rows-per-page, copy to clipboard and XLSX export
  - Uses the shared SYNC/UNSYNC + UP/DOWN mode from common.js
  To add or rename a report: edit REPORTS_PAPERMAKING / REPORTS_CONVERTING in common.js.
*/

(async () => {
  "use strict";

  const UI_VERSION = "sprint3-ui-1";   // must match params_panel.js (cache compatibility)
  const DEFAULT_PAGE_SIZE = 10;
  const PAGE_SIZES = [10, 25, 50, 100];

  const api = window.TraceabilityPhaseB;
  const profile = document.body?.dataset?.reportProfile;   // "pm" | "cv"
  const host = document.getElementById("reports");
  const errorNode = document.getElementById("pageError");

  // ---------- helpers ----------
  function fail(msg) {
    if (errorNode) {
      errorNode.className = "status err";
      errorNode.textContent = msg;
    }
    console.error("[report_page]", msg);
  }

  function make(tag, className = "", text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const deepClone = (v) => JSON.parse(JSON.stringify(v));

  function detailMessage(value) {
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map((x) => x?.msg || JSON.stringify(x)).join("; ");
    return value ? JSON.stringify(value) : "";
  }

  const modeLabel = (mode) => (mode.sync ? `SYNC-${String(mode.direction || "up").toUpperCase()}` : "UNSYNC");

  // ---------- init checks ----------
  if (!api) return fail("Init failed: common.js missing.");

  // Report lists are defined once in common.js; map them to the card format used here.
  const REPORTS = {
    pm: (api.REPORTS_PAPERMAKING || []).map((r) => ({ id: r.id, number: r.badge, title: r.label })),
    cv: (api.REPORTS_CONVERTING || []).map((r) => ({ id: r.id, number: r.badge, title: r.label })),
  };

  if (!profile || !REPORTS[profile]) return fail("Init failed: invalid page profile.");
  if (!host) return fail("Init failed: #reports not found.");

  await api.ready;   // parameter definitions (SECTIONS) must be loaded before building request params

  const pageName = profile === "pm" ? api.PAGE_PAPERMAKING : api.PAGE_CONVERTING;
  const runId = api.getRunId(profile);
  const globals = api.getGlobalParams() || {};
  const snapshot = globals.byProfile?.[profile] || null;   // parameters pinned by the last Run Query

  // ---------- parameters ----------
  /*
    Build the request params from the saved snapshot.
    Every field defined in SECTIONS gets a default, so new parameters need no change here:
    multi-value fields -> {values: []}, others -> null.
  */
  const MULTI_TYPES = new Set(["multi", "multiselect", "enum-multi"]);

  function buildParams(saved) {
    const p = saved || {};
    const out = { active_profile: profile === "pm" ? "pm_prod" : "cv_prod" };
    api.SECTIONS.forEach((group) =>
      group.sections.forEach((section) =>
        section.fields.forEach((f) => {
          if (f.key in out) return;
          out[f.key] = MULTI_TYPES.has(f.type) ? p[f.key] || { values: [] } : p[f.key] || null;
        })
      )
    );
    return out;
  }

  const params = buildParams(snapshot);

  const currentRunStillValid = () => Boolean(runId && api.getRunId(profile) === runId);

  // ---------- clipboard ----------
  // Tab-separated text pastes cleanly into Excel, e-mails and chat tools.
  function toClipboardText(columns, rows) {
    const clean = (v) => (v == null ? "" : String(v).replace(/[\t\r\n]+/g, " "));
    const lines = [columns.map(clean).join("\t")];
    rows.forEach((row) => lines.push((row || []).map(clean).join("\t")));
    return lines.join("\n");
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fallback for browsers/iframes where the async clipboard API is blocked.
      const area = make("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      area.remove();
      return ok;
    }
  }

  // ---------- one card per report ----------
  REPORTS[profile].forEach((report) => {
    const card = make("section", "report-card");

    // title
    const heading = make("div", "report-title");
    heading.append(
      make("span", "report-badge report-number-badge", report.number),
      make("h2", "", report.title)
    );
    card.appendChild(heading);

    // status line
    const status = make("div", "status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    card.appendChild(status);

    // table
    const wrapper = make("div", "table-wrapper");
    const table = make("table");
    const head = make("thead");
    const body = make("tbody");
    table.append(head, body);
    wrapper.appendChild(table);
    card.appendChild(wrapper);

    // controls
    const controls = make("div", "table-controls");
    const left = make("div", "controls-left");
    const count = make("span", "record-count");
    const pageSizeWrap = make("label", "page-size-wrap", "Rows per page:");
    const pageSize = make("select");
    PAGE_SIZES.forEach((n) => pageSize.append(new Option(String(n), String(n))));
    pageSizeWrap.appendChild(pageSize);
    left.append(count, pageSizeWrap);

    const right = make("div", "pagination");
    const btnPrev = make("button", "btn-small", "<- Previous");
    const pageInfo = make("span", "page-info");
    const btnNext = make("button", "btn-small", "Next ->");
    const btnRefresh = make("button", "btn-small", "Refresh");
    const btnCopy = make("button", "btn-small", "Copy");
    const btnExport = make("button", "btn btn-export", "Export XLSX");
    [btnPrev, btnNext, btnRefresh, btnCopy, btnExport].forEach((b) => { b.type = "button"; });
    btnCopy.title = "Copy the rows shown to the clipboard (paste into Excel, e-mail or chat)";

    right.append(btnPrev, pageInfo, btnNext, btnRefresh, btnCopy, btnExport);
    controls.append(left, right);
    card.appendChild(controls);
    host.appendChild(card);

    // cached first page prepared by Run Query (ignored if from another run/profile/UI version)
    let cache = api.loadReportCache(report.id, pageName);
    if (cache?.runId !== runId || cache?.profile !== profile || cache?.uiVersion !== UI_VERSION) {
      cache = null;
    }

    let loading = false;

    // The backend pins the SYNC mode to each run, so a changed mode needs a new Run Query.
    const modeStale = () => {
      const mode = api.getMode();
      return Boolean(cache) && (Boolean(cache.sync) !== Boolean(mode.sync) || (mode.sync && cache.direction !== mode.direction));
    };

    function setMessage(kind, text) {
      status.className = `status ${kind}`;
      status.textContent = text;
    }

    function persistCache() {
      api.saveReportCache(report.id, cache, pageName);
    }

    function draw() {
      const success = Boolean(cache && !cache.error);
      wrapper.style.display = success ? "" : "none";

      if (success) {
        api.renderTable(head, body, cache.columns || [], cache.rows || []);
        const rowCount = (cache.rows || []).length;
        count.textContent = rowCount
          ? `Showing ${cache.offset + 1}-${cache.offset + rowCount}${cache.hasMore ? "+" : ""}`
          : "Showing 0 records";
        pageInfo.textContent = `Page ${Math.floor(cache.offset / cache.limit) + 1}`;
        pageSize.value = String(cache.limit);
      } else {
        head.replaceChildren();
        body.replaceChildren();
        count.textContent = "";
        pageInfo.textContent = "";
      }

      const enabled = currentRunStillValid() && !modeStale();
      btnPrev.disabled = loading || !enabled || !success || cache.offset === 0;
      btnNext.disabled = loading || !enabled || !success || !cache.hasMore;
      btnRefresh.disabled = loading || !enabled;
      pageSize.disabled = loading || !enabled;
      btnCopy.disabled = loading || !success || !(cache.rows || []).length;   // copying needs no live run
      btnExport.disabled = loading || !enabled || !success;
    }

    function showStateMessage() {
      const mode = api.getMode();

      if (!runId || !snapshot) {
        setMessage("ok", "Set parameters above and click Run Query.");
        return;
      }
      if (!currentRunStillValid()) {
        setMessage("err", "A newer run exists. Click Run Query again.");
        return;
      }
      if (!cache) {
        setMessage("ok", `No cached result. Click Refresh (${modeLabel(mode)}).`);
        return;
      }
      if (modeStale()) {
        setMessage("ok", `Mode changed to ${modeLabel(mode)}. Click Run Query to refresh.`);
        return;
      }
      if (cache.error) {
        setMessage("err", cache.error);
        return;
      }
      if (!(cache.rows || []).length) {
        setMessage("ok", `No records matched (${modeLabel(mode)}).`);
        return;
      }

      status.className = "status";
      status.textContent = "";
    }

    // Fetch one page from the backend and cache it (used by refresh, paging and page-size change).
    async function load(offset, limit) {
      if (loading || !currentRunStillValid()) return;
      loading = true;
      draw();

      const mode = api.getMode();
      setMessage("loading", `Loading report (${modeLabel(mode)})...`);

      try {
        const data = await api.fetchReport(pageName, report, deepClone(params), runId, limit, offset, mode);
        if (!currentRunStillValid()) throw new Error("A newer run exists. Reload this page.");

        cache = {
          uiVersion: UI_VERSION,
          runId,
          profile,
          offset,
          limit,
          sync: Boolean(mode.sync),
          direction: mode.direction || "up",
        };

        if (!data.success) {
          cache.error = detailMessage(data.error) || "The report could not be generated.";
          cache.missing = Boolean(data.missing);
          cache.cancelled = Boolean(data.cancelled);
        } else {
          cache.columns = data.columns || [];
          cache.rows = data.rows || [];
          cache.hasMore = Boolean(data.has_more);
        }

        persistCache();
        showStateMessage();
      } catch (e) {
        setMessage("err", e.message || String(e));
      } finally {
        loading = false;
        draw();
      }
    }

    // ---------- events ----------
    const currentLimit = () => Number(pageSize.value) || DEFAULT_PAGE_SIZE;

    btnRefresh.onclick = () => load(0, currentLimit());
    pageSize.onchange = () => load(0, currentLimit());

    btnPrev.onclick = () => {
      if (!cache || cache.error) return;
      load(Math.max(0, cache.offset - cache.limit), cache.limit);
    };

    btnNext.onclick = () => {
      if (!cache || cache.error || !cache.hasMore) return;
      load(cache.offset + cache.limit, cache.limit);
    };

    btnCopy.onclick = async () => {
      if (!cache?.rows?.length) return;
      const ok = await copyText(toClipboardText(cache.columns || [], cache.rows || []));
      if (ok) {
        const old = btnCopy.textContent;
        btnCopy.textContent = "Copied";
        setTimeout(() => { btnCopy.textContent = old; }, 1200);
      } else {
        setMessage("err", "Copy failed. Browser blocked clipboard access.");
      }
    };

    btnExport.onclick = async () => {
      if (loading || !currentRunStillValid() || !cache || cache.error) return;
      loading = true;
      draw();

      const mode = api.getMode();
      setMessage("loading", `Preparing XLSX export (${modeLabel(mode)})...`);

      try {
        const response = await api.exportReport(pageName, report, deepClone(params), runId, mode);
        if (!response.ok) {
          const errBody = await response.json().catch(() => ({}));
          throw new Error(detailMessage(errBody.detail) || `Export failed (HTTP ${response.status}).`);
        }

        const blob = await response.blob();
        if (!currentRunStillValid()) throw new Error("A newer run exists. Download discarded.");

        const url = URL.createObjectURL(blob);
        const link = make("a");
        link.href = url;
        link.download = `${pageName}_${report.id}.xlsx`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);

        showStateMessage();
      } catch (e) {
        setMessage("err", e.message || String(e));
      } finally {
        loading = false;
        draw();
      }
    };

    // initial paint
    showStateMessage();
    draw();
  });
})();