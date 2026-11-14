"""Cron expressions of the Horarium (lib/cron.py) against the classic cron rules.

Reference day: Saturday, 14 November 2026, 21:20. The "a-a" cases are the ones croniter got wrong (it reads an
equal range as the whole cycle, so "Saturdays" written as "6-6" fired every day)."""
from datetime import datetime

import pytest

from aifred.lib.cron import next_cron_run
from aifred.lib.scheduler import _calculate_next_run

SATURDAY_EVENING = datetime(2026, 11, 14, 21, 20)


@pytest.mark.parametrize(("expression", "expected"), [
    # an equal range is the one value
    ("0 9 * * 6-6", datetime(2026, 11, 21, 9, 0)),
    ("0 9 * * sat-sat", datetime(2026, 11, 21, 9, 0)),
    ("0 9 * * 1-1/3", datetime(2026, 11, 16, 9, 0)),
    ("0 12-12 * * *", datetime(2026, 11, 15, 12, 0)),
    ("*/15 6-6 * * *", datetime(2026, 11, 15, 6, 0)),
    ("0 9 6-6 * *", datetime(2026, 12, 6, 9, 0)),
    ("0 9 * 6-6 *", datetime(2027, 6, 1, 9, 0)),
    ("0 9 * jun-jun *", datetime(2027, 6, 1, 9, 0)),
    # the ordinary forms
    ("45 6 * * *", datetime(2026, 11, 15, 6, 45)),
    ("0 9,14,19 * * *", datetime(2026, 11, 15, 9, 0)),
    ("30 21 * * *", datetime(2026, 11, 14, 21, 30)),
    ("20 21 * * *", datetime(2026, 11, 15, 21, 20)),    # the moment itself is not "after"
    ("*/7 * * * *", datetime(2026, 11, 14, 21, 21)),
    ("50/5 21 * * *", datetime(2026, 11, 14, 21, 50)),  # "a/n" runs from a to the end of the field
    ("0 8 * * 1-5", datetime(2026, 11, 16, 8, 0)),
    ("0 8 * * MON-fri", datetime(2026, 11, 16, 8, 0)),
    ("0 8 * * 7", datetime(2026, 11, 15, 8, 0)),        # 7 is Sunday
    ("0 8 * * 5-7", datetime(2026, 11, 15, 8, 0)),
    ("0 8 1 * *", datetime(2026, 12, 1, 8, 0)),
    ("0 0 29 2 *", datetime(2028, 2, 29, 0, 0)),
    ("0 0 1 jan *", datetime(2027, 1, 1, 0, 0)),
    # day of month and day of week both restricted: either one
    ("0 8 1 * 1", datetime(2026, 11, 16, 8, 0)),
    ("0 8 15 * 3", datetime(2026, 11, 15, 8, 0)),
    # one of them a star (also "*/n"): both must hold
    ("0 8 */2 * 1", datetime(2026, 11, 23, 8, 0)),
])
def test_next_run(expression: str, expected: datetime) -> None:
    assert next_cron_run(expression, SATURDAY_EVENING) == expected


@pytest.mark.parametrize("expression", [
    "0 9 * *",              # four fields
    "0 9 * * * *",          # six fields (seconds)
    "@daily",
    "60 9 * * *",
    "0 24 * * *",
    "0 9 0 * *",
    "0 9 * 13 *",
    "0 9 * * 8",
    "0 9 * * fri-mon",      # backwards, no wrap-around
    "0 9 * * */0",
    "0 9 * * L",
    "0 9 * * 1#2",
    "0 9 1, * *",
    "0 9 ? * *",
    "0 0 30 2 *",           # never comes
    "0 0 31 4,6,9,11 *",
])
def test_rejected(expression: str) -> None:
    with pytest.raises(ValueError):
        next_cron_run(expression, SATURDAY_EVENING)


def test_scheduler_rejects_what_never_fires() -> None:
    # The job store refuses a job without a next run, so it can never run at some other time instead.
    assert _calculate_next_run("cron", "0 9 * * fri-mon", "2026-11-14T21:20:00") is None
    assert _calculate_next_run("cron", "0 9 * * 6-6", "2026-11-14T21:20:00") == "2026-11-21T09:00:00"
