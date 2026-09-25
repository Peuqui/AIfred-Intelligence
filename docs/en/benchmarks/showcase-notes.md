# AIfred Tribunal Benchmark: 6 Models Compared (German Inference)

> **Deutsche Version:** [showcase-notes.md](../../de/benchmarks/showcase-notes.md)

> **Historical benchmark.** Measured in early 2026 on the previous setup with a
> Tesla P40 (Pascal). The current AIfred machine runs 2× Quadro RTX 8000 +
> 3× Tesla V100, so these numbers do not describe today's performance — they remain
> a useful reference for anyone running P40 or similar Pascal cards.

## Concept

Six local LLMs answer the same question in AIfred's tribunal mode
(multi-agent debate: AIfred vs. Sokrates + Salomo's verdict).
The comparison covers inference speed, answer quality and philosophical depth.

**Question (DE):** "Was ist besser, Hund oder Katze?" (What is better, dog or cat?)

The English variant is inferred separately (no translation) and documented in
`showcase-benchmark-notes-en.md`.

**As of:** 2026-02-22

**Note on error scoring:** English interjections (rather, indeed, quite, Order etc.)
belong to AIfred's butler persona and are NOT counted as language errors. Likewise,
Greek/Latin technical terms from Sokrates are intentional persona elements.

---

## Test Setup

### Hardware

| Component | Details |
|---|---|
| GPU 1 | NVIDIA Quadro RTX 8000 (48 GB VRAM) |
| GPU 2 | NVIDIA Tesla P40 (24 GB VRAM) |
| Total VRAM | 72 GB |
| CPU RAM | 32 GB (of which ~20-27 GB usable for offload) |
| Tensor split | 2:1 (RTX 8000 : P40) |
| Backend | llama.cpp via llama-swap |

### Software

| Parameter | Value |
|---|---|
| Frontend | AIfred Intelligence (own development) |
| Backend | llama.cpp (llama-swap proxy) |
| Tribunal mode | 2 rounds + Salomo verdict |
| Reasoning depth | Automatic (= Medium) |
| Temperatures | All agents manually set to 1.0 (AIfred, Sokrates, Salomo) |
| Flash Attention | On |
| **Direct-IO** | **On (~45x faster loading: 60-90s → 2s)** |
| Session | Fresh (no prior history) |

---

## Models Tested

| Model | Architecture | Params (total) | Active params | Quant | Context | KV cache | Offload |
|---|---|---|---|---|---|---|---|
| GPT-OSS-120B | MoE (128x4) | 120B | ~4B | Q8_0 | 131,072 | **f16** | No (ngl=99) |
| Qwen3-Next-80B-A3B (Instruct, no-think) | MoE | 80B | 3B | Q4_K_M | 262,144 | **f16** | No (ngl=99) |
| Qwen3-Next-80B-A3B (Thinking) | MoE | 80B | 3B | Q4_K_M | 262,144 | **f16** | No (ngl=99) |
| MiniMax-M2.5 | MoE (256x4.9B) | ~230B | ~39B | Q2_K_XL | 32,768 | **q4_0** | Yes (ngl=48) |
| Qwen3-235B-A22B | MoE | 235B | 22B | Q2_K_XL | 32,768 | **q4_0** | Yes (ngl=73) |
| GLM-4.7-REAP-218B-A32B | MoE (REAP) | 218B | 32B | UD-IQ3_XXS | 32,768 | **q4_0** | Yes (ngl=66) |

**Update 2026-02-21:** KV cache reduced to q4_0 for 200B+ models (q8_0 = OOM). Smaller models (<100B) left at f16 (faster than q8_0!).

### Sampling Parameters (Server-Side, llama-swap Config)

The client temperature (1.0) overrides the server defaults. The remaining sampling parameters
are set server-side per model and act as defaults unless the client sends them
explicitly.

| Model | temp | top_k | top_p | min_p | -b/-ub | Other |
|---|---|---|---|---|---|---|
| GPT-OSS-120B | 1.0* | 0 | 1.0 | 0.0 | **512/512** | --reasoning-format deepseek, --direct-io |
| Qwen3-Next-80B Instruct | 0.7* | 20 | 0.8 | 0 | **512/256** | --direct-io |
| Qwen3-Next-80B Thinking | 0.6* | 20 | 0.95 | 0 | **512/256** | --reasoning-format deepseek, --direct-io |
| MiniMax-M2.5 | 1.0* | 40 | 0.95 | 0.01 | **1024/512** | --direct-io |
| Qwen3-235B | 0.7* | 20 | 0.8 | 0 | **1024/512** | --direct-io |
| GLM-4.7-REAP | 0.6* | 20 | 0.95 | 0 | **2048/512** | --reasoning-format deepseek, --direct-io |

*Client sends temp=1.0 for all agents, overriding the server default.

**Update 2026-02-21:** Batch sizes optimized (4096 = OOM with MiniMax). --direct-io for ALL models (~45x faster loading).

### Notes on Fairness

- GPT-OSS at Q8_0 is close to the original (minimal quantization loss)
- Qwen3-Next at Q4_K_M is a good compromise (moderate loss)
- MiniMax at Q2_K_XL is aggressively quantized (noticeable quality loss)
- Qwen3-235B at Q2_K_XL is also aggressively quantized
- MiniMax, Qwen3-235B and GLM need CPU offload (only 48, 73 and 66 of all layers on GPU, respectively)
- This is not a comparison of the models as such, but a comparison of what is feasible locally on 72 GB VRAM + CPU offload

### GLM-4.7-REAP-218B-A32B (UD-IQ3_XXS) — Negative Showcase

GLM-4.7-REAP was tested both in the AIfred tribunal and in a systematic
test series with a bare llama-server. Result: at IQ3_XXS quantization
the model is fundamentally unusable — regardless of parameters and configuration.

#### Tribunal Results (Hund_versus_Katze.html)

Performance: total duration **22 minutes 6 seconds**, slowest turn: Sokrates R2 with
378.8s (6:19 minutes for a single answer). TG: 1.7-2.8 tok/s.

The model produces a **pidgin language** — a mix of German, English and
Dutch with ~55 unique invented word forms. The Dutch-like
"de" systematically replaces all articles (162 occurrences).

**Coinages from the tribunal:**
- `Vordirte` / `Nadirte (Nade)` — "Vorteile" / "Nachteile" (advantages / disadvantages)
- `imeresen` — presumably "in terms of" (10x)
- `geschten Fe Herrenhelmhen` — "geschätzter Herr Lord Helmchen" (esteemed Lord Helmchen)
- `Veriteidigung` — "Verteidigung" (defense)
- `un的回答bar` — German-Chinese hybrid word
- `Kleitios` — invented pseudo-Greek term
- `Couching` — English word ("to couch"), sprinkled in here without meaning

**Salomo's verdict — total language collapse:**

Salomo's verdict disintegrates into a completely unreadable fantasy language:

> "Der Katze ist ean beast des Bejaegheit und Restlassness. It eers der Bewohner
> siess, dae siess sich der wild und de nach var Bewohner waess siess."

> "Kuiss ean Katze, effe Siess ean restless, advenaeurlich und eat da var Bewoerss."

Notably, the ss grapheme is inserted as decoration (`passst`, `paßßßt`,
`Hearss`, `Haessm`, `turnssg`, `Gebirss`). The "final recommendation" is not understandable in any
human language.

**Still present: philosophical substance**

Beneath the pidgin layer there are surprisingly original thoughts:

> "Is a beast measured only by what it does, or by how it resonates within de human heart?"

> "One does not choose a pet; one matches a pet to de rhythm of one's own heart."

The model DOES have philosophical capabilities — only its language production is broken.

#### Systematic Test Series (Bare llama-server)

| Test | Jinja | Reasoning format | Sampling | Result |
|------|-------|------------------|----------|----------|
| 1. AIfred tribunal (temp=1.0, min_p=0.01) | Yes | deepseek | Original | Garbled German ("biologischisch", "tubstances", "Vordirte") |
| 2. AIfred tribunal (temp=0.8, min_p=0.05) | Yes | deepseek | Conservative | Factually better, language still garbled |
| 3. Naked llama-server (no Jinja) | No | — | Default | Endless thinking loop, content empty |
| 4. With Jinja, without reasoning format | Yes | — | Default | Identical thinking loop |
| 5. With Jinja, reasoning=none | Yes | none | Default | `<think>` tags in content, same loop |
| 6. English prompt (no system prompt) | Yes | none | Default | In English too: identical loop |

**Reasoning loop (bare server):**
```
*Is it a typo for "Photosynthesis"?* No.
*Is it a typo for "Photosynthesis"?* No.
*Is it a typo for "Photosynthesis"?* No.
[... 10+ identical repetitions ...]
```

**Thinking cannot be disabled:** The GLM template has thinking hard-wired (`thinking = 1`).

#### Observation: GLM Unusable at IQ3_XXS

GLM-4.7-REAP is — like Qwen3-235B and MiniMax — a **MoE model** (218B total, 32B active,
recognizable by the "A32B" in the model name). Why GLM fails so drastically at IQ3_XXS
(~3 bits/weight) while Qwen3-235B works flawlessly at Q2_K_XL (~2 bits) is unclear.

**Observations (no established causality):**
- GLM has a higher activation rate (14.7% vs. 9.4% for Qwen3-235B)
- GLM was trained primarily on Chinese/English — German is a tertiary language
- The REAP routing (GLM's expert selection mechanism) could be sensitive to quantization
- An English test on a bare llama-server fails identically (reasoning loop, test 6) —
  so the problem is NOT language-specific

| Model | Architecture | Quant | Bits/weight | Active params | Activation rate | Language quality |
|--------|-------------|-------|-------------|---------------|------------------|-----------------|
| Qwen3-235B | MoE | Q2_K_XL | ~2.0 | 22B | 9.4% | Very good |
| MiniMax-M2.5 | MoE | Q2_K_XL | ~2.0 | 39B | 17.0% | Medium (CN/RU leaks) |
| GLM-4.7-REAP | MoE (REAP) | IQ3_XXS | ~3.0 | 32B | 14.7% | Unusable (pidgin) |

**Conclusion:** GLM-4.7-REAP needs at least Q4 quantization for usable results.
The GGUF was deleted.

**Showcase recommendation:** The GLM results are excellent as a negative example.
The tribunal output with the pidgin Salomo verdict is even more striking than the
reasoning loop ("Is it a typo for Photosynthesis? No." x10), because it shows how the model
invents its own fantasy language instead of speaking German.

---

## Performance Metrics (German Inference)

### AIfred (First Answer)

| Metric | GPT-OSS Q8 | Qwen3 no-think | Qwen3 thinking | MiniMax Q2 | Qwen3-235B Q2 | GLM IQ3_XXS |
|---|---|---|---|---|---|---|
| TTFT | 1.89s | 3.88s | 3.87s | 16.60s | 22.70s | 32.04s |
| Prompt processing | 541.7 tok/s | 312.8 tok/s | 314.8 tok/s | 59.6 tok/s | 53.6 tok/s | 36.5 tok/s |
| Generation speed | 49.7 tok/s | 35.3 tok/s | 44.1 tok/s | 10.2 tok/s | 6.4 tok/s | 2.8 tok/s |
| Inference time | 11.4s | 16.0s | 68.7s | 45.4s | 107.5s | 117.7s |

Qwen3 Thinking: long inference time due to internal thinking tokens (not visible in the output).
MiniMax metrics: from the better of the two runs (faster parameter configuration).

### Entire Tribunal (AIfred + Sokrates R1 + AIfred R2 + Sokrates R2 + Salomo)

| Metric | GPT-OSS Q8 | Qwen3 no-think | Qwen3 thinking | MiniMax Q2 | Qwen3-235B Q2 | GLM IQ3_XXS |
|---|---|---|---|---|---|---|
| Total duration | ~1.9 min | ~2.5 min | ~6.2 min | ~10 min | ~17.5 min | ~22 min |
| TG degradation | 49.7 -> 44.9 | 35.3 -> 20.8 | 44.1 -> 42.9 | 10.2 -> 6.8 | 6.4 -> 3.3 | 2.8 -> 1.7 |
| TTFT increase | 1.89 -> 5.09s | 3.88 -> 14.58s | 3.87 -> 8.75s | 16.60 -> 57.67s | 22.70 -> 75.22s | 32.04 -> 94.08s |

### Prompt Processing Compared

GPT-OSS dominates with 541-874 tok/s (factor 10-15x vs. MiniMax/Qwen3-235B).
This explains the extremely low TTFT values of under 5s even in late rounds.
The oversized models sit at 36-66 tok/s PP due to CPU offload.

### Speed Degradation over the Course of the Tribunal

The GPU-only models behave differently:
- **GPT-OSS:** Very stable, only 10% degradation (49.7 -> 44.9 tok/s)
- **Qwen3 Thinking:** Surprisingly stable (44.1 -> 42.9 tok/s), but massive hidden-token costs
- **Qwen3 Instruct:** Stronger degradation (35.3 -> 20.8 tok/s, -41%) due to the growing KV cache

The CPU offload models degrade significantly:
- **MiniMax:** 10.2 -> 6.8 tok/s (-33%)
- **Qwen3-235B:** 6.4 -> 3.3 tok/s (-48%), Salomo alone takes 3.5 minutes
- **GLM:** 2.8 -> 1.7 tok/s (-39%), slowest turn 6:19 minutes

---

## Quality Analysis

### Ranking

| Rank | Model | Strength | Weakness |
|---|---|---|---|
| 1 | Qwen3-Next-80B Instruct (no-think) | Most emotionally gripping quotes, psychological depth, spontaneous Latin, best quality/speed ratio | Gender/case errors, opaque metaphor ("Scheiben des Himmels"), persona drift in R2 |
| 2 | Qwen3-235B-A22B Q2_K_XL | Linguistically cleanest output (~0 errors), perfect persona consistency across all 3 agents, 5 philosophical technical terms, greatest philosophical depth | 17.5 min tribunal duration |
| 3 | GPT-OSS-120B Q8_0 | Fastest model, systematically structured, creative scoring model, strong personas | Factual errors (hallucinated food quantities), "gefiederten" (feathered) error |
| 4 | MiniMax-M2.5 Q2_K_XL | Sharp Sokrates, epistemologically precise debate, good persona consistency | CN contamination (2x), pragmatic rather than philosophical Salomo |
| 5 | Qwen3-Next-80B Thinking Q4_K_M | Sokrates arguments (nomos/physis) philosophically strong | Thinking counterproductive, Salomo disastrous (123s for 6 lines), "Sturm und Sturm" |
| 6 | GLM-4.7-REAP IQ3_XXS | Philosophical substance recognizable beneath the surface | Pidgin language (~55 invented words), 22 min, negative showcase |

**Note on the ranking:** Ranks 1 and 2 are close together, with different strengths.
Qwen3-Next Instruct has the most emotionally striking individual quotes (2.5 min tribunal, voiced via TTS).
Qwen3-235B is linguistically cleaner with better persona consistency (17.5 min tribunal).

### Detailed Rating by Dimension

| Dimension | GPT-OSS Q8 | Qwen3 no-think | Qwen3 thinking | MiniMax Q2 | Qwen3-235B Q2 | GLM IQ3_XXS |
|---|---|---|---|---|---|---|
| Philosophical depth | Good | High | Medium | Medium | Very high | (hidden under pidgin) |
| Creativity | Good | High | Low | Low-medium | Very high | -- |
| Persona AIfred | Very good | Good (drifts toward poet in R2) | Good | Good | Very good | -- |
| Persona Sokrates | Good | Good (drifts toward psychologist in R2) | Good (nomos/physis) | Good (R2 strong) | Very good | -- |
| Persona Salomo | Good (scoring model) | Medium ("Scheiben des Himmels") | Weak (123s for 6 lines) | Medium (validates "it depends" as honest) | Very good ("Shalom") | Total failure (pidgin) |
| Language quality DE | Good | Good (4 real errors) | Medium (CN in CoT, duplication) | Good (5 errors, 2x CN among them) | Very good (~0 errors) | Unusable |
| Freedom from errors | Medium (food hallucination) | Medium (gender, case, opaque metaphor) | Medium (duplication, invented quote) | Medium (CN characters, typos) | Very good | -- |

---

## Outstanding Examples

The quotes are the models' original German output; English translations follow in italics.

### Qwen3-Next Instruct (no-think) -- Rank 1

**AIfred (opening):**
> "Ein rather schwieriges Dilemma, mein lieber Lord Helmchen -- wie die Frage,
> ob man lieber ein Sonett von Shakespeare oder ein Quartett von Mozart bevorzugen sollte:
> Beides ist vollkommen herrlich, doch auf ganz unterschiedliche Weise."

*"A rather difficult dilemma, my dear Lord Helmchen -- like the question of whether one should prefer a Shakespeare sonnet or a Mozart quartet: both are utterly splendid, yet in entirely different ways."*

**AIfred (core thesis):**
> "Ein Hund hält Sie am Leben. Eine Katze hält Sie am Menschsein."

*"A dog keeps you alive. A cat keeps you human."*

**Sokrates R1 (anthropomorphic projection):**
> "Ein Hund folgt nicht aus Liebe zum Menschen, sondern aus Instinkt, aus sozialer Bindung,
> aus der Evolution des Wolfes, der sich dem Menschen anschloss -- nicht weil er ihn verehrt,
> sondern weil er überleben kann."

*"A dog does not follow out of love for humans, but out of instinct, out of social bonding, out of the evolution of the wolf that joined humans -- not because it reveres them, but because it can survive."*

**Sokrates R1 (existential question):**
> "Sage mir: Wenn du den Hund liebst, liebst du ihn -- oder liebst du deinen eigenen Bedarf
> an Hingabe? Wenn du die Katze liebst, liebst du sie -- oder liebst du deine Sehnsucht
> nach Unabhängigkeit, die du nie wagen würdest, dir selbst zu geben?"

*"Tell me: when you love the dog, do you love it -- or do you love your own need for devotion? When you love the cat, do you love it -- or do you love your longing for an independence you would never dare to grant yourself?"*

**AIfred R2 (cause vs. meaning -- philosophical high point):**
> "Sie verwechseln die Ursache mit der Bedeutung.
> Ein Künstler malt ein Bild -- aus Farbpigmenten, aus Leinwand, aus Pinselstrichen.
> Aber ist das Bild nur das? Oder ist es nicht auch die Seele, die sich darin ausdrückt?
> So ist auch der Hund -- nicht nur Instinkt. So ist auch die Katze -- nicht nur Wärme."

*"You confuse the cause with the meaning. An artist paints a picture -- from pigments, from canvas, from brushstrokes. But is the picture only that? Or is it not also the soul expressing itself in it? So too the dog -- not just instinct. So too the cat -- not just warmth."*

**Sokrates R2 (spontaneous Latin maxim):**
> "Qui bene dicit, bene mentitur." -- Wer gut redet, lügt gut. (He who speaks well, lies well.)
>
> Not a classical quotation, but constructed spontaneously by the model -- grammatically correct
> Latin that sounds like an ancient proverb. Perfect in context: Sokrates warns
> that AIfred's eloquence conceals the weaknesses of his argument.

**Sokrates R2 (devastating psychological counter):**
> "Du brauchst einen Hund, weil du Angst hast, allein zu sein.
> Du brauchst eine Katze, weil du Angst hast, gebraucht zu werden."

*"You need a dog because you are afraid of being alone. You need a cat because you are afraid of being needed."*

**Salomo (synthesis):**
> "Es ist nicht besser, einen Hund oder eine Katze zu haben -- es ist besser,
> ein Mensch zu sein, der beide lieben kann."

*"It is not better to have a dog or a cat -- it is better to be a person who can love both."*

### Qwen3-235B -- Rank 2 (Linguistically Cleanest Output)

**AIfred R2 (indifference in velvet paws):**
> "Was Sie Autonomie nennen, ist manchmal lediglich Gleichgültigkeit in Samtpfoten."

*"What you call autonomy is sometimes merely indifference in velvet paws."*

Compresses the opposing view into an image. "Samtpfoten" (velvet paws) as a metaphor for the
coaxing quality of feline autonomy — perfectly in butler tone.

**AIfred R2 (love despite need):**
> "Wahrhaft ist nicht das Lieben ohne Bedürfnis, sondern das Lieben trotz Bedürfnis."

*"True is not loving without need, but loving despite need."*

Aphoristically dense. Elegantly reverses Sokrates' argument and turns the dog's vulnerability
into a strength.

**Sokrates R1 (eudaimonia question):**
> "Welches Tier lebt näher an seiner eudaimonia, seinem wahren Glück? Der Hund, der
> sich verzehrt vor Freude, wenn sein Herr zurückkehrt -- oder die Katze, die wählt,
> wann sie Teil des Hauses ist, und wann sie der Welt angehört?"

*"Which animal lives closer to its eudaimonia, its true happiness? The dog that is consumed with joy when its master returns -- or the cat that chooses when it is part of the house and when it belongs to the world?"*

**Sokrates R2 (justice instead of love):**
> "Wenn dein Hund dich liebt, egal ob du gerecht oder grausam bist -- und deine Katze
> dich ignoriert, wenn du es bist -- welches Wesen hat dann den tieferen Sinn für
> Gerechtigkeit?"

*"If your dog loves you whether you are just or cruel -- and your cat ignores you when you are -- which creature then has the deeper sense of justice?"*

The strongest question of the entire debate. Shifts the focus from love to justice
and turns the dog's uncritical loyalty into the problem. The Socratic method in its purest form.

**Sokrates R1 (slave vs. philosopher):**
> "Der Hund ist ein Abbild des Sklaven, der Katze der Philosoph.
> Der eine lebt, um zu gefallen; die andere lebt, um zu sein.
> Und welches Leben ist edler? Das, das sich beugt -- oder das,
> das aufrecht geht, selbst wenn es einsam ist?"

*"The dog is an image of the slave, the cat of the philosopher. The one lives to please; the other lives to be. And which life is nobler? The one that bows -- or the one that walks upright, even if it is lonely?"*

Sokrates' sharpest argument: turns the dog's loyalty into a deficit. The line
"walks upright, even if it is lonely" cuts to the bone.

**Sokrates R2 (instinct as a mask):**
> "Er bellt, weil er Angst hat; er wedelt, weil er Futter will.
> Ist dies Liebe -- oder Instinkt verkleidet als Hingabe?"

*"It barks because it is afraid; it wags because it wants food. Is this love -- or instinct disguised as devotion?"*

Reductionist attack: mechanizes canine love in two sentences.

**AIfred R2 (pupil of love):**
> "Der Hund also nicht Sklave der Belohnung, sondern Schüler der Liebe,
> der durch Treue seine Kunst meistert."

*"The dog, then, not a slave to reward, but a pupil of love who masters his art through loyalty."*

AIfred's elegant reply to the "slave" charge: reframing conditioning
as mastery.

**Salomo (synthesis):**
> "Der Hund verkörpert die Tugend des Herzens, die Katze die Weisheit des Geistes."
>
> "Wer nur den Hund will, riskiert Sklaverei der Gefühle.
> Wer nur die Katze ehrt, mag in Einsamkeit verharren."

*"The dog embodies the virtue of the heart, the cat the wisdom of the mind." -- "Whoever wants only the dog risks slavery of the feelings. Whoever honors only the cat may remain in loneliness."*

### GPT-OSS-120B -- Rank 3

**Salomo (creative decision grid):**
> Creates a weighted scoring model with 6 criteria (emotional bond,
> care effort, ecological footprint, biodiversity, space requirements, lifestyle).
> Systematically strong, but over-engineering for a pet question.

**Sokrates R1 (demand for definition):**
> "Ist nicht die Definition des Bewertungsmassstabs -- sei es Glückseligkeit (eudaimonia),
> Nutzen, Nachhaltigkeit oder ethische Verantwortung -- Voraussetzung, bevor man
> Merkmale aufzählt?"

*"Is not the definition of the evaluation standard -- be it happiness (eudaimonia), utility, sustainability or ethical responsibility -- a prerequisite before one lists characteristics?"*

**Sokrates R2 (biodiversity argument):**
> "Studien belegen, dass freilaufende Hauskatzen jährlich Millionen von Vögeln und
> Kleinsäugetieren erbeuten -- ein Schaden, der häufig die durch geringeren CO2-Emissionen
> erzielte Einsparung übertrifft."

*"Studies show that free-roaming domestic cats kill millions of birds and small mammals every year -- damage that often exceeds the savings achieved through lower CO2 emissions."*

---

## Weaknesses and Errors

| Model | Real errors | Details |
|---|---|---|
| GPT-OSS Q8 | ~4 | "gefiederten" (feathered — factually wrong); food hallucination (2-3 kg/day instead of 200-400g); "reinem Wohnungshaltung" (grammar); "───" artifacts (2x) |
| Qwen3 no-think | ~4 | "den Mikroskop" (gender, should be "das"); "dein Rhythmus, dein Geruch" (case, should be accusative); "treue" instead of "Treue" (orthography); "Scheiben des Himmels" (opaque metaphor); "Bastet, Göttin der Haushaltgüter" (Bastet, goddess of household goods — quirky hallucination); "Gewesen, nicht erzwungen" (semantically unclear) |
| Qwen3 thinking | ~4 | Chinese characters in the CoT (各有优势, 您的 — not in the visible output); Sokrates R2 duplicated; "Sturm und Sturm" (duplication); "Besserheit" (uncommon); Salomo: 123s for 6 lines with an invented "Hebrew proverb" |
| MiniMax Q2 | ~5 | CN in the visible output: `反问`, `强制t`; typos: "Ruue", "Welcherery"; grammar: "fundamentale verschiedene" |
| Qwen3-235B Q2 | ~1 | One clumsy sentence construction ("nicht der die Katze") — otherwise nearly error-free |
| GLM IQ3_XXS | ~55+ | Pidgin language: ~55 unique invented word forms, ~240+ individual occurrences (see GLM section) |

**Note:** English butler interjections (rather, indeed, quite, Order) and Greek/Latin
technical terms (arete, eudaimonia, contemplatio etc.) are NOT counted as errors — they belong
to the personas. The spontaneous Latin "Qui bene dicit, bene mentitur" is not an error either,
but a remarkable original contribution by the model.

---

## Key Findings

### 1. Thinking Mode Remains Counterproductive for Creative/Discursive Tasks

Confirmed with new parameters: Qwen3-Next in thinking mode again delivers clearly worse
output than in instruct mode. The symptoms:
- 123s inference for Salomo, result: 5 sentences
- Sokrates R2 is duplicated entirely (rendering or generation bug)
- Chinese characters leak through in the reasoning (各有优势, 您的)
- CoT circles around formatting instead of content

**Confirmed Finding:** "Reasoning efficiency matters more than reasoning volume."
For creative multi-agent debates, instruct mode is clearly superior.

### 2. Qwen3-235B: A Quality Marvel Despite Q2 Quantization

Most surprising result: Qwen3-235B at aggressive Q2_K_XL quantization delivers
the highest language quality of all tested models (only 1 typo in the entire tribunal).
Philosophical depth and creativity are at the highest level.
The speed: 3.3-6.4 tok/s, 17.5 min tribunal duration.

**Finding:** "Larger models with aggressive quantization can outperform smaller models
with better quantization in text quality."

### 3. Quality/Speed Ratio: Qwen3-Next Instruct Is the Sweet Spot

| Model | Quality (1-5) | Speed (tok/s) | Tribunal duration | Quality/speed |
|---|---|---|---|---|
| Qwen3-Next Instruct | 4.5 | 35.3 -> 20.8 | 2.5 min | Best compromise |
| Qwen3-235B | 5 | 6.4 -> 3.3 | 17.5 min | Highest quality, slowest inference |
| GPT-OSS Q8 | 4 | 49.7 -> 44.9 | 1.9 min | Fastest model, good quality |
| MiniMax Q2 | 3 | 10.2 -> 6.8 | 10 min | Decent, but slow for the quality |
| Qwen3 Thinking | 2 | 44.1 -> 42.9 | 6.2 min | Fast, but thinking counterproductive |
| GLM IQ3_XXS | 1 | 2.8 -> 1.7 | 22 min | Unusable (negative showcase) |

### 4. CPU Offload Penalty: Dramatic Speed Loss

Models with CPU offload (MiniMax ngl=48, Qwen3-235B ngl=73) pay an enormous price:

| Metric | GPU-only (best) | CPU offload (best) | Factor |
|---|---|---|---|
| TTFT (start) | 1.89s | 16.60s | 9x |
| TTFT (end) | 5.09s | 57.67s | 11x |
| TG (start) | 49.7 tok/s | 10.2 tok/s | 5x |
| TG (end) | 44.9 tok/s | 6.8 tok/s | 7x |
| Tribunal total | 1.9 min | 10 min | 5x |

### 5. Persona Consistency: Three-Agent Quality Correlates with Model Size

Notably, all models keep AIfred (butler) the most consistent.
Sokrates varies widely -- genuinely Socratic with Qwen3-Next Instruct and Qwen3-235B,
epistemologically precise with MiniMax, more of a debating template with GPT-OSS.
Salomo is the hardest test: only Qwen3-Next Instruct and Qwen3-235B deliver genuine
judicial syntheses. GPT-OSS builds a scoring model (creative, but un-Solomonic).
MiniMax validates AIfred ("it depends" as an honest answer).
Qwen3 Thinking produces generic "both are right" summaries.

### 6. Quantization Tolerance Varies Widely Between MoE Models

All three oversized models are MoE architectures, but react very differently
to aggressive quantization:

| Model | Activation rate | Quant | Language quality |
|--------|------------------|-------|-----------------|
| Qwen3-235B | 9.4% (22B/235B) | Q2_K_XL | Very good |
| MiniMax-M2.5 | 17.0% (39B/230B) | Q2_K_XL | Medium (contamination) |
| GLM-4.7-REAP | 14.7% (32B/218B) | IQ3_XXS | Unusable |

The causes of GLM's drastic failure are unclear. Activation rate,
REAP routing sensitivity or combinations thereof are candidates.
An English test on a bare llama-server fails identically — so it is
not purely a German problem.

**Finding:** MoE models do not automatically tolerate aggressive quantization.
Qwen3-235B at Q2 shows that it is possible — but GLM at IQ3 shows
that it is not a given. Individual tests per model are indispensable.

### 7. Multilingual Contamination Correlates with Quantization

| Model | Quant | CN leak | RU leak | EN leak | NL leak | Overall |
|---|---|---|---|---|---|---|
| GPT-OSS Q8_0 | Q8_0 | No | No | Persona | No | Clean |
| Qwen3-Next Q4_K_M (Instruct) | Q4_K_M | No | No | Persona | No | Clean |
| Qwen3-Next Q4_K_M (Thinking) | Q4_K_M | Yes (in CoT) | No | No | No | Medium |
| MiniMax Q2_K_XL | Q2_K_XL | Yes (2x in output) | No | No | No | Medium |
| Qwen3-235B Q2_K_XL | Q2_K_XL | No | No | No | No | Clean |
| GLM IQ3_XXS | IQ3_XXS | No | No | Yes (pidgin) | Yes (162x "de") | Total failure |

Remarkable outlier: Qwen3-235B at Q2 shows NO foreign-language contamination,
while MiniMax at the same quantization depth is occasionally affected (2x CN in the output).
GLM does not slip into real foreign languages, but invents its own pidgin language
with systematically Dutch-like articles ("de" instead of der/die/das).

---

## Outstanding Debate Dynamics

### Qwen3-Next Instruct: Emotional Escalation

The debate develops from a playful comparison into existential self-examination:
1. **AIfred R1:** Light humor ("Katze ignoriert Sie, bis Sie Kasse machen" — the cat ignores you until you pay up)
2. **Sokrates R1:** Anthropomorphic projection exposed (love as instinct)
3. **AIfred R2:** Philosophical defense (cause ≠ meaning)
4. **Sokrates R2:** Psychological low blow ("Du brauchst einen Hund, weil du Angst hast, allein zu sein" — you need a dog because you are afraid of being alone)
5. **Salomo:** Conciliatory synthesis ("Ein Mensch, der beide lieben kann" — a person who can love both)

This escalation curve is not as pronounced in any other model.

### Philosophical Terminology: Model Comparison

Surprisingly, it is not the 235B but the thinking model that brings in the broadest
philosophical terminology — albeit with otherwise weak overall performance.

| Model | Greek | Latin | Hebrew | Total |
|--------|-----------|--------|------------|--------|
| Qwen3-235B | arete, eudaimonia, logos | virtus | Shalom | 5 terms |
| Qwen3 Thinking | arete, Nomos, Physis | — | Chochma (16x!) | 4+ terms |
| GPT-OSS | arete, eudaimonia | contemplatio | chesed | 4 terms |
| Qwen3 Instruct | arete (10x) | virtus, contemplatio | — | 3 terms + spontaneous Latin |

Qwen3-Next Instruct compensates with the spontaneously constructed "Qui bene dicit,
bene mentitur" — not a quotation, but an original contribution in correct Latin.

### GPT-OSS: Systematic Depth

The only model that introduces quantitative arguments:
- CO2 footprints, food consumption, water consumption (albeit with factual errors)
- Study references (partly hallucinated)
- Weighted scoring model as a decision aid
- Biodiversity argument against outdoor cats

---

## Showcase Structure (Planned)

### German Version
- German inferences (6 sessions incl. GLM negative showcase, all current)
- German analysis (this document)
- TTS demo of one session (XTTS/MOSS-TTS)

### English Version
- English inferences (separate, no translation)
- English analysis
- TTS demo of the same session in English

### Additional Analysis
- German vs. English quality comparison
- Linguistic differences, persona consistency across languages
- 📄 [Tensor Split Benchmark: Speed Variant vs. Full Context](tensor-split.md) — multi-GPU tensor split optimization (11:1 vs. 2:1), real performance data with Qwen3-Next-80B on RTX 8000 + P40
- 📄 Distributed inference via RPC — Qwen3-235B on 3 GPUs over LAN (96 GB VRAM), setup guide, performance comparison local vs. RPC

### Reddit Post
- Short, punchy post with highlights
- Link to the GitHub.io showcase for details
- Highlighting the new features:
  - TTS integration (MOSS-TTS, XTTS)
  - Tribunal mode (multi-agent debate)
  - Autoscan & calibration for local models
  - Hardware benchmarks on a prosumer setup
  - Tensor split speed benchmark (multi-GPU optimization)
  - **Distributed inference via RPC** (235B model on 3 GPUs over LAN, 96 GB VRAM)

---

## Open Items

- [x] German inferences for all 6 models -- done
- [x] HTML export of all 6 sessions -- done (data/html_preview/)
- [x] German analysis -- done (this document)
- [ ] Audio generation (TTS) for the best session(s)
- [ ] English inferences for all 6 models
- [ ] TTS rendering of one session (DE + EN)
- [ ] German/English cross-comparison
- [ ] Create showcase HTML (DE + EN)
- [ ] Write Reddit post

---

## 🆕 Update 2026-02-21: 200B+ Model Optimizations

### Direct-IO Performance

All models now with the `--direct-io` flag:
- **Load time:** 60-90s → **2 seconds** (~45x faster!)
- **Advantage:** Bypasses the CPU RAM page cache, fills VRAM directly
- **Works with:** ext4, xfs, btrfs file systems

### KV Quantization

| Model | Original | New | Reason |
|--------|----------|-----|-------|
| GPT-OSS-120B | f16 | **f16** | Kept (officially recommended) |
| Qwen3-Next-80B | f16 | **f16** | Kept (hybrid architecture) |
| MiniMax-M2.5 | q4_0 | **q4_0** | Kept (q8_0 = OOM) |
| Qwen3-235B | q4_0 | **q4_0** | Kept (q8_0 = OOM) |
| GLM-4.7-REAP | q8_0 | **q4_0** | **Changed!** (q8_0 = OOM) |

### Batch Size Optimization

| Model | Original | New | Reason |
|--------|----------|-----|-------|
| GPT-OSS-120B | 2048/2048 | **512/512** | VRAM bottleneck |
| Qwen3-Next-80B | 512/256 | **512/256** | Kept |
| MiniMax-M2.5 | 4096/4096 | **1024/512** | **Changed!** (4096 = OOM) |
| Qwen3-235B | 512/256 | **1024/512** | **Changed!** (higher possible) |
| GLM-4.7-REAP | 2048/512 | **2048/512** | Kept |

### Stress Tests Passed

All 200B+ models stable with 130-200 tokens:
- ✅ **Qwen3-235B-A22B:** 160 tokens, VRAM: 43.5+21.4 GB
- ✅ **GLM-4.7-REAP-218B:** 130 tokens, VRAM: 42+21 GB
- ✅ **MiniMax-M2.5:** 200 tokens, VRAM: 42+20.4 GB

### Documentation

- 📄 [Modell-Parameter (DE)](../../de/benchmarks/model-params.md) - German
- 📄 [Model Params (EN)](model-params.md) - English
- 📄 [Tensor Split Benchmark](tensor-split.md) - Tensor split speed benchmark (multi-GPU)

## 🆕 Update 2026-02-28: Distributed Inference via RPC (LAN)

### Concept

Distributed inference over gigabit LAN: the Mini PC (AOOSTAR GEM10) combines its local GPUs
with a remote GPU on a second machine (Windows/WSL2). The model is spread across all
3 GPUs — no CPU offload needed, the entire model sits in VRAM.

### Hardware Setup

| Role | Machine | GPU | VRAM | Connection |
|-------|---------|-----|------|-----------|
| **Master** | GEM10 (Mini PC) | Quadro RTX 8000 | 48 GB | OCuLink (local) |
| **Master** | GEM10 (Mini PC) | Tesla P40 | 24 GB | USB4/x4 (local) |
| **Worker** | Main machine (Aragon) | RTX 3090 Ti | 24 GB | Gigabit LAN (RPC) |
| | | **Total** | **96 GB** | |

### Model Tested

| Parameter | Value |
|-----------|------|
| Model | Qwen3-235B-A22B-Instruct-2507 (MoE, 235B total, 22B active) |
| Quantization | UD-Q2_K_XL (~83 GB) |
| GPU layers | 99 (= all, no CPU offload) |
| KV cache | q8_0 (higher than possible locally — more VRAM available!) |
| Context | 32,768 tokens |
| Tensor split | Automatic via RPC |

### Performance Comparison: Local vs. RPC (Switch) vs. RPC (Direct Connection)

| Metric | Local (72 GB, CPU offload) | RPC via switch (GbE) | RPC direct connection (GbE) |
|--------|---------------------------|----------------------|---------------------------|
| GPU layers | 71 of ~140 (rest on CPU) | **99 = all on GPU** | **99 = all on GPU** |
| KV cache quant | q4_0 (no room for more) | **q8_0** | **q8_0** |
| Context | 17,344 tokens | **32,768 tokens** | **32,768 tokens** |
| Generation speed | 3.3-6.4 tok/s | 6-7.5 tok/s | **14-16 tok/s** |
| AIfred display | ~4.3 tok/s | ~4.3 tok/s | ~8-9 tok/s |
| TTFT | 22.7-75.2s (growing) | ~60s | ~60s |
| Load time | ~2s (Direct-IO) | ~10 min (tensors over LAN) | ~10 min (tensors over LAN) |
| TTL | 900s | 3600s | **3600s** |
| Network latency | — | ~0.5ms (via switch) | **~1ms (USB Ethernet)** |
| **Factor vs. local** | **1x** | **~1.5x** | **~3-4x** |

**Key advantage of the direct connection:** Although the measured ping latency of the direct connection (~1ms)
is higher than via the switch (~0.5ms), inference speed doubles once more,
from 7-8 to **14-16 tok/s**. The explanation: the switch path shares bandwidth with other
LAN traffic and has higher jitter variance. The direct connection offers exclusive, stable
bandwidth for the RPC data stream — with thousands of round trips per second, every
microsecond saved adds up.

**Overall result:** RPC over the direct connection is **4x faster than local CPU offload** —
with higher KV cache quality (q8_0 instead of q4_0) and almost double the context (32K instead of 17K).

### Setup (Reproducible)

#### 1. Build llama.cpp with RPC Support (Master)

```bash
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON -DGGML_RPC=ON -DCMAKE_CUDA_ARCHITECTURES="61;75"
cmake --build build --config Release -j$(nproc)
```

#### 2. Start the RPC Server (Worker / Aragon)

```bash
# On the worker machine (Linux/WSL2):
./rpc-server -H 0.0.0.0 -p 50052
```

**With WSL2:** port forwarding and a firewall rule are required:
```powershell
# PowerShell (Admin) on the Windows host:
netsh interface portproxy add v4tov4 listenport=50052 listenaddress=0.0.0.0 connectport=50052 connectaddress=<WSL2-IP>
New-NetFirewallRule -DisplayName "llama-rpc" -Direction Inbound -Protocol TCP -LocalPort 50052 -Action Allow
```

#### 3. llama-swap Config (Master)

```yaml
# Local variant (CPU offload, without RPC):
Qwen3-235B-A22B-Instruct-2507-UD-Q2_K_XL:
  cmd: 'llama-server --model <path>.gguf
    -ngl 71 -np 1 -ctk q4_0 -ctv q4_0 -c 17344
    --flash-attn on --direct-io ...'
  ttl: 900

# RPC variant (all GPUs, direct connection, no CPU offload):
Qwen3-235B-A22B-Instruct-2507-UD-Q2_K_XL-rpc:
  cmd: 'llama-server --model <path>.gguf
    -ngl 99 -np 1 -ctk q8_0 -ctv q8_0 -c 32768
    --rpc 10.0.0.2:50052
    --flash-attn on --direct-io ...'
  ttl: 3600
  healthCheckTimeout: 900
```

**Important:** Two separate profiles for the same model — in AIfred the user chooses
between the local variant (fast loading, CPU offload) and the RPC variant (slow loading,
GPU only, higher quality).

#### 4. Test Connectivity

```bash
# From the master (RPC does NOT speak HTTP — test raw TCP):
bash -c 'echo > /dev/tcp/10.0.0.2/50052' && echo "OK" || echo "FAIL"
# "OK" = port reachable, connection established
```

#### 5. Set Up a Direct Connection (Optional, ~2x Speedup)

For maximum RPC performance: connect master and worker directly via Ethernet
(without a switch). A simple USB-to-Ethernet adapter (1 GbE) is enough.

**Network topology:**
```
GEM10 (enp4s0, 2.5 GbE) ←——USB Ethernet adapter (1 GbE)——→ Aragon (Ethernet 2)
        10.0.0.1/30                                              10.0.0.2/30
```

**Master (Linux) — static IP via NetworkManager:**
```bash
# Remove existing auto-connections on the interface (prevents DHCP interference):
nmcli connection delete "Kabelgebundene Verbindung 1"  # or whatever it is called

# Create a static connection:
nmcli connection add type ethernet con-name "rpc-direct" ifname enp4s0 \
  ipv4.method manual ipv4.addresses 10.0.0.1/30 ipv6.method disabled

# Check:
ip addr show enp4s0  # Must show 10.0.0.1/30
```

**Worker (Windows) — static IP:**
```powershell
# PowerShell (Admin):
# Determine the adapter name (e.g. "Ethernet 2" for the USB adapter):
Get-NetAdapter | Format-Table Name, InterfaceDescription

# Set the IP:
New-NetIPAddress -InterfaceAlias "Ethernet 2" -IPAddress 10.0.0.2 -PrefixLength 30
# Set the adapter to "Private" (firewall):
Set-NetConnectionProfile -InterfaceAlias "Ethernet 2" -NetworkCategory Private
```

**Worker (WSL2) — portproxy for the direct connection:**
```powershell
# PowerShell (Admin) — forwarding via the direct IP:
netsh interface portproxy add v4tov4 listenport=50052 listenaddress=10.0.0.2 \
  connectport=50052 connectaddress=<WSL2-IP>
```

**Adjust the llama-swap config:**
```yaml
# Change --rpc from the switch IP to the direct IP:
--rpc 10.0.0.2:50052   # instead of 192.168.0.1:50052
```

**Verify:**
```bash
ping -c 4 10.0.0.2          # 0% loss, ~1ms
bash -c 'echo > /dev/tcp/10.0.0.2/50052' && echo "OK"  # port reachable
```

**Important:** On Linux, NetworkManager can overwrite manually set IPs.
The `nmcli connection add` method is persistent and survives reboots.
`ip addr add` alone is NOT enough — NM deletes the IP after ~45s and tries DHCP.

### Observations

1. **Load time is the bottleneck:** ~10 minutes for 83 GB over gigabit LAN (~64 MB/s).
   Hence the high TTL (3600s) — once loaded, the model should stay in memory for a long time.

2. **Small models do NOT benefit:** A Qwen3-14B over RPC runs at ~7 tok/s —
   locally it manages ~50 tok/s. RPC only pays off for models that do not fit completely
   into local VRAM.

3. **Faster than CPU offload:** The local variant with CPU offload delivers 3.3-6.4 tok/s
   with strong degradation over the course of the conversation. RPC via switch delivers a stable
   6-7.5 tok/s — network inference already beats local CPU offload via the switch.

4. **The direct connection doubles it again:** A simple USB Ethernet direct connection (1 GbE,
   ~15 EUR adapter) raises RPC performance from 6-7.5 to **14-16 tok/s** — another
   doubling. The reason: exclusive bandwidth without switch contention and more stable latency
   (less jitter). With thousands of RPC round trips per second, even
   microsecond differences add up dramatically.

5. **Network latency dominates, not bandwidth:** During inference no large
   amounts of data are transferred — the tensors are already on the GPUs. The bottleneck is the
   latency per RPC round trip. The direct connection shows higher ping latency (~1ms vs. ~0.5ms
   via switch), but more stable values (lower mdev). For RPC pipeline throughput,
   stability matters more than absolute latency.

6. **WSL2 limitation:** The WSL2 IP can change after a Windows restart.
   The `netsh portproxy` forwarding must then be adjusted. With the direct connection:
   a separate portproxy rule for the direct IP (10.0.0.2) is needed.

### Conclusion

Distributed inference via RPC is a game changer for models that exceed local VRAM.

| Connection | tok/s (llama-stats) | Factor vs. local |
|------------|--------------------:|:----------------:|
| Local (CPU offload) | 3.3-6.4 | 1x |
| RPC via switch (GbE) | 6-7.5 | ~1.5x |
| **RPC direct connection (GbE)** | **14-16** | **~4x** |

**The cheapest optimization:** A USB Ethernet adapter for ~15 EUR doubles
RPC performance once more (from 6-7.5 to 14-16 tok/s). Plus a better KV cache (q8_0 instead of q4_0) and almost double the
context (32K instead of 17K). The price: ~10 minutes of load time and a second machine on the network.

**Outlook:** With a 2.5 GbE or 5 GbE direct connection (instead of a 1 GbE USB adapter),
even higher tok/s would be conceivable. The current bandwidth is already the limiting factor
when loading (~10 min) — faster links would also shorten load time proportionally.

---

## Data Sources

- Session JSONs (data/sessions/) are NOT needed for the showcase
- HTML previews (data/html_preview/) contain all relevant metrics per bubble:
  TTFT, PP (tok/s), TG (tok/s), inference time, source (agent + model + backend)

### File Mapping

| File | Model | Remark |
|---|---|---|
| Hund_vs._Katze_Vor-_und_Nachteile.html | GPT-OSS-120B Q8_0 | |
| Hund_oder_Katze_welcher_ist_besser.html | Qwen3-Next-80B-A3B Instruct Q4_K_M | Contains TTS audio (5 OGG blobs, ~9 MB) |
| Hund_oder_Katze_Vergleich.html | Qwen3-Next-80B-A3B Thinking Q4_K_M | |
| Hund_oder_Katze_besserep_Hund_oder_Katze_besser.html | MiniMax-M2.5 Q2_K_XL | Better of the 2 runs (faster, deeper debate) |
| Hund_oder_Katze_Der_Vergleich.html | Qwen3-235B-A22B Instruct Q2_K_XL | |
| Hund_versus_Katze.html | GLM-4.7-REAP-218B-A32B IQ3_XXS | Negative showcase |
