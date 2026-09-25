# AIfred Intelligence — Documentation Index

Central entry point for everything under `docs/`. Every document exists in
English (`en/`) and German (`de/`) with the **same file name** — when you change
one, update the other.

> **Deutsch:** Jedes Dokument gibt es auf Englisch (`en/`) und Deutsch (`de/`)
> unter demselben Dateinamen. Wer eine Fassung ändert, zieht die andere nach.

## Guides

| Doc | EN · DE | Content |
|---|---|---|
| Deployment | [EN](en/guides/deployment.md) · [DE](de/guides/deployment.md) | Fresh install, services, `.env`, Polkit, reverse proxy, calibration, vision + Vigilantia setup |
| llama.cpp + llama-swap | [EN](en/guides/llamacpp-setup.md) · [DE](de/guides/llamacpp-setup.md) | 3-tier architecture, autoscan, GPU management |
| Configuration & operation | [EN](en/guides/configuration.md) · [DE](de/guides/configuration.md) | Backends, settings, reasoning, history compression, ChromaDB, performance, multi-user |
| REST API | [EN](en/guides/rest-api.md) · [DE](de/guides/rest-api.md) | Browser remote control, endpoints, auth, examples |
| Plugin overview | [EN](en/guides/plugins-overview.md) · [DE](de/guides/plugins-overview.md) | All tool + channel plugins, security tiers |
| Plugin development | [EN](en/guides/plugin-development.md) · [DE](de/guides/plugin-development.md) | Writing tool/channel plugins, templates in `docs/examples/` |
| Telegram setup | [EN](en/guides/telegram-setup.md) · [DE](de/guides/telegram-setup.md) | Bot creation, allowlist, first contact |
| Discord setup | [EN](en/guides/discord-setup.md) · [DE](de/guides/discord-setup.md) | Bot creation, IDs, allowlist |
| Per-plugin guides | [EN](en/guides/plugins/) · [DE](de/guides/plugins/) | One doc per plugin (email, discord, vision, workspace, …) |

## Architecture

| Doc | EN · DE | Content |
|---|---|---|
| Multi-agent system | [EN](en/architecture/multi-agent.md) · [DE](de/architecture/multi-agent.md) | Agents, discussion modes, prompt layers, perspectives |
| Research pipeline | [EN](en/architecture/research-pipeline.md) · [DE](de/architecture/research-pipeline.md) | Research modes, pre-processing, pipeline, code map |
| LLM call | [EN](en/architecture/llm-call.md) · [DE](de/architecture/llm-call.md) | The path of a single LLM call through the stack |
| Codebase overview | [EN](en/architecture/codebase.md) · [DE](de/architecture/codebase.md) | Directory map, conventions, checks |
| Message Hub | [EN](en/architecture/message-hub.md) · [DE](de/architecture/message-hub.md) | Headless channel processing: listeners, envelopes, routing table |
| Scheduler | [EN](en/architecture/scheduler.md) · [DE](de/architecture/scheduler.md) | Job store, cron, webhook API |
| Security | [EN](en/architecture/security.md) · [DE](de/architecture/security.md) | Tiers, owner elevation, auth |
| Sub-agents | [EN](en/architecture/subagent-plugin.md) · [DE](de/architecture/subagent-plugin.md) | Delegation design |
| Calibration strategy (SSOT) | [EN](en/architecture/calibration-strategy.md) · [DE](de/architecture/calibration-strategy.md) | Greedy cascade, burn-in, capacity guard — authoritative reference |
| vLLM calibration | [EN](en/architecture/calibration-vllm.md) · [DE](de/architecture/calibration-vllm.md) | vLLM calibration algorithm |
| Calibration challenge: LLM vs. algorithm | [EN](en/architecture/calibration-llm-challenge.md) · [DE](de/architecture/calibration-llm-challenge.md) | Experiment design: AI-agent vs. algorithmic calibration (open) |
| RPC calibration (draft) | [EN](en/architecture/calibration-rpc.md) · [DE](de/architecture/calibration-rpc.md) | Work-package sketch for distributed inference |
| Audio pipeline | [EN](en/architecture/audio-pipeline.md) · [DE](de/architecture/audio-pipeline.md) | STT/TTS flow, browser + FreeEcho.2 output paths |
| Browser push bus | [EN](en/architecture/browser-push-bus.md) · [DE](de/architecture/browser-push-bus.md) | Server→browser events without Reflex yields |
| Proactive alerts | [EN](en/architecture/proactive-alerts.md) · [DE](de/architecture/proactive-alerts.md) | From vision events to user notification |
| Vision routing | [EN](en/architecture/vision-routing.md) · [DE](de/architecture/vision-routing.md) | Swap vs. no-swap decision for vision workloads |
| TTS container conventions | [EN](en/architecture/tts-container-conventions.md) · [DE](de/architecture/tts-container-conventions.md) | Rules for integrating new TTS engines |
| TTS + VRAM workflow | [EN](en/architecture/tts-vram-workflow.md) · [DE](de/architecture/tts-vram-workflow.md) | TTS container lifecycle for browser and FreeEcho.2 |
| DashScope voice cloning | [EN](en/architecture/dashscope-voice-cloning.md) · [DE](de/architecture/dashscope-voice-cloning.md) | Cloud Qwen3-TTS voice cloning |

