"""Bubble artifacts through the whole LLM pipeline: collected where the tools
ran, each camera image once, sub-agent results merged, history references kept."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from aifred.lib.perf_metrics import InferenceWork
from aifred.lib.vision_analyzer import vlm_stats
from aifred.lib.bubble import (
    KIND_COLLAPSIBLE,
    KIND_SANDBOX_HTML,
    KIND_SOURCES,
    KIND_VISION_IMAGES,
    KIND_VLM_OUTPUT,
    render_bubble,
)
from aifred.lib.llm_pipeline import run_llm_stream
from aifred.lib.sandbox import SANDBOX_HTML_URL_MARKER

BURST = ["/_upload/vigilantia/s/f1.jpg", "/_upload/vigilantia/s/f2.jpg"]
PAGE = "/_upload/sandbox_output/s/page.html"


class _Turn:
    """A scripted tool turn: burst snapshot, analyze of its last frame, a
    delegation whose sub-agent built a page and fetched a web page, then the
    answer (which echoes the analysed image and the page marker)."""

    backend_type = "vllm"

    async def chat_stream(self, model, messages, options, toolkit=None):
        yield {"type": "content", "text": "<think>plan</think>Ich schaue nach."}
        yield {"type": "tool_call", "name": "vision_snapshot", "arguments": "{}"}
        yield {"type": "tool_result", "name": "vision_snapshot", "result": json.dumps({
            "success": True, "source_id": "cam", "image_url": BURST[-1], "image_urls": BURST,
        })}
        yield {"type": "tool_call", "name": "vision_analyze", "arguments": "{}"}
        yield {"type": "tool_result", "name": "vision_analyze", "result": json.dumps({
            "success": True, "source_id": "cam", "image_url": BURST[-1], "vlm_raw": "Eine Tür.",
            "vlm_stats": vlm_stats(InferenceWork(decode_tokens=40, decode_s=1.0), backend="llamacpp",
                                   wall_clock_s=1.5, ttft_s=0.4, load_s=0.0),
            "model": "vlm",
        })}
        yield {"type": "content", "text": "<think>weiter</think>Ich delegiere."}
        yield {"type": "tool_call", "name": "delegate_task", "arguments": "{}"}
        yield {"type": "tool_artifacts", "name": "delegate_task", "artifacts": [
            {"kind": KIND_COLLAPSIBLE, "data": {"title": "Sub-Agent", "content": "lief"}},
            {"kind": KIND_SOURCES, "data": {"urls": [{"url": "https://example.org", "success": True}]}},
            {"kind": KIND_SANDBOX_HTML, "data": {"url": PAGE}},
        ]}
        yield {"type": "tool_result", "name": "delegate_task", "result": f"{SANDBOX_HTML_URL_MARKER}{PAGE}\nBericht"}
        yield {"type": "content", "text": f"Fertig. ![Tür]({BURST[-1]})"}
        yield {"type": "done", "metrics": {}}


def _result():
    async def run():
        async for event in run_llm_stream(
            _Turn(), "m", [], SimpleNamespace(enable_thinking=False), "AIfred", retry=False,  # type: ignore[arg-type]
        ):
            if event["type"] == "pipeline_result":
                return event["result"]
        return None

    result = asyncio.run(run())
    assert result is not None
    return result


def test_artifacts_in_turn_order_each_image_once():
    result = _result()
    kinds = [a.kind for a in result.artifacts]
    # The analysed frame was already shown by the burst: no second image
    # artifact, only the VLM description. The page reported twice (artifact
    # from the sub-agent, marker in its report) appears once.
    assert kinds == [KIND_VISION_IMAGES, KIND_VLM_OUTPUT, KIND_COLLAPSIBLE, KIND_SOURCES, KIND_SANDBOX_HTML]
    assert result.artifacts[0].data["urls"] == BURST
    offsets = [a.offset for a in result.artifacts]
    assert offsets == sorted(offsets)
    assert "\x00" not in result.text


def test_history_text_keeps_one_reference_per_camera_image():
    result = _result()
    # The last frame is referenced once at the top (the model's echo removed);
    # that reference is what later turns can re-examine.
    assert result.text.count(f"]({BURST[-1]})") == 1
    assert result.text.startswith("![")
    assert f"]({BURST[-1]})" in result.text_clean


def test_rendered_bubble_follows_the_turn():
    result = _result()
    html = render_bubble(result.text, result.artifacts, model_name="AIfred (m)")
    markers = ["plan", "Ich schaue nach.", BURST[0], "Eine Tür.", "weiter", "Ich delegiere.",
               "Sub-Agent", "example.org", "page.html", "Fertig."]
    positions = [html.index(m) for m in markers]
    assert positions == sorted(positions)
    # Every frame shown once, although the text references the last one twice
    # over (top reference and the model's own echo).
    assert html.count(BURST[-1]) == 1 and html.count(BURST[0]) == 1
