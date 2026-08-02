# AIfred Intelligence — Documentation Index

Central entry point for everything under `docs/`. The docs are bilingual by
convention: **architecture deep-dives are mostly written in German**,
**setup guides and benchmarks mostly in English**. Files that exist in both
languages live under `de/` and `en/` with the same relative path; single-language
files are tagged below.

> Deutsch: Architektur-Docs sind überwiegend auf Deutsch, Setup/Benchmarks
> überwiegend auf Englisch. Querverlinkung über die Sprachgrenze ist Absicht,
> kein Versehen.

## Guides

| Doc | Languages | Content |
|---|---|---|
| [Deployment Guide](en/guides/deployment.md) | [DE](de/guides/deployment.md) · [EN](en/guides/deployment.md) | Fresh-install walkthrough, calibration, vision + Vigilantia setup |
| [llama.cpp + llama-swap Setup](en/guides/llamacpp-setup.md) | EN | 3-tier architecture, autoscan, GPU management (partly German) |
| [Plugin Overview](en/guides/plugins-overview.md) | [DE](de/guides/plugins-overview.md) · [EN](en/guides/plugins-overview.md) | All tool + channel plugins, security tiers, Vigilantia |
| [Plugin Development](en/guides/plugin-development.md) | EN | Writing new tool/channel plugins, with templates |
| [Telegram Setup](en/guides/telegram-setup.md) | [DE](de/guides/telegram-setup.md) · [EN](en/guides/telegram-setup.md) | Bot creation, allowlist, first contact |
| Per-plugin guides | [DE](de/guides/plugins/) · [EN](en/guides/plugins/) | One doc per plugin (email, discord, vision, workspace, …) |

## Architecture

| Doc | Languages | Content |
|---|---|---|
| [Calibration Strategy (SSOT)](de/architecture/calibration-strategy.md) | DE | Greedy cascade, burn-in, capacity guard — authoritative reference |
| [Calibration Challenge: LLM vs. Algorithm](de/architecture/calibration-llm-challenge.md) | DE | Experiment design comparing AI-agent and algorithmic calibration |
| [Message Hub](de/architecture/message-hub.md) | DE | Headless channel processing: listeners, envelopes, routing table |
| [Scheduler & Proactive Features](en/architecture/scheduler.md) | [DE](de/architecture/scheduler.md) · [EN](en/architecture/scheduler.md) | Job store, cron, webhook API |
| [Security Architecture](en/architecture/security.md) | [DE](de/architecture/security.md) · [EN](en/architecture/security.md) | Tiers, owner elevation, auth |
| [LLM Call Architecture](en/architecture/llm-call.md) | EN | The path of a single LLM call through the stack |
| [Audio Pipeline](de/architecture/audio-pipeline.md) | DE | STT/TTS flow, browser + FreeEcho.2 output paths |
| [Browser Push Bus](de/architecture/browser-push-bus.md) | DE | Server→browser events without Reflex yields |
| [Proactive Alerts](de/architecture/proactive-alerts.md) | DE | Alert pipeline from vision events to user notification |
| [Vision Routing](de/architecture/vision-routing.md) | DE | Swap vs. no-swap decision for vision workloads |
| [TTS Container Conventions](de/architecture/tts-container-conventions.md) | DE | Rules for integrating new TTS engines |
| [TTS + VRAM Workflow](de/architecture/tts-vram-workflow.md) | DE | FreeEcho.2 & browser TTS container lifecycle |
| [DashScope Voice Cloning](en/architecture/dashscope-voice-cloning.md) | [DE](de/architecture/dashscope-voice-cloning.md) · [EN](en/architecture/dashscope-voice-cloning.md) | Cloud Qwen3-TTS voice cloning |

## Benchmarks

| Doc | Languages | Content |
|---|---|---|
| [Performance History](de/benchmarks/performance-history.md) | DE | Running chronicle of inference milestones on the MiniPC |
| [Model Parameters](en/benchmarks/model-params.md) | [DE](de/benchmarks/model-params.md) · [EN](en/benchmarks/model-params.md) | Recommended llama-server parameters per model |
| [Tensor Split Benchmark](en/benchmarks/tensor-split.md) | EN | Speed variant vs. full context on multi-GPU |
| [Benchmark Analysis v2](en/benchmarks/analysis-v2.md) | [DE](de/benchmarks/analysis-v2.md) · [EN](en/benchmarks/analysis-v2.md) | Dog-vs-cat tribunal sessions across models |
| [Benchmark Model Overview](en/benchmarks/models-v2.md) | EN | Models used in the v2 benchmark runs |
| [Tribunal Showcase Notes](de/benchmarks/showcase-notes.md) | DE | 6-model tribunal comparison (German inference) |

## Models

| Doc | Languages | Content |
|---|---|---|
| [TTS Model Comparison](en/models/tts-comparison.md) | [DE](de/models/tts-comparison.md) · [EN](en/models/tts-comparison.md) | All integrated TTS engines compared |
| [MOSS-TTS 8B Notes](de/models/moss-tts-8b-notes.md) | DE | Running MOSS-TTS Delay 8B on Turing GPUs |

## Blog / Posts

- [Reddit Post: r/LocalLLaMA Follow-Up](en/blog/reddit-post-v2.md) (EN)
- [Reddit Post: Tensor Split](en/blog/reddit-post-v5-tensor-split.md) (EN)

## Examples & Showcases

- [Examples README](examples/README.md) — curated HTML showcases (tribunal
  debates, benchmarks, chemistry/math/coding sessions), also served via the
  [GitHub Pages landing page](index.html)

## Historical

- [vLLM notes](vllm/README.md) — pre-state-refactor vLLM backend docs.
  **Deliberately kept**: vLLM returns as the main backend after the GPU
  migration (see TODO.md).
