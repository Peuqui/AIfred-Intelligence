"""iCalendar events (VEVENT) and tasks (VTODO) for the Nextcloud plugin.

Reading turns times into local wall-clock time; an all-day event's end is
reported as its last day (iCalendar's DTEND is the day after). Writing uses
UTC for timed values and DATE values for all-day events. Updates change only
the named properties and keep everything else (alarms, attendees, custom
properties); recurring events are left to the calendar apps.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import icalendar

PRODID = "-//AIfred//Nextcloud plugin//EN"
CONTENT_TYPE = "text/calendar; charset=utf-8"
# iCalendar priority: 1-4 high, 5 medium, 6-9 low, 0 undefined.
PRIORITY_VALUES = {"high": 1, "medium": 5, "low": 9, "none": 0}


class RecurringRefused(ValueError):
    """The object is a recurring series or one of its occurrences."""


def _now() -> datetime:
    return datetime.now(UTC)


def local(value: date | datetime) -> date | datetime:
    """Aware datetimes → naive local wall-clock time; dates and floating
    times stay as they are."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _utc(value: datetime) -> datetime:
    """Naive values are local wall-clock time."""
    return value.astimezone(UTC)


def priority_name(value: int | None) -> str:
    if not value:
        return "none"
    return "high" if value <= 4 else "medium" if value == 5 else "low"


@dataclass(frozen=True)
class Event:
    uid: str
    title: str
    start: date | datetime
    end: date | datetime
    all_day: bool
    location: str | None
    description: str | None
    recurring: bool

    def as_dict(self) -> dict[str, Any]:
        if self.all_day:
            last_day = self.end - timedelta(days=1) if self.end > self.start else self.start
            start: Any = self.start
            end: Any = last_day
        else:
            start, end = local(self.start), local(self.end)
        return {"title": self.title, "start": start, "end": end, "all_day": self.all_day,
                "location": self.location, "description": self.description, "recurring": self.recurring}


@dataclass(frozen=True)
class Task:
    uid: str
    title: str
    due: date | datetime | None
    done: bool
    priority: str
    description: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "due": local(self.due) if self.due is not None else None,
                "done": self.done, "priority": self.priority, "description": self.description}


def _text(component: Any, name: str) -> str | None:
    value = component.get(name)
    return str(value) if value is not None and str(value) != "" else None


def _event(component: Any) -> Event:
    start = component.decoded("DTSTART")
    all_day = not isinstance(start, datetime)
    if "DTEND" in component:
        end = component.decoded("DTEND")
    elif "DURATION" in component:
        end = start + component.decoded("DURATION")
    else:
        end = start + timedelta(days=1) if all_day else start
    recurring = any(name in component for name in ("RRULE", "RDATE", "RECURRENCE-ID"))
    return Event(uid=str(component.get("UID")), title=_text(component, "SUMMARY") or "",
                 start=start, end=end, all_day=all_day, location=_text(component, "LOCATION"),
                 description=_text(component, "DESCRIPTION"), recurring=recurring)


def parse_events(data: str) -> list[Event]:
    """Every VEVENT of a calendar object (an expanded series yields one per occurrence)."""
    return [_event(c) for c in icalendar.Calendar.from_ical(data).walk("VEVENT")]


def parse_tasks(data: str) -> list[Task]:
    tasks = []
    for c in icalendar.Calendar.from_ical(data).walk("VTODO"):
        tasks.append(Task(
            uid=str(c.get("UID")), title=_text(c, "SUMMARY") or "",
            due=c.decoded("DUE") if "DUE" in c else None,
            done=str(c.get("STATUS", "")).upper() == "COMPLETED" or "COMPLETED" in c,
            priority=priority_name(int(c.get("PRIORITY", 0))),
            description=_text(c, "DESCRIPTION")))
    return tasks


def _calendar(component: Any) -> str:
    cal = icalendar.Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add_component(component)
    result: str = cal.to_ical().decode("utf-8")
    return result


def _set(component: Any, name: str, value: Any) -> None:
    """Set or (for None) remove one property."""
    if name in component:
        del component[name]
    if value is not None:
        component.add(name, value)


