"""CUPS through its command-line tools (lp, lpstat, ipptool) — nothing to install.

The printers come from CUPS itself: every destination it can print to,
including the ones cups-browsed finds on the network (they exist only while
the printer is reachable). Commands run with ``LC_ALL=C`` so their output can
be parsed independent of the system language.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# ipptool's standard test file: "get-printer-attributes" for every attribute.
_IPP_GET_ATTRIBUTES = "get-printer-attributes.test"
_IPP_LINE = re.compile(r"^\s*(?P<name>[a-z0-9-]+) \([^)]*\) = (?P<value>.*)$")
_LP_JOB = re.compile(r"request id is (?P<job>\S+)")
_COMMAND_TIMEOUT_S = 15


class PrinterError(RuntimeError):
    """A CUPS command failed or no printer can be used; the message says why."""


def _run(*command: str) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_S, check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except FileNotFoundError as missing:
        raise PrinterError(f"{command[0]} not installed (CUPS client tools)") from missing
    except subprocess.TimeoutExpired as slow:
        raise PrinterError(f"{command[0]} did not answer within {_COMMAND_TIMEOUT_S} s") from slow
    if result.returncode != 0:
        raise PrinterError(f"{' '.join(command[:2])} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def printers() -> list[str]:
    """Every destination CUPS can print to right now, as CUPS names it."""
    return [line.strip() for line in _run("lpstat", "-e").splitlines() if line.strip()]


def choose_printer(chosen: str) -> str:
    """The printer to use: the one chosen in the plugin settings, or the only
    one there is. ``PrinterError`` when none is reachable or a choice is due."""
    available = printers()
    if not available:
        raise PrinterError("no printer reachable (CUPS lists none — switched off?)")
    if chosen:
        if chosen not in available:
            raise PrinterError(f"printer '{chosen}' is not reachable right now (CUPS lists: {', '.join(available)})")
        return chosen
    if len(available) == 1:
        return available[0]
    raise PrinterError(f"several printers and none chosen in the plugin settings: {', '.join(available)}")


def submit(printer: str, path: Path, copies: int, pages: str, duplex: bool) -> str:
    """Hand the file to CUPS; returns the job id."""
    command = ["lp", "-d", printer, "-n", str(copies)]
    if pages:
        command += ["-P", pages]
    command += ["-o", "sides=two-sided-long-edge" if duplex else "sides=one-sided", "--", str(path)]
    match = _LP_JOB.search(_run(*command))
    if match is None:
        raise PrinterError("lp accepted the file but named no job id")
    return match.group("job")


@dataclass
class Supply:
    """One marker (toner, ink, drum, waste container …) with its level in
    percent (None when the printer does not report one). For a waste
    container (kind ``waste-…``) the level is how full it is."""

    name: str
    kind: str
    level: int | None
    low_level: int | None
    high_level: int | None

    @property
    def is_waste(self) -> bool:
        return self.kind.startswith("waste")

    @property
    def low(self) -> bool:
        """A consumable at or below its low mark — replace soon."""
        return (not self.is_waste and self.level is not None and self.low_level is not None
                and self.level <= self.low_level)

    @property
    def full(self) -> bool:
        """A waste container at or above its high mark — empty or replace."""
        return (self.is_waste and self.level is not None and self.high_level is not None
                and self.level >= self.high_level)


@dataclass
class PrinterStatus:
    printer: str
    state: str                       # idle, processing, stopped
    reasons: list[str]               # printer-state-reasons without "none"
    message: str
    queued_jobs: int
    supplies: list[Supply] = field(default_factory=list)


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",")] if value else []


def parse_attributes(output: str) -> dict[str, str]:
    """``name (type) = value`` lines of ``ipptool -tv`` (the first occurrence wins)."""
    attributes: dict[str, str] = {}
    for line in output.splitlines():
        match = _IPP_LINE.match(line)
        if match and match.group("name") not in attributes:
            attributes[match.group("name")] = match.group("value").strip()
    return attributes


def status_from_attributes(printer: str, attributes: dict[str, str]) -> PrinterStatus:
    names = _split(attributes.get("marker-names", ""))
    kinds = _split(attributes.get("marker-types", ""))
    levels = _split(attributes.get("marker-levels", ""))
    lows = _split(attributes.get("marker-low-levels", ""))
    highs = _split(attributes.get("marker-high-levels", ""))

    def percent(values: list[str], index: int) -> int | None:
        # IPP reports -1 (unavailable), -2 (unknown), -3 (some remaining) as non-levels.
        if index >= len(values) or not values[index].lstrip("-").isdigit():
            return None
        number = int(values[index])
        return number if number >= 0 else None

    return PrinterStatus(
        printer=printer,
        state=attributes.get("printer-state", "unknown"),
        reasons=[reason for reason in _split(attributes.get("printer-state-reasons", "")) if reason != "none"],
        message=attributes.get("printer-state-message", ""),
        queued_jobs=int(attributes.get("queued-job-count", "0") or 0),
        supplies=[
            Supply(
                name, kinds[index] if index < len(kinds) else "",
                percent(levels, index), percent(lows, index), percent(highs, index),
            )
            for index, name in enumerate(names)
        ],
    )


def status(printer: str) -> PrinterStatus:
    """State, warnings and supply levels as CUPS knows them for this printer."""
    # -t prints the test results, -v the attributes received — -v alone prints nothing.
    output = _run("ipptool", "-tv", f"ipp://localhost/printers/{printer}", _IPP_GET_ATTRIBUTES)
    return status_from_attributes(printer, parse_attributes(output))
