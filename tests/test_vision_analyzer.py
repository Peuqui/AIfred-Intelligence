"""Tests für aifred.lib.vision_analyzer — VLM-Call via Ollama.

Ollama wird per Monkeypatch gemockt — keine echten LLM-Calls in der CI.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from aifred.lib.frame_sources import Frame
from aifred.lib.vision_analyzer import (
    DEFAULT_MODEL,
    VisionAnalysis,
    analyze_frame,
    analyze_sequence,
)


def run(coro):
    return asyncio.run(coro)


def _make_frame(idx: int = 0) -> Frame:
    return Frame(
        source_id="cam/test",
        timestamp=datetime.now(),
        image_bytes=f"FAKEJPEG-{idx}".encode(),
        format="jpeg",
        width=320,
        height=240,
    )


class TestAnalyzeFrame:
    def test_single_frame_call(self, monkeypatch):
        captured_kwargs: dict = {}

        async def fake_generate(self, **kwargs):
            captured_kwargs.update(kwargs)
            return {
                "response": "A cat sitting on a chair.",
                "eval_count": 12,
                "total_duration": 850_000_000,
            }

        # Patch the AsyncClient inside the ollama module
        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)

        result = run(analyze_frame(_make_frame(0), "What is in this image?"))

        assert isinstance(result, VisionAnalysis)
        assert result.text == "A cat sitting on a chair."
        assert result.n_frames == 1
        assert result.model == DEFAULT_MODEL
        assert result.prompt == "What is in this image?"
        assert result.duration_ms >= 0.0
        assert "eval_count" in result.metadata

        # Verify Ollama was called with correct shape
        assert captured_kwargs["model"] == DEFAULT_MODEL
        assert captured_kwargs["prompt"] == "What is in this image?"
        assert captured_kwargs["stream"] is False
        assert captured_kwargs["keep_alive"] == "30m"
        assert captured_kwargs["options"]["num_ctx"] == 4096
        # Image is base64-encoded
        imgs = captured_kwargs["images"]
        assert len(imgs) == 1
        decoded = base64.b64decode(imgs[0])
        assert decoded == b"FAKEJPEG-0"

    def test_custom_model_and_ctx(self, monkeypatch):
        captured: dict = {}

        async def fake_generate(self, **kwargs):
            captured.update(kwargs)
            return {"response": "ok"}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)

        run(
            analyze_frame(
                _make_frame(0),
                "Describe",
                model="qwen2.5vl:7b",
                num_ctx=2048,
                keep_alive="5m",
                extra_options={"temperature": 0.2},
            )
        )

        assert captured["model"] == "qwen2.5vl:7b"
        assert captured["keep_alive"] == "5m"
        assert captured["options"]["num_ctx"] == 2048
        assert captured["options"]["temperature"] == 0.2


class TestAnalyzeSequence:
    def test_multi_frame_passes_all_images(self, monkeypatch):
        captured: dict = {}

        async def fake_generate(self, **kwargs):
            captured.update(kwargs)
            return {"response": "Person walking from left to right."}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)

        frames = [_make_frame(i) for i in range(3)]
        result = run(analyze_sequence(frames, "What is happening?"))

        assert result.n_frames == 3
        imgs = captured["images"]
        assert len(imgs) == 3
        for i, img_b64 in enumerate(imgs):
            assert base64.b64decode(img_b64) == f"FAKEJPEG-{i}".encode()

    def test_empty_sequence_raises(self):
        with pytest.raises(ValueError):
            run(analyze_sequence([], "anything"))


class TestErrorHandling:
    def test_ollama_failure_raises_runtime_error(self, monkeypatch):
        async def boom(self, **kwargs):
            raise ConnectionError("ollama unreachable")

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", boom)

        with pytest.raises(RuntimeError, match="VLM call failed"):
            run(analyze_frame(_make_frame(0), "hi"))


class TestResponseParsing:
    def test_pydantic_style_response(self, monkeypatch):
        """Newer ollama clients return a Pydantic model with model_dump()."""
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "response": "ok",
            "eval_count": 5,
        }

        async def fake_generate(self, **kwargs):
            return mock_response

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)

        result = run(analyze_frame(_make_frame(0), "hi"))
        assert result.text == "ok"
        assert result.metadata["eval_count"] == 5
