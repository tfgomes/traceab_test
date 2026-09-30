"""
Configuration settings for the Delta Table Manager application
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Databricks Connection Settings
    DATABRICKS_HOST: str = "adb-5283192070519636.16.azuredatabricks.net"
    DATABRICKS_HTTP_PATH: str = "/sql/1.0/warehouses/487d41ca53a4b949"
    DATABRICKS_CATALOG: Optional[str] = None  # Unity Catalog not enabled
    DATABRICKS_SCHEMA: Optional[str] = "traceability_poc"
    DATABRICKS_TABLE: str = "ZFIN_orders"
    
    # Application Settings
    HOST: str = "0.0.0.0"  # Bind to all interfaces for Databricks Apps
    PORT: int = 8000  # Alternative port to avoid conflicts
    DEBUG: bool = False
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


# Create global settings instance
settings = Settings()
