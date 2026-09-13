#!/usr/bin/env python3
"""
KV-Cache Benchmark: f16 vs q4_0

Sendet identische Prompts direkt an llama-server (via llama-swap)
und vergleicht Performance-Metriken und Output-Qualität.

Usage:
    python kv-cache-benchmark.py --model gpt-oss [--runs 5]
    python kv-cache-benchmark.py --model qwen80b [--runs 5]
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import requests

LLAMA_SWAP_URL = "http://localhost:{port}/v1/chat/completions"

# Vorkonfigurierte Modell-Paare für Benchmarks
MODEL_PRESETS = {
    "gpt-oss": {
        "f16": "gpt-oss-120b-q8_0",
        "q4_0": "gpt-oss-120b-q8_0-kvq4",
    },
    "qwen80b": {
        "f16": "qwen3-next-80b-a3b-instruct-q4_k_m-kvf16",
        "q4_0": "qwen3-next-80b-a3b-instruct-q4_k_m",
    },
    "qwen80b-thinking": {
        "f16": "Qwen3-Next-80B-A3B-Thinking-Q4_K_M-kvf16",
        "q4_0": "Qwen3-Next-80B-A3B-Thinking-Q4_K_M",
    },
}

BENCHMARK_PROMPT = (
    "Erkläre die drei wichtigsten Unterschiede zwischen "
    "Transformer-Architektur und Mamba (State Space Models). "
    "Gib für jeden Unterschied ein konkretes Beispiel."
)

SYSTEM_PROMPT = "Du bist ein hilfreicher KI-Assistent. Antworte präzise und strukturiert."


def run_single_inference(
    url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
) -> dict:
    """Einzelne Inferenz gegen llama-swap, misst Timing."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }

    t_start = time.monotonic()
    resp = requests.post(url, json=payload, timeout=600)
    t_end = time.monotonic()

    resp.raise_for_status()
    data = resp.json()

    usage = data.get("usage", {})
    choice = data["choices"][0]
    content = choice["message"].get("content", "")
    reasoning = choice["message"].get("reasoning_content", "")

    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total_time = t_end - t_start

    return {
        "content": content,
        "reasoning": reasoning,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_time_s": round(total_time, 2),
        "tokens_per_sec": round(completion_tokens / total_time, 2) if total_time > 0 else 0,
        "content_length": len(content),
        "reasoning_length": len(reasoning),
    }


def warmup(url: str, model: str) -> None:
    """Kurzer Warmup-Request damit das Modell geladen ist."""
    print(f"  Warmup für {model}...")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Hallo"}],
        "temperature": 0,
        "max_tokens": 10,
        "stream": False,
    }
    resp = requests.post(url, json=payload, timeout=600)
    resp.raise_for_status()
    print("  Modell geladen.")


def run_benchmark(url: str, model_key: str, model_name: str, runs: int) -> list[dict]:
    """N Durchläufe für ein Modell."""
    print(f"\n{'='*60}")
    print(f"Benchmark: {model_key} ({model_name})")
    print(f"{'='*60}")

    warmup(url, model_name)

    results = []
    for i in range(runs):
        print(f"  Run {i+1}/{runs}...", end=" ", flush=True)
        result = run_single_inference(url, model_name, SYSTEM_PROMPT, BENCHMARK_PROMPT)
        print(f"{result['tokens_per_sec']} t/s, {result['completion_tokens']} tokens, {result['total_time_s']}s")
        results.append(result)

    return results


def print_summary(label: str, results: list[dict]) -> None:
    """Statistik für eine Testreihe."""
    tps = [r["tokens_per_sec"] for r in results]
    times = [r["total_time_s"] for r in results]
    tokens = [r["completion_tokens"] for r in results]
    content_lens = [r["content_length"] for r in results]

    print(f"\n--- {label} ({len(results)} runs) ---")
    print(f"  Tokens/s:        {statistics.mean(tps):.1f} avg, {statistics.stdev(tps):.1f} stddev" if len(tps) > 1 else f"  Tokens/s:        {tps[0]:.1f}")
    print(f"  Total Time:      {statistics.mean(times):.1f}s avg")
    print(f"  Completion Tok:  {statistics.mean(tokens):.0f} avg")
    print(f"  Content Length:  {statistics.mean(content_lens):.0f} chars avg")

    # Reasoning stats (wenn vorhanden)
    reasoning_lens = [r["reasoning_length"] for r in results]
    if any(r > 0 for r in reasoning_lens):
        print(f"  Reasoning Len:   {statistics.mean(reasoning_lens):.0f} chars avg")


def save_results(results: dict, output_path: Path) -> None:
    """Ergebnisse als JSON speichern."""
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nErgebnisse gespeichert: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="KV-Cache Benchmark: f16 vs q4_0")
    parser.add_argument("--model", type=str, required=True, choices=MODEL_PRESETS.keys(),
                        help="Modell-Preset (z.B. gpt-oss, qwen80b)")
    parser.add_argument("--runs", type=int, default=5, help="Anzahl Durchläufe pro Variante (default: 5)")
    parser.add_argument("--port", type=int, default=11435, help="llama-swap Port (default: 11435)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON Pfad")
    args = parser.parse_args()

    models = MODEL_PRESETS[args.model]
    url = LLAMA_SWAP_URL.format(port=args.port)

    print(f"KV-Cache Benchmark: f16 vs q4_0 ({args.model})")
    print(f"Runs: {args.runs}, Port: {args.port}")
    print(f"f16 → {models['f16']}")
    print(f"q4_0 → {models['q4_0']}")
    print(f"Prompt: {BENCHMARK_PROMPT[:80]}...")

    all_results = {}

    for key, model_name in models.items():
        results = run_benchmark(url, key, model_name, args.runs)
        all_results[key] = results

    # Zusammenfassung
    print(f"\n{'='*60}")
    print("ZUSAMMENFASSUNG")
    print(f"{'='*60}")

    for key in models:
        print_summary(key, all_results[key])

    # Vergleich
    f16_tps = statistics.mean([r["tokens_per_sec"] for r in all_results["f16"]])
    q4_tps = statistics.mean([r["tokens_per_sec"] for r in all_results["q4_0"]])
    diff_pct = ((q4_tps - f16_tps) / f16_tps) * 100

    print("\n--- Vergleich ---")
    print(f"  Speed-Diff:  {diff_pct:+.1f}% (q4_0 vs f16)")
    if diff_pct > 0:
        print(f"  → q4_0 ist {diff_pct:.1f}% schneller")
    else:
        print(f"  → f16 ist {abs(diff_pct):.1f}% schneller")

    # Speichern
    output_path = Path(args.output) if args.output else Path(__file__).parent / f"kv-cache-benchmark-{args.model}.json"
    save_results({
        "model_preset": args.model,
        "prompt": BENCHMARK_PROMPT,
        "system_prompt": SYSTEM_PROMPT,
        "runs_per_variant": args.runs,
        "models": models,
        "results": all_results,
    }, output_path)


if __name__ == "__main__":
    main()
