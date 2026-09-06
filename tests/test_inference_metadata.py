"""Performance-Zeile: Denkzeit und Cold-Start-Ladezeit als eigene Punkte,
Builder und Footer als eine Quelle."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aifred.lib.formatting import build_inference_metadata, format_performance_footer
from aifred.lib.llm_pipeline import run_llm_stream


def test_footer_shows_thinking_and_load_in_order() -> None:
    footer = format_performance_footer({
        "ttft": 6.1, "tokens_per_sec": 54.9, "inference_time": 280.0,
        "thinking_time": 230.0, "load_time": 444.0, "source": "AIfred (m)",
    })
    positions = [footer.index(label) for label in ("TTFT", "Thinking", "Inference", "Load", "Source")]
    assert positions == sorted(positions)


def test_footer_omits_thinking_and_load_when_zero() -> None:
    footer = format_performance_footer({
        "ttft": 0.4, "tokens_per_sec": 60.0, "inference_time": 12.0,
        "thinking_time": 0.0, "load_time": 0.0, "source": "AIfred (m)",
    })
    assert "Thinking" not in footer and "Load" not in footer


def test_build_inference_metadata_renders_through_the_footer() -> None:
    metadata, display, debug_msg = build_inference_metadata(
        ttft=1.0, inference_time=30.0, tokens_generated=100, tokens_per_sec=50.0,
        source="AIfred (m)", thinking_time=20.0, load_time=300.0,
    )
    assert metadata["thinking_time"] == 20.0 and metadata["load_time"] == 300.0
    assert display == format_performance_footer(metadata)
    assert "thinking 20,0s" in debug_msg or "thinking 20.0s" in debug_msg
    assert "cold start load" in debug_msg


class _FakeClient:
    """Streams a <think> block, then the answer, with real delays."""

    async def chat_stream(self, model, messages, options, toolkit=None):
        for text, pause in [("<think>", 0.0), ("Ich denke.", 0.05), ("</think>\n\n", 0.05), ("Antwort.", 0.05)]:
            await asyncio.sleep(pause)
            yield {"type": "content", "text": text}
        yield {"type": "done", "metrics": {"tokens_per_second": 10.0, "tokens_generated": 4}}


def test_pipeline_measures_thinking_time() -> None:
    async def run():
        result = None
        async for event in run_llm_stream(
            _FakeClient(), "m", [], SimpleNamespace(), "AIfred", retry=False,  # type: ignore[arg-type]
        ):
            if event["type"] == "pipeline_result":
                result = event["result"]
        return result

    result = asyncio.run(run())
    assert result is not None
    # Denken endet beim ersten </think>: nach den ersten beiden Pausen (~0,1 s),
    # vor der Antwort (~0,15 s gesamt).
    assert 0.08 <= result.thinking_time < result.inference_time
    assert result.metadata_dict["thinking_time"] == result.thinking_time
    assert "Thinking" in result.metadata_display
