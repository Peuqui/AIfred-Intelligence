"""The describer list: who describes images, in which order, and the burn-in of another machine's describer.

Core, not part of the vision plugin: the chat's images are described with the plugin switched off too
(lib/describer_list.py).
"""

import asyncio
from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from .app import api_app


class DescriberRow(BaseModel):
    index: int
    source: str = Field(description="'<machine>/<model>', 'local' (this machine's image model) or 'main' (the loaded model)")
    label: str = Field(description="Model · place")
    enabled: bool
    note: str = Field(description="An entry of another machine: what holds it back, or what it needs as measured")
    warn: bool = Field(description="The note says why the entry is skipped")
    remote: bool = Field(description="A describer of another machine: it can be burnt in")


class Describers(BaseModel):
    rows: list[DescriberRow]
    measuring: str = Field(description="The source being burnt in now (empty: none)")


class DescriberMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    delta: int = Field(ge=-1, le=1)


class DescriberSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    enabled: bool


class DescriberBurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str


def _describers() -> Describers:
    from ..describer_list import measuring, rows
    from ..prompt_loader import get_language

    return Describers(rows=[DescriberRow(**asdict(row)) for row in rows(get_language())], measuring=measuring())


@api_app.get("/describers", response_model=Describers, tags=["Describers"])
async def describers_list() -> Describers:
    """The list as it stands now (every configured machine asked) and the burn-in that runs."""
    return await asyncio.to_thread(_describers)


@api_app.post("/describers/move", response_model=Describers, tags=["Describers"])
async def describers_move(move: DescriberMove) -> Describers:
    """An entry one place up or down; 422 for a position the list no longer has."""
    from ..describer_list import move_entry

    await asyncio.to_thread(move_entry, move.index, move.delta)
    log_message(f"🖼️ Describer list: entry {move.index} moved by {move.delta}")
    return await asyncio.to_thread(_describers)


@api_app.post("/describers/switch", response_model=Describers, tags=["Describers"])
async def describers_switch(switch: DescriberSwitch) -> Describers:
    """An entry on or off; 422 for a position the list no longer has."""
    from ..describer_list import enable_entry

    await asyncio.to_thread(enable_entry, switch.index, switch.enabled)
    log_message(f"🖼️ Describer list: entry {switch.index} {'on' if switch.enabled else 'off'}")
    return await asyncio.to_thread(_describers)


@api_app.post("/describers/burn-in", response_model=Describers, status_code=202, tags=["Describers"])
async def describers_burn_in(burn_in: DescriberBurnIn) -> Describers:
    """Start the burn-in of a describer of another machine: it unloads every model there, loads this one with the
    stress picture and keeps the peak of the cards minus what they held before. Answers at once; ``GET`` follows it
    (``measuring``, then the row's note). 409 while another one runs, 422 for a source of no other machine."""
    from ..describer_list import start_burn_in

    start_burn_in(burn_in.source)
    return await asyncio.to_thread(_describers)
