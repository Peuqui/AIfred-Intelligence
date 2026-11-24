"""plan_record: the schema from the plan, every rejection, and what is accepted and collected."""

from __future__ import annotations

import json
from typing import Any

import pytest

from aifred.lib.function_calling import Tool
from aifred.lib.plan_record import PartRecords, PlanEntry, make_record_tool
from aifred.lib.security import TIER_READONLY
from aifred.lib.task_plan import EXTRA_POSITION_ID, TaskPlan

PLAN = TaskPlan.from_payload({
    "frame": "Preise",
    "parts": [{"id": "a", "title": "Teil A", "task": "Suche",
               "positions": [{"id": "p1", "name": "Eins"}, {"id": "p2", "name": "Zwei"}]}],
    "columns": [{"name": "preis", "type": "zahl"}, {"name": "seite", "type": "link"}, {"name": "notiz", "type": "text"}],
})
PART = PLAN.parts[0]


class Setup:
    def __init__(self, lang: str = "de") -> None:
        self.read: set[str] = set()
        self.records = PartRecords()
        self.tool: Tool = make_record_tool(PLAN, PART, lambda: self.read, self.records, lang)

    def call(self, **arguments: Any) -> str:
        return str(self.tool.executor(**arguments))

    def error(self, **arguments: Any) -> str:
        result = json.loads(self.call(**arguments))
        assert isinstance(result, dict)
        return str(result["error"])


@pytest.fixture
def setup() -> Setup:
    return Setup()


def test_the_schema_is_built_from_the_plan(setup: Setup) -> None:
    tool = setup.tool
    assert tool.name == "plan_record" and tool.tier == TIER_READONLY
    schema = tool.parameters
    assert schema["required"] == ["position_id", "status", "befund"]
    properties = schema["properties"]
    assert properties["position_id"]["enum"] == ["p1", "p2", EXTRA_POSITION_ID]
    assert properties["status"]["enum"] == ["gefunden", "nicht_gefunden", "fehler"]
    assert [properties[name]["type"] for name in ("befund", "quelle", "preis", "seite", "notiz")] == [
        "string", "string", "number", "string", "string",
    ]
    assert properties["neue_quellen"] == {"type": "array", "items": {"type": "string"},
                                          "description": properties["neue_quellen"]["description"]}
    assert set(properties) == {"position_id", "status", "befund", "quelle", "preis", "seite", "notiz", "neue_quellen"}
    assert all(property["description"] for property in properties.values())


def test_the_description_names_the_positions_and_both_languages_load() -> None:
    for lang in ("de", "en"):
        tool = Setup(lang).tool
        assert "Teil A" in tool.description and "p1: Eins" in tool.description and "p2: Zwei" in tool.description
    assert Setup("de").tool.description != Setup("en").tool.description


def test_a_plan_without_columns_has_only_the_fixed_fields() -> None:
    plan = TaskPlan.from_payload({"parts": [{"id": "a", "title": "A", "task": "t", "positions": [{"id": "p", "name": "P"}]}]})
    tool = make_record_tool(plan, plan.parts[0], set, PartRecords(), "de")
    assert set(tool.parameters["properties"]) == {"position_id", "status", "befund", "quelle", "neue_quellen"}


class TestRejections:
    def test_unknown_position(self, setup: Setup) -> None:
        assert "p9" in setup.error(position_id="p9", status="nicht_gefunden", befund="x")
        assert setup.records.entries == []

    def test_unknown_status(self, setup: Setup) -> None:
        assert "vielleicht" in setup.error(position_id="p1", status="vielleicht", befund="x")

    def test_missing_finding(self, setup: Setup) -> None:
        setup.read.add("shop.test/a")
        assert setup.error(position_id="p1", status="gefunden", befund=None, quelle="https://shop.test/a")
        assert setup.records.entries == []

    @pytest.mark.parametrize("status", ["nicht_gefunden", "fehler"])
    @pytest.mark.parametrize("befund", ["", "   "])
    def test_not_found_and_error_need_a_finding(self, setup: Setup, status: str, befund: str) -> None:
        assert status in setup.error(position_id="p1", status=status, befund=befund)
        assert setup.records.entries == []

    def test_a_number_as_text_hints_at_the_dot(self, setup: Setup) -> None:
        setup.read.add("shop.test/a")
        error = setup.error(position_id="p1", status="gefunden", befund="x", quelle="https://shop.test/a", preis="48,00")
        assert "preis" in error and "48,00" in error and "48.5" in error
        assert setup.records.entries == []

    @pytest.mark.parametrize("value", [True, [1], {"a": 1}])
    def test_a_number_column_takes_only_a_number(self, setup: Setup, value: Any) -> None:
        assert "preis" in setup.error(position_id="p1", status="nicht_gefunden", befund="x", preis=value)

    @pytest.mark.parametrize("column", ["seite", "notiz"])
    def test_a_text_column_takes_text(self, setup: Setup, column: str) -> None:
        assert column in setup.error(position_id="p1", status="nicht_gefunden", befund="x", **{column: 5})

    def test_a_source_must_be_a_text(self, setup: Setup) -> None:
        assert "quelle" in setup.error(position_id="p1", status="nicht_gefunden", befund="x", quelle=3)

    def test_unknown_field(self, setup: Setup) -> None:
        error = setup.error(position_id="p1", status="nicht_gefunden", befund="x", farbe="rot")
        assert "farbe" in error and "preis" in error

    def test_new_sources_must_be_a_list_of_texts(self, setup: Setup) -> None:
        assert "neue_quellen" in setup.error(position_id="p1", status="nicht_gefunden", befund="x", neue_quellen="https://a.test")
        assert "neue_quellen" in setup.error(position_id="p1", status="nicht_gefunden", befund="x", neue_quellen=[1])
        assert setup.records.new_sources == set()

    def test_found_needs_a_source(self, setup: Setup) -> None:
        assert "Quelle" in setup.error(position_id="p1", status="gefunden", befund="x")
        assert "Quelle" in setup.error(position_id="p1", status="gefunden", befund="x", quelle="  ")

    def test_found_needs_a_source_read_in_this_run(self, setup: Setup) -> None:
        setup.read.add("shop.test/other")
        error = setup.error(position_id="p1", status="gefunden", befund="x", quelle="https://shop.test/a")
        assert error == "Quelle in diesem Lauf nicht abgerufen: https://shop.test/a — erst abrufen (web_fetch), dann eintragen."
        assert setup.records.entries == [] and setup.records.new_sources == set()

    def test_a_rejection_in_english(self) -> None:
        english = Setup("en")
        assert "not fetched" in english.error(position_id="p1", status="gefunden", befund="x", quelle="https://shop.test/a")


