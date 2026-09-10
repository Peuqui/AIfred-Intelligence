"""Leistungskennzahlen — eine Wahrheit je Groesse.

Bewusst ohne Projekt-Importe: der Kalibrator laeuft unter AIFRED_CLI_MODE
und darf nicht die halbe App nachziehen.
"""

from typing import NamedTuple


class Prefill(NamedTuple):
    """Prefill-Rate samt der Tokenmenge, auf der sie beruht.

    Die Menge gehoert in die Ausgabe, weil die Rate ohne sie nicht
    einzuordnen ist: seit dem statischen System-Prompt (2026-08-31) kommt
    bei Folgefragen fast alles aus dem Praefix-Cache, und ein Dutzend
    gerechneter Token ergibt eine Rate, die nur noch den Grundaufwand
    misst (gemessen 2026-09-01: 18,3 tok/s bei 12 Token, waehrend
    derselbe Server kalt 1.187 tok/s liefert).
    """

    rate: float | None
    tokens: int


def prefill_tokens_per_second(
    *,
    server_rate: float | None = None,
    server_tokens: int = 0,
    prompt_tokens: int = 0,
    cached_tokens: int | None = None,
    elapsed_s: float = 0.0,
) -> Prefill:
    """Prefill-Durchsatz in Token pro Sekunde. Drei Regeln, eine Stelle.

    1. Meldet das Backend eine eigene Rate (llama.cpp, Ollama), gewinnt
       die. Sie teilt durch die reine Prefill-Dauer, schleppt also weder
       gecachte Token noch den ersten Decode-Schritt mit.
    2. Sonst selbst rechnen — aber NUR ueber tatsaechlich verarbeitete
       Token, also Prompt minus Cache-Treffer. Zaehlt man aus dem
       Praefix-Cache gezogene Token als Rechenleistung, explodiert die
       Rate ins Absurde (vLLM-Fussnoten mit 8.000 tok/s, 2026-09-01).
    3. Ist die Zahl der Cache-Treffer UNBEKANNT, kommt ``None`` zurueck —
       die Fussnote zeigt dann "n/a". Lieber sichtbar keine Zahl als still
       eine falsche: ohne diesen Zweig wuerde ein Server ohne
       ``--enable-prompt-tokens-details`` wieder alle Prompt-Token als
       gerechnet zaehlen (die 8.000-tok/s-Fussnoten vom 2026-09-01).

    ``elapsed_s`` ist eine Obergrenze der echten Prefill-Zeit (TTFT bzw.
    Wanduhr des Requests), der Wert also eher konservativ.
    """
    if server_rate and server_rate > 0:
        return Prefill(float(server_rate), max(server_tokens, 0))
    if cached_tokens is None:
        return Prefill(None, 0)
    computed = max(prompt_tokens - cached_tokens, 0)
    if computed <= 0 or elapsed_s <= 0:
        return Prefill(0.0, computed)
    return Prefill(computed / elapsed_s, computed)


def decode_tokens_per_second(
    *,
    server_rate: float | None = None,
    tokens_generated: int = 0,
    inference_time: float = 0.0,
    first_token_s: float | None = None,
) -> float:
    """Decode-Durchsatz in Token pro Sekunde. Drei Regeln, eine Stelle.

    1. Meldet das Backend eine eigene Rate (llama.cpp
       ``predicted_per_second``, Ollama ``eval_duration``, vLLMs Zaehler),
       gewinnt die: sie teilt durch die reine Generierungszeit.
    2. Sonst selbst rechnen, aber nur ueber die Zeit NACH dem ersten Token.
       Ueber die ganze Dauer geteilt steckt der Prefill im Nenner, die Rate
       faellt mit der Promptlaenge und ist mit Regel 1 nicht vergleichbar
       (am 122B 7-15 % zu niedrig, gemessen 2026-09-01). Das trifft
       Cloud-APIs immer und vLLM, wenn seine Zaehler nichts Eindeutiges
       liefern (etwa die erste Antwort nach jedem Serverstart).
    3. Ist nichts messbar (kein Token, kein Zeitpunkt des ersten Tokens,
       keine Zeit danach), kommt 0.0 zurueck.

    Bei Tool-Runden liegen Werkzeugzeit und weitere Prefills zwischen
    erstem und letztem Token — die Rate ist dann eine Untergrenze.
    """
    if server_rate and server_rate > 0:
        return float(server_rate)
    if tokens_generated <= 0 or first_token_s is None:
        return 0.0
    decode_s = inference_time - first_token_s
    if decode_s <= 0:
        return 0.0
    return tokens_generated / decode_s
