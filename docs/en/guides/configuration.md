# Configuration & Operation

> **Deutsche Version:** [configuration.md](../../de/guides/configuration.md)

Backends, settings, reasoning, history compression, the vector store, the main
`config.py` knobs, performance tuning and what multi-user operation means.
Installation and services: [Deployment Guide](deployment.md).

---

## Backends

Switchable in the UI settings:

| Backend | Models | Notes |
|---|---|---|
| **llama.cpp** via llama-swap | GGUF | Best raw performance and full GPU control; 3-tier setup **llama-swap** (Go proxy, model management) → **llama-server** → **llama.cpp**. Autoscan + calibration, see [llama.cpp setup](llamacpp-setup.md) |
| **vLLM** | safetensors checkpoints (e.g. NVFP4, MXFP4, FP8) | Runs as `-vllm` entries **under llama-swap**, same URL and catalog as llama.cpp; own calibration, see [vLLM calibration](../architecture/calibration-vllm.md) |
| **Ollama** | GGUF | Easiest setup, automatic model management |
| **Cloud APIs** | provider models | Claude (Anthropic), Qwen (DashScope), DeepSeek, Kimi (Moonshot) — OpenAI-compatible |

**Cloud APIs** need only the key in `.env` (`ANTHROPIC_API_KEY`,
`DASHSCOPE_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`). Model lists are
fetched live from the provider's `/models` endpoint; each provider remembers its
last model; `-vl` variants are kept out of the main-LLM dropdown; token usage
appears in the debug console. Handy for trying large models without hardware,
for laptops without a GPU, or to compare cloud and local quality. History
compression always uses local models (where the real context limit is known).

**GPU compatibility:** at startup AIfred reads each GPU's compute capability
and warns when a backend can only use some of the cards or none of them
(`aifred/lib/gpu_detection.py`).

---

## Settings

Global settings live in `data/settings.json`, written by the UI, the REST API
(`PATCH /api/settings`) and the Message Hub alike.

- **`backend_models`** is the single source of truth for every model role, per
  backend — switching backends restores that backend's models:

  ```json
  {
    "backend_type": "llamacpp",
    "backend_models": {
      "llamacpp": {
        "aifred": "<model id>",
        "automatik": "<model id>",
        "vision": "<model id>",
        "sokrates": "",
        "salomo": ""
      },
      "ollama": { "aifred": "qwen3:4b-instruct-2507-q4_K_M", "vision": "qwen3-vl:4b" }
    },
    "agent_tuning": {
      "aifred": { "temperature": 0.7, "reasoning": true, "thinking": true, "reasoning_effort": "" }
    }
  }
  ```

  An empty role falls back to AIfred's model. Every agent (custom ones too) has
  its own entry.
- **`agent_tuning`** holds per-agent sampling, temperature, reasoning, thinking,
  reasoning effort, personality and speed-variant toggles.
- On first start the defaults from `aifred/lib/config.py`
  (`BACKEND_DEFAULT_MODELS`) are used.
