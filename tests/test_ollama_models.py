"""Tests für aifred.lib.ollama_models — VLM/embedding/text classification."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from aifred.lib.ollama_models import (
    OllamaModelInfo,
    classify_model,
    is_embedding_family,
    is_vlm_family,
    list_ollama_models,
    list_ollama_vlm_models,
)


def _info(name: str, family: str, families: tuple[str, ...] = ()) -> OllamaModelInfo:
    return OllamaModelInfo(
        name=name,
        family=family,
        families=families or ((family,) if family else ()),
        parameter_size="",
        quantization="",
        size_bytes=0,
    )


class TestVlmFamilyDetection:
    def test_qwen3vl_is_vlm(self):
        assert is_vlm_family("qwen3vl")

    def test_qwen25vl_is_vlm(self):
        assert is_vlm_family("qwen25vl")

    def test_qwen2vl_is_vlm(self):
        assert is_vlm_family("qwen2vl")

    def test_llava_is_vlm(self):
        assert is_vlm_family("llava")

    def test_minicpm_v_is_vlm(self):
        assert is_vlm_family("minicpm-v")

    def test_minicpm_llama3_v2_is_vlm(self):
        assert is_vlm_family("minicpm-v2")

    def test_internvl_is_vlm(self):
        assert is_vlm_family("internvl")

    def test_phi_vision_is_vlm(self):
        assert is_vlm_family("phi-vision")

    def test_text_only_qwen_is_not_vlm(self):
        # Plain text Qwen — no "vl" or "vision" in family
        assert not is_vlm_family("qwen3")

    def test_bert_family_is_not_vlm(self):
        # bge-m3 reports family=bert; absolutely not a VLM
        assert not is_vlm_family("bert")

    def test_nomic_bert_moe_is_not_vlm(self):
        assert not is_vlm_family("nomic-bert-moe")

    def test_families_list_used_if_family_empty(self):
        # Sometimes the singular family is missing but the plural list has it
        assert is_vlm_family("", families=("qwen3vl",))


class TestEmbeddingFamilyDetection:
    def test_bert_is_embedding(self):
        assert is_embedding_family("bert")

    def test_nomic_embed_is_embedding(self):
        assert is_embedding_family("nomic-embed-text-v2-moe")

    def test_nomic_bert_moe_is_embedding(self):
        assert is_embedding_family("nomic-bert-moe")

    def test_vlm_is_not_embedding(self):
        assert not is_embedding_family("qwen3vl")
        assert not is_embedding_family("llava")


class TestClassifyModel:
    def test_qwen3vl_4b(self):
        info = _info("qwen3-vl:4b-instruct-q8_0", "qwen3vl")
        assert classify_model(info) == "vlm"

    def test_bge_m3(self):
        info = _info("bge-m3:latest", "bert")
        assert classify_model(info) == "embedding"

    def test_nomic_embed(self):
        info = _info("nomic-embed-text-v2-moe:latest", "nomic-bert-moe")
        assert classify_model(info) == "embedding"

    def test_plain_qwen_text_model(self):
        info = _info("qwen3:7b-instruct", "qwen3")
        assert classify_model(info) == "text"

    def test_llava_name_fallback_when_family_missing(self):
        # Some older Ollama entries miss the family field — name-fallback
        info = _info("llava:7b", "")
        assert classify_model(info) == "vlm"

    def test_minicpm_v_name_fallback(self):
        info = _info("minicpm-v:8b", "")
        assert classify_model(info) == "vlm"

    def test_embedding_wins_over_name_fallback(self):
        # Even if name has "v2" in it (which could match), embedding wins
        info = _info("nomic-embed-text-v2-moe:latest", "nomic-bert-moe")
        assert classify_model(info) == "embedding"


class TestListOllamaModels:
    def _payload(self) -> dict:
        """Realistic /api/tags response, modeled on the user's actual setup."""
        return {
            "models": [
                {
                    "name": "qwen3-vl:8b-instruct-q8_0",
                    "size": 9830285285,
                    "details": {
                        "family": "qwen3vl",
                        "families": ["qwen3vl"],
                        "parameter_size": "8.8B",
                        "quantization_level": "Q8_0",
                    },
                },
                {
                    "name": "qwen3-vl:4b-instruct-q8_0",
                    "size": 5088910597,
                    "details": {
                        "family": "qwen3vl",
                        "families": ["qwen3vl"],
                        "parameter_size": "4.4B",
                        "quantization_level": "Q8_0",
                    },
                },
                {
                    "name": "bge-m3:latest",
                    "size": 1157672605,
                    "details": {
                        "family": "bert",
                        "families": ["bert"],
                        "parameter_size": "566.70M",
                        "quantization_level": "F16",
                    },
                },
                {
                    "name": "nomic-embed-text-v2-moe:latest",
                    "size": 957680763,
                    "details": {
                        "family": "nomic-bert-moe",
                        "families": ["nomic-bert-moe"],
                        "parameter_size": "475.29M",
                        "quantization_level": "F16",
                    },
                },
            ]
        }

    def test_parses_all_fields(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                json=lambda: self._payload(),
                raise_for_status=lambda: None,
            )
            models = list_ollama_models()
            assert len(models) == 4
            qwen8 = next(m for m in models if "8b" in m.name)
            assert qwen8.family == "qwen3vl"
            assert qwen8.parameter_size == "8.8B"
            assert qwen8.quantization == "Q8_0"
            assert qwen8.size_gb == 9830285285 / 1e9

    def test_vlm_filter_excludes_embeddings(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                json=lambda: self._payload(),
                raise_for_status=lambda: None,
            )
            vlms = list_ollama_vlm_models()
            names = [m.name for m in vlms]
            assert "qwen3-vl:4b-instruct-q8_0" in names
            assert "qwen3-vl:8b-instruct-q8_0" in names
            assert all("bge-m3" not in n and "nomic" not in n for n in names)

    def test_vlm_list_sorted_ascending_by_size(self):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                json=lambda: self._payload(),
                raise_for_status=lambda: None,
            )
            vlms = list_ollama_vlm_models()
            # 4B < 8B, so 4B comes first
            assert vlms[0].name == "qwen3-vl:4b-instruct-q8_0"
            assert vlms[1].name == "qwen3-vl:8b-instruct-q8_0"

    def test_connection_failure_returns_empty(self):
        import httpx

        with patch("httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("ollama down")
            models = list_ollama_models()
            assert models == []
