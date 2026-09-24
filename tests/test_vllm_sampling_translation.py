"""vLLM-Adapter: Sampling-Felder, die in vLLM etwas anderes bedeuten, gehen nicht auf die Leitung.

repetition_penalty bestraft bei vLLM jedes Token des gesamten Prompts
(llama.cpp: nur die letzten 64) — die Einstellung muss in jedem Backend
dasselbe bewirken, also faellt sie hier weg. min_p geht durch: unser vLLM
wendet es auch unter Spekulation an (Prüfschritt des Rejection-Samplers).
"""
from aifred.backends.base import LLMOptions
from aifred.backends.vllm import vLLMBackend


def test_repetition_penalty_is_dropped_and_min_p_is_sent():
    backend = vLLMBackend.__new__(vLLMBackend)
    body = backend._build_extra_body(LLMOptions(repeat_penalty=1.1, min_p=0.05, top_k=20), "test-model")
    assert "repetition_penalty" not in body
    assert body["min_p"] == 0.05
    assert body["top_k"] == 20
