"""
Pydantic models for request/response validation
"""

from pydantic import BaseModel
from typing import Dict, Any, List, Optional


class ColumnInfo(BaseModel):
    """Model for column information"""
    name: str
    type: str
    comment: Optional[str] = None


class TableInfo(BaseModel):
    """Model for table information"""
    table_name: str
    columns: List[ColumnInfo]
    row_count: int


class RecordResponse(BaseModel):
    """Model for record query response"""
    records: List[Dict[str, Any]]
    columns: List[str]
    total_count: int
    limit: int
    offset: int