"""Leistungskennzahlen — eine Wahrheit je Groesse.

Bewusst ohne Projekt-Importe: der Kalibrator laeuft unter AIFRED_CLI_MODE
und darf nicht die halbe App nachziehen.
"""

from dataclasses import asdict, dataclass
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


@dataclass
class InferenceWork:
    """What a server really computed: tokens and pure phase time of prefill
    and decode, for one request or summed over many.

    A turn is several requests (tool rounds, and every request of a
    sub-agent it delegated to). Its footer shows the whole turn, so the rates
    are total tokens over total phase time: a time-weighted mean, not an
    average of per-request rates (a 12-token follow-up prefill must not
    weigh as much as a 30,000-token cold one).

    ``None`` as a time means at least one request was not measurable. The
    sum stays unknown then: a rate over the measured part would silently
    leave work out.
    """

    prefill_tokens: int = 0
    prefill_s: float | None = 0.0
    decode_tokens: int = 0
    decode_s: float | None = 0.0

    def __add__(self, other: "InferenceWork") -> "InferenceWork":
        return InferenceWork(
            prefill_tokens=self.prefill_tokens + other.prefill_tokens,
            prefill_s=_add_known(self.prefill_s, other.prefill_s),
            decode_tokens=self.decode_tokens + other.decode_tokens,
            decode_s=_add_known(self.decode_s, other.decode_s),
        )

    def prefill_rate(self) -> float | None:
        """Prefill tok/s; ``None`` = not measurable (the footer shows n/a)."""
        if self.prefill_s is None:
            return None
        if self.prefill_tokens <= 0 or self.prefill_s <= 0:
            return 0.0
        return self.prefill_tokens / self.prefill_s

    def decode_rate(self) -> float:
        """Decode tok/s; 0.0 = nothing measurable (the footer leaves it out)."""
        if self.decode_s is None or self.decode_tokens <= 0 or self.decode_s <= 0:
            return 0.0
        return self.decode_tokens / self.decode_s

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "InferenceWork":
        return cls(**raw)

    @classmethod
    def unmeasured(cls) -> "InferenceWork":
        return cls(prefill_s=None, decode_s=None)

    @classmethod
    def from_wall_clock(
        cls,
        *,
        prompt_tokens: int,
        cached_tokens: int | None,
        tokens_generated: int,
        first_token_s: float | None,
        elapsed_s: float,
    ) -> "InferenceWork":
        """One request measured from outside, for servers that report no
        phase times (cloud APIs, vLLM when its counters are ambiguous).

        Prefill: only the tokens really computed (prompt minus cache hits)
        over the time to the first token, an upper bound of the prefill
        time. Unknown cache hits make it unmeasurable: counting cached
        tokens as work gave 8,000 tok/s footers (2026-09-01).

        Decode: over the time AFTER the first token only. Dividing by the
        whole request put the prefill into the denominator, 7-15 % too low
        on the 122B (2026-09-01).
        """
        if cached_tokens is None or first_token_s is None:
            prefill = cls.unmeasured()
        else:
            prefill = cls(prefill_tokens=max(prompt_tokens - cached_tokens, 0), prefill_s=first_token_s)
        if tokens_generated <= 0:
            decode_tokens, decode_s = 0, 0.0
        elif first_token_s is None:
            decode_tokens, decode_s = tokens_generated, None
        else:
            decode_tokens, decode_s = tokens_generated, max(elapsed_s - first_token_s, 0.0)
        return cls(
            prefill_tokens=prefill.prefill_tokens,
            prefill_s=prefill.prefill_s,
            decode_tokens=decode_tokens,
            decode_s=decode_s,
        )


def _add_known(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a + b
