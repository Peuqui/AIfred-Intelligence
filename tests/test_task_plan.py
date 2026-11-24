"""TaskPlan: the plan structure of a split task, its round trip and its one check (plan_problem)."""

import copy
from typing import Any

import pytest

from aifred.lib.scheduler import job_problem
from aifred.lib.task_plan import TaskPlan, plan_problem
from aifred.lib.user_error import Invalid

VALID: dict[str, Any] = {
    "frame": "Preise prüfen",
    "parts": [
        {"id": "a", "title": "Teil A", "task": "Suche A",
         "positions": [{"id": "p1", "name": "Eins"}, {"id": "p2", "name": "Zwei"}]},
        {"id": "b", "title": "Teil B", "task": "Suche B", "positions": [{"id": "p1", "name": "Eins"}]},
    ],
    "columns": [{"name": "preis", "type": "zahl"}, {"name": "seite", "type": "link"}, {"name": "notiz", "type": "text"}],
    "trend": {"value": "preis"},
}


def _changed(**changes: Any) -> dict[str, Any]:
    payload = copy.deepcopy(VALID)
    payload.update(changes)
    return payload


def _problem_key(payload: dict[str, Any]) -> str:
    problem = plan_problem(payload)
    assert isinstance(problem, Invalid)
    return problem.key


class TestRoundTrip:
    def test_valid_plan_survives_the_round_trip(self):
        assert plan_problem(VALID) is None
        assert TaskPlan.from_payload(VALID).to_payload() == VALID

    def test_without_trend_the_key_is_absent(self):
        payload = {k: v for k, v in VALID.items() if k != "trend"}
        plan = TaskPlan.from_payload(payload)
        assert plan.trend_value is None
        assert plan.to_payload() == payload

    def test_fields_are_read(self):
        plan = TaskPlan.from_payload(VALID)
        assert plan.frame == "Preise prüfen" and plan.trend_value == "preis"
        assert [p.id for p in plan.parts] == ["a", "b"]
        assert plan.parts[0].positions[1].name == "Zwei"
        assert (plan.columns[0].name, plan.columns[0].type) == ("preis", "zahl")

    def test_frame_and_columns_alone_are_valid(self):
        assert plan_problem({"frame": "x"}) is None
        assert plan_problem({"columns": [{"name": "a", "type": "text"}]}) is None


class TestInvalid:
    def test_duplicate_part_ids(self):
        payload = _changed()
        payload["parts"][1]["id"] = "a"
        assert _problem_key(payload) == "plan_duplicate_part"

    def test_duplicate_position_ids_within_a_part(self):
        payload = _changed()
        payload["parts"][0]["positions"][1]["id"] = "p1"
        assert _problem_key(payload) == "plan_duplicate_position"

    def test_same_position_id_in_two_parts_is_fine(self):
        assert plan_problem(VALID) is None  # "p1" in a and b

    def test_position_id_zusatz_is_reserved(self):
        payload = _changed()
        payload["parts"][0]["positions"][0]["id"] = "zusatz"
        assert _problem_key(payload) == "plan_reserved_position"

    def test_part_without_positions(self):
        payload = _changed()
        payload["parts"][1]["positions"] = []
        assert _problem_key(payload) == "plan_part_without_positions"

    def test_duplicate_column_names(self):
        payload = _changed()
        payload["columns"][1]["name"] = "preis"
        assert _problem_key(payload) == "plan_duplicate_column"

    @pytest.mark.parametrize("name", ["position_id", "status", "befund", "quelle", "neue_quellen"])
    def test_column_named_like_a_mandatory_field(self, name):
        payload = _changed()
        payload["columns"][2]["name"] = name
        assert _problem_key(payload) == "plan_reserved_column"

    def test_unknown_column_type(self):
        payload = _changed()
        payload["columns"][2]["type"] = "datum"
        assert _problem_key(payload) == "plan_unknown_column_type"

    def test_trend_value_must_be_a_number_column(self):
        assert _problem_key(_changed(trend={"value": "notiz"})) == "plan_trend_not_a_number_column"
        assert _problem_key(_changed(trend={"value": "gibtsnicht"})) == "plan_trend_not_a_number_column"

    def test_parts_and_fixed_text_exclude_each_other(self):
        assert _problem_key(_changed(fixed_text="Hallo")) == "plan_parts_with_fixed_text"

    @pytest.mark.parametrize("payload", [
        {"parts": "x"},
        {"parts": ["x"]},
        {"parts": [{"id": "a", "title": "t"}]},
        {"parts": [{"id": "a", "title": "t", "task": "k", "positions": "x"}]},
        {"parts": [{"id": "a", "title": "t", "task": "k", "positions": [{"id": "p"}]}]},
        {"columns": [{"name": "a"}]},
        {"columns": "x"},
        {"trend": {}},
        {"frame": 3},
    ])
    def test_malformed_shapes(self, payload):
        assert _problem_key(payload) == "plan_malformed"

    def test_every_refusal_has_a_text_in_both_languages(self):
        payload = _changed(trend={"value": "notiz"})
        problem = plan_problem(payload)
        assert problem is not None
        assert "notiz" in problem.message("de") and "notiz" in problem.message("en")
        assert not problem.message("de").startswith("error_")


class TestJobProblem:
    def test_plan_in_a_job_is_checked(self):
        assert job_problem("cron", copy.deepcopy(VALID)) is None
        bad = _changed(trend={"value": "notiz"})
        problem = job_problem("cron", bad)
        assert problem is not None and problem.key == "plan_trend_not_a_number_column"

    def test_fixed_text_with_parts_is_refused(self):
        payload = _changed(fixed_text="Hallo", delivery="announce", channel="x")
        problem = job_problem("cron", payload)
        assert problem is not None and problem.key == "plan_parts_with_fixed_text"

    def test_jobs_without_a_plan_stay_valid(self):
        assert job_problem("cron", {}) is None
        assert job_problem("cron", {"fixed_text": "Hallo", "delivery": "announce", "channel": "x"}) is None

    def test_columns_alone_are_checked(self):
        problem = job_problem("cron", {"columns": [{"name": "status", "type": "text"}]})
        assert problem is not None and problem.key == "plan_reserved_column"
