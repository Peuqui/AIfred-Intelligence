"""Geteilte Helfer fuer Background-Cleanup-Tasks.

Die Garbage-Collection-Tasks (Vector-Cache, Audio-State, Lookup-Cache)
sollen alle zur gleichen lokalen Uhrzeit laufen. Diese Funktion berechnet
die Wartezeit bis zum naechsten Slot.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def seconds_until_next_run(target_hour: int) -> float:
    """Sekunden bis zum naechsten Wartungsslot (target_hour:00 lokale Zeit).

    Wenn die aktuelle Zeit bereits ueber target_hour:00 ist, kommt der
    naechste Slot morgen.

    Args:
        target_hour: Stunde des Slots (0-23) in lokaler Zeit.

    Returns:
        Sekunden bis zum naechsten Slot.
    """
    now = datetime.now()
    next_run = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()
