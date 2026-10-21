/*
  Sprint 3 report-page controller
  - reads cached first-page results prepared by params_panel.js
  - supports refresh, pagination, copy and export
  - uses shared SYNC/UNSYNC + UP/DOWN state from common.js
*/

(() => {
  "use strict";

  const UI_VERSION = "sprint3-ui-1";
  const api = window.TraceabilityPhaseB;
  const profile = document.body?.dataset?.reportProfile;
  const host = document.getElementById("reports");
  const errorNode = document.getElementById("pageError");

  const REPORTS = {
    pm: [
      { id: "pm_report_3", number: "2", title: "Paper machine production order summary" },
      { id: "pm_report_4", number: "3", title: "Paper machine production batch details" },
      { id: "cmb_summary", number: "4", title: "Combiner production order summary" },
      { id: "cmb_report_2", number: "5", title: "Combiner production batch details" },
    ],
    cv: [
      { id: "summary", number: "1", title: "Material consumption summary" },
      { id: "details", number: "2", title: "Material consumption batches and handling units" },
      { id: "orders", number: "3", title: "Production Orders and Batches" },
      { id: "pallets", number: "4", title: "Produced pallets" },
    ],
  };

  function fail(msg) {
    if (errorNode) {
      errorNode.className = "status err";
      errorNode.textContent = msg;
    }
    console.error("[report_page]", msg);
  }

  if (!api) return fail("Init failed: common.js missing.");
  if (!profile || !REPORTS[profile]) return fail("Init failed: invalid page profile.");
  if (!host) return fail("Init failed: #reports not found.");

  const pageName = profile === "pm" ? api.PAGE_PAPERMAKING : api.PAGE_CONVERTING;
  const runId = api.getRunId(profile);
  const globals = api.getGlobalParams() || {};
  const snapshot = globals.byProfile?.[profile] || null;

  function deepClone(v) {
    return JSON.parse(JSON.stringify(v));
  }

  function make(tag, className = "", text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function flattenParams(profileId, saved) {
    const p = saved || {};
    return {
      active_profile: profileId === "pm" ? "pm_prod" : "cv_prod",
      plant_code_name: p.plant_code_name || globals.default_plant_code_name || null,

      converting_production_line: p.converting_production_line || { values: [] },
      zfin_material_id: p.zfin_material_id || { values: [] },
      zfin_order: p.zfin_order || { values: [] },
      zfin_batch_id: p.zfin_batch_id || { values: [] },
      zfin_sscc: p.zfin_sscc || { values: [] },
      production_date_from: p.production_date_from || null,
      production_time_from: p.production_time_from || null,
      production_date_to: p.production_date_to || null,
      production_time_to: p.production_time_to || null,

      consumed_material_type_id: p.consumed_material_type_id || { values: [] },
      consumed_material_id: p.consumed_material_id || { values: [] },
      consumed_batch_id: p.consumed_batch_id || { values: [] },
      consumed_sscc: p.consumed_sscc || { values: [] },
      consumed_date_from: p.consumed_date_from || null,
      consumed_time_from: p.consumed_time_from || null,
      consumed_date_to: p.consumed_date_to || null,
      consumed_time_to: p.consumed_time_to || null,

      cmb_line: p.cmb_line || { values: [] },
      cmb_material_id: p.cmb_material_id || { values: [] },
      cmb_order_number: p.cmb_order_number || { values: [] },
      cmb_reel_batch_id: p.cmb_reel_batch_id || { values: [] },
      cmb_produced_sscc: p.cmb_produced_sscc || { values: [] },
      cmb_prod_date_from: p.cmb_prod_date_from || null,
      cmb_prod_time_from: p.cmb_prod_time_from || null,
      cmb_prod_date_to: p.cmb_prod_date_to || null,
      cmb_prod_time_to: p.cmb_prod_time_to || null,

      pm_line: p.pm_line || { values: [] },
      pm_material_id: p.pm_material_id || { values: [] },
      pm_order_number: p.pm_order_number || { values: [] },
      pm_reel_batch_id: p.pm_reel_batch_id || { values: [] },
      pm_produced_sscc: p.pm_produced_sscc || { values: [] },
      pm_prod_date_from: p.pm_prod_date_from || null,
      pm_prod_time_from: p.pm_prod_time_from || null,
      pm_prod_date_to: p.pm_prod_date_to || null,
      pm_prod_time_to: p.pm_prod_time_to || null,
    };
  }

  const params = flattenParams(profile, snapshot);

  function currentRunStillValid() {
    return Boolean(runId && api.getRunId(profile) === runId);
  }

  function detailMessage(value) {
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map((x) => x?.msg || JSON.stringify(x)).join("; ");
    return value ? JSON.stringify(value) : "";
  }

  function modeLabel(mode) {
    return mode.sync ? `SYNC-${String(mode.direction || "up").toUpperCase()}` : "UNSYNC";
  }

  REPORTS[profile].forEach((report) => {
    const card = make("section", "report-card");

    const heading = make("div", "report-title");
    heading.append(
      make("span", "report-badge report-number-badge", report.number),
      make("h2", "", report.title)
    );
    card.appendChild(heading);

    const status = make("div", "status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    card.appendChild(status);

    const wrapper = make("div", "table-wrapper");
    const table = make("table");
    const head = make("thead");
    const body = make("tbody");
    table.append(head, body);
    wrapper.appendChild(table);
    card.appendChild(wrapper);

    const controls = make("div", "table-controls");
    const left = make("div", "controls-left");
    const count = make("span", "record-count");
    const pageSizeWrap = make("label", "page-size-wrap", "Rows per page:");
    const pageSize = make("select");
    [10, 25, 50, 100].forEach((n) => pageSize.append(new Option(String(n), String(n))));
    pageSizeWrap.appendChild(pageSize);
    left.append(count, pageSizeWrap);

    const right = make("div", "pagination");
    const btnPrev = make("button", "btn-small", "<- Previous");
    const pageInfo = make("span", "page-info");
    const btnNext = make("button", "btn-small", "Next ->");
    const btnRefresh = make("button", "btn-small", "Refresh");
    const btnCopy = make("button", "btn-small", "Copy");
    const btnExport = make("button", "btn btn-export", "Export XLSX");

    [btnPrev, btnNext, btnRefresh, btnCopy, btnExport].forEach((b) => {
      b.type = "button";
    });

    right.append(btnPrev, pageInfo, btnNext, btnRefresh, btnCopy, btnExport);
    controls.append(left, right);
    card.appendChild(controls);

    host.appendChild(card);

    let cache = api.loadReportCache(report.id, pageName);
    if (cache?.runId !== runId || cache?.profile !== profile || cache?.uiVersion !== UI_VERSION) {
      cache = null;
    }

    let loading = false;

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

      const enabled = currentRunStillValid();
      btnPrev.disabled = loading || !enabled || !success || cache.offset === 0;
      btnNext.disabled = loading || !enabled || !success || !cache.hasMore;
      btnRefresh.disabled = loading || !enabled;
      pageSize.disabled = loading || !enabled;
      btnCopy.disabled = loading || !enabled || !success;
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

    btnRefresh.onclick = () => load(0, Number(pageSize.value) || 10);
    pageSize.onchange = () => load(0, Number(pageSize.value) || 10);

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
      const lines = [];
      lines.push((cache.columns || []).join("\t"));
      (cache.rows || []).forEach((row) => {
        lines.push((row || []).map((v) => (v == null ? "" : String(v))).join("\t"));
      });

      try {
        await navigator.clipboard.writeText(lines.join("\n"));
        const old = btnCopy.textContent;
        btnCopy.textContent = "Copied";
        setTimeout(() => { btnCopy.textContent = old; }, 1200);
      } catch {
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
          const body = await response.json().catch(() => ({}));
          throw new Error(detailMessage(body.detail) || `Export failed (HTTP ${response.status}).`);
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

    showStateMessage();
    draw();
  });
})();