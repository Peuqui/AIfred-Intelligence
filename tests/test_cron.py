"""Cron expressions of the Horarium (lib/cron.py) against the classic cron rules.

Reference day: Saturday, 14 November 2026, 21:20. The "a-a" cases are the ones croniter got wrong (it reads an
equal range as the whole cycle, so "Saturdays" written as "6-6" fired every day)."""
from datetime import datetime

import pytest

from aifred.lib.cron import CronError, CronExpression, next_cron_run
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
    # a range whose start lies after its end runs forward across the end of the field
    ("0 9 * * fri-mon", datetime(2026, 11, 15, 9, 0)),
    ("0 9 * * 5-1", datetime(2026, 11, 15, 9, 0)),
    ("0 22-2 * * *", datetime(2026, 11, 14, 22, 0)),
    ("30 1-0 * * *", datetime(2026, 11, 14, 21, 30)),
    ("0 9 25-5 * *", datetime(2026, 11, 25, 9, 0)),
    ("0 9 1 nov-feb *", datetime(2026, 12, 1, 9, 0)),
    ("0 9 1 dec-jan *", datetime(2026, 12, 1, 9, 0)),
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


def test_the_scheduler_passes_the_refusal_on() -> None:
    # The job store refuses a job without a next run, so it can never run at some other time instead; the refusal
    # says what is wrong instead of a general "never fires".
    with pytest.raises(CronError, match="cron_out_of_range|not between"):
        _calculate_next_run("cron", "0 9 * * 9", "2026-11-14T21:20:00")
    assert _calculate_next_run("cron", "0 9 * * 6-6", "2026-11-14T21:20:00") == "2026-11-21T09:00:00"


@pytest.mark.parametrize(("field", "expected"), [
    ("1 * * * fri-mon", [0, 1, 5, 6]),
    ("1 * * * sat-tue/2", [1, 6]),          # Saturday, Monday: Sunday (0 and 7) counted once
    ("1 * * * 7-2", [0, 1, 2]),             # 7 is the Sunday that starts the week, no wrap
    ("1 * * * 6-0", [0, 6]),
])
def test_weekdays_across_the_end_of_the_week(field: str, expected: list[int]) -> None:
    assert sorted(CronExpression(field).weekdays) == expected


def test_steps_count_on_across_the_end() -> None:
    assert CronExpression("0 22-4/2 * * *").hours == [0, 2, 4, 22]
    assert CronExpression("50-10/5 * * * *").minutes == [0, 5, 10, 50, 55]
    assert sorted(CronExpression("0 0 1 oct-mar/2 *").months) == [2, 10, 12]    # Oct, Dec, Feb


@pytest.mark.parametrize(("expression", "german", "english"), [
    ("0 9 * *", "Ein Cron-Ausdruck hat fünf Felder (Minute Stunde Tag Monat Wochentag), „0 9 * *“ hat 4.",
     "A cron expression has five fields (minute hour day month weekday), “0 9 * *” has 4."),
    ("0 24 * * *", "Feld Stunde, „24“: 24 liegt nicht zwischen 0 und 23.",
     "Field hour, “24”: 24 is not between 0 and 23."),
    ("0 9 * * mon-fri,8", "Feld Wochentag, „8“: 8 liegt nicht zwischen 0 und 7.",
     "Field weekday, “8”: 8 is not between 0 and 7."),
    ("0 9 * * L", ("Feld Wochentag, „L“: „L“ ist weder eine Zahl noch ein Name "
                   "(erlaubt: Zahl, Name, *, a-b, /Schritt, Liste mit Komma)."),
     "Field weekday, “L”: “L” is neither a number nor a name (allowed: number, name, *, a-b, /step, list with commas)."),
    ("*/0 9 * * *", "Feld Minute, „*/0“: Der Schritt nach „/“ muss eine ganze Zahl ab 1 sein.",
     "Field minute, “*/0”: the step after “/” must be a whole number from 1 up."),
    ("0 0 30 2 *", "„0 0 30 2 *“ träfe nie ein: Keinen der Tage gibt es in den gewählten Monaten.",
     "“0 0 30 2 *” would never fire: none of the days exists in the chosen months."),
])
def test_refusals_name_what_is_wrong(expression: str, german: str, english: str) -> None:
    with pytest.raises(CronError) as refused:
        next_cron_run(expression, SATURDAY_EVENING)
    assert (refused.value.message("de"), refused.value.message("en")) == (german, english)
