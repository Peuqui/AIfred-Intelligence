"""Tests für aifred.lib.vision_routing — Name-Matching + Auto-Routing."""

from __future__ import annotations

from unittest.mock import patch

from aifred.lib.ollama_models import OllamaModelInfo
from aifred.lib.vision_routing import (
    _normalize,
    find_ollama_equivalent,
    maybe_route_to_ollama,
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


_FAKE_SWAP_MODELS = {
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


class TestMaybeRouteToOllama:
    def test_already_ollama_passes_through(self):
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ):
            url, btype, model, rerouted = maybe_route_to_ollama(
                backend_url="http://localhost:11434",
                backend_type="ollama",
                vision_model="qwen3-vl:4b-instruct-q8_0",
            )
            assert url == "http://localhost:11434"
            assert btype == "ollama"
            assert model == "qwen3-vl:4b-instruct-q8_0"
            assert rerouted is False

    def test_cloud_api_passes_through_even_with_match(self):
        # User explicitly chose cloud — don't silently fall back to local Ollama
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ):
            url, btype, model, rerouted = maybe_route_to_ollama(
                backend_url=None,
                backend_type="cloud_api",
                vision_model="Qwen3VL-4B-Instruct-Q8_0",
            )
            assert btype == "cloud_api"
            assert model == "Qwen3VL-4B-Instruct-Q8_0"
            assert rerouted is False

    def test_vllm_with_visiond_profile_prefers_llamaswap(self):
        # Backend vLLM laeuft ueber llama-swap — ein -visiond-Profil wird
        # wie unter llamacpp bevorzugt (parallele vision-Gruppe, kein Swap).
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ), patch(
            "aifred.lib.vision_routing.visiond_profile_for",
            return_value="Qwen3VL-4B-Instruct-Q8_0-visiond",
        ):
            _, btype, model, rerouted = maybe_route_to_ollama(
                backend_url="http://localhost:8000",
                backend_type="vllm",
                vision_model="Qwen3VL-4B-Instruct-Q8_0",
            )
            assert btype == "vllm"
            assert model == "Qwen3VL-4B-Instruct-Q8_0-visiond"
            assert rerouted is True

    def test_vllm_without_visiond_falls_back_to_ollama(self):
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ), patch(
            "aifred.lib.vision_routing.visiond_profile_for",
            return_value=None,
        ):
            _, btype, model, rerouted = maybe_route_to_ollama(
                backend_url="http://localhost:8000",
                backend_type="vllm",
                vision_model="Qwen3VL-4B-Instruct-Q8_0",
            )
            assert btype == "ollama"
            assert model == "qwen3-vl:4b-instruct-q8_0"
            assert rerouted is True

    def test_llamaswap_with_visiond_profile_prefers_llamacpp(self):
        # Ein -visiond-Profil (llama-swap vision-Gruppe) schlägt die
        # Ollama-Umleitung: gleicher Backend, nur der Profilname wechselt.
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ), patch(
            "aifred.lib.vision_routing.visiond_profile_for",
            return_value="Qwen3VL-4B-Instruct-Q8_0-visiond",
        ):
            url, btype, model, rerouted = maybe_route_to_ollama(
                backend_url="http://localhost:11435",
                backend_type="llamacpp",
                vision_model="Qwen3VL-4B-Instruct-Q8_0",
            )
            assert btype == "llamacpp"
            assert model == "Qwen3VL-4B-Instruct-Q8_0-visiond"
            assert rerouted is True
            assert url == "http://localhost:11435"

    def test_llamaswap_with_ollama_match_routes(self):
        # Ohne -visiond-Profil bleibt die Ollama-Umleitung der Parallel-Pfad.
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ), patch(
            "aifred.lib.vision_routing.visiond_profile_for",
            return_value=None,
        ):
            url, btype, model, rerouted = maybe_route_to_ollama(
                backend_url="http://localhost:11435",  # llama-swap
                backend_type="llamacpp",
                vision_model="Qwen3VL-4B-Instruct-Q8_0",
            )
            assert btype == "ollama"
            assert model == "qwen3-vl:4b-instruct-q8_0"
            assert rerouted is True
            assert url is not None and "11434" in url

    def test_llamaswap_without_match_stays(self):
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ), patch(
            "aifred.lib.vision_routing.visiond_profile_for",
            return_value=None,
        ):
            # 30B has no Ollama pendant in this scenario
            url, btype, model, rerouted = maybe_route_to_ollama(
                backend_url="http://localhost:11435",
                backend_type="llamacpp",
                vision_model="Qwen3-VL-30B-A3B-Instruct-Q8_0",
            )
            assert btype == "llamacpp"
            assert model == "Qwen3-VL-30B-A3B-Instruct-Q8_0"
            assert rerouted is False
            assert url == "http://localhost:11435"

    def test_explicit_ollama_host_override(self):
        with patch(
            "aifred.lib.vision_routing.list_ollama_vlm_models",
            return_value=[_ollama("qwen3-vl:4b-instruct-q8_0")],
        ), patch(
            "aifred.lib.vision_routing.visiond_profile_for",
            return_value=None,
        ):
            url, _, _, rerouted = maybe_route_to_ollama(
                backend_url="http://localhost:11435",
                backend_type="llamacpp",
                vision_model="Qwen3VL-4B-Instruct-Q8_0",
                ollama_host="http://192.168.1.5:11434",
            )
            assert rerouted is True
            assert url == "http://192.168.1.5:11434"
