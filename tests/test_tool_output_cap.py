"""The cap on tool output: what is cut, and what stays."""
import json

import pytest

from aifred.lib.security import UNTRUSTED_DATA_END, wrap_untrusted_data
from aifred.lib.tool_output_cap import cap_tool_output, compute_budget

BUDGET = 1000
LONG_TEXT = "Heizöl kostet heute wieder mehr als gestern. " * 2000


def test_what_fits_stays_as_it_is() -> None:
    assert cap_tool_output("kurz", BUDGET) == "kurz"


def test_a_long_text_is_cut_from_the_end_with_a_note() -> None:
    capped = cap_tool_output(LONG_TEXT, BUDGET)
    assert capped.startswith(LONG_TEXT[:100])
    assert len(capped) < len(LONG_TEXT)
    assert "truncated to ~1000 tokens" in capped


def test_a_fence_around_retrieved_data_stays_closed() -> None:
    capped = cap_tool_output(wrap_untrusted_data(LONG_TEXT, "nextcloud"), BUDGET)
    assert capped.count(UNTRUSTED_DATA_END) == 1
    data, note = capped.split(UNTRUSTED_DATA_END)
    assert data.startswith('<untrusted_data source="nextcloud"')
    assert "truncated to" in note and "truncated to" not in data


def test_a_list_loses_entries_from_the_end_and_says_so() -> None:
    result = json.dumps({"results": [{"text": LONG_TEXT[:900], "number": number} for number in range(20)]})
    capped = json.loads(cap_tool_output(result, BUDGET))
    kept = [entry["number"] for entry in capped["results"]]
    assert kept == list(range(len(kept))) and 0 < len(kept) < 20
    assert "_truncated" in capped


@pytest.mark.parametrize(("context", "used", "budget"), [
    (40_000, 10_000, 20_000),     # 75 % of the context, minus what is taken
    (200_000, 20_000, 130_000),
    (40_000, 40_000, 2_000),      # the floor on a full context
])
def test_the_budget_is_what_is_left_of_three_quarters(context: int, used: int, budget: int) -> None:
    assert compute_budget(context, used, 0) == budget
