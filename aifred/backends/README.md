# AIfred LLM Backends

Adapters between AIfred and the inference servers. Every backend implements
`LLMBackend` (`base.py`); the OpenAI-compatible ones share
`OpenAICompatibleBackend` (chat, streaming, `chat_template_kwargs` for thinking
and `reasoning_effort`).

| Backend type | File | Default URL (`aifred/lib/config.py`) | Serves |
|---|---|---|---|
| `llamacpp` | `llamacpp.py` | `DEFAULT_LLAMACPP_URL` = `http://localhost:11435/v1` (env `LLAMACPP_URL`) | GGUF models via llama-swap |
| `vllm` | `vllm.py` | `DEFAULT_VLLM_URL` = same as `llamacpp` | vLLM checkpoints, run as `-vllm` entries under llama-swap |
| `ollama` | `ollama.py` | `DEFAULT_OLLAMA_URL` = `http://localhost:11434` | Ollama models |
| `cloud_api` | `cloud_api.py` | per provider in `CLOUD_API_PROVIDERS` | Claude, Qwen (DashScope), DeepSeek, Kimi (Moonshot) |

llama.cpp and vLLM share one llama-swap catalog: the backend choice is only a
different view on it. Setup of llama-swap, the autoscan and the calibration:
[docs/en/guides/llamacpp-setup.md](../../docs/en/guides/llamacpp-setup.md) and
[docs/en/architecture/calibration-vllm.md](../../docs/en/architecture/calibration-vllm.md).

Class hierarchy: `OllamaBackend` derives from `LLMBackend` directly;
`LlamaCppBackend`, `vLLMBackend` and `CloudAPIBackend` derive from
`OpenAICompatibleBackend`.

Cloud API keys come from the environment variable named in the provider's
`env_key` in `CLOUD_API_PROVIDERS` (`ANTHROPIC_API_KEY`, `DASHSCOPE_API_KEY`,
`DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`); model lists are fetched from the
provider's API, not hardcoded. Without an explicit provider the factory uses
`qwen`.

Before llama-swap loads a model, `OpenAICompatibleBackend` clears the cards: local
TTS containers that sit on a card the model will use are stopped (a
`-tts-<engine>` profile keeps its engine on the side-channel card). TTS gets its
card back later through the TTS escalation list
([docs/en/guides/configuration.md](../../docs/en/guides/configuration.md#speech-output-tts)).

## Creating a backend

```python
from aifred.backends import BackendFactory

backend = BackendFactory.create("llamacpp")            # URL from BACKEND_URLS
backend = BackendFactory.create("cloud_api", provider="claude")

models = await backend.list_models()
healthy = await backend.health_check()
```

The backend used by the app is selected in the UI settings; code should go
through the factory instead of instantiating a backend class directly.

## Adding a backend

1. Subclass `LLMBackend` — or `OpenAICompatibleBackend` if the server speaks the
   OpenAI API — and implement the abstract methods.
2. Register the class in `BackendFactory._backends` (`__init__.py`).
3. Add its default URL to `BACKEND_URLS` and its label to `BACKEND_LABELS` in
   `aifred/lib/config.py`.
