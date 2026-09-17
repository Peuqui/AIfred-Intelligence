"""Performance-Zeile: Denkzeit und Cold-Start-Ladezeit als eigene Punkte,
Builder und Footer als eine Quelle."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aifred.lib.formatting import build_inference_metadata, format_performance_footer
from aifred.lib.llm_pipeline import run_llm_stream
from aifred.lib.perf_metrics import InferenceWork


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
        ttft=1.0, inference_time=30.0,
        work=InferenceWork(decode_tokens=100, decode_s=2.0, thinking_s=20.0),
        source="AIfred (m)", load_time=300.0,
    )
    assert metadata["thinking_time"] == 20.0 and metadata["load_time"] == 300.0
    assert display == format_performance_footer(metadata)
    assert "thinking 20,0s" in debug_msg or "thinking 20.0s" in debug_msg
    assert "cold start load" in debug_msg


class _FakeClient:
    """Streams a <think> block, then the answer; the done metrics carry the
    backend's measured work (thinking time of the whole turn)."""

    backend_type = "vllm"

    async def chat_stream(self, model, messages, options, toolkit=None):
        for text in ["<think>", "Ich denke.", "</think>\n\n", "Antwort."]:
            yield {"type": "content", "text": text}
        work = InferenceWork(decode_tokens=4, decode_s=0.4, thinking_s=2.5)
        yield {"type": "done", "metrics": {"tokens_per_second": 10.0, "tokens_generated": 4, "work": work.to_dict()}}


def test_pipeline_takes_thinking_time_from_the_backend_work() -> None:
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
    assert result.work.thinking_s == 2.5
    assert result.metadata_dict["thinking_time"] == 2.5
    assert "Thinking" in result.metadata_display


def test_metadata_line_wraps_between_values_never_inside() -> None:
    """Mobile bubbles use word-break: a separator of four non-breaking spaces
    made a whole group one word that got cut anywhere ("48,5 tok/" | "s")."""
    display = format_performance_footer({"ttft": 1.63, "tokens_per_sec": 48.5, "inference_time": 100.0})
    body = display.strip("*( )")
    # Every gap between two values holds a normal (breakable) space ...
    assert "    " in body and "    " not in body
    # ... while each value keeps its own spaces non-breaking.
    for value in body.split("    "):
        assert " " not in value


def test_metadata_parentheses_never_wrap_alone() -> None:
    """A nearly full footer line wrapped only the closing ")" onto its own line:
    the spaces next to the parentheses were normal (breakable) spaces."""
    display = format_performance_footer({"ttft": 6.44, "tokens_per_sec": 55.0, "source": "AIfred (m)"})
    assert display.startswith("*(\u00A0") and display.endswith("\u00A0)*")
