"""Printer plugin — print documents and read printer state through CUPS.

Generic: the printers come from CUPS (``cups.printers``), none is configured
here. One of them is the default — chosen in the plugin settings, or the
only one there is. ``printer_status`` reports state, warnings and supply
levels; a scheduler job can poll it and notify on any channel.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any

from ....lib import file_manager as fm
from ....lib.function_calling import Tool
from ....lib.logging_utils import log_message
from ....lib.plugin_base import (
    CredentialField,
    PluginContext,
    load_plugin_instructions,
    load_plugin_settings,
    load_tool_description,
    load_tool_parameters,
    save_plugin_settings,
)
from ....lib.security import TIER_COMMUNICATE, TIER_READONLY
from . import cups

DEFAULT_PRINTER_KEY = "PRINTER_DEFAULT"
MAX_COPIES_KEY = "PRINTER_MAX_COPIES"
DEFAULT_MAX_COPIES = "10"

# File types CUPS prints without a conversion step of ours (its own filters
# handle them); anything else (Office, HTML …) needs a PDF first.
PRINTABLE_SUFFIXES = (".pdf", ".txt", ".png", ".jpg", ".jpeg", ".ps")


@dataclass
class PrinterPlugin:
    name: str = "printer"

    # ── Plugin settings (settings.json next to this module) ──────────
    @property
    def credential_fields(self) -> list[CredentialField]:
        """The default printer, picked from what CUPS lists right now."""
        try:
            available = cups.printers()
        except cups.PrinterError:
            available = []
        return [
            CredentialField(
                env_key=DEFAULT_PRINTER_KEY,
                label_key="printer_cred_default",
                options=[(name, name) for name in available],
                default=available[0] if len(available) == 1 else "",
            ),
            CredentialField(
                env_key=MAX_COPIES_KEY,
                label_key="printer_cred_max_copies",
                default=DEFAULT_MAX_COPIES,
            ),
        ]

    def _load_settings(self) -> dict[str, str]:
        return load_plugin_settings(__file__)

    def _save_settings(self, settings: dict[str, str]) -> None:
        save_plugin_settings(__file__, settings)

    def is_available(self) -> bool:
        return True

    # ── Tools ───────────────────────────────────────────────────────
    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        settings = self._load_settings()
        chosen = settings.get(DEFAULT_PRINTER_KEY, "")
        max_copies = int(settings.get(MAX_COPIES_KEY, DEFAULT_MAX_COPIES) or DEFAULT_MAX_COPIES)

        async def _print_file(filename: str, copies: int = 1, pages: str = "", duplex: bool = False) -> str:
            path, error = fm.safe_resolve(filename)
            if error:
                return json.dumps({"error": error})
            if path is None or not path.is_file():
                return json.dumps({"error": f"File not found: {filename}"})
            if path.suffix.lower() not in PRINTABLE_SUFFIXES:
                return json.dumps({
                    "error": f"{path.suffix or 'this file type'} cannot be printed directly — "
                             f"printable: {', '.join(PRINTABLE_SUFFIXES)}. Convert it to PDF first.",
                })
            if not 1 <= copies <= max_copies:
                return json.dumps({"error": f"copies must be between 1 and {max_copies} (plugin setting)"})
            try:
                printer = await asyncio.to_thread(cups.choose_printer, chosen)
                job = await asyncio.to_thread(cups.submit, printer, path, copies, pages, duplex)
            except cups.PrinterError as failed:
                log_message(f"🖨️ print_file {filename} failed: {failed}", "warning")
                return json.dumps({"error": str(failed)})
            log_message(f"🖨️ print_file {path.name} → {printer} (job {job}, {copies}x, pages '{pages}', duplex {duplex})")
            return json.dumps({"printer": printer, "job": job, "file": filename, "copies": copies})

        async def _printer_status() -> str:
            try:
                printer = await asyncio.to_thread(cups.choose_printer, chosen)
                state = await asyncio.to_thread(cups.status, printer)
            except cups.PrinterError as failed:
                return json.dumps({"error": str(failed)})
            result: dict[str, Any] = asdict(state)
            result["low_supplies"] = [supply.name for supply in state.supplies if supply.low]
            result["full_waste_containers"] = [supply.name for supply in state.supplies if supply.full]
            return json.dumps(result, ensure_ascii=False)

        return [
            Tool(
                name="print_file",
                tier=TIER_COMMUNICATE,
                description=load_tool_description(__file__, "print_file"),
                parameters=load_tool_parameters(__file__, "print_file"),
                executor=_print_file,
            ),
            Tool(
                name="printer_status",
                tier=TIER_READONLY,
                description=load_tool_description(__file__, "printer_status"),
                parameters=load_tool_parameters(__file__, "printer_status"),
                executor=_printer_status,
            ),
        ]

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "print_file":
            return f"🖨️ {tool_args.get('filename', '')}"
        if tool_name == "printer_status":
            return "🖨️ Status"
        return ""


plugin = PrinterPlugin()
