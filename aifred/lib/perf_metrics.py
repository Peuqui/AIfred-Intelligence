"""Leistungskennzahlen — eine Wahrheit je Groesse.

Bewusst ohne Projekt-Importe: der Kalibrator laeuft unter AIFRED_CLI_MODE
und darf nicht die halbe App nachziehen.
"""


def prefill_tokens_per_second(
    *,
    server_rate: float | None = None,
    prompt_tokens: int = 0,
    cached_tokens: int | None = None,
    elapsed_s: float = 0.0,
) -> float:
    """Prefill-Durchsatz in Token pro Sekunde. Drei Regeln, eine Stelle.

    1. Meldet das Backend eine eigene Rate (llama.cpp, Ollama), gewinnt
       die. Sie teilt durch die reine Prefill-Dauer, schleppt also weder
       gecachte Token noch den ersten Decode-Schritt mit.
    2. Sonst selbst rechnen — aber NUR ueber tatsaechlich verarbeitete
       Token, also Prompt minus Cache-Treffer. Zaehlt man aus dem
       Praefix-Cache gezogene Token als Rechenleistung, explodiert die
       Rate ins Absurde (vLLM-Fussnoten mit 8.000 tok/s, 2026-09-01).
    3. Ist die Zahl der Cache-Treffer UNBEKANNT, wird nichts gemeldet.
       Lieber keine Zahl als eine falsche.

    ``elapsed_s`` ist eine Obergrenze der echten Prefill-Zeit (TTFT bzw.
    Wanduhr des Requests), der Wert also eher konservativ.
    """
    if server_rate and server_rate > 0:
        return float(server_rate)
    if cached_tokens is None:
        return 0.0
    computed = max(prompt_tokens - cached_tokens, 0)
    if computed <= 0 or elapsed_s <= 0:
        return 0.0
    return computed / elapsed_s
