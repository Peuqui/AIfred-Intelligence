"""A reply's background TTS finalize and the next reply's TTS init share one state:
the next reply must wait for the finalize (symposion: every second agent was silent)."""

import asyncio
from typing import Any

from aifred.state._tts_streaming_mixin import TTSStreamingMixin, get_tts_backend_state


class _Reply:
    """The parts of the Reflex state the TTS init/finalize touch."""

    enable_tts = True
    tts_speech_unit = "sentence"
    session_id = "race-test"

    def __init__(self) -> None:
        self._tts_sentence_buffer = ""
        self._tts_short_carry = ""
        self._tts_in_collapsible_block = False
        self._tts_streaming_active = False
        self._tts_finalize_spawned = False
        self._tts_streaming_agent = "aifred"
        self._pending_audio_urls: list[str] = []

    def add_debug(self, message: str) -> None:
        pass

    def _resolve_tts_language(self, agent: str) -> str:
        return "de"

    _init_streaming_tts = TTSStreamingMixin._init_streaming_tts
    _finalize_streaming_tts = TTSStreamingMixin._finalize_streaming_tts
    _finalize_streaming_tts_in_background = TTSStreamingMixin._finalize_streaming_tts_in_background
    _spawn_tts_finalize = TTSStreamingMixin._spawn_tts_finalize
    _wait_for_tts_finalize = TTSStreamingMixin._wait_for_tts_finalize


def test_next_reply_waits_for_the_previous_finalize() -> None:
    async def scenario() -> dict[str, Any]:
        reply = _Reply()
        tts_state = get_tts_backend_state(reply.session_id)

        reply._init_streaming_tts("sokrates")
        tts_state.pending_requests.append("sentence-in-flight")
        reply._spawn_tts_finalize()  # waits for the sentence in the background

        async def sentence_done() -> None:
            await asyncio.sleep(0.5)
            tts_state.pending_requests.clear()

        sentence = asyncio.create_task(sentence_done())
        await reply._wait_for_tts_finalize()
        finalize_was_done = tts_state.finalize_task is not None and tts_state.finalize_task.done()
        reply._init_streaming_tts("pater")  # the next agent starts
        await sentence
        return {"finalize_was_done": finalize_was_done, "streaming_active": reply._tts_streaming_active}

    outcome = asyncio.run(scenario())
    assert outcome == {"finalize_was_done": True, "streaming_active": True}


def test_without_a_finalize_in_flight_nothing_waits() -> None:
    async def scenario() -> None:
        reply = _Reply()
        reply.session_id = "no-finalize"
        await asyncio.wait_for(reply._wait_for_tts_finalize(), timeout=0.1)

    asyncio.run(scenario())
