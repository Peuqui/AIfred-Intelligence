"""Tests für aifred.lib.vision_routing — Name-Matching + Auto-Routing."""

from __future__ import annotations

from unittest.mock import patch

from aifred.lib.ollama_models import OllamaModelInfo
from aifred.lib.vision_routing import (
    _normalize,
    find_ollama_equivalent,
    visiond_profile_for,
)


def _ollama(name: str, family: str = "qwen3vl") -> OllamaModelInfo:
    return OllamaModelInfo(
        name=name,
        family=family,
        families=(family,),
        parameter_size="",
        quantization="",
        size_bytes=0,
    )


_FAKE_SWAP_MODELS: dict[str, dict] = {
    "Qwen3VL-4B-Instruct-Q8_0-visiond": {},
    "Qwen3VL-4B-Instruct-Q8_0": {},
    "Qwen3.8-27B-MTP-UD-Q8_K_XL": {},
}


class TestVisiondProfileFor:
    """Auflösung zum llama-swap-Describer-Profil (<base>-visiond)."""

    def _patched(self):
        return patch(
            "aifred.lib.calibration.llamaswap_io.parse_llamaswap_config",
            return_value=_FAKE_SWAP_MODELS,
        )

    def test_llamaswap_name_resolves(self):
        with self._patched():
            assert visiond_profile_for("Qwen3VL-4B-Instruct-Q8_0") == (
                "Qwen3VL-4B-Instruct-Q8_0-visiond"
            )

    def test_variant_suffix_stripped(self):
        # Aufgelöste Rollen-Ids (resolve_variant_suffix) tragen Suffixe —
        # das Describer-Profil hängt am Basis-Namen.
        with self._patched():
            assert visiond_profile_for(
                "Qwen3VL-4B-Instruct-Q8_0-vlm-qwen3vl4b"
            ) == "Qwen3VL-4B-Instruct-Q8_0-visiond"

    def test_ollama_name_maps_normalized(self):
        # Vigilantia-Plugin-Settings nutzen die Ollama-Schreibweise —
        # sie muss namens-normalisiert auf den Describer matchen.
        with self._patched():
            assert visiond_profile_for("qwen3-vl:4b-instruct-q8_0") == (
                "Qwen3VL-4B-Instruct-Q8_0-visiond"
            )

    def test_no_profile_returns_none(self):
        with self._patched():
            assert visiond_profile_for("Qwen3.8-27B-MTP-UD-Q8_K_XL") is None

    def test_idempotent_on_describer_id(self):
        # Doppelte Auflösung (z.B. sandbox + analyze_sequence) bleibt stabil.
        with self._patched():
            assert visiond_profile_for("Qwen3VL-4B-Instruct-Q8_0-visiond") == (
                "Qwen3VL-4B-Instruct-Q8_0-visiond"
            )


class TestNormalize:
    def test_dash_and_colon_collapse(self):
        a = _normalize("Qwen3VL-4B-Instruct-Q8_0")
        b = _normalize("qwen3-vl:4b-instruct-q8_0")
        assert a == b

    def test_30b_a3b_variant(self):
        a = _normalize("Qwen3-VL-30B-A3B-Instruct-Q8_0")
        b = _normalize("qwen3-vl:30b-a3b-instruct-q8_0")
        assert a == b

    def test_8b_variant(self):
        a = _normalize("Qwen3VL-8B-Instruct-Q8_0")
        b = _normalize("qwen3-vl:8b-instruct-q8_0")
        assert a == b

    def test_distinct_models_differ(self):
        # 4B and 8B must not collapse to the same string
        a = _normalize("Qwen3VL-4B-Instruct-Q8_0")
        b = _normalize("Qwen3VL-8B-Instruct-Q8_0")
        assert a != b


class TestFindOllamaEquivalent:
    def test_llamaswap_to_ollama_4b(self):
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[
                _ollama("qwen3-vl:4b-instruct-q8_0"),
                _ollama("qwen3-vl:8b-instruct-q8_0"),
            ],
        ):
            assert (
                find_ollama_equivalent("Qwen3VL-4B-Instruct-Q8_0")
                == "qwen3-vl:4b-instruct-q8_0"
            )

    def test_llamaswap_to_ollama_8b(self):
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[
                _ollama("qwen3-vl:4b-instruct-q8_0"),
                _ollama("qwen3-vl:8b-instruct-q8_0"),
            ],
        ):
            assert (
                find_ollama_equivalent("Qwen3VL-8B-Instruct-Q8_0")
                == "qwen3-vl:8b-instruct-q8_0"
            )

    def test_no_match_returns_none(self):
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ):
            assert (
                find_ollama_equivalent("Qwen3VL-8B-Instruct-Q8_0") is None
            )

    def test_empty_ollama_returns_none(self):
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[],
        ):
            assert find_ollama_equivalent("Qwen3VL-4B-Instruct-Q8_0") is None

    def test_ollama_to_ollama_self_match(self):
        # If the user passes an Ollama-name through this lookup, it should
        # find itself in the list.
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ):
            assert (
                find_ollama_equivalent("qwen3-vl:4b-instruct-q8_0")
                == "qwen3-vl:4b-instruct-q8_0"
            )

    def test_empty_input_is_none(self):
        assert find_ollama_equivalent("") is None
