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
    DEFAULT_NUM_CTX,
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
        assert captured_kwargs["options"]["num_ctx"] == DEFAULT_NUM_CTX
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


class TestLlamacppDispatch:
    """Modelle mit nativem --mmproj (SSOT model_has_mmproj) laufen über
    llama-swap (OpenAI-API) statt Ollama — das Haupt-LLM beschreibt selbst."""

    def test_mmproj_model_routes_to_llamacpp(self, monkeypatch):
        import aifred.lib.vision_utils as vu
        monkeypatch.setattr(vu, "model_has_mmproj", lambda m: m == "main-llm")

        captured: dict = {}

        class FakeResponse:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {
                    "choices": [
                        {"message": {"content": "Beschreibung vom Hauptmodell."}}
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                    "timings": {
                        "prompt_ms": 500.0, "predicted_ms": 800.0,
                        "prompt_n": 100, "predicted_n": 20,
                        "prompt_per_second": 200.0,
                        "predicted_per_second": 25.0,
                    },
                }

        class FakeAsyncClient:
            def __init__(self, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, json=None):
                captured["url"] = url
                captured["payload"] = json
                return FakeResponse()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

        result = run(analyze_sequence(
            [_make_frame(0)], "Was ist zu sehen?", model="main-llm"
        ))

        assert result.text == "Beschreibung vom Hauptmodell."
        # Regression: BACKEND_URLS["llamacpp"] enthält bereits /v1
        assert captured["url"].endswith("/v1/chat/completions")
        assert "/v1/v1/" not in captured["url"]
        parts = captured["payload"]["messages"][0]["content"]
        assert parts[0]["type"] == "image_url"
        assert parts[-1] == {"type": "text", "text": "Was ist zu sehen?"}
        assert result.metadata["stats"]["eval_tokens"] == 20.0

    def test_non_mmproj_model_stays_on_ollama(self, monkeypatch):
        import aifred.lib.vision_utils as vu
        monkeypatch.setattr(vu, "model_has_mmproj", lambda m: False)

        async def fake_generate(self, **kwargs):
            return {"response": "ollama-pfad", "eval_count": 3}

        import ollama
        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)

        result = run(analyze_sequence([_make_frame(0)], "hi", model="qwen3-vl:4b"))
        assert result.text == "ollama-pfad"