class TestAcceptance:
    def test_found_with_a_read_source(self, setup: Setup) -> None:
        setup.read.add("shop.test/a")
        result = setup.call(position_id="p1", status="gefunden", befund="48 Euro", quelle="https://shop.test/a",
                            preis=48.5, seite="https://shop.test/a", notiz="ok")
        assert result == "Position p1 eingetragen; noch offen: p2"
        assert setup.records.entries == [PlanEntry("p1", "gefunden", "48 Euro", "https://shop.test/a",
                                                   {"preis": 48.5, "seite": "https://shop.test/a", "notiz": "ok"})]

    @pytest.mark.parametrize("quelle", [
        "https://www.shop.test/a/", "http://shop.test/a#preise", "https://r.jina.ai/https://shop.test/a",
        "https://SHOP.test/a/",
    ])
    def test_spellings_of_a_read_address(self, setup: Setup, quelle: str) -> None:
        setup.read.add("shop.test/a")
        setup.call(position_id="p1", status="gefunden", befund="x", quelle=quelle)
        assert len(setup.records.entries) == 1

    def test_a_whole_number_is_a_number(self, setup: Setup) -> None:
        setup.call(position_id="p1", status="nicht_gefunden", befund="x", preis=48)
        assert setup.records.entries[0].values == {"preis": 48}

    def test_left_out_and_null_columns_stay_out(self, setup: Setup) -> None:
        setup.call(position_id="p1", status="nicht_gefunden", befund="Shop führt es nicht", preis=None)
        assert setup.records.entries[0].values == {}

    def test_not_found_and_error_need_no_source(self, setup: Setup) -> None:
        setup.call(position_id="p1", status="nicht_gefunden", befund="nicht im Sortiment")
        setup.call(position_id="p2", status="fehler", befund="404")
        assert [entry.status for entry in setup.records.entries] == ["nicht_gefunden", "fehler"]

    def test_the_extra_find(self, setup: Setup) -> None:
        setup.read.add("shop.test/neu")
        result = setup.call(position_id=EXTRA_POSITION_ID, status="gefunden", befund="Zubehör", quelle="https://shop.test/neu")
        assert result == "Position zusatz eingetragen; noch offen: p1, p2"

    def test_several_entries_per_position(self, setup: Setup) -> None:
        setup.read.update({"a.test/x", "b.test/y"})
        setup.call(position_id="p1", status="gefunden", befund="A", quelle="https://a.test/x", preis=10)
        setup.call(position_id="p1", status="gefunden", befund="B", quelle="https://b.test/y", preis=12)
        assert [entry.values["preis"] for entry in setup.records.entries] == [10, 12]

    def test_new_sources_are_collected_once(self, setup: Setup) -> None:
        setup.call(position_id="p1", status="nicht_gefunden", befund="x", neue_quellen=["https://n.test", "https://m.test"])
        setup.call(position_id="p2", status="nicht_gefunden", befund="y", neue_quellen=["https://n.test"])
        assert setup.records.new_sources == {"https://n.test", "https://m.test"}

    def test_the_open_list_shrinks_and_ends(self, setup: Setup) -> None:
        assert setup.call(position_id="p2", status="nicht_gefunden", befund="x") == "Position p2 eingetragen; noch offen: p1"
        assert setup.records.open_positions(PART) == ["p1"]
        assert setup.call(position_id="p1", status="fehler", befund="x") == (
            "Position p1 eingetragen; alle Positionen des Teils haben einen Eintrag.")
        assert setup.records.open_positions(PART) == []

    def test_the_extra_find_does_not_close_a_position(self, setup: Setup) -> None:
        setup.call(position_id=EXTRA_POSITION_ID, status="fehler", befund="x")
        assert setup.records.open_positions(PART) == ["p1", "p2"]

    def test_the_read_addresses_are_looked_up_at_every_call(self, setup: Setup) -> None:
        assert "nicht abgerufen" in setup.error(position_id="p1", status="gefunden", befund="x", quelle="https://shop.test/a")
        setup.read.add("shop.test/a")   # read later in the same run
        setup.call(position_id="p1", status="gefunden", befund="x", quelle="https://shop.test/a")
        assert len(setup.records.entries) == 1