## Benchmarks

| Doc | EN · DE | Content |
|---|---|---|
| Performance history | [EN](en/benchmarks/performance-history.md) · [DE](de/benchmarks/performance-history.md) | Running chronicle of inference milestones on the MiniPC |
| vLLM auto-calibration | [EN](en/benchmarks/vllm-autocalibration.md) · [DE](de/benchmarks/vllm-autocalibration.md) | Topology search + MTP sweep on mixed Turing/Volta GPUs |
| Quantization quality | [EN](en/benchmarks/quantization-quality.md) · [DE](de/benchmarks/quantization-quality.md) | Quantization formats vs. answer quality |
| Model parameters *(historical)* | [EN](en/benchmarks/model-params.md) · [DE](de/benchmarks/model-params.md) | llama-server parameters per model (P40 era) |
| Tensor split *(historical)* | [EN](en/benchmarks/tensor-split.md) · [DE](de/benchmarks/tensor-split.md) | Speed variant vs. full context (P40 era) |
| Benchmark analysis v2 *(historical)* | [EN](en/benchmarks/analysis-v2.md) · [DE](de/benchmarks/analysis-v2.md) | Dog-vs-cat tribunal sessions across models |
| Benchmark models v2 *(historical)* | [EN](en/benchmarks/models-v2.md) · [DE](de/benchmarks/models-v2.md) | Models used in the v2 runs |
| Tribunal showcase notes *(historical)* | [EN](en/benchmarks/showcase-notes.md) · [DE](de/benchmarks/showcase-notes.md) | 6-model tribunal comparison |

*Historical* = measured on the earlier Tesla P40 setup; still a useful reference
for Pascal cards.

## Models

| Doc | EN · DE | Content |
|---|---|---|
| TTS model comparison | [EN](en/models/tts-comparison.md) · [DE](de/models/tts-comparison.md) | All integrated TTS engines compared |
| MOSS-TTS 8B notes | [EN](en/models/moss-tts-8b-notes.md) · [DE](de/models/moss-tts-8b-notes.md) | Running MOSS-TTS Delay 8B on Turing GPUs |

## Examples & showcases

- [Examples README](examples/README.md) — exported HTML sessions (tribunal
  debates, benchmarks, chemistry/maths/coding/research), also on the
  [GitHub Pages landing page](index.html)
- Plugin templates: `examples/plugin_template_tool.py`,
  `examples/plugin_template_channel.py`

## Historical

- The old vLLM notes (`docs/vllm/`, RTX 3060 era, 2025-11) were removed on
  2026-09-25; they describe code that no longer exists. Last version:
  `git show d6ff5b5b:docs/vllm/README.md`.
