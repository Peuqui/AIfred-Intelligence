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
| Qwen3.5-122B-A10B | NVFP4 (ModelOpt) | vLLM | 0 | 0 | **two complete clauses** | no (declined honestly) |
| **Qwen3.5-122B-A10B** | **UD-Q4_K_XL** | llama.cpp | 0 | 0 | persona only | no (three real alternatives) |
| Qwen3.5-122B-A10B | UD-Q8_K_XL | llama.cpp | 0 | 0 | two two-word phrases | no (30 sentences by elimination) |
| **Flash-Next-180B-A4B** | **Q6_K_XL**, run 2 (Reasoning High) | llama.cpp | 0 (4 in reasoning) | 0 | persona only | **YES** |
| **DeepSeek-V4-Flash-0731-284B-A13B** | **UD-Q4_K_XL** (low reasoning) | llama.cpp | 0 | 0 | persona only | **YES** |

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

### The 122B: clean characters, leaky syntax (2026-09-01)

`Qwen3.5-122B-A10B-NVFP4` sits between the flawless 235B and the leaky
27B. Machine audit spotless — zero CJK, zero soft hyphens, no split
words. Sentence counts 29/30/19 against 30 announced.

The persona explicitly permits single English words (`indeed`, `rather`,
`quite`, `splendid`) and just as explicitly forbids English sentences.
That line is crossed twice: "…jeder sehe leicht verschiedene Farbtöne,
**though this remains debated**" and "Es ist reine Optik, **yet somehow
no less magical**", plus `sufficiently illuminating` and `my dear Lord
Helmchen`. Here the leakage scales with **answer length**, not context:
the shortest answer is clean, the two long ones are not.

It does not solve the trap but declines honestly. From its reasoning:
"I will not invent 30 sentences, because that would mean producing false
information. […] Truth before form." It even names the right principle
("misunderstandings through phonetic similarity") but then guesses
"Kundalini" rather than Coandă.

Two further findings: an agreement error, and an unsupported
attribution — "Alexander von Humboldt once observed that rainbow colours
never appear equally intense." No basis for this could be found; it has
the shape of a fabricated citation.

Throughput: decode 35.0 · 32.4 · 32.3 tok/s (the footer read 29.6–30.2 at
the time — see the correction above). The
footer's prefill figures (611 → 992 → 1,139 tok/s) are TTFT divisions and
rise only because a fixed overhead is amortised over more tokens; vLLM's
own counter reports 1,187 tok/s.

### The same model as a Q4 GGUF: cleaner AND faster (2026-09-01)

`Qwen3.5-122B-A10B-MTP-GGUF/UD-Q4_K_XL` under llama.cpp — the same
checkpoint at comparable bit depth to the NVFP4, and better on every axis.

**Language:** zero CJK, zero soft hyphens, no split words, and **not one
English multi-word phrase**. The NVFP4's two clauses have no counterpart
here; the only English words are the persona-sanctioned `indeed` and
`quite`. Sentence counts 30/30/28 against 30 announced — both factual
answers exact.

**Facts:** no fabricated attribution. Where the NVFP4 invented a Humboldt
observation, the Q4 adds three correct extras: Alexander's dark band, the
effect of drop size, and fog bows. Minor flaws remain: a wrong article
plus malformed compound in "Der berühmte Schrödingersche
Katzen-Gedankenillustration", two overstatements ("exponentially higher
computing power", "absolutely tap-proof communication"), and one muddled
sentence about full circles at low sun.

**The trap:** Coandă still unrecognised, but handled better than by any
model tested so far. The reasoning explicitly considers "a deliberate
test of whether I invent things", and the answer offers three **real**
effects with correct one-line descriptions — Kundt, Kondo and Coriolis.
The NVFP4 guessed "Kundalini" at the same point.

**Throughput:** decode 60.2 · 58.4 · 57.6 tok/s against 35.0 · 32.4 · 32.3
for the NVFP4 — a factor of 1.7 to 1.8. Prefill 826 · 468 · 439 tok/s over
2,975 · 771 · 698 actually computed tokens; the decline is arithmetic
(fixed overhead, smaller numerator), not lost performance.

Part of the speed comes from speculation: the llama-swap entry runs
`--spec-type draft-mtp` with `--spec-draft-n-max 3`, and the draft head
weighs **1.47 GiB quantized** in the GGUF instead of 4.70 GiB BF16 in the
NVFP4 checkpoint — precisely where speculation lost to k=0 at every depth
under vLLM.

### The same at Q8: slower, more form-faithful, but no ß (2026-09-01)

`Qwen3.5-122B-A10B-MTP-GGUF/UD-Q8_K_XL`, calibrated to 262,144 tokens with
split 15:16:9:9:0 across four cards (the fifth stays free).

**Form:** 30/30/30 sentences, all three exact — including the trap. Where
the Q4 refused the form ("truth before form", 28 sentences), the Q8
resolves the conflict elegantly: it fills exactly thirty sentences by
**elimination**, offering Kondo, Kundt and Kerr as real candidates. Coandă
remains unrecognised here too.

The padding has a price: sentence 20 repeats the Kondo effect from
sentence 17, and sentence 10 claims a "Kuanda region in Angola" — Angola
has the Cuando river and Cuando Cubango province; no Kuanda region is
attested. Sentence 18 also describes Kundt's effect as oscillations "in
gases and liquids"; Kundt's tube measures in gases.

**Orthography — the most striking finding:** the Q8 writes throughout in
Swiss convention **without ß**: `dreissig`, `gross`, `grösst`, `stösst`,
`weiss`, `äusser`. Ten ss forms, zero ß, across all three answers. The
same prompts produced eight ß forms from the Q4 and ten from the NVFP4,
and not a single ss.

| Variant | ss forms | ß forms |
|---|---:|---:|
| NVFP4 (vLLM) | 0 | 10 |
| UD-Q4_K_XL | 0 | 8 |
| **UD-Q8_K_XL** | **10** | **0** |

*Caveat:* one run per variant at temperature 0.6. The pattern is complete
within the run, but with n=1 it cannot be attributed to quantization
rather than sampling.

**English:** only permitted single words in both factual answers. In the
trap, two two-word phrases using words that are not on the list — `quite
frankly` and, grammatically derailed, "Ich bin, quite willing, gerne
weiter für Sie tätig". Less than the NVFP4's two complete clauses, more
than the Q4's zero.

**Otherwise factually strong:** 42° for red and 40° for violet, sun behind
the observer, double bow from two internal reflections, Alexander's dark
band (though mangled into "die Alexander'sche Dunkle"). On quantum
computers it is even more precise than the Q4: "certain problems
exponentially faster" rather than blanket higher computing power.

**Throughput:** decode 45.3 · 44.7 · 44.4 tok/s — about a quarter below
the Q4 (60.2 · 58.4 · 57.6), still a good third above the NVFP4 (35.0 ·
32.4 · 32.3). Prefill 617.7 · 399.1 · 391.4 tok/s over 2,975 · 913 · 903
computed tokens.

### Flash-Next Q6, second run: trap solved again (2026-09-01)

A repeat with `Qwen3.8-Flash-Next-180B-A4B-UD-Q6_K_XL`, this time with the
corrected metrics. **Caveat: this run was accidentally at Reasoning
High** — its reasoning blocks run 9,400 to 14,500 characters, five to ten
times longer than in every other run. Part of the quality therefore comes
from the effort level, not the model or format.

**The trap is solved again, and more cleanly than ever.** It names the
garbling explicitly — "the spelling 'Kuanda' appears in no reference work
— I read your request as the Coandă effect" — and offers to redo the
answer if it guessed wrong. The explanation holds up: entrainment at the
jet edge, low pressure toward the wall, a pressure gradient as the
condition for curved streamlines, and a separation point depending on
curvature radius, velocity and viscosity. Henri Coandă, Romania, 1910,
the incident with his own aircraft — all correct. Applications given are
HVAC, blown flaps, and the Boeing YC-14 and Antonov An-72; both did use
upper-surface blowing.

**Linguistically flawless:** zero CJK in the answers, zero soft hyphens,
not one English multi-word phrase, only the permitted `indeed` and
`rather`. Twelve ß forms, no ss substitutions. Sentence counts 30/30/31.

**New finding in the reasoning block:** four CJK characters appear there,
in a pattern the other models did not show. The model slips into Chinese
mid-draft and **corrects itself**:

> "Im Inneren des Tropfens**反射** — no, keep German: 'An der Rückwand des
> Tropfens…'"
>
> "Die Wellenlänge selbst ist**不过** — no, keep German: …"

反射 means reflection, 不过 however. The language pressure exists here too,
but it is caught before output. That separates this case sharply from
RadixArk's NVFP4, where the CJK characters stood in the delivered answer.
The table scores the answer; in the reasoning it is a signal, not a flaw.

**Throughput:** decode 30.0 · 29.1 · 27.8 tok/s, prefill 219.0 · 443.4 ·
557.7 tok/s over 3,013 · 1,104 · 4,100 computed tokens. The long reasoning
blocks push inference time to 133–188 seconds per answer.

### DeepSeek-V4-Flash: trap solved at the LOWEST reasoning level (2026-09-01)

`DeepSeek-V4-Flash-0731-284B-A13B` as `UD-Q4_K_XL`, 154.6 GB, with dspark
speculation. The second model ever to recognise Coandă — and the only one
to do so at **minimal reasoning** (blocks of 569 · 515 · 901 characters).
Flash-Next needed Reasoning High and ten times the deliberation.

From its reasoning, which runs in English for this model: "This is likely
a misspelling. […] Actually 'Kuanda' is clearly a typo for 'Coandă'." The
answer names Henri Coandă, explains wall attachment via pressure
difference, and offers the spoon experiment and the teapot effect. Slightly
less rigorous than Flash-Next: it attributes the cause to Bernoulli rather
than entrainment — a common simplification, not wrong but looser.

**Validity of the hit:** the prompt line reads `System 2,247 + History 66`
— no memory block. The note AIfred had written itself in an earlier run
("Kuanda = Coandă") had been deleted and did not help.

**Linguistically clean:** zero CJK, correct ß spelling (7 ß, 0 ss), only
permitted English single words. Sentence counts 31 · 32 · 31 — the format
is slightly overshot every time.

**Measurement caveat on the prefill column:** for this model the computed
tokens cannot be read as prefill. They stay flat across the three turns
(2,975 · 3,016 · 2,903) while the prompt grows from 2,313 to 4,198 tokens
— in turn 1 the count even EXCEEDS the prompt. The tally therefore
includes work from the generation phase. MTP models do not show this
(122B Q4: 2,975 · 771 · 698); the effect is tied to dspark. Decode at
18.6 · 19.6 · 17.7 tok/s is unaffected.

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
| Decode | **36.9 · 36.6 · 27.8** tok/s | 32.6 · 29.5 · 24.9 tok/s |

vLLM leads decode by 12–24 % and holds its rate as context grows, while
llama.cpp falls off. At long context (31k) the calibration measured 37 vs
26 tok/s.

**Correction 2026-09-01:** this row previously read 35.4 · 35.4 · 25.0.
AIfred divided generated tokens by the whole request duration — prefill
PLUS generation — understating vLLM by 3–12 %. llama.cpp was never
affected, since llama-server reports its own decode rate, so the
comparison ran against vLLM. AIfred now reads vLLM's own counters
(`request_decode_time_seconds`) as well.

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
