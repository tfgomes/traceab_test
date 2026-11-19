-- ============================================================
-- Traceability APP - Preflight Checks (Databricks SQL)
-- Target env: traceability_poc
-- Date: 2026-10-20
-- ============================================================

-- ---------- 0) Context ----------
SELECT current_catalog() AS current_catalog, current_schema() AS current_schema;

-- ---------- 1) Required objects exist ----------
-- Add/remove objects here as needed.
WITH required_objects AS (
  SELECT 'traceability_poc' AS schema_name, 'v_SALCONS_summary' AS object_name UNION ALL
  SELECT 'traceability_poc', 'master_config_report_columns'
),
existing AS (
  SELECT table_schema AS schema_name, table_name AS object_name
  FROM information_schema.tables
  WHERE table_schema = 'traceability_poc'
)
SELECT
  r.schema_name,
  r.object_name,
  CASE WHEN e.object_name IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS status
FROM required_objects r
LEFT JOIN existing e
  ON r.schema_name = e.schema_name
 AND r.object_name = e.object_name
ORDER BY status, r.object_name;

-- ---------- 2) Required columns in summary view ----------
WITH required_cols AS (
  SELECT 'PlantId' AS col UNION ALL
  SELECT 'ConvertingProductionLine' UNION ALL
  SELECT 'OrderNumber' UNION ALL
  SELECT 'OrderMaterialId' UNION ALL
  SELECT 'OrderMaterial' UNION ALL
  SELECT 'OrderBatch' UNION ALL
  SELECT 'FirstConsumedDate' UNION ALL
  SELECT 'LastConsumedDate' UNION ALL
  SELECT 'ConsumedMaterialId' UNION ALL
  SELECT 'ConsumedMaterialText' UNION ALL
  SELECT 'ConsumedMaterialTypeId' UNION ALL
  SELECT 'Quantity' UNION ALL
  SELECT 'UoM' UNION ALL
  SELECT 'schema_name'
),
actual_cols AS (
  SELECT column_name
  FROM information_schema.columns
  WHERE table_schema = 'traceability_poc'
    AND table_name = 'v_SALCONS_summary'
)
SELECT
  r.col AS required_column,
  CASE WHEN a.column_name IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS status
FROM required_cols r
LEFT JOIN actual_cols a
  ON lower(r.col) = lower(a.column_name)
ORDER BY status, required_column;

-- ---------- 3) Config rows for Converting summary (cv-1) ----------
SELECT
  `FileldID`,
  `Page`,
  `Section`,
  `Table`,
  `Column`,
  `Name`,
  `DataType`,
  `ParameterID`,
  `ParameterIDmax`,
  `Mandatory`
FROM traceability_poc.master_config_report_columns
WHERE `Page` = 'Converting'
  AND `Section` = 'cv-1'
ORDER BY `FileldID`;

-- ---------- 4) Validate cv-1 points to canonical table ----------
SELECT
  `Table`,
  COUNT(*) AS rows_count
FROM traceability_poc.master_config_report_columns
WHERE `Page` = 'Converting'
  AND `Section` = 'cv-1'
GROUP BY `Table`
ORDER BY rows_count DESC;

-- ---------- 5) Check forbidden legacy typo ----------
SELECT
  COUNT(*) AS typo_rows
FROM traceability_poc.master_config_report_columns
WHERE lower(`Table`) LIKE '%v_salcon_summary%';

-- ---------- 6) Smoke query for summary view ----------
SELECT
  PlantId,
  ConvertingProductionLine,
  OrderNumber,
  OrderMaterialId,
  OrderMaterial,
  OrderBatch,
  FirstConsumedDate,
  LastConsumedDate
FROM traceability_poc.v_SALCONS_summary
LIMIT 10;

-- ---------- 7) Optional quick data sanity ----------
SELECT
  COUNT(*) AS total_rows,
  SUM(CASE WHEN FirstConsumedDate IS NULL THEN 1 ELSE 0 END) AS null_first_consumed_date,
  SUM(CASE WHEN LastConsumedDate IS NULL THEN 1 ELSE 0 END) AS null_last_consumed_date
FROM traceability_poc.v_SALCONS_summary;