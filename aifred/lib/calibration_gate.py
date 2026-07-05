"""Prozessweites Kalibrier-Gate — SSoT für „GPUs gehören gerade der Kalibrierung".

``is_calibrating`` im Reflex-State ist nur Browser-UI (Spinner/Buttons)
einer Session. Die Inferenz-Einstiege laufen aber teils komplett an
Reflex vorbei (Message Hub: E-Mail/Telegram/Discord/Puck/Scheduler in
eigenen Event-Loops) — und seit die Kalibrierung ein Background-Event
ist, hält sie auch im Browser keinen State-Lock mehr, der Requests
zufällig blockiert hätte. Ohne dieses Gate startet ein Chat-Request
mitten in der Kalibrierung llama-swap neu und lädt ein Modell auf die
GPUs, die gerade vermessen werden (beobachtet 2026-07-05 17:17: Messwerte
verdorben, Profil-Tanz mitten im Verify).

Bewusst ein nacktes Modul-Flag statt Lock/Event: ein bool-Read/Write ist
unter dem GIL atomar, und es gibt genau einen Schreiber (den
Kalibrier-Wrapper).
"""

_active: bool = False


def set_calibration_active(value: bool) -> None:
    """Vom Kalibrier-Wrapper beim Start (True) / im finally (False) gesetzt."""
    global _active
    _active = bool(value)


def is_calibration_active() -> bool:
    """True solange eine Kalibrierung läuft — Inferenz-Einstiege lehnen ab."""
    return _active
