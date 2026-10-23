"""Strict parsing of EPIM tool arguments and of the plugin settings."""

# EPIM stores naive local wall-clock time; the tests use exactly that.
# ruff: noqa: DTZ001

from datetime import date, datetime
from pathlib import Path

import pytest

from aifred.plugins.tools.epim.settings import parse_settings
from aifred.plugins.tools.epim.values import (
    InvalidValue,
    parse_bool,
    parse_date,
    parse_datetime,
    parse_id,
    parse_optional_text,
    parse_text,
    reject_unknown,
)


@pytest.mark.parametrize("raw", ["2026-11-03 10:30", "2026-11-03T10:30", "2026-11-03 10:30:00"])
def test_datetime_accepts_iso_forms(raw):
    assert parse_datetime(raw, "start") == datetime(2026, 11, 3, 10, 30)


@pytest.mark.parametrize("raw", ["10/11/2026 10:00", "TOMORROW", "2026-11-03", "2026-13-01 10:00", 1730000000, None])
def test_datetime_rejects_everything_else(raw):
    with pytest.raises(InvalidValue):
        parse_datetime(raw, "start")


def test_date():
    assert parse_date("2026-11-03", "date_from") == date(2026, 11, 3)
    with pytest.raises(InvalidValue):
        parse_date("03.11.2026", "date_from")


@pytest.mark.parametrize("raw, expected", [(True, True), (False, False), ("true", True), ("False", False)])
def test_bool(raw, expected):
    assert parse_bool(raw, "all_day") is expected


@pytest.mark.parametrize("raw", ["ja", "0", 1, None])
def test_bool_rejects_guesses(raw):
    with pytest.raises(InvalidValue):
        parse_bool(raw, "all_day")


def test_text_accepts_numbers_and_rejects_structures():
    assert parse_text(34119, "PLZ") == "34119"
    for raw in (["a"], {"a": 1}, True, None):
        with pytest.raises(InvalidValue):
            parse_text(raw, "x")


def test_optional_text_clears_on_null_or_empty():
    assert parse_optional_text(None, "x") is None
    assert parse_optional_text("  ", "x") is None
    assert parse_optional_text("Musterort", "x") == "Musterort"


def test_id_from_string_or_int():
    assert parse_id("271045353429653301", "entity_id") == 271045353429653301
    assert parse_id(5, "entity_id") == 5
    for raw in ("12a", True, 3.5):
        with pytest.raises(InvalidValue):
            parse_id(raw, "entity_id")


def test_reject_unknown_names_the_fields():
    with pytest.raises(InvalidValue, match="end_time"):
        reject_unknown({"start": 1, "end_time": 2}, {"start", "end"}, "task")


def test_settings_modules_default_and_strict_levels():
    defaults = parse_settings({})
    assert defaults.db_path is None and not defaults.modules.any_write
    assert defaults.modules.can_read("calendar") and not defaults.modules.can_read("passwords")
    parsed = parse_settings({"EPIM_DB_PATH": "/x/y.epim", "EPIM_MODULE_CONTACTS": "write",
                             "EPIM_MODULE_NOTES": "off"})
    assert parsed.db_path == Path("/x/y.epim")
    assert parsed.modules.can_write("contacts") and parsed.modules.any_write
    assert not parsed.modules.can_read("notes") and not parsed.modules.can_write("calendar")
    with pytest.raises(ValueError):
        parse_settings({"EPIM_MODULE_TODOS": "yes"})
