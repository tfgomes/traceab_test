"""
Databricks Delta table client for reading records.

Read-only client scoped to a single, already-identified table with a fixed
set of filterable columns used to power select/dropdown filters in the app UI.
"""

from databricks import sql
from typing import List, Dict, Optional
import pandas as pd
from config import settings
import time


# Columns exposed as filter (select box) options in the app UI.
FILTER_COLUMNS = [
    "PlantId",
    "MaterialId",
    "ProductionDate",

]


class DatabricksClient:
    """Read-only client for querying a single Databricks Delta table"""

    def __init__(self, table_name: Optional[str] = None):
        # Set these first so __del__ / close() never fails even if __init__ raises
        self.connection = None
        self._filter_cache = {}
        self._filter_cache_time = {}

        self.table_name = table_name or getattr(settings, "DATABRICKS_TABLE", None)
        if not self.table_name:
            raise ValueError(
                "table_name must be provided, or set DATABRICKS_TABLE in settings"
            )

    def _get_connection(self):
        """Get or create a connection to Databricks"""
        if self.connection is None:
            from databricks.sdk.core import Config

            cfg = Config()

            self.connection = sql.connect(
                server_hostname=cfg.host,
                http_path=settings.DATABRICKS_HTTP_PATH,
                credentials_provider=lambda: cfg.authenticate
            )
        return self.connection

    def _get_full_table_name(self) -> str:
        """Get fully qualified table name with catalog and schema"""
        table_name = self.table_name
        catalog = getattr(settings, "DATABRICKS_CATALOG", None)
        schema = getattr(settings, "DATABRICKS_SCHEMA", None)

        if '.' in table_name:
            return table_name

        if catalog and schema:
            return f"{catalog}.{schema}.{table_name}"
        elif schema:
            return f"{schema}.{table_name}"
        else:
            return table_name

    def _build_where_conditions(
        self,
        filters: Optional[Dict[str, str]],
        exclude_column: Optional[str] = None
    ) -> List[str]:
        """
        Build SQL WHERE conditions from selected filter values.
        Only columns in FILTER_COLUMNS are honored - this whitelist also
        protects against SQL injection via column names.
        """
        conditions = []
        if not filters:
            return conditions

        for col, val in filters.items():
            if col == exclude_column or not val or col not in FILTER_COLUMNS:
                continue
            escaped_val = str(val).replace("'", "''")
            conditions.append(f"{col} = '{escaped_val}'")

        return conditions

    def get_record_count(self, filters: Optional[Dict[str, str]] = None) -> int:
        """Get total count of records matching the filters"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            full_table_name = self._get_full_table_name()
            query = f"SELECT COUNT(*) FROM {full_table_name}"
            where_conditions = self._build_where_conditions(filters)
            if where_conditions:
                query += " WHERE " + " AND ".join(where_conditions)
            cursor.execute(query)
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        finally:
            cursor.close()

    def get_filter_options(self, filters: Optional[Dict[str, str]] = None) -> Dict[str, List[str]]:
        """
        Get distinct values for each filter column, respecting any
        already-selected filters so the dropdowns cascade correctly.
        Results are cached for 5 minutes.
        """
        filter_key = ",".join(f"{k}={v}" for k, v in sorted((filters or {}).items()))
        cache_key = f"{self.table_name}:{filter_key}"
        current_time = time.time()

        if cache_key in self._filter_cache:
            cache_time = self._filter_cache_time.get(cache_key, 0)
            if current_time - cache_time < 300:  # 5 minutes
                return self._filter_cache[cache_key]

        conn = self._get_connection()
        cursor = conn.cursor()
        result: Dict[str, List[str]] = {}

        try:
            full_table_name = self._get_full_table_name()

            for col in FILTER_COLUMNS:
                try:
                    where_conditions = self._build_where_conditions(filters, exclude_column=col)
                    query = f"SELECT DISTINCT {col} FROM {full_table_name} WHERE {col} IS NOT NULL"
                    if where_conditions:
                        query += " AND " + " AND ".join(where_conditions)
                    query += f" ORDER BY {col} LIMIT 100"

                    cursor.execute(query)
                    rows = cursor.fetchall()
                    result[col] = [str(row[0]) for row in rows if row[0] is not None]
                except Exception as e:
                    # Log which column failed to help diagnose column name mismatches
                    print(f"Warning: could not fetch distinct values for column '{col}': {e}")
                    result[col] = []

            self._filter_cache[cache_key] = result
            self._filter_cache_time[cache_key] = current_time

            return result
        finally:
            cursor.close()

    def get_records(
        self,
        filters: Optional[Dict[str, str]] = None,
        limit: Optional[int] = 500,
        offset: int = 0
    ) -> pd.DataFrame:
        """
        Retrieve records from the table as a pandas DataFrame.
        Pass limit=None to fetch every matching row.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            full_table_name = self._get_full_table_name()
            query = f"SELECT * FROM {full_table_name}"

            where_conditions = self._build_where_conditions(filters)
            if where_conditions:
                query += " WHERE " + " AND ".join(where_conditions)

            if limit is not None:
                query += f" LIMIT {limit} OFFSET {offset}"

            cursor.execute(query)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]

            return pd.DataFrame(rows, columns=columns)
        finally:
            cursor.close()

    def close(self):
        """Close the connection safely"""
        conn = getattr(self, "connection", None)
        if conn is not None:
            conn.close()
            self.connection = None

    def __del__(self):
        """Cleanup connection on deletion"""
        self.close()