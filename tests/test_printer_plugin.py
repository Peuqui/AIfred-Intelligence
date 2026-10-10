"""Printer plugin: CUPS through lp/lpstat/ipptool — printer choice, job, status parsing."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aifred.lib.plugin_base import PluginContext
from aifred.plugins.tools.printer import cups
from aifred.plugins.tools.printer import plugin as printer_plugin

IPPTOOL_OUTPUT = """\
"/usr/share/cups/ipptool/get-printer-attributes.test":
    Get printer attributes using get-printer-attributes                  [PASS]
        RECEIVED: 4321 bytes in response
        status-code = successful-ok (successful-ok)
        printer-state (enum) = idle
        printer-state-reasons (1setOf keyword) = media-empty-warning,toner-low-warning
        printer-state-message (textWithoutLanguage) = Paper empty in tray 1
        queued-job-count (integer) = 2
        marker-names (1setOf nameWithoutLanguage) = Black Toner,Waste Toner Box
        marker-types (1setOf keyword) = toner,waste-toner
        marker-levels (1setOf integer) = 8,96
        marker-low-levels (1setOf integer) = 10,0
        marker-high-levels (1setOf integer) = 100,95
"""


class TestStatusParsing:
    def test_state_warnings_jobs_and_supplies(self) -> None:
        state = cups.status_from_attributes("Kyocera", cups.parse_attributes(IPPTOOL_OUTPUT))
        assert (state.state, state.queued_jobs, state.message) == ("idle", 2, "Paper empty in tray 1")
        assert state.reasons == ["media-empty-warning", "toner-low-warning"]
        assert [(s.name, s.level, s.low, s.full) for s in state.supplies] == [
            ("Black Toner", 8, True, False),
            ("Waste Toner Box", 96, False, True),   # waste: the level is how full it is
        ]

    def test_an_empty_waste_box_is_no_warning_and_unknown_levels_are_none(self) -> None:
        # The Kyocera at 10.10.2026: waste box at 0 with low mark 0 — must not count as "low".
        attributes = {"marker-names": "Black,Waste", "marker-types": "toner,waste-toner",
                      "marker-levels": "-3,0", "marker-low-levels": "3,0", "marker-high-levels": "100,95"}
        black, waste = cups.status_from_attributes("P", attributes).supplies
        assert (black.level, black.low) == (None, False)    # -3 = "some remaining", no percentage
        assert (waste.low, waste.full) == (False, False)

    def test_none_is_no_warning(self) -> None:
        attributes = {"printer-state": "idle", "printer-state-reasons": "none"}
        assert cups.status_from_attributes("P", attributes).reasons == []


@pytest.fixture
def lpstat(monkeypatch: pytest.MonkeyPatch):
    """CUPS lists the printers in the list the test sets."""
    listed: list[str] = []
    monkeypatch.setattr(cups, "printers", lambda: list(listed))
    return listed


class TestChoosePrinter:
    def test_the_only_printer_is_the_default(self, lpstat) -> None:
        lpstat.append("Kyocera")
        assert cups.choose_printer("") == "Kyocera"

    def test_the_chosen_one_wins_among_several(self, lpstat) -> None:
        lpstat.extend(["Kyocera", "Xerox"])
        assert cups.choose_printer("Xerox") == "Xerox"

    @pytest.mark.parametrize(("listed", "chosen", "message"), [
        ([], "", "no printer reachable"),
        (["Kyocera", "Xerox"], "", "none chosen"),
        (["Kyocera"], "Xerox", "not reachable"),
    ])
    def test_no_usable_printer_is_an_error(self, lpstat, listed, chosen, message) -> None:
        lpstat.extend(listed)
        with pytest.raises(cups.PrinterError, match=message):
            cups.choose_printer(chosen)


@pytest.fixture
def printing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lpstat):
    """A documents area with a PDF and a DOCX; lp records its command line."""
    from aifred.lib import file_manager

    (tmp_path / "brief.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "brief.docx").write_bytes(b"PK")
    monkeypatch.setattr(file_manager, "safe_resolve", lambda name: (tmp_path / name, None))
    monkeypatch.setattr(printer_plugin, "_load_settings", lambda: {"PRINTER_MAX_COPIES": "3"})
    lpstat.append("Kyocera")
    commands: list[tuple[str, ...]] = []

    def run(*command: str) -> str:
        commands.append(command)
        return "request id is Kyocera-42 (1 file(s))\n"

    monkeypatch.setattr(cups, "_run", run)
    tools = {tool.name: tool for tool in printer_plugin.get_tools(PluginContext(agent_id="aifred", lang="de", session_id="t"))}
    return tools, commands, tmp_path


def _call(tool, **arguments) -> dict:
    return json.loads(asyncio.run(tool.executor(**arguments)))


class TestPrintFile:
    def test_a_pdf_goes_to_the_printer(self, printing) -> None:
        tools, commands, tmp_path = printing
        result = _call(tools["print_file"], filename="brief.pdf", copies=2, pages="1-2", duplex=True)
        assert result == {"printer": "Kyocera", "job": "Kyocera-42", "file": "brief.pdf", "copies": 2}
        assert commands == [(
            "lp", "-d", "Kyocera", "-n", "2", "-P", "1-2", "-o", "sides=two-sided-long-edge",
            "--", str(tmp_path / "brief.pdf"),
        )]

    def test_office_files_need_a_pdf_first(self, printing) -> None:
        tools, commands, _ = printing
        assert "Convert it to PDF" in _call(tools["print_file"], filename="brief.docx")["error"]
        assert commands == []

    @pytest.mark.parametrize("copies", [0, 4])
    def test_copies_are_capped_by_the_plugin_setting(self, printing, copies: int) -> None:
        tools, commands, _ = printing
        assert "between 1 and 3" in _call(tools["print_file"], filename="brief.pdf", copies=copies)["error"]
        assert commands == []

    def test_a_missing_file_is_reported(self, printing) -> None:
        tools, commands, _ = printing
        assert "File not found" in _call(tools["print_file"], filename="fehlt.pdf")["error"]
        assert commands == []


def test_printer_status_names_the_low_supplies(printing, monkeypatch: pytest.MonkeyPatch) -> None:
    tools, _, _ = printing
    monkeypatch.setattr(cups, "_run", lambda *command: IPPTOOL_OUTPUT)
    result = _call(tools["printer_status"])
    assert result["printer"] == "Kyocera" and result["low_supplies"] == ["Black Toner"]
    assert result["full_waste_containers"] == ["Waste Toner Box"]
    assert result["reasons"] == ["media-empty-warning", "toner-low-warning"]
