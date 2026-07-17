"""Pydantic models shared by multiple router modules."""

from pydantic import BaseModel
from typing import Optional


class SystemActionResponse(BaseModel):
    """Response for system actions"""
    success: bool
    message: str
    details: Optional[str] = None
