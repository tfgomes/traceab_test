"""
Application configuration.

Purpose
-------
Centralize environment-driven settings for:
- Databricks SQL Warehouse connectivity
- FastAPI host/port/debug runtime

Notes
-----
- Keep imports complete (missing typing imports can crash startup).
- Values can be overridden by environment variables or .env.
"""

from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings loaded from environment variables and optional .env file."""

    # --- Databricks SQL Warehouse connection ---
    DATABRICKS_HOST: str = "adb-5283192070519636.16.azuredatabricks.net"
    DATABRICKS_HTTP_PATH: str = "/sql/1.0/warehouses/487d41ca53a4b949"
    DATABRICKS_CATALOG: Optional[str] = None   # reserved for future UC use
    DATABRICKS_SCHEMA: Optional[str] = "traceability_poc"

    # --- App runtime ---
    HOST: str = "0.0.0.0"   # required for Databricks Apps
    PORT: int = 8000
    DEBUG: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()