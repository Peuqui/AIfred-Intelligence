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
_cancel_requested: bool = False


def set_calibration_active(value: bool) -> None:
    """Vom Kalibrier-Wrapper beim Start (True) / im finally (False) gesetzt.

    Setzt das Cancel-Flag in beiden Richtungen zurück — ein neuer Lauf
    darf kein altes Cancel erben, und nach dem Ende ist nichts mehr
    abzubrechen."""
    global _active, _cancel_requested
    _active = bool(value)
    _cancel_requested = False


def is_calibration_active() -> bool:
    """True solange eine Kalibrierung läuft — Inferenz-Einstiege lehnen ab."""
    return _active


def request_cancel() -> None:
    """User-Abbruch anfordern — prozessweit sichtbar.

    Das Reflex-Flag (calibration_cancel) wird vom Wrapper nur ZWISCHEN
    Kalibrier-Schritten geprüft; ein laufender Verify (llama-server lädt
    minutenlang ein Modell) merkt davon nichts. Dieses Flag reicht bis in
    die Lade-Warteschleife des Verifiers hinein: die pollt sekündlich,
    killt bei Cancel den Test-Server (Port 19999) und beendet den Schritt
    als "cancelled" — Abbruch-Latenz ~2 s statt Minuten."""
    global _cancel_requested
    if _active:
        _cancel_requested = True


def is_cancel_requested() -> bool:
    """Von verify()/_wait_ready geprüft, um Minuten-Schritte abzubrechen."""
    return _cancel_requested
