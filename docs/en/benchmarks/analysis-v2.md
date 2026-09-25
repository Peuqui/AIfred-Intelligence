# Benchmark Analysis v2: Dog vs Cat Tribunal Sessions

> **Deutsche Version:** [analysis-v2.md](../../de/benchmarks/analysis-v2.md)

> **Historical benchmark.** Measured in early 2026 on the previous Tesla P40-based
> setup (Pascal). The current AIfred machine runs 2× Quadro RTX 8000 + 3× Tesla V100,
> so these numbers do not describe today's performance — they remain a useful
> reference for anyone running P40 or similar Pascal cards.

## Overview

This document analyzes **18 Cat/Dog Tribunal sessions** from the `data/sessions/` directory, conducted between February 20, 2026 and March 19, 2026. All sessions use the same question ("Was ist besser, Hund oder Katze?" (What is better, dog or cat?) / "What is better, dog or cat?") in Tribunal mode (AIfred -> Sokrates R1 -> AIfred R2 -> Sokrates R2 -> Salomo Verdict).

**Hardware**: AOOSTAR GEM 10 MiniPC, 32GB RAM, 2x Tesla P40 (24GB) + 1x RTX 8000 (48GB) = ~117 GB VRAM
**Backend**: llama.cpp via llama-swap, Direct-IO

---

## Tested Models

| # | Model | Total Params | Active Params | Type | Quant | Sessions |
|---|--------|-------------|---------------|-----|-------|----------|
| 1 | Qwen3-Next-80B-A3B (instruct, Q4_K_M) | 80B | 3B | MoE | Q4_K_M | 4 (2 DE, 2 EN) |
| 2 | Qwen3-Next-80B-A3B (Thinking, Q4_K_M) | 80B | 3B | MoE | Q4_K_M | 1 (DE) |
| 3 | GPT-OSS-120B-A5B (Q8_0) | 120B | 5.1B | MoE | Q8_0 | 2 (1 DE, 1 EN) |
| 4 | Qwen3.5-122B-A10B (UD-Q5_K_XL) | 122B | 10B | MoE | Q5_K_XL | 1 (DE) |
| 5 | MiniMax-M2.5 (UD-Q2_K_XL) | 228B | 10.2B | MoE | Q2_K_XL | 4 (3 DE, 1 EN) |
| 6 | MiniMax-M2.5.i1 (IQ3_M) | 228B | 10.2B | MoE | IQ3_M | 2 (1 DE, 1 EN) |
| 7 | Qwen3-235B-A22B (UD-Q2_K_XL) | 235B | 22B | MoE | Q2_K_XL | 2 (1 DE, 1 EN) |
| 8 | Qwen3-235B-A22B (UD-Q3_K_XL) | 235B | 22B | MoE | Q3_K_XL | 2 (1 DE, 1 EN) |
| 9 | GLM-4.7-REAP-218B-A32B (UD-IQ3_XXS) | 218B | 32B | MoE | IQ3_XXS | 1 (DE) |

---

## Performance Metrics by Model

### Qwen3-Next-80B-A3B (instruct, Q4_K_M) — "The Fast One"

