# Databricks notebook source
:root {
  --pink: #F50082; --navy: #00005A; --green: #009A44; --red: #E4002B;
  --border: #E5E7EB; --bg: #F8F9FA; --card-border: #D8DCE3;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: Arial, sans-serif; background: var(--navy); min-height: 100vh; padding: 20px; overflow-y: scroll; }
.container { max-width: 1800px; margin: 0 auto; background: white; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }

header { background: linear-gradient(135deg, #F50082, #FF1A99); color: white; padding: 30px 40px; text-align: center; position: relative; }
header h1 { font-size: 2.2rem; margin-bottom: 8px; }
header p { opacity: 0.9; }

.page-nav-wrap { display: flex; justify-content: space-between; align-items: center; padding: 14px 40px; background: #fff; border-bottom: 1px solid var(--border); }
.page-nav { display: inline-flex; background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 4px; }
.page-nav a { text-decoration: none; color: var(--navy); font-weight: 700; padding: 8px 14px; border-radius: 8px; font-size: 0.9rem; }
.page-nav a.active { background: var(--navy); color: #fff; }

.table-section { padding: 20px 40px 40px; }
.report-card { background: var(--bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 16px; margin-bottom: 18px; }
.report-title { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.report-badge { background: var(--navy); color: #fff; font-size: 0.72rem; font-weight: 700; padding: 4px 8px; border-radius: 999px; }
.report-title h2 { font-size: 1.05rem; color: var(--navy); font-weight: 700; }

.filter-actions { display: flex; gap: 10px; justify-content: flex-end; flex-wrap: wrap; margin-top: 12px; }
.btn { padding: 9px 22px; border: none; border-radius: 8px; font-size: 0.9rem; font-weight: 700; cursor: pointer; color: white; }
.btn-primary { background: var(--pink); } .btn-primary:hover { background: #D4006D; }
.btn-secondary { background: #6C757D; } .btn-secondary:hover { background: #5A6268; }
.btn:disabled { opacity: 0.5; cursor: default; }

.status { padding: 10px 16px; border-radius: 6px; margin-bottom: 14px; font-weight: 600; font-size: 0.9rem; display: none; }
.status.ok { background: #F5F7FF; color: #374151; border: 1px solid #E5E7EB; display: block; }
.status.err { background: var(--red); color: white; display: block; }
.status.loading { background: #ddd; color: #555; display: block; }

.table-wrapper { overflow-x: auto; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
table { width: 100%; border-collapse: collapse; background: white; font-size: 0.85rem; }
thead { background: var(--navy); color: white; }
th { padding: 13px 16px; text-align: left; white-space: nowrap; }
td { padding: 11px 16px; border-bottom: 1px solid var(--border); white-space: nowrap; max-width: 280px; overflow: hidden; text-overflow: ellipsis; }
tr:hover td { background: var(--bg); }
tr:last-child td { border-bottom: none; }

.table-controls { display: flex; justify-content: space-between; align-items: center; margin-top: 14px; padding: 16px 20px; background: var(--bg); border-radius: 8px; flex-wrap: wrap; gap: 10px; border: 1px solid var(--card-border); }
.controls-left { display: flex; align-items: center; gap: 16px; }
.record-count { font-size: 0.9rem; color: #555; }
.page-size-wrap { display: flex; align-items: center; gap: 6px; font-size: 0.85rem; color: #555; }
.page-size-wrap select { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; background: white; }
.pagination { display: flex; align-items: center; gap: 10px; }
.btn-small { padding: 6px 16px; font-size: 0.85rem; background: var(--navy); color: white; border: none; border-radius: 6px; cursor: pointer; }
.btn-small:disabled { opacity: 0.35; cursor: default; }
.page-info { font-size: 0.85rem; color: #555; min-width: 80px; text-align: center; }

/* ---- Global parameters page ---- */
.param-head, .param-row { display: grid; grid-template-columns: 200px 1fr 130px 130px; gap: 12px; align-items: center; margin-bottom: 10px; }
.param-head span { font-size: 0.78rem; font-weight: 700; color: var(--navy); }
.param-label { font-size: 0.85rem; font-weight: 700; color: var(--navy); }
.param-row input, .param-row select { width: 100%; height: 34px; padding: 6px 10px; border: 2px solid var(--border); border-radius: 6px; font-size: 0.85rem; background: white; }
.param-row input:focus, .param-row select:focus { outline: none; border-color: var(--pink); }
.param-row.wide { grid-template-columns: 200px 1fr 1fr; }

.chip-box { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-height: 34px; padding: 3px 6px; border: 2px solid var(--border); border-radius: 6px; background: white; }
.chip-box:focus-within { border-color: var(--pink); }
.chip-box input { border: none !important; flex: 1; min-width: 140px; height: 26px !important; padding: 0 4px !important; }
.chip { display: inline-flex; align-items: center; gap: 4px; background: var(--navy); color: #fff; font-size: 0.8rem; padding: 2px 4px 2px 10px; border-radius: 999px; }
.chip-x { background: none; border: none; color: #fff; cursor: pointer; font-size: 1rem; line-height: 1; padding: 0 4px; }

.report-status-list { margin-top: 6px; }
.report-status-item { display: flex; gap: 10px; align-items: center; padding: 6px 0; font-size: 0.9rem; color: #374151; }
.params-summary { font-size: 0.85rem; color: #555; margin-bottom: 12px; }