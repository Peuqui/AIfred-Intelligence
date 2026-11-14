"""Cron expressions of the Horarium: the next time a five-field expression fires.

Classic cron rules (Vixie cron, crontab(5)):
- fields: minute hour day-of-month month day-of-week
- each field: "*", a number, a range "a-b", a step "*/n", "a/n" or "a-b/n", and lists of these with ","
- names for months (jan..dec) and weekdays (sun..sat), any case; day-of-week 7 is Sunday like 0
- a range "a-a" is the one value a
- day-of-month and day-of-week both restricted (neither starts with "*"): a day matches if EITHER does

One addition classic cron rejects: a range whose start lies after its end runs forward across the end of the field,
the only way it can be meant — "fri-mon" is Friday to Monday, "22-2" the hours 22 to 2, "nov-feb" November to
February; a step counts on across the end ("22-4/2" is 22, 0, 2, 4).

croniter, used before, wraps too, but takes "6-6" or "jan-jan" for a whole round of the cycle — a job for
"Saturdays" written as "6-6" ran every day.

Every refusal is a CronError (an Invalid, 422) that names the field and the part it could not take.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .i18n import t
from .user_error import Invalid

MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
WEEKDAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

# (name, lowest, highest, names counted from lowest) per field; the name is the i18n key cron_field_<name>.
_FIELDS: tuple[tuple[str, int, int, tuple[str, ...]], ...] = (
    ("minute", 0, 59, ()),
    ("hour", 0, 23, ()),
    ("day", 1, 31, ()),
    ("month", 1, 12, MONTH_NAMES),
    ("weekday", 0, 7, WEEKDAY_NAMES),
)

# The Gregorian calendar repeats every 400 years (weekdays and leap days alike): a day no search of that length
# finds never comes.
_CALENDAR_CYCLE_YEARS = 400


class CronError(Invalid):
    """A cron expression refused, with the field it concerns worded in the UI language (the i18n texts cannot nest a
    translated value themselves)."""

    def __init__(self, key: str, field: str = "", **values: Any) -> None:
        super().__init__(key, **values)
        self.field = field

    def message(self, lang: str | None = None) -> str:
        values = dict(self.values)
        if self.field:
            values["field"] = t(f"cron_field_{self.field}", lang=lang)
        return t(f"error_{self.key}", lang=lang, **values)


class CronExpression:
    """A parsed five-field expression. CronError for anything it does not take."""

    def __init__(self, expression: str) -> None:
        fields = expression.split()
        if len(fields) != len(_FIELDS):
            raise CronError("cron_field_count", expression=expression, found=len(fields))
        self.expression = expression
        minutes, hours, days, months, weekdays = (
            _parse_field(text, *spec) for text, spec in zip(fields, _FIELDS, strict=True))
        self.minutes = sorted(minutes)
        self.hours = sorted(hours)
        self.days = days
        self.months = months
        self.weekdays = {weekday % 7 for weekday in weekdays}
        self._day_star = fields[2].startswith("*")
        self._weekday_star = fields[4].startswith("*")

    def matches_day(self, day: date) -> bool:
        cron_weekday = (day.weekday() + 1) % 7      # cron counts from Sunday = 0
        day_hit, weekday_hit = day.day in self.days, cron_weekday in self.weekdays
        if self._day_star or self._weekday_star:
            return day_hit and weekday_hit
        return day_hit or weekday_hit

    def next_after(self, after: datetime) -> datetime:
        """The first moment after `after` (to the minute) the expression fires. CronError if it never does."""
        start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = start.date()
        last_day = date(start.year + _CALENDAR_CYCLE_YEARS, 12, 31)
        while day <= last_day:
            if day.month not in self.months:
                day = _first_of_next_month(day)
                continue
            if self.matches_day(day):
                for hour in self.hours:
                    for minute in self.minutes:
                        moment = datetime(day.year, day.month, day.day, hour, minute)
                        if moment >= start:
                            return moment
            day += timedelta(days=1)
        # Only possible with days of the month none of the chosen months has (minutes, hours and months are never
        # empty, and every day of a month falls on every weekday within the calendar cycle).
        raise CronError("cron_never_fires", expression=self.expression)


def next_cron_run(expression: str, after: datetime) -> datetime:
    """The next time `expression` fires after `after`. CronError for an invalid one or one that never fires."""
    return CronExpression(expression).next_after(after)


def _parse_field(text: str, field: str, lowest: int, highest: int, names: tuple[str, ...]) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        span, slash, step_text = part.partition("/")
        step = _step(step_text, field, part) if slash else 1
        if span == "*":
            first, last = lowest, highest
        elif "-" in span:
            first_text, _, last_text = span.partition("-")
            first = _value(first_text, field, lowest, highest, names, part)
            last = _value(last_text, field, lowest, highest, names, part)
        else:
            first = _value(span, field, lowest, highest, names, part)
            last = highest if slash else first    # "a/n" steps from a to the end of the field
        cycle_end = highest
        if field == "weekday" and first > last:
            # Across the end of the week the cycle is 0..6: 7 is the Sunday that starts it, counted once.
            first, cycle_end = first % 7, 6
        if first <= last:
            values.update(range(first, last + 1, step))
        else:
            # Forward across the end of the field: first .. end, then lowest .. last, one count of steps.
            values.update((list(range(first, cycle_end + 1)) + list(range(lowest, last + 1)))[::step])
    return values


def _step(text: str, field: str, part: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise CronError("cron_bad_step", field=field, part=part)
    return int(text)


def _value(text: str, field: str, lowest: int, highest: int, names: tuple[str, ...], part: str) -> int:
    name = text.lower()
    if name in names:
        return lowest + names.index(name)
    if not text.isdigit():
        raise CronError("cron_not_understood", field=field, part=part, value=text)
    number = int(text)
    if not lowest <= number <= highest:
        raise CronError("cron_out_of_range", field=field, part=part, value=number, lowest=lowest, highest=highest)
    return number


def _first_of_next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)