def _time_value(value: date | datetime, all_day: bool) -> date | datetime:
    if all_day:
        return value.date() if isinstance(value, datetime) else value
    if not isinstance(value, datetime):
        raise TypeError("a timed event needs date and time")
    return _utc(value)


def build_event(uid: str, title: str, start: date | datetime, end: date | datetime, all_day: bool,
                location: str | None, description: str | None) -> str:
    """``end`` is exclusive: for all-day events the day after the last day."""
    event = icalendar.Event()
    event.add("uid", uid)
    event.add("dtstamp", _now())
    event.add("created", _now())
    event.add("summary", title)
    event.add("dtstart", _time_value(start, all_day))
    event.add("dtend", _time_value(end, all_day))
    _set(event, "location", location)
    _set(event, "description", description)
    return _calendar(event)


def _single(data: str, kind: str) -> tuple[Any, Any]:
    cal = icalendar.Calendar.from_ical(data)
    components = cal.walk(kind)
    if len(components) != 1:
        raise RecurringRefused(f"the object holds {len(components)} {kind} components (a series with exceptions)")
    component = components[0]
    if any(name in component for name in ("RRULE", "RDATE", "RECURRENCE-ID")):
        raise RecurringRefused("recurring series are changed in the calendar app")
    return cal, component


def _touch(component: Any) -> None:
    _set(component, "dtstamp", _now())
    _set(component, "last-modified", _now())
    _set(component, "sequence", int(component.get("SEQUENCE", 0)) + 1)


def event_times(data: str) -> Event:
    """The single, non-recurring event of an object (for updates)."""
    return _event(_single(data, "VEVENT")[1])


def update_event(data: str, *, title: str | None = None, start: date | datetime | None = None,
                 end: date | datetime | None = None, all_day: bool | None = None,
                 location: str | None = None, description: str | None = None,
                 clear: frozenset[str] = frozenset()) -> str:
    """Change the named properties; ``clear`` removes location/description."""
    cal, event = _single(data, "VEVENT")
    if title is not None:
        _set(event, "summary", title)
    if start is not None or end is not None or all_day is not None:
        current = _event(event)
        day = current.all_day if all_day is None else all_day
        _set(event, "dtstart", _time_value(start if start is not None else current.start, day))
        _set(event, "dtend", _time_value(end if end is not None else current.end, day))
        if "DURATION" in event:
            del event["DURATION"]
    for name, value in (("location", location), ("description", description)):
        if name in clear:
            _set(event, name, None)
        elif value is not None:
            _set(event, name, value)
    _touch(event)
    result: str = cal.to_ical().decode("utf-8")
    return result


def build_task(uid: str, title: str, due: date | datetime | None, priority: str | None,
               description: str | None) -> str:
    task = icalendar.Todo()
    task.add("uid", uid)
    task.add("dtstamp", _now())
    task.add("created", _now())
    task.add("summary", title)
    task.add("status", "NEEDS-ACTION")
    if due is not None:
        task.add("due", _utc(due) if isinstance(due, datetime) else due)
    if priority is not None:
        task.add("priority", PRIORITY_VALUES[priority])
    _set(task, "description", description)
    return _calendar(task)


def update_task(data: str, *, title: str | None = None, due: date | datetime | None = None,
                done: bool | None = None, priority: str | None = None, description: str | None = None,
                clear: frozenset[str] = frozenset()) -> str:
    """``clear`` removes due/description."""
    cal, task = _single(data, "VTODO")
    if title is not None:
        _set(task, "summary", title)
    if "due" in clear:
        _set(task, "due", None)
    elif due is not None:
        _set(task, "due", _utc(due) if isinstance(due, datetime) else due)
    if priority is not None:
        _set(task, "priority", PRIORITY_VALUES[priority])
    if "description" in clear:
        _set(task, "description", None)
    elif description is not None:
        _set(task, "description", description)
    if done is not None:
        _set(task, "status", "COMPLETED" if done else "NEEDS-ACTION")
        _set(task, "completed", _now() if done else None)
        _set(task, "percent-complete", 100 if done else None)
    _touch(task)
    result: str = cal.to_ical().decode("utf-8")
    return result