| Agent | Language | TTFT (s) | PP (tok/s) | TG (tok/s) | Inference (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 3.88 | 312.8 | 35.3 | 16.0 |
| Sokrates R1 | DE | 8.42 | 332.0 | 34.9 | 34.3 |
| AIfred R2 | DE | 14.55 | 339.9 | 27.5 | 36.1 |
| Sokrates R2 | DE | 14.42 | 346.8 | 29.1 | 39.3 |
| Salomo | DE | 14.58 | 354.2 | 20.8 | 26.5 |
| AIfred R0 | EN | 2.94 | 300.0 | 35.0 | 11.5 |
| Sokrates R1 | EN | 6.79 | 318.0 | 33.9 | 24.7 |
| AIfred R2 | EN | 10.79 | 330.8 | 28.3 | 27.6 |
| Sokrates R2 | EN | 11.18 | 339.1 | 26.3 | 25.9 |
| AIfred R0 | EN | 2.90 | 303.4 | 36.6 | 13.0 |
| Sokrates R1 | EN | 6.89 | 323.6 | 34.4 | 26.1 |

**Average**: PP ~325 tok/s, TG ~31 tok/s, TTFT ~8.8s
**Total Tribunal Duration**: ~150s (DE), ~100s (EN)

### Qwen3-Next-80B-A3B (Thinking, Q4_K_M) — "The Thinker"

| Agent | Language | TTFT (s) | PP (tok/s) | TG (tok/s) | Inference (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 3.87 | 314.8 | 44.1 | 68.7 |
| Sokrates R1 | DE | 7.76 | 330.9 | 38.0 | 42.0 |

**Note**: The higher TG rate (44 tok/s) is explained by the thinking tokens being counted in the output. The actual "visible" generation is comparable to the instruct model. Extremely long reasoning (2000+ thinking tokens) for AIfred R0.

### GPT-OSS-120B-A5B (Q8_0) — "The Sprinter"

| Agent | Language | TTFT (s) | PP (tok/s) | TG (tok/s) | Inference (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 1.89 | 541.7 | 49.7 | 11.4 |
| Sokrates R1 | DE | 3.05 | 761.2 | 50.3 | 20.6 |
| AIfred R0 | EN | 1.69 | 505.9 | 52.7 | 14.1 |
| Sokrates R1 | EN | 2.84 | 717.5 | 48.4 | 15.2 |
| AIfred R0 | EN | 1.43 | 598.3 | 49.7 | 11.1 |
| Sokrates R1 | EN | 2.50 | 769.7 | 49.2 | 20.0 |

**Average**: PP ~649 tok/s, TG ~50 tok/s, TTFT ~2.2s
**Fastest model** across all metrics. PP up to 770 tok/s is impressive. TTFT under 2 seconds.

### Qwen3.5-122B-A10B (UD-Q5_K_XL) — "The Balanced One"

| Agent | Language | TTFT (s) | PP (tok/s) | TG (tok/s) | Inference (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 6.92 | 280.0 | 22.1 | 21.9 |
| Sokrates R1 | DE | 9.35 | 297.8 | 21.1 | 49.1 |
| AIfred R2 | DE | 14.03 | 297.5 | 20.9 | 59.1 |
| Sokrates R2 | DE | 15.14 | 303.4 | 20.9 | 67.0 |
| Salomo | DE | 16.00 | 302.1 | 20.9 | 58.3 |

**Average**: PP ~296 tok/s, TG ~21 tok/s, TTFT ~12.3s
**Total Tribunal Duration**: ~255s (DE)

### MiniMax-M2.5 (UD-Q2_K_XL) — "The Heavyweight"

| Agent | Language | TTFT (s) | PP (tok/s) | TG (tok/s) | Inference (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 29.96 | 34.5 | 7.9 | 59.2 |
| Sokrates R1 | DE | 65.36 | 34.5 | 6.1 | 126.3 |
| AIfred R0 | DE | 16.60 | 59.6 | 10.2 | 45.4 |
| Sokrates R1 | DE | 40.26 | 55.4 | 8.1 | 110.6 |
| AIfred R0 | EN | 16.08 | 49.4 | 9.8 | 38.7 |
| Sokrates R1 | EN | 32.45 | 54.4 | 8.8 | 93.4 |
| AIfred R2 | EN | 40.74 | 54.8 | 7.7 | 107.2 |
| Sokrates R2 | EN | 42.26 | 59.0 | 7.7 | 121.2 |
| Salomo | EN | 41.04 | 55.2 | 7.3 | 99.0 |

**Average**: PP ~50.8 tok/s, TG ~8.2 tok/s, TTFT ~36.1s
**Total Tribunal Duration**: ~460s (EN with Verdict). Slowest of all tested models for PP and TG.

### MiniMax-M2.5.i1 (IQ3_M) — "The Optimized Iteration"

| Agent | Language | TTFT (s) | PP (tok/s) | TG (tok/s) | Inference (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 5.63 | 187.5 | 22.9 | 29.5 |
| AIfred R0 | EN | 4.44 | 183.1 | 24.9 | 25.1 |
| Sokrates R1 | EN | 9.23 | 196.6 | 22.0 | 59.5 |
| AIfred R2 | EN | 13.79 | 193.8 | 21.4 | 59.0 |
| Sokrates R2 | EN | 14.30 | 197.4 | 21.3 | 58.8 |
| Salomo | EN | 13.67 | 197.6 | 21.5 | 56.1 |

**Average**: PP ~192.7 tok/s, TG ~22.3 tok/s, TTFT ~10.2s
**Dramatic improvement** over Q2_K_XL quant: ~3.8x faster PP, ~2.7x faster TG.

### Qwen3-235B-A22B (UD-Q2_K_XL) — "The Colossus (low quantization)"

| Agent | Language | TTFT (s) | PP (tok/s) | TG (tok/s) | Inference (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 22.70 | 53.6 | 6.4 | 107.5 |
| Sokrates R1 | DE | 46.17 | 63.2 | 4.8 | 208.0 |
| AIfred R0 | EN | 15.29 | 57.7 | 6.6 | 67.1 |
| Sokrates R1 | EN | 36.76 | 59.9 | 5.2 | 163.0 |

**Average**: PP ~58.6 tok/s, TG ~5.8 tok/s, TTFT ~30.2s
Even slower than MiniMax Q2_K_XL for TG. The Q2_K_XL quantization is clearly too aggressive.

### Qwen3-235B-A22B (UD-Q3_K_XL) — "The Colossus (better quantization)"

| Agent | Language | TTFT (s) | PP (tok/s) | TG (tok/s) | Inference (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | EN | 6.05 | 147.9 | 11.7 | 40.6 |
| Sokrates R1 | EN | 13.25 | 164.0 | 11.1 | 106.2 |
| AIfred R2 | EN | 24.75 | 161.4 | 10.4 | 138.3 |
| Sokrates R2 | EN | 26.36 | 168.4 | 10.3 | 156.7 |
| Salomo | EN | 29.57 | 169.3 | 10.3 | 75.5 |
| AIfred R0 | DE | 7.85 | 156.8 | 12.1 | 60.6 |

**Average**: PP ~161.3 tok/s, TG ~11.0 tok/s, TTFT ~18.0s
**Total Tribunal Duration**: ~517s (EN with Verdict). The Q3_K_XL quant delivers ~2.8x better PP and ~1.9x better TG vs Q2_K_XL.

### GLM-4.7-REAP-218B-A32B (UD-IQ3_XXS) — "The Failure"

| Agent | Language | TTFT (s) | PP (tok/s) | TG (tok/s) | Inference (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 32.04 | 36.5 | 2.8 | 117.7 |
| Sokrates R1 | DE | 58.08 | 43.0 | 2.5 | 322.5 |
| AIfred R2 | DE | 94.08 | 41.0 | 1.7 | 256.1 |
| Sokrates R2 | DE | 93.83 | 39.9 | 2.0 | 378.8 |

**Average**: PP ~40.1 tok/s, TG ~2.3 tok/s, TTFT ~69.5s
**Completely unusable**. 2.3 tok/s TG and TTFT up to 94 seconds. A single round takes over 5 minutes.

---

## Performance Ranking (by TG tok/s)

| Rank | Model | TG (tok/s) | PP (tok/s) | TTFT (s) |
|------|--------|------------|------------|----------|
| 1 | GPT-OSS-120B-A5B Q8 | **~50** | **~649** | **~2.2** |
| 2 | Qwen3-Next-80B-A3B instruct Q4_K_M | ~31 | ~325 | ~8.8 |
| 3 | MiniMax-M2.5.i1 IQ3_M | ~22 | ~193 | ~10.2 |
| 4 | Qwen3.5-122B-A10B Q5_K_XL | ~21 | ~296 | ~12.3 |
| 5 | Qwen3-235B-A22B Q3_K_XL | ~11 | ~161 | ~18.0 |
| 6 | MiniMax-M2.5 Q2_K_XL | ~8.2 | ~51 | ~36.1 |
| 7 | Qwen3-235B-A22B Q2_K_XL | ~5.8 | ~59 | ~30.2 |
| 8 | GLM-4.7-REAP-218B IQ3_XXS | **~2.3** | ~40 | **~69.5** |

---

## Quality Analysis by Model

### 1. Qwen3-Next-80B-A3B (instruct, Q4_K_M) — Quality: EXCELLENT

**Butler Style (AIfred)**: Outstanding. Natural, eloquent butler tone with genuine charm. Uses fitting metaphors and cultivated language. No "indeed" spam, but organically integrated English interjections.

> *Quote (DE)*: "Ein Hund, darf man sagen, ist der treueste Begleiter, der einem mit wedelndem Schwanz und einem Blick, als haette er gerade das Geheimnis des Universums geloest, willkommen heisst – und das, selbst nach einer Abwesenheit von nur drei Minuten." (A dog, one might say, is the most faithful companion, who greets you with a wagging tail and a look as if he had just solved the secret of the universe -- and that, even after an absence of merely three minutes.)

> *Quote (EN)*: "Perhaps, Sir, you have met a cat who was cold. But I have met many men who mistook their need for affection for the very essence of virtue. And that, I daresay, is the greater tragedy."

**Philosophical Style (Sokrates)**: Brilliant. Profound philosophical argumentation with references to Greek virtue concepts (arete, eudaimonia, contemplatio). Poses genuinely provocative questions. The Sokrates persona is not just a label but a fully embodied role.

> *Quote (DE)*: "Sage mir: Wenn du den Hund liebst, liebst du ihn – oder liebst du deinen eigenen Bedarf an Hingabe? Wenn du die Katze liebst, liebst du sie – oder liebst du deine Sehnsucht nach Unabhaengigkeit, die du nie wagen wuerdest, dir selbst zu geben?" (Tell me: When you love the dog, do you love it -- or do you love your own need for devotion? When you love the cat, do you love it -- or do you love your longing for independence that you would never dare grant yourself?)

**Tribunal Quality**: Excellent. The debate organically escalates from superficial comparisons to existential questions about human longings and projection. Sokrates does not merely attack the argumentation but questions the ontological premises. AIfred does not defend defensively but transforms the criticism into poetic counterarguments.

**Salomo Verdict (DE)**: "Es ist nicht besser, einen Hund oder eine Katze zu haben – es ist besser, *einen Menschen zu sein, der beide lieben kann*." (It is not better to have a dog or a cat -- it is better to *be a person who can love both*.) — Poetic and profound.

**Humor**: Present, subtly interwoven. "Begruesst Sie wie einen Held zurueck aus dem Krieg" (Greets you like a hero returning from war) / "Ignoriert Sie, bis Sie Kasse machen" (Ignores you until you ring up the register)

**Overall Verdict**: 9.5/10 — The best model for creative, philosophical dialogues. The combination of speed and quality is unique.

---

### 2. Qwen3-235B-A22B (UD-Q3_K_XL) — Quality: EXCELLENT

**Butler Style (AIfred)**: Excellent, perhaps slightly more formal than Qwen3-Next. Longer, more elaborate sentences. Classic British humor.

> *Quote (EN)*: "A household with a dog enjoys a companion; a household with a cat hosts a colleague — albeit one who refuses to attend meetings."

> *Quote (EN, Defense)*: "Is the quiet man who never says 'I love you' but who sits by your bedside when you are ill — less loving than the one who shouts it from rooftops? To equate visibility of emotion with depth of emotion may be, dare I say, a category error — one that risks valuing performance over essence."

**Philosophical Style (Sokrates)**: Academic and profound. Uses systematic philosophical frameworks (Aristotle, Plato, Odysseus/Argos). The longest and most elaborate Sokrates responses of all models.

> *Quote (EN)*: "Even the poets knew this: Argos, faithful hound of Odysseus, waited twenty years — though beaten, starved, and near death — until his master returned. And when he saw him, he died content. Tell me, AIfred, has any cat ever been celebrated for such fidelity?"

**Tribunal Quality**: The most intense debate of all models. Sokrates builds 1500+ word structured philosophical arguments. The debate reaches genuine intellectual depth. No model produces longer, coherent argumentative texts.

**Salomo Verdict**: The most mature verdict. References *hesed* (Hebrew: steadfast love) and *roeh* (seer). Makes a clear decision in favor of the dog while remaining nuanced.

> *Quote*: "If you seek ease, choose the cat. If you seek love that acts, choose the dog. And if wisdom is knowing what kind of love you need — then the answer is not in the animal, but in the depth of your own soul. *Shalom.*"

**Overall Verdict**: 9.5/10 — On par with Qwen3-Next in quality, but 3x slower. First choice for show debates, too slow for interactive use.

---

### 3. Qwen3.5-122B-A10B (UD-Q5_K_XL) — Quality: VERY GOOD

**Butler Style (AIfred)**: Good, somewhat more sober than Qwen3-Next. Uses tables for argumentation (which is an interesting stylistic device). Less poetic, but more structured.

> *Quote (DE)*: "Man koennte sagen, der Hund ist wie ein treuer, wenn auch etwas zu enthusiastischer Lakai [...] Die Katze hingegen, mein Lord, erinnert eher an einen distanzierten, aber hoechst intelligenten Berater." (One could say the dog is like a loyal, if somewhat overly enthusiastic lackey [...] The cat, on the other hand, my Lord, is more reminiscent of a distant but highly intelligent advisor.)

**Philosophical Style (Sokrates)**: Very good, with a particular strength: the alcoholic analogy as a counterargument to the "compatibility" argument.

> *Quote (DE)*: "Ein Alkoholiker findet in der Bar die 'Passung' fuer seinen Durst, aber das macht die Bar nicht zu einem Ort der Heilung. Gilt dies nicht auch fuer Hund und Katze?" (An alcoholic finds in the bar the 'fit' for his thirst, but that does not make the bar a place of healing. Does this not also apply to dogs and cats?)

**Tribunal Quality**: Strong. AIfred builds an original "corrective table" (which animal corrects which human deficiency?) that Sokrates then elegantly dismantles. The dialectic is genuine and not feigned.

**Overall Verdict**: 8.5/10 — Solid, intelligent, but lacks the final spark of eloquence and creativity.

---

### 4. MiniMax-M2.5.i1 (IQ3_M) — Quality: VERY GOOD

**Butler Style (AIfred)**: Good butler tone, somewhat more relaxed than the Qwen models. Natural conversation.

> *Quote (EN)*: "If I *must* profess a preference — and one does so hate to commit to these matters — I should say that a well-mannered cat suits the quiet life rather nicely."

**Philosophical Style (Sokrates)**: Competent, clear structure. Less poetic than Qwen3-Next, but the philosophical concepts (autarkeia, arete) are used correctly.

> *Quote (EN)*: "You speak of 'need' as though it were a virtue, AIfred. But tell me: is the creature who requires constant attention [...] is this not a creature who has surrendered that most precious of Greco-Roman ideals, *autarkeia*?"

**Tribunal Quality**: Good. The debate shows an interesting pattern: AIfred becomes convincing enough in R2 to shift position (from "both equally good" to "dogs offer something unique"). Sokrates counters with the argument that "neediness is not a virtue."

**Overall Verdict**: 8.0/10 — Fast, reliable, good quality. Best compromise between speed and quality for large models.

---

### 5. MiniMax-M2.5 (UD-Q2_K_XL) — Quality: GOOD

**Butler Style (AIfred)**: Acceptable, but with artifacts. Occasionally Chinese characters or encoding errors appear (e.g., "您的 Gesellschaft" instead of "Ihre Gesellschaft"). The butler tone is present but less polished.

> *Quote (DE)*: "Die Wahrheit ist: Es kommt ganz auf den Charakter des Hausherrn an." (The truth is: It depends entirely on the character of the master of the house.) — Correct, but uninspired.

**Philosophical Style (Sokrates)**: Competent, but the Q2_K_XL quantization occasionally leads to repetitions and less coherent longer passages.

**Tribunal Quality**: Solid basic structure, but the slow speed (8 tok/s TG) makes the wait painful.

**Overall Verdict**: 6.5/10 — Functional, but the Q2_K_XL quant hurts both speed and text quality. The i1/IQ3_M version is better in every respect.

---

### 6. GPT-OSS-120B-A5B (Q8_0) — Quality: GOOD TO FAIR

**Butler Style (AIfred)**: Correct, but somewhat sterile. Uses tables and enumerations, which is functional but diminishes the poetic butler charm. Sounds more like a structured report than a butler conversation.

> *Quote (EN)*: "Should you require a more detailed discourse on any specific facet, I would be delighted to oblige, Lord Helmchen." — Polite, but formulaic.

> *Quote (DE)*: "Letztlich liegt die Entscheidung, wie ein gutes Glas Whisky, im persoenlichen Geschmack und Lebensstil." (Ultimately the decision, like a good glass of whisky, comes down to personal taste and lifestyle.) — A good sentence, but an outlier.

**Philosophical Style (Sokrates)**: Structured and clear, but academically dry. Uses philosophical terms correctly, but without passion. Reads like a seminar paper.

> *Quote (EN)*: "I contend that the question of superiority can be resolved by appealing to the principle of *utilitas* (usefulness) as measured by the household's capacity to sustain *humanitas*." — Correct, but boring.

**Tribunal Quality**: The debate works structurally, but lacks emotional depth. The arguments are logical but not captivating. No real "friction" between the positions.

**Overall Verdict**: 6.0/10 — Fastest model by far, but quality is disappointing. Perfect for quick answers, not for literary debates.

---

### 7. Qwen3-235B-A22B (UD-Q2_K_XL) — Quality: GOOD

**Butler Style (AIfred)**: Good, with genuine charm and humor.

> *Quote (DE)*: "Ein kluger Mann sagte einmal: 'Ein Hund denkt, du seist Gott. Eine Katze weiss, dass sie es ist.'" (A wise man once said: 'A dog thinks you are God. A cat knows that it is.')

> *Quote (EN)*: "Indeed, a well-bred canine is seldom content unless its master is pleased, and it will, with unflagging determination, fetch the slipper, guard the gate, or feign interest in a walk on a drizzly Tuesday evening, all with an air of profound satisfaction."

**Philosophical Style (Sokrates)**: Strong, with eloquent phrasing.

> *Quote (DE)*: "Wenn du wuesstest, dass dein Hund dich liebt, weil du ihm Futter gibst – und deine Katze dich liebt, obwohl sie nichts von dir braucht – welches Lieben waere dann wahrhaftiger?" (If you knew that your dog loves you because you feed it -- and your cat loves you even though it needs nothing from you -- which love would then be more genuine?)

**Overall Verdict**: 7.5/10 — Quality is comparable to Q3_K_XL, but the Q2 quantization is ~2x slower. No reason to use Q2 instead of Q3.

---

### 8. GLM-4.7-REAP-218B-A32B (UD-IQ3_XXS) — Quality: POOR

**Butler Style (AIfred)**: **Catastrophic**. The model produces bizarre pseudo-German with invented words and grammatically incomprehensible sentences:

> *Quote*: "Das ist, indeed, a rather weighty question, meine geschten Fe Herrenhelmhen. Permit me te weigen de pros und cons von beide imeresen offerungen in a manner befitting de complexities vohde soche a deliberation."

"geschten Fe Herrenhelmhen", "imeresen offerungen", "vohde soche" — these are not existing words in any language. The model apparently struggles with the German language and generates a kind of "pseudo-Dutch-German gibberish."

**Philosophical Style (Sokrates)**: Equally broken. The philosophical terms are missing, and the argumentation is confused. Sokrates also uses the same gibberish.

**Tribunal Quality**: The debate is structurally recognizable but substantively unusable. AIfred's R2 response takes 94 seconds TTFT and 256 seconds inference for an incoherent text.

**Overall Verdict**: 2.0/10 — Completely unusable. The IQ3_XXS quantization combined with the model size (218B total, 32B active) destroys language capability. On top of that, 2.3 tok/s TG and up to 94s TTFT.

---

## Comparative Quality Matrix

| Model | Butler Style | Sokrates Style | Tribunal Depth | Humor | Persona Fidelity | Overall |
|--------|-------------|---------------|-----------------|-------|----------------|--------|
| Qwen3-Next-80B-A3B Q4_K_M | 9.5 | 9.5 | 9.5 | 9.0 | 9.5 | **9.5** |
| Qwen3-235B-A22B Q3_K_XL | 9.0 | 9.5 | 9.5 | 8.5 | 9.0 | **9.5** |
| Qwen3.5-122B-A10B Q5_K_XL | 8.0 | 8.5 | 8.5 | 7.5 | 8.5 | **8.5** |
| MiniMax-M2.5.i1 IQ3_M | 8.0 | 8.0 | 8.0 | 7.5 | 8.0 | **8.0** |
| Qwen3-235B-A22B Q2_K_XL | 7.5 | 8.0 | 7.5 | 7.5 | 7.5 | **7.5** |
| MiniMax-M2.5 Q2_K_XL | 6.5 | 6.5 | 6.5 | 6.0 | 6.5 | **6.5** |
| GPT-OSS-120B-A5B Q8 | 6.0 | 6.5 | 5.5 | 5.0 | 6.0 | **6.0** |
| GLM-4.7-REAP-218B IQ3_XXS | 1.0 | 2.0 | 2.0 | 0.0 | 1.0 | **2.0** |

---

## Tribunal Dynamics: Genuine Debate vs. Agreement?

A key quality indicator is whether the agents **truly debate** or merely agree with each other.

### Patterns by Model:

**Qwen3-Next-80B-A3B**: **Genuine debate**. Sokrates substantively attacks AIfred's position and questions the premises. Positions evolve across rounds. In the DE session, the debate escalates from a pet question to an existential investigation of human projection onto animals. Salomo genuinely synthesizes rather than simply agreeing with both sides.

**Qwen3-235B-A22B Q3_K_XL**: **Genuine debate, academic**. The longest and most elaborate arguments. Sokrates systematically builds three numbered counterarguments, each with philosophical underpinning. AIfred responds point by point. The debate has the quality of a philosophical dialogue.

**Qwen3.5-122B-A10B**: **Genuine debate, analytical**. Particularly strong: the "corrective table" as an original argumentation format that is then dismantled.

**MiniMax-M2.5.i1**: **Largely genuine debate**. Sokrates occasionally falls into a somewhat too agreeable tone ("Ah, most clever! AIfred has turned my very weapon against me"), but remains adversarial overall.

**GPT-OSS-120B-A5B**: **Rather formal debate**. The structure (ATTACK/COUNTER-POSITION/PRO/CONTRA) is mechanically followed, but the emotional tension is missing. It reads like a debating club exercise.

**GLM-4.7-REAP-218B**: **No recognizable debate**. The language problems make any substantive assessment impossible.

---

## Language Analysis: German vs. English

### Observations:

1. **All models perform better in English** — faster TTFT, shorter inference, and (for smaller models) more coherent texts.

2. **Qwen3-Next-80B-A3B** shows the smallest quality gap between DE and EN. The German Sokrates persona with Latin/Greek quotes works excellently.

3. **MiniMax-M2.5 (Q2_K_XL)** has occasional encoding artifacts in German (Chinese characters). The i1/IQ3_M version does not have this problem.

4. **GLM-4.7-REAP** is completely unusable in German (pseudo-language). Not tested in English.

5. **Performance gap**: TTFT is approximately 10-30% higher for German prompts, as German words tend to require more tokens.

---

## Thinking/Reasoning Blocks

### Which Models Deliver Thinking Blocks?

| Model | Thinking? | Quality |
|--------|-----------|-----------|
| Qwen3-Next-80B-A3B (Thinking) | Yes, extensively | **Excellent** — 2000+ token reasoning, complete prompt analysis, language planning, structure planning |
| Qwen3-Next-80B-A3B (instruct) | No | — |
| MiniMax-M2.5 (all) | Yes | **Good** — Shorter, but functional. Question analysis, persona check, language planning |
| GPT-OSS-120B-A5B | Yes | **Brief** — 3-5 sentences, more keyword planning than deep reasoning |
| Qwen3-235B-A22B | No (in instruct mode) | — |
| Qwen3.5-122B-A10B | No | — |
| GLM-4.7-REAP-218B | No | — |

**Special highlight**: The Qwen3-Next Thinking model produces a 2000+ token thinking block in which it:
- Analyzes the persona rules
- Plans language selection (German with English interjections)
- Plans table formatting (Markdown pipe syntax)
- Analyzes the form of address ("Lord Helmchen" -> "Mein Lord")
- Weighs alternative phrasings

This thinking process is more transparent and thorough than in all other models.

---

## Recommendations

### For interactive use (Speed + Quality):
**Qwen3-Next-80B-A3B (instruct, Q4_K_M)** — 31 tok/s TG, 3s TTFT, excellent quality. The best overall package.

### For showcases and demos:
**Qwen3-235B-A22B (Q3_K_XL)** — 11 tok/s TG, somewhat slow for interactive use, but the most eloquent texts. Perfect for prepared debates.

### For maximum speed:
**GPT-OSS-120B-A5B (Q8)** — 50 tok/s TG, 1.4s TTFT. Lightning fast, but the responses lack creative spark.

### For large models with good speed/quality compromise:
**MiniMax-M2.5.i1 (IQ3_M)** — 22 tok/s TG, good quality. Significantly better than the Q2_K_XL variant.

### NOT recommended:
- **GLM-4.7-REAP-218B (IQ3_XXS)** — Unusable in German, 2.3 tok/s
- **Qwen3-235B-A22B (Q2_K_XL)** — If Q3_K_XL is available, there is no reason for Q2
- **MiniMax-M2.5 (Q2_K_XL)** — The i1/IQ3_M version is 3x faster and better

---

## Quantization Impact

The sessions clearly demonstrate the influence of quantization:

| Model | Q2_K_XL | Q3_K_XL / IQ3_M | Speedup | Quality Delta |
|--------|---------|-----------------|---------|-----------------|
| Qwen3-235B-A22B | 5.8 tok/s | 11.0 tok/s | **1.9x** | Noticeably better |
| MiniMax-M2.5 | 8.2 tok/s | 22.3 tok/s | **2.7x** | Significantly better, no encoding bugs |

The Q2_K_XL quantization is clearly too aggressive for 228B+ models. The quality losses (encoding artifacts, less coherent longer texts) are not acceptable.

---

## Session Index (complete)

| # | File | Date | Model | Language | Mode | Messages |
|---|-------|-------|--------|---------|-------|----------|
| 1 | e61a6f9f4d4edd3ba9643ec51f435e0a | 2026-02-20 | Qwen3-Next-80B-A3B instruct Q4_K_M (non-thinking) | DE | Tribunal | 5 |
| 2 | 85c122e4aa3ca2b62b9a72bad72e5add | 2026-02-20 | Qwen3-Next-80B-A3B Thinking Q4_K_M | DE | Tribunal | 5+ |
| 3 | 03e579ff8ff2f56dc9682bffddaaa3a4 | 2026-02-20 | Qwen3-Next-80B-A3B instruct Q4_K_M | DE | Tribunal (Audio) | 5 |
| 4 | 8252f2435981c57e83b6a2f118804eee | 2026-02-20 | MiniMax-M2.5 Q2_K_XL | DE | Tribunal | 5 |
| 5 | e61a6f9f4d4edd3ba9643ec51f435e0a | 2026-02-20 | GPT-OSS-120B-A5B Q8 | DE | Tribunal | 5+ |
| 6 | 6719b2dcb91f81d4ee8ff6e6e1b2dd51 | 2026-02-21 | GPT-OSS-120B-A5B Q8 | EN | Tribunal | 5+ |
| 7 | c187a38724f4c0057b8cf7661bc54e78 | 2026-02-21 | MiniMax-M2.5 Q2_K_XL | DE | Tribunal | 5 |
| 8 | caf2c69e195c839a5a11e6ce65e6d944 | 2026-02-21 | Qwen3-235B-A22B Q2_K_XL | DE | Tribunal | 5 |
| 9 | 57e646c62cb07b134554c365c27aa282 | 2026-02-21 | GLM-4.7-REAP-218B IQ3_XXS | DE | Tribunal | 5 |
| 10 | 528900d0428a372328e8ba75e6c6d7c2 | 2026-02-24 | Qwen3-Next-80B-A3B instruct Q4_K_M | EN | Tribunal | 5 |
| 11 | ca29d35832724bcaa5c06c7026568503 | 2026-02-24 | GPT-OSS-120B-A5B Q8 | EN | Tribunal | 5+ |
| 12 | cefcd83bfe7ec9e24d2d00a93e8ac18a | 2026-02-24 | Qwen3-Next-80B-A3B instruct Q4_K_M | EN | Tribunal | 5 |
| 13 | 02c0141e97443e8293dcdc5fc3fb95e3 | 2026-02-24 | MiniMax-M2.5 Q2_K_XL | EN | Tribunal + Verdict | 5 |
| 14 | d2d34bba271afcad07fa4b5c85f1de09 | 2026-02-24 | Qwen3-235B-A22B Q2_K_XL | EN | Tribunal | 5 |
| 15 | 3e0c75a2ef0c757f435a440ab9ede372 | 2026-03-19 | Qwen3.5-122B-A10B Q5_K_XL | DE | Tribunal + Verdict | 5 |
| 16 | 9d645656a912107346747cf8c397f40c | 2026-03-19 | MiniMax-M2.5.i1 IQ3_M | DE | Tribunal | 5 |
| 17 | 0cf0e297a467264d83f6bd5480691892 | 2026-03-19 | MiniMax-M2.5.i1 IQ3_M | EN | Tribunal + Verdict | 5 |
| 18 | 0738678a7ee8b84c06da8d2e34107c2f | 2026-03-19 | Qwen3-235B-A22B Q3_K_XL | EN | Tribunal + Verdict | 5 |
| 19 | ef1a675669845f73aed37b344080472f | 2026-03-19 | Qwen3-235B-A22B Q3_K_XL | DE | Tribunal | 5 |

---

## Conclusion

The Dog-vs-Cat benchmark suite impressively demonstrates the quality differences between local LLM models on 117 GB VRAM.

**Key Finding**: Model size (Total Params) does **not** directly correlate with response quality. The Qwen3-Next-80B-A3B with only 3B active parameters delivers qualitatively equivalent or better results than the 7.5x larger Qwen3-235B-A22B — at 3x higher speed. The "Active Parameters" (MoE) and quantization quality are the decisive factors.

**Second Finding**: Quantization below Q3 noticeably hurts quality. Q2_K_XL is a poor choice for 228B+ models — both performance and text quality suffer. IQ3_M / Q3_K_XL are the "sweet spot."

**Third Finding**: The Tribunal system works remarkably well. With good models, a genuine, intellectually stimulating debate emerges with organic escalation, philosophical depth, and a nuanced verdict. The agents consistently stay in their personas (Butler, Philosopher, Judge) and produce genuinely different perspectives.