- Per-session settings (agent, discussion mode, research mode) are **not** here
  but in the session file — see [REST API → Global vs. per-session](rest-api.md#global-vs-per-session).

**Sampling persistence**

| Parameter | On restart | On model change |
|---|---|---|
| Temperature | kept | reset to YAML |
| Top-K, Top-P, Min-P, repeat penalty | reset to YAML | reset to YAML |

Source of truth for the sampling defaults are the `--temp`, `--top-k`,
`--top-p`, `--min-p`, `--repeat-penalty` flags of the model's entry in
`~/.config/llama-swap/config.yaml`.

---

## Reasoning and thinking

Two independent toggles per agent in the LLM settings:

| Toggle | Effect |
|---|---|
| 💭 **Reasoning** | Injects the chain-of-thought instructions (prompt layer 2) — works with every model, instruct models benefit most |
| 🧠 **Thinking** | Sends `enable_thinking` to thinking-capable models (Qwen3, QwQ, NemoTron, …) so they produce `<think>` blocks |

- **Reasoning effort:** for models whose chat template knows levels
  (e.g. `low` … `xhigh`), a per-agent selector picks one; empty means the
  template's default, shown in the label.
- Reasoning output appears as a collapsible with model name and inference time.
- Temperature is independent of reasoning (intent detection or manual value).
- The Automatik-LLM always runs with thinking off — its decisions are fast and
  structured.
- Tool chains with vision: turn **thinking off for the vision LLM** — reasoning
  models with speculative decoding (MoE/MTP) can swallow tool calls after
  thinking.

---

## History compression

A check runs **before** every LLM call. At 70 % context utilisation the oldest
messages are compressed into a summary:

| Parameter (`config.py`) | Value | Meaning |
|---|---|---|
| `HISTORY_COMPRESSION_TRIGGER` | 0.7 | compress at 70 % utilisation |
| `HISTORY_COMPRESSION_TARGET` | 0.3 | target after compression (room for ~2 round trips) |
| `HISTORY_SUMMARY_RATIO` | 0.25 | summary = 25 % of the compressed content (4:1) |
| `HISTORY_SUMMARY_MIN_TOKENS` | 500 | minimum for a useful summary |
| `HISTORY_SUMMARY_TOLERANCE` | 0.5 | allowed overshoot, beyond that it is truncated |
| `HISTORY_SUMMARY_MAX_RATIO` | 0.2 | at most 20 % of the context for summaries |
| `HISTORY_MAX_SUMMARIES` | 10 | hard cap on kept summaries |

**Algorithm**
1. Pre-check before each LLM call, trigger at 70 %
2. Maximum number of summaries from the context size (20 % budget / 500 tokens, capped at 10)
3. Too many summaries → the oldest is dropped first (FIFO)
4. Collect the oldest messages until less than 30 % remain
5. Compress them 4:1 into a summary
6. New history = [summaries] + [remaining messages]

| Context | Trigger | Target | Compressed | Summary |
|---|---|---|---|---|
| 7K | 4,900 tok | 2,100 tok | ~2,800 tok | ~700 tok |
| 40K | 28,000 tok | 12,000 tok | ~16,000 tok | ~4,000 tok |
| 200K | 140,000 tok | 60,000 tok | ~80,000 tok | ~20,000 tok |

The token estimate ignores `<details>`, `<span>` and `<think>` blocks (they are
not sent to the LLM). Summaries appear inline in the chat where compression
happened, as collapsibles; the FIFO limit only applies to `llm_history` — the
visible `chat_history` keeps every summary.

---

## ChromaDB: documents & agent memory

ChromaDB runs in Docker (data in `data/chromadb/`) and holds two kinds of
collections, both embedded with **bge-m3** (multilingual, 8192-token context,
1024 dimensions; `aifred/lib/embeddings.py`):

| Collection | Content | Access |
|---|---|---|
| `aifred_documents` | Indexed documents (token-accurate chunks) | `search_documents` tool; Document UI and Workspace plugin |
| `agent_memory_<agent_id>` | Per-agent long-term memory | Recalled before an answer (recent entries + semantic search); memory tools for the agent |

The embedding model is served by the llama-swap embed profile or, as second
choice, by Ollama (`ollama pull bge-m3`). Bulk indexing runs in GPU mode, single
queries in CPU mode (no VRAM competition with the active LLM).

Web research results are **not** stored — every research runs fresh.

**Maintenance:** browse and delete entries in the settings modal (Database and
Memory tabs). `scripts/chromadb-vacuum.sh` returns space freed by deletes to the
OS (stops the container for a few seconds).

**Reset** (deletes indexed documents and memories):

```bash
cd docker
docker compose stop chromadb
sudo rm -rf ../data/chromadb/   # created by the container as root
docker compose up -d chromadb
```

A single collection can be deleted in the settings modal or via the Python
client (`chromadb.HttpClient(host='localhost', port=8000).delete_collection(...)`).

---

## Other `config.py` knobs

```python
# Intent-based temperature (auto mode)
INTENT_TEMPERATURE_FAKTISCH = 0.2    # factual
INTENT_TEMPERATURE_GEMISCHT = 0.5    # mixed
INTENT_TEMPERATURE_KREATIV = 1.0     # creative
SOKRATES_TEMPERATURE_OFFSET = 0.2    # added to AIfred's temperature
SALOMO_TEMPERATURE_OFFSET = 0.3

# Security
SECURITY_MAX_TOOL_CHAIN_DEPTH = 100  # tool calls per request

# Default models per backend
BACKEND_DEFAULT_MODELS = {...}       # Ollama: qwen3:4b-instruct-2507-q4_K_M, qwen3-vl:4b
```

URLs: `LLAMACPP_URL` (default `http://localhost:11435/v1`, llama-swap — also
used by vLLM) and the Ollama default `http://localhost:11434`.

The Ollama client has no HTTP timeout (`httpx.Timeout(None)`), so long first
tokens on big research contexts do not abort.

**Restart button** in the UI: runs `systemctl restart aifred-intelligence`
(needs the [Polkit rule](deployment.md#polkit-rule-restart-without-sudo)); the
browser reloads after a short delay, sessions are kept.

---

## Performance

### Ollama: `OLLAMA_NUM_PARALLEL=1`

Ollama's default of 2 parallel slots **doubles the KV-cache allocation** for a
slot a single user never uses. With 1, a 30B model fitted ~222K context instead
of ~111K on the same GPUs.

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d/
sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_NUM_PARALLEL=1"
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Keep 2+ only for real concurrent multi-user load. Recalibrate afterwards.

### llama.cpp vs. Ollama

Historical measurement (Qwen3-30B-A3B Q8_0, 2× Tesla P40); the relative
advantage carries over to newer hardware:

| Metric | llama.cpp | Ollama | Advantage |
|---|:---:|:---:|:---:|
| Time to first token | 1.1 s | 1.5 s | llama.cpp −27 % |
| Generation | 39.3 tok/s | 27.4 tok/s | llama.cpp +43 % |
| Prompt processing | 1,116 tok/s | 862 tok/s | llama.cpp +30 % |
| Intent detection | 0.8 s | 0.7 s | similar |

Choose **llama.cpp** for speed, multi-GPU tensor-split control and large
contexts; **Ollama** for the quickest start. Recommended llama-server parameters
per model: [Model Parameters](../benchmarks/model-params.md).

---

## Multiple users

AIfred is a **personal assistant** — built for one user, fine for a household
of two or three.

**Per user:**
- Accounts with password (bcrypt) and a registration whitelist, managed with
  `aifred-admin` (see [Deployment](deployment.md#user-management))
- Chat sessions belong to their owner (`data/sessions/`) and are reachable from
  any device after login
- Each browser tab streams its own answers; external identities (Telegram ID,
  e-mail address) map to AIfred users via `data/user_mapping.json`

**Shared by everyone:**
- Backend, models and the settings in `data/settings.json` — a model change by
  one user applies to all
- The GPUs: requests are processed one after another

Running requests are safe (their parameters are already with the backend); the
*next* request of another user uses the new settings. Settings changes reach
open tabs within a second or two; the model dropdown may only update when it is
reopened (Reflex limitation).

This is deliberate: local hardware runs one set of models efficiently, not one
per user. AIfred is not a multi-tenant service — no quotas, no per-user
settings, and every logged-in user can change global settings. Give accounts
only to people you trust.

---

## Troubleshooting

**Service does not start**
```bash
journalctl -u aifred-intelligence -n 50
systemctl status llama-swap ollama
```

**Restart button has no effect** — check the Polkit rule
(`/etc/polkit-1/rules.d/10-aifred.rules`, see [Deployment](deployment.md#polkit-rule-restart-without-sudo)).

**Model missing, skip list, OOM** — see [Deployment → Troubleshooting](deployment.md#12-troubleshooting).

**Debug log:** `tail -f data/logs/aifred_debug.log`
