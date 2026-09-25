# AIfred Intelligence - Example Showcases
# AIfred Intelligence - Beispiel-Showcases

This folder contains example conversations demonstrating AIfred's capabilities.
*Dieser Ordner enthält Beispiel-Konversationen, die AIfred's Fähigkeiten demonstrieren.*

---

## 🔧 Hardware

### The Frankenstein MiniPC — 5 GPUs, 192 GB VRAM / Der Frankenstein-MiniPC — 5 GPUs, 192 GB VRAM

How a tiny AOOSTAR GEM 10 MiniPC ended up with five eGPUs over M.2→OCuLink and USB4 — photos, cost breakdown, model configurations, lessons learned. Today: 2× Quadro RTX 8000 (48 GB) + 3× Tesla V100 (32 GB).

*Wie ein winziger AOOSTAR-GEM-10-MiniPC zu fünf eGPUs über M.2→OCuLink und USB4 kam — mit Fotos, Kostenaufstellung, Modellkonfigurationen und Erfahrungen. Heute: 2× Quadro RTX 8000 (48 GB) + 3× Tesla V100 (32 GB).*

- 🇬🇧 **[Full setup (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Hardware_Setup_Frankenstein_MiniPC.html)**

---

## 📈 Benchmarks *(historical, P40 era / historisch, P40-Zeit)*

### "Dog vs Cat" Tribunal — 9 models, 18 sessions / „Hund oder Katze"-Tribunal — 9 Modelle, 18 Sessions

Same question, nine models in Tribunal mode. Qwen3-Next-80B (3B active) matches Qwen3-235B in debate quality at three times the speed; GPT-OSS-120B is the speed champion; GLM-4.7-REAP at IQ3_XXS invented its own language.

*Dieselbe Frage, neun Modelle im Tribunal-Modus. Qwen3-Next-80B (3B aktiv) erreicht die Debattenqualität von Qwen3-235B bei dreifachem Tempo; GPT-OSS-120B ist der Tempo-Sieger; GLM-4.7-REAP in IQ3_XXS erfand eine eigene Sprache.*

- ⭐ [Qwen3-Next-80B (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_Qwen3-Next-80B_Tribunal_DE.html) · [(EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_Qwen3-Next-80B_Tribunal_EN.html) · [Thinking (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_Qwen3-Next-80B-Thinking_Tribunal_DE.html)
- ⭐ [Qwen3-235B Q3 (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_Qwen3-235B-Q3_Tribunal_DE.html) · [Q2 (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_Qwen3-235B-Q2_Tribunal_DE.html) · [Q2 (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_Qwen3-235B-Q2_Tribunal_EN.html)
- 🏃 [GPT-OSS-120B (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_GPT-OSS-120B_Tribunal_DE.html) · [(EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_GPT-OSS-120B_Tribunal_EN.html)
- 📊 [Qwen3.5-122B (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_Qwen3.5-122B_Tribunal_DE.html) · [MiniMax-M2.5 IQ3 (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_MiniMax-M2.5-IQ3_Tribunal_EN.html) · [MiniMax-M2.5 Q2 (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_MiniMax-M2.5-Q2_Tribunal_EN.html)
- 💀 [GLM-4.7-REAP IQ3_XXS (DE/EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_GLM-4.7-REAP-218B_Tribunal_DE.html)
- 📄 Analysis / Analyse: [EN](https://github.com/Peuqui/AIfred-Intelligence/blob/main/docs/en/benchmarks/analysis-v2.md) · [DE](https://github.com/Peuqui/AIfred-Intelligence/blob/main/docs/de/benchmarks/analysis-v2.md)

### Tensor split: speed vs. full context / Tensor-Split: Tempo gegen vollen Kontext

A 46.6 GB model on two unequal GPUs, balanced 2:1 split against an aggressive 11:1 split — measured through a real tribunal debate.

*Ein 46,6-GB-Modell auf zwei ungleichen GPUs, ausgewogene 2:1-Aufteilung gegen aggressive 11:1 — gemessen in einer echten Tribunal-Debatte.*

- 🇬🇧 **[Full benchmark (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Benchmark_Tensor_Split_EN.html)** · data: [EN](https://github.com/Peuqui/AIfred-Intelligence/blob/main/docs/en/benchmarks/tensor-split.md) · [DE](https://github.com/Peuqui/AIfred-Intelligence/blob/main/docs/de/benchmarks/tensor-split.md)

---

## 🏆 Multi-Agent Debates / Multi-Agent Debatten

### Philosophical Debate: Dog or Cat? / Philosophische Debatte: Hund oder Katze?

**Why this example is remarkable:** Research shows multi-agent debate systems commonly fail through "rubber-stamping" (critics just agreeing), echo chambers, and information loss during synthesis ([arxiv:2503.13657](https://arxiv.org/abs/2503.13657)). This debate demonstrates AIfred avoiding all these failure modes – running on a local 30B model.

*Warum dieses Beispiel bemerkenswert ist: Forschung zeigt, dass Multi-Agent-Debate-Systeme häufig durch "Rubber-Stamping" (Kritiker stimmen nur zu), Echo-Kammern und Informationsverlust bei der Synthese scheitern. Diese Debatte zeigt, wie AIfred all diese Fehlerarten vermeidet – auf einem lokalen 30B-Modell.*

**Categorical Progression / Kategoriale Progression:**
1. **Phase 1 - Characterology:** Dog = Servant, Cat = Queen
2. **Phase 2 - Virtue Ethics:** Dog = aretē (virtue), Cat = contemplatio
3. **Phase 3 - Relationship Theory:** Both as teachers of the soul
4. **Phase 4 - Meta-Ethics:** Animal as fellow citizen, not tool

**What makes this special / Was es besonders macht:**
- Sokrates **actually disagrees** (no rubber-stamping)
- Salomo **synthesizes without information loss**
- Roles **remain stable** across all turns
- Running on **Qwen3:30B locally** (not GPT-4)

**Interactive Debate / Interaktive Debatte:**
- 🇩🇪 **[Vollständige Debatte MIT Persönlichkeiten (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Hund_oder_Katze_DE.html)**
- 🇬🇧 **[Full Debate WITH Personalities (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Dog_or_Cat_EN.html)**

**Analysis / Analyse:**
- 🇩🇪 **[Analyse inkl. A/B-Vergleich (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Showcase_Hund_oder_Katze_MultiAgent_Debatte_DE.html)**
- 🇬🇧 **[Analysis incl. A/B Comparison (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Showcase_Dog_or_Cat_MultiAgent_Debate_EN.html)**

---

### NEW: A/B Comparison - With vs. Without Personality Prompts

**Experimental evidence for the value of personality prompts:** The same question was asked twice – once with personality prompts enabled, once without. The results show significant differences in argument depth, voice distinction, and dialectical sharpness.

*Experimenteller Nachweis für den Wert der Persönlichkeits-Prompts: Dieselbe Frage wurde zweimal gestellt – einmal mit aktivierten Persönlichkeits-Prompts, einmal ohne. Die Ergebnisse zeigen signifikante Unterschiede in Argumentationstiefe, Stimm-Distinktion und dialektischer Schärfe.*

| Aspect / Aspekt | WITH Personalities | WITHOUT Personalities |
|-----------------|-------------------|----------------------|
| **Crux identification** | Sokrates finds "What does 'better' mean?" | Topic switch to "responsibility" |
| **Voice distinction** | Clear Butler/Philosopher/Judge voices | Similar academic tone |
| **Argumentation** | Direct attack on premise | Constructive additions |
| **Final result** | "A house with both has heart and mind" | "Between convenience and responsibility" |

**Comparison Debates / Vergleichs-Debatten:**
- 🇩🇪 **[Debatte OHNE Persönlichkeiten (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Hund_oder_Katze_OHNE_Persoenlichkeiten_DE.html)**
- 🇬🇧 **[Debate WITHOUT Personalities (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Dog_or_Cat_WITHOUT_Personalities_EN.html)**

**Detailed analysis in the main showcase / Detaillierte Analyse im Haupt-Showcase** (see links above)

---

### Tribunal: "Should every error be logged?" / Tribunal: „Sollte jeder Fehler geloggt werden?"

In Tribunal mode Sokrates acts as prosecutor, not coach — AIfred must defend or revise, there is no voting. A/B comparison with and without personality prompts.

*Im Tribunal-Modus ist Sokrates Ankläger, nicht Coach — AIfred muss verteidigen oder revidieren, abgestimmt wird nicht. A/B-Vergleich mit und ohne Persönlichkeits-Prompts.*

- 🇩🇪 [MIT Persönlichkeiten (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Error_Handling_Tribunal_MIT_Persoenlichkeiten_DE.html) · [OHNE (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Error_Handling_Tribunal_OHNE_Persoenlichkeiten_DE.html) · [Analyse (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Showcase_Error_Handling_Tribunal_Debatte_DE.html)
- 🇬🇧 [WITH personalities (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Error_Handling_Tribunal_WITH_Personalities_EN.html) · [WITHOUT (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Error_Handling_Tribunal_WITHOUT_Personalities_EN.html) · [Analysis (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Showcase_Error_Handling_Tribunal_Debate_EN.html)
- Tribunal version of the dog-or-cat debate / Tribunal-Fassung der Hund-oder-Katze-Debatte: [DE](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Hund_oder_Katze_Tribunal_DE.html) · [EN](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Dog_or_Cat_Tribunal_EN.html) · analysis [DE](https://peuqui.github.io/AIfred-Intelligence/examples/Showcase_Hund_oder_Katze_Tribunal_Debatte_DE.html) · [EN](https://peuqui.github.io/AIfred-Intelligence/examples/Showcase_Dog_or_Cat_Tribunal_Debate_EN.html)

### A/B test: code review with vs. without personalities / A/B-Test: Code-Review mit und ohne Persönlichkeiten

"Should I split this Python function?" in Auto-Consensus mode — personality adds style, the technical analysis stays equivalent.

*„Sollte ich diese Python-Funktion aufteilen?" im Auto-Konsens-Modus — die Persönlichkeit bringt Stil, die technische Analyse bleibt gleichwertig.*

- 🇩🇪 [MIT Persönlichkeit (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Code_Review_MIT_Persoenlichkeiten_DE.html) · [OHNE (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Code_Review_OHNE_Persoenlichkeiten_DE.html)
- 🇬🇧 [WITH personality (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Code_Review_WITH_Personalities_EN.html) · [WITHOUT (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/AIfred_Code_Review_WITHOUT_Personalities_EN.html)

---

## 🔬 Science & Math / Wissenschaft & Mathematik

### Chemistry: Balancing Combustion Equations / Chemie: Verbrennungsgleichungen ausgleichen

AIfred explains how to balance the combustion of ethanol step-by-step, with proper chemical notation rendered via mhchem.

*AIfred erklärt Schritt für Schritt, wie man die Verbrennung von Ethanol ausgleicht, mit korrekter chemischer Notation via mhchem.*

![Chemistry Example](Chemie.png)

- 🇩🇪 **[Vollständiger Chat (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Chemie.html)**
- 🇬🇧 **[Full Chat (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Chemistry_EN.html)** 
---

### Physics: Schrödinger Equation for a Victorian Gentleman / Physik: Schrödinger-Gleichung für einen viktorianischen Gentleman

"Explain the Schrödinger equation as if I'm a Victorian gentleman" – AIfred delivers with historical context, elegant LaTeX formulas, and drawing-room analogies.

*"Erkläre die Schrödinger-Gleichung, als wäre ich ein viktorianischer Gentleman" – AIfred liefert mit historischem Kontext, eleganten LaTeX-Formeln und Salon-Analogien.*

![Math Example](Math.png)

- 🇩🇪 **[Vollständiger Chat (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Math.html)**
- 🇬🇧 **[Full Chat (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Math_EN.html)** 
---

## 💻 Coding

### Python: Prime Number Calculator / Python: Primzahl-Rechner

A refined Sieve of Eratosthenes implementation with type hints, docstrings, and Butler-style code comments.

*Eine verfeinerte Sieb-des-Eratosthenes-Implementierung mit Type Hints, Docstrings und Butler-Stil Kommentaren.*

![Coding Example](Coding.png)

- 🇩🇪 **[Vollständiger Chat (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/Coding.html)**
- 🇬🇧 **[Full Chat (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/Coding_EN.html)** 
---

## 🌐 Web Research

### Medical Research: Spinal Anesthesia Guidelines / Medizinische Recherche: Spinalanästhesie-Leitlinien

Complex medical query about spinal anesthesia in patients with myasthenia gravis. AIfred searches medical literature, synthesizes findings, and provides well-referenced answers.

*Komplexe medizinische Anfrage über Spinalanästhesie bei Patienten mit Myasthenia gravis. AIfred durchsucht medizinische Literatur, synthetisiert Ergebnisse und liefert gut referenzierte Antworten.*

![Web Research Example](WebResearch.png)

- 🇩🇪 **[Vollständiger Chat (DE)](https://peuqui.github.io/AIfred-Intelligence/examples/WebResearch.html)**
- 🇬🇧 **[Full Chat (EN)](https://peuqui.github.io/AIfred-Intelligence/examples/WebResearch_EN.html)** 
---

## 📊 Model Comparison / Modellvergleich

Performance and quality analysis of different LLMs for the AIfred Butler style.

*Performance- und Qualitätsanalyse verschiedener LLMs für den AIfred Butler-Stil.*

- 🇩🇪 **[Modellvergleich (DE)](MODEL_COMPARISON_DE.md)**
- 🇬🇧 **[Model Comparison (EN)](MODEL_COMPARISON_EN.md)**

---

## 📸 Screenshots

**Chat Export (Portable HTML) / Chat-Export (Portables HTML)**

![Chat Export](cats-vs-dogs-export.png)

**AIfred UI with Debug Console / AIfred UI mit Debug-Konsole**

![AIfred UI](cats-vs-dogs-ui.png)

---

*AIfred Intelligence – Self-hosted Multi-Agent AI Assistant*
