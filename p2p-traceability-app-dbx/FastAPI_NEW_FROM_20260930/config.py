"""
Application configuration.

Goal
----
Centralize environment-driven settings for:
- Databricks SQL Warehouse connectivity
- FastAPI host/port/debug runtime

Notes for humans and AI
-----------------------
- Every type used in annotations must be imported (e.g. Optional).
  A missing import here crashes the app at startup, before any route loads.
- Values can be overridden with environment variables or a .env file.
- DATABRICKS_CATALOG / SCHEMA are kept for future use; the current
  Converting reports use fully qualified view names in params.py.
"""

from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings loaded from environment variables (and optional .env file)."""

    # --- Databricks SQL Warehouse connection ---
    DATABRICKS_HOST: str = "adb-5283192070519636.16.azuredatabricks.net"
    DATABRICKS_HTTP_PATH: str = "/sql/1.0/warehouses/487d41ca53a4b949"
    DATABRICKS_CATALOG: Optional[str] = None  # Unity Catalog not enabled yet
    DATABRICKS_SCHEMA: Optional[str] = "traceability_poc"
    DATABRICKS_CATALOG: Optional[str] = None  # Unity Catalog not enabled

    # --- App runtime ---
    HOST: str = "0.0.0.0"  # bind to all interfaces for Databricks Apps
    PORT: int = 8000
    DEBUG: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()