"""Strict parsing of tool arguments (shared by the EPIM and Nextcloud plugins).

The model sends JSON; nothing in this module guesses. A value that is not in
the documented form raises :class:`InvalidValue` with a message the model can
act on, instead of being coerced into something it did not mean (Firebird,
for one, reads "10/11/2026" as October 11th and accepts "TOMORROW").
"""

import re
from datetime import date, datetime, timedelta

# Local wall-clock time without zone; ISO dates with an optional time
# (space or "T", minutes or seconds) are the one accepted form.
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")


class InvalidValue(ValueError):
    """A tool argument is not in the documented form."""


def parse_datetime(value: object, field: str) -> datetime:
    """``YYYY-MM-DD HH:MM[:SS]`` (or with ``T``) → naive local datetime."""
    if not isinstance(value, str) or not _DATETIME_RE.fullmatch(value.strip()):
        raise InvalidValue(f"{field}: expected 'YYYY-MM-DD HH:MM', got {value!r}")
    try:
        return datetime.fromisoformat(value.strip().replace(" ", "T"))
    except ValueError as exc:
        raise InvalidValue(f"{field}: {exc}") from exc


def parse_date(value: object, field: str) -> date:
    """``YYYY-MM-DD`` → date."""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value.strip()):
        raise InvalidValue(f"{field}: expected 'YYYY-MM-DD', got {value!r}")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidValue(f"{field}: {exc}") from exc


def parse_when(value: object, field: str, is_end: bool) -> datetime:
    """A date with time is exact. A bare date means the whole day: a start
    at 00:00, an end as the inclusive last day (stored as the next midnight)."""
    if isinstance(value, str) and _DATE_RE.fullmatch(value.strip()):
        day = parse_date(value, field)
        return datetime.combine(day + timedelta(days=1) if is_end else day, datetime.min.time())
    return parse_datetime(value, field)


def parse_bool(value: object, field: str) -> bool:
    """JSON boolean, or the strings "true"/"false" that models sometimes send."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise InvalidValue(f"{field}: expected true or false, got {value!r}")


def parse_text(value: object, field: str) -> str:
    """A text field. Numbers are accepted as their decimal form (a postal
    code sent as 34119); lists, objects and booleans are rejected."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InvalidValue(f"{field}: expected text, got {type(value).__name__}")
    return str(value)


def parse_optional_text(value: object, field: str) -> str | None:
    """Like :func:`parse_text`, but ``null`` or an empty string clear the field."""
    if value is None:
        return None
    text = parse_text(value, field)
    return text if text.strip() else None


def parse_id(value: object, field: str) -> int:
    """Entity IDs travel as strings (models round long integers)."""
    if isinstance(value, bool):
        raise InvalidValue(f"{field}: expected an ID, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise InvalidValue(f"{field}: expected an ID, got {value!r}")


def reject_unknown(data: dict, allowed: set[str], what: str) -> None:
    """Fail loud on keys the tool does not support — dropping them would
    report success for a change that never happened."""
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise InvalidValue(
            f"{what}: unsupported field(s) {', '.join(unknown)}; supported: {', '.join(sorted(allowed))}"
        )
