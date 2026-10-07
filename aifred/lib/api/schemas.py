"""Pydantic models shared by multiple router modules."""

from pydantic import BaseModel, Field
from typing import Annotated, Optional

# Name, unter dem sich ein Aufrufer der Service-Token-Endpunkte (inject, trigger, announce)
# zu erkennen gibt. Pflicht, reine Anzeige: Echtheit sichert das Token, nie der Name.
CallerName = Annotated[str, Field(
    min_length=1, max_length=64,
    description="Who is calling: the agent's or system's name (required, display only — "
    "authentication is the token, never the name)",
)]


class SystemActionResponse(BaseModel):
    """Response for system actions"""
    success: bool
    message: str
    details: Optional[str] = None
