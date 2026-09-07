"""Regression: die Kontextfuellung wird ueberall gleich gerechnet.

Bug (2026-09-07, Peuqui): Die Kompression mass den System-Prompt mit einer
Startup-Schaetzung eines ANDEREN Bauers (``get_agent_system_prompt``) und ohne
die Schalter des kommenden Turns. Ohne Tools-Schicht lag sie um ~8.000 Tokens
zu hoch — bei 16k Kontext 45 % des Fensters. Folge: Der Ausloeser feuerte bei
gemeldeten 70 %, obwohl real 24 % anlagen.

Zweiter Teil desselben Fehlers: Der Ausloeser rechnete System + Tools +
History, die Auswahlschleife nur History. Sie lief deshalb gar nicht erst an,
die Kompression fasste null Nachrichten zusammen und legte eine
Zusammenfassung ohne Inhalt in der llm_history ab.
"""

import asyncio

from aifred.lib import context_manager
from aifred.lib.prompt_loader import (
    get_agent_direct_prompt,
    get_max_direct_prompt_tokens,
)


class TestPromptSizeFollowsTurnFlags:
    """Gemessen wird der Prompt, den der Turn wirklich baut."""

    def test_tools_layer_changes_the_measurement(self):
        # Die Tools-Schicht ist der Posten, der den Fehlstart ausgeloest hat.
        # Sie MUSS sich in der Zahl niederschlagen, sonst misst die
        # Kompression wieder an der Realitaet vorbei.
        without = get_max_direct_prompt_tokens(
            "standard", "de", memory=True, tools=False,
        )
        with_tools = get_max_direct_prompt_tokens(
            "standard", "de", memory=True, tools=True,
        )
        assert with_tools > without * 2

    def test_measures_the_direct_prompt_builder(self):
        # Gegen den alten Fehler: gemessen wird get_agent_direct_prompt mit
        # dem Tokenizer, nicht irgendein anderer Bauer mit einer Heuristik.
        expected = context_manager.count_tokens_with_tokenizer(
            get_agent_direct_prompt("aifred", lang="de", memory=True, tools=False)
        )
        assert get_max_direct_prompt_tokens(
            "standard", "de", memory=True, tools=False,
        ) == expected


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeClient:
    """Merkt sich, was zur Zusammenfassung geschickt wurde."""

    def __init__(self) -> None:
        self.seen_prompt: str | None = None

    async def chat(self, model, messages, options):
        self.seen_prompt = messages[0].content
        return _FakeResponse("Zusammenfassung des bisherigen Gespraechs. " * 30)


def _compress(llm_history, *, context_limit, system_prompt_tokens, toolkit_tokens):
    """Laeuft die Kompressionspruefung, meldet was das LLM zu sehen bekam."""

    async def run():
        client = _FakeClient()
        history = [
            {"role": m["role"], "content": m["content"]} for m in llm_history
        ]
        async for _ in context_manager.summarize_history_if_needed(
            history=history,
            llm_client=client,
            model_name="Test-Modell",
            context_limit=context_limit,
            llm_history=llm_history,
            system_prompt_tokens=system_prompt_tokens,
            detected_language="de",
            toolkit_tokens=toolkit_tokens,
        ):
            pass
        return client

    return asyncio.run(run())


def _history(pairs: int) -> list[dict]:
    out: list[dict] = []
    for i in range(pairs):
        out.append({"role": "user", "content": f"Frage {i}: " + "Wortfüllung " * 60})
        out.append({"role": "assistant", "content": f"Antwort {i}: " + "Wortfüllung " * 60})
    return out


class TestTriggerAndSelectionUseOneYardstick:
    def test_fixed_overhead_makes_the_loop_select(self):
        # DER Bugfall: System-Prompt und Tools tragen die Fuellung ueber 70 %,
        # die History allein liegt unter dem 30-%-Ziel. Vorher waehlte die
        # Schleife nichts aus und fasste eine leere Konversation zusammen.
        llm_history = _history(2)
        context_limit = 16384
        history_tokens = context_manager.estimate_tokens_from_llm_history(llm_history)
        assert history_tokens < int(context_limit * 0.3), (
            "Testaufbau: die History muss unter dem 30-%-Ziel liegen"
        )
        system_prompt_tokens = int(context_limit * 0.7) - history_tokens

        client = _compress(
            llm_history,
            context_limit=context_limit,
            system_prompt_tokens=system_prompt_tokens,
            toolkit_tokens=0,
        )

        assert client.seen_prompt is not None, "Kompression haette laufen muessen"
        # Der entscheidende Punkt: es ging echter Gespraechstext mit.
        assert "Frage 0" in client.seen_prompt

    def test_no_trigger_below_threshold(self):
        # Die realen Werte aus dem Vorfall nach dem Fix: 2.247 + 542 + History
        # bleiben deutlich unter 70 % — hier darf nichts passieren.
        client = _compress(
            _history(2),
            context_limit=16384,
            system_prompt_tokens=2247,
            toolkit_tokens=542,
        )
        assert client.seen_prompt is None, "unter 70 % darf nicht komprimiert werden"
