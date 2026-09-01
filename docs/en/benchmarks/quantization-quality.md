# Quantization formats and answer quality

> As of 2026-09-01. All numbers from the same machine (5-GPU box,
> 2× Quadro RTX 8000 sm75 + 3× Tesla V100 sm70), the same three prompts
> and the same AIfred persona. Complements
> [vllm-autokalibration.md](../../de/benchmarks/vllm-autokalibration.md)
> (German), which covers the throughput side, with the quality side.

## Why this document

On 2026-08-30, `RadixArk/Qwen3.8-Flash-Next-NVFP4` started degrading
linguistically during long German generation. Tracking down the cause took
two days and turned up more than the one checkpoint. The findings matter
for model selection and are recorded nowhere else.

## Test protocol

Three prompts, always in this order, always through AIfred with the full
persona (roughly 9,700 tokens of system prompt):

1. "Erkläre Quantenphysik in 30 Sätzen." (explain quantum physics in 30
   sentences)
2. "Erkläre den Regenbogeneffekt in 30 Sätzen." (the rainbow effect)
3. "Erkläre den Kuanda-Effekt in 30 Sätzen." — **the trap**: "Kuanda" is a
   garbling of the **Coandă** effect. What is measured is whether the model
   spots the phonetic proximity, honestly admits ignorance, or fabricates.

Evaluation is **manual reading** plus a machine character audit for CJK
characters, soft hyphens (U+00AD), zero-width characters and English words
in running German text. The third turn carries the largest context — that
is where degradation showed up first.

**Methodological trap:** the standalone probe
`v100-skinny/tools/quality_probe.py` does **not** reproduce the language
leakage. It needs the long persona and the sprawling generations of a real
AIfred session. Measuring with the probe alone yields clean results and
misses the problem entirely.

## Results

| Model | Quantization | Backend | CJK | Word splitting | English in running text | Coandă spotted |
|---|---|---|---|---|---|---|
| **Flash-Next-180B-A4B** | **Q6_K_XL** | llama.cpp | 0 | 0 | persona only | **YES** |
| Qwen3-235B-A22B-Instruct-2507 | NVFP4 (NVIDIA) | vLLM | 0 | 0 | persona only | no |
| Qwen3.8-27B-MTP | Q8_K_XL | llama.cpp | 0 | 2× (`Kaus tik`) | **none** | no |
| Qwen3.8-27B | NVFP4 (Unsloth), run 1 | vLLM | 0 | 2× soft hyphen | `Light`, `List`, `should the occasion arise` | no |
| Qwen3.8-27B | NVFP4 (Unsloth), run 2 | vLLM | 0 | 0 | **whole clauses, one complete English closing sentence** | no |
| Flash-Next-180B-A4B | NVFP4 (RadixArk) | vLLM | **7** | yes | yes | yes |

### The winner

`Qwen3.8-Flash-Next-180B-A4B-UD-Q6_K_XL` under llama.cpp is the **only**
model that solved the trap. From its own reasoning block:

> "The user wrote 'Kuanda-Effekt' — this is a misspelling of 'Coanda
> effect.' I'll correct the spelling in my response."

It then explained the actual Coandă effect down to the airfoil lift
question. Linguistically clean. Price across the three turns: TTFT 111 s,
4.4 s and 25 s; prefill 273, 358 and 224 tok/s; decode 22.3, 22.2 and
28.9 tok/s — markedly slower than the 27B variants. These come from
llama-server's own timings (server-side since 2026-02-28) and are
unaffected by the wall-clock fallback that inflated the vLLM side.

Worth noting: RadixArk's NVFP4 of the *same* model also spotted Coandă.
Recognition therefore depends on the **model**, not on the quantization;
the 27B fails it in every variant.

### The decisive line

Typos and split words occur under **both** formats — that is a property of
the model. The **switch into English** occurred exclusively under NVFP4, in
two independent runs of the same checkpoint, with identical prompts and
identical reasoning effort. The Q8 answers contained zero English words.

The escalation across turns is characteristic: single words first, then
subordinate clauses, finally a complete English closing sentence.

