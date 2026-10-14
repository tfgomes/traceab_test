# Traceability APP (Converting-only)

## What this app currently does

This FastAPI app provides:

1. **Global Parameters page** (`/`)  
   - Lets users set one shared parameter set
   - Starts a server-side run (`run_id`)
   - Preloads first page of each Converting report

2. **Converting page** (`/converting`)  
   - Shows 4 report tables:
     - Production Orders and Batches
     - Produced pallets
     - Material consumption summary
     - Material consumption batches and handling units
   - Supports pagination, refresh and CSV export per report
   - Shows report-specific validation errors under each report

---

## File map

- `main.py`  
  App entrypoint, routes, run lifecycle, report fetch/export APIs

- `params.py`  
  Parameter models, report catalog, mandatory-field rules, SQL builder

- `config.py`  
  Runtime and Databricks connection settings

- `static/common.js`  
  Shared frontend metadata (`SECTIONS`, `REPORTS`) and helper functions

- `templates/global_params.html`  
  Global parameter UI and run trigger behavior

- `templates/converting.html`  
  Report rendering, per-report actions (refresh/export), status handling

- `static/common.css`  
  Shared styling

---

## How to add future parameters

Update **`static/common.js`** in `SECTIONS`:

- Add a field with:
  - `key`
  - `label`
  - `type` (`select`, `multi`, `date`, `text`)
- For dropdowns, add `source` endpoint
- For dependent dropdowns, add `dependsOn`

Then update **`params.py`** (`GlobalParams`) to include the new key.

---

## How to add a new report table (future)

1. Add a report entry in:
   - `params.py` → `REPORTS`
   - `static/common.js` → `REPORTS` (UI badge + label)

2. In `params.py` → `build_report_query(...)`:
   - Add mandatory rules in `missing_for(...)` if needed
   - Add filter conditions for the new report_id
   - Set `view` and optional `order_by`

No extra frontend page is needed if it belongs to Converting.

---

## Multi-user behavior

- Each browser session receives an owner cookie.
- Each run belongs to one owner.
- One user's run/cancel does not cancel other users' runs.

---

## Operational notes

- This implementation keeps in-memory run state per app process.
- If horizontal scaling is introduced later, run-state/cancel design must be revisited.
- Keep SQL/table/view names centralized in `params.py` for maintainability.