"""Cron expressions of the Horarium: the next time a five-field expression fires.

Classic cron rules (Vixie cron, crontab(5)), nothing beyond them:
- fields: minute hour day-of-month month day-of-week
- each field: "*", a number, a range "a-b" (a <= b), a step "*/n", "a/n" or "a-b/n", and lists of these with ","
- names for months (jan..dec) and weekdays (sun..sat), any case; day-of-week 7 is Sunday like 0
- a range "a-a" is the one value a; a range running backwards ("fri-mon") is an error, not a wrap
- day-of-month and day-of-week both restricted (neither starts with "*"): a day matches if EITHER does

croniter, used before, reads "6-6" or "jan-jan" as the whole cycle on purpose (its own wrap-around model) — a
job for "Saturdays" written as "6-6" ran every day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
WEEKDAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

# (lowest, highest, names counted from lowest) per field.
_FIELDS: tuple[tuple[int, int, tuple[str, ...]], ...] = (
    (0, 59, ()),
    (0, 23, ()),
    (1, 31, ()),
    (1, 12, MONTH_NAMES),
    (0, 7, WEEKDAY_NAMES),
)

# The Gregorian calendar repeats every 400 years (weekdays and leap days alike): a day no search of that length
# finds never comes.
_CALENDAR_CYCLE_YEARS = 400


class CronExpression:
    """A parsed five-field expression. ValueError for anything classic cron does not accept."""

    def __init__(self, expression: str) -> None:
        fields = expression.split()
        if len(fields) != len(_FIELDS):
            raise ValueError(f"cron expression needs {len(_FIELDS)} fields, got {len(fields)}: '{expression}'")
        self.expression = expression
        minutes, hours, days, months, weekdays = (
            _parse_field(text, *spec) for text, spec in zip(fields, _FIELDS))
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
        """The first moment after `after` (to the minute) the expression fires. ValueError if it never does."""
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
        raise ValueError(f"cron expression never fires: '{self.expression}'")


def next_cron_run(expression: str, after: datetime) -> datetime:
    """The next time `expression` fires after `after`. ValueError for an invalid one or one that never fires."""
    return CronExpression(expression).next_after(after)


def _parse_field(text: str, lowest: int, highest: int, names: tuple[str, ...]) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        span, slash, step_text = part.partition("/")
        step = _number(step_text, 1, None, part) if slash else 1
        if span == "*":
            first, last = lowest, highest
        elif "-" in span:
            first_text, _, last_text = span.partition("-")
            first = _value(first_text, lowest, highest, names, part)
            last = _value(last_text, lowest, highest, names, part)
            if first > last:
                raise ValueError(f"cron range runs backwards: '{part}'")
        else:
            first = _value(span, lowest, highest, names, part)
            last = highest if slash else first    # "a/n" steps from a to the end of the field
        values.update(range(first, last + 1, step))
    return values


def _value(text: str, lowest: int, highest: int, names: tuple[str, ...], part: str) -> int:
    name = text.lower()
    if name in names:
        return lowest + names.index(name)
    return _number(text, lowest, highest, part)


def _number(text: str, lowest: int, highest: int | None, part: str) -> int:
    if not text.isdigit():
        raise ValueError(f"cron field not understood: '{part}'")
    number = int(text)
    if number < lowest or (highest is not None and number > highest):
        raise ValueError(f"cron value out of range: '{part}'")
    return number


def _first_of_next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)