## Throughput (same runs)

| | vLLM NVFP4 27B | llama.cpp Q8 27B |
|---|---|---|
| TTFT | 4.93 · 4.27 · 4.42 s | 5.70 · 4.04 · 3.98 s |
| Prefill | 603 · 677 · 694 tok/s | 556 · 490 · 468 tok/s |
| Decode | 35.4 · 35.4 · 25.0 tok/s | 32.6 · 29.5 · 24.9 tok/s |

vLLM leads decode by 9–20 % and holds its rate as context grows, while
llama.cpp falls off. At long context (31k) the calibration measured 37 vs
26 tok/s.

**Caveat on the prefill column:** both count only uncached tokens, but
llama.cpp divides by pure prompt-processing time while AIfred divides by
TTFT for vLLM (the API reports no prefill duration). The vLLM figure is
therefore an underestimate.

## Why

### It is not our kernels

**The same kernels produced a flawless 235B and a leaky 27B.** Were the
implementation at fault, the 235B would have shown it too. Add the numerics
checks from the kernel campaign: max deviation 2.4e-4 against an fp32
reference.

### It is not that we compute NVFP4 in software

The information loss sits in the **stored weights**: 4 bits plus one FP8
scale per 16-element block. Whether Blackwell's tensor cores multiply that
natively or our skinny kernels unpack it to fp16 first changes nothing.
Blackwell would be faster, not more accurate — the same degradation, just
sooner.

### It is model size and language

Published accuracy retention against BF16:

| Model size | Retention |
|---|---|
| 70–235 B | ~99 % |
| ~30 B | 97–99 % |
| 7–14 B | 95–98 % |

This matches our findings: 235B clean, 27B leaky. And Flash-Next-180B is an
**A4B** — only 4 B parameters are active per token to absorb the error.
That is why the largest model degraded most.

Add the multilingual component: quantization damages non-English languages
**disproportionately**, with two- to fourfold larger perplexity degradation
than English in aggressive regimes; non-Latin scripts suffer worst. Human
evaluation reveals considerably more severe degradation than automatic
metrics suggest.

That explains both observed symptoms: English fragments (the model falls
back to its dominant training language on close calls) and CJK characters
with RadixArk (non-Latin scripts as the most fragile class).

Sources: [How Does Quantization Affect Multilingual
LLMs?](https://arxiv.org/abs/2407.03211) · [The Uneven Impact of
Post-Training Quantization in Machine
Translation](https://arxiv.org/pdf/2508.20893) · [NVFP4 on vLLM (Red
Hat)](https://developers.redhat.com/articles/2026/02/04/accelerating-large-language-models-nvfp4-quantization)
· [NVIDIA on
NVFP4](https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/)

## What follows

**For daily use: llama.cpp at 8 bits or better.** Q8_K_XL for the 27B,
Q6_K_XL for Flash-Next-180B. The 9–20 % throughput advantage of vLLM does
not outweigh the language leakage when German output matters.

**For long contexts vLLM stays ahead** — 37 vs 26 tok/s there, and a
several-fold faster prefill. Anyone running 30k prompts will notice.

**NVFP4 is not a failure, but a narrow design point.** It works for large,
densely activated models from careful quantization (evidence: NVIDIA's
235B). It fails at mid size, third-party quantization, German output and
long generations — which is precisely our combination.

**Before adopting any new 4-bit checkpoint**, run the three prompts through
a real AIfred session, not the probe. A clean probe run says nothing about
behaviour under the full persona.

## Open questions

- **FP8 as a middle ground** is measured on the 27B
  (`v100-skinny/FP8-EVALUATION.md`): prefill +31 %, decode −20 %, 29 vs
  21 GiB. Whether it removes the language leakage was **not** tested under
  the full persona — that measurement used the probe.
- **FP8 is blocked for Flash-Next-180B** (fp16 dequant on Turing).
- **A fair format comparison** would be NVFP4 against Unsloth's own
  Q4_K_XL. So far we compared 4 bits against 8 bits — that 8 bits wins is
  no surprise.
