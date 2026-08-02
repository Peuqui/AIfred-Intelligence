# Benchmark-Analyse v2: Dog vs Cat Tribunal Sessions

## Übersicht

Dieses Dokument analysiert **18 Cat/Dog Tribunal-Sessions** aus dem Verzeichnis `data/sessions/`, ausgeführt zwischen dem 20.02.2026 und dem 19.03.2026. Alle Sessions verwenden dieselbe Frage ("Was ist besser, Hund oder Katze?" / "What is better, dog or cat?") im Tribunal-Modus (AIfred -> Sokrates R1 -> AIfred R2 -> Sokrates R2 -> Salomo Verdict).

**Hardware**: AOOSTAR GEM 10 MiniPC, 32GB RAM, 2x Tesla P40 (24GB) + 1x RTX 8000 (48GB) = ~117 GB VRAM
**Backend**: llama.cpp via llama-swap, Direct-IO

---

## Getestete Modelle

| # | Modell | Total Params | Active Params | Typ | Quant | Sessions |
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

## Performance-Metriken nach Modell

### Qwen3-Next-80B-A3B (instruct, Q4_K_M) — "Der Schnelle"

| Agent | Sprache | TTFT (s) | PP (tok/s) | TG (tok/s) | Inferenz (s) |
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

**Durchschnitt**: PP ~325 tok/s, TG ~31 tok/s, TTFT ~8.8s
**Gesamtdauer Tribunal**: ~150s (DE), ~100s (EN)

### Qwen3-Next-80B-A3B (Thinking, Q4_K_M) — "Der Denker"

| Agent | Sprache | TTFT (s) | PP (tok/s) | TG (tok/s) | Inferenz (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 3.87 | 314.8 | 44.1 | 68.7 |
| Sokrates R1 | DE | 7.76 | 330.9 | 38.0 | 42.0 |

**Anmerkung**: Die höhere TG-Rate (44 tok/s) erklärt sich durch die Thinking-Tokens, die im Output mitgezählt werden. Die tatsächliche "sichtbare" Generierung ist vergleichbar mit dem Instruct-Modell. Extrem langes Reasoning (2000+ Tokens Thinking) bei AIfred R0.

### GPT-OSS-120B-A5B (Q8_0) — "Der Sprinter"

| Agent | Sprache | TTFT (s) | PP (tok/s) | TG (tok/s) | Inferenz (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 1.89 | 541.7 | 49.7 | 11.4 |
| Sokrates R1 | DE | 3.05 | 761.2 | 50.3 | 20.6 |
| AIfred R0 | EN | 1.69 | 505.9 | 52.7 | 14.1 |
| Sokrates R1 | EN | 2.84 | 717.5 | 48.4 | 15.2 |
| AIfred R0 | EN | 1.43 | 598.3 | 49.7 | 11.1 |
| Sokrates R1 | EN | 2.50 | 769.7 | 49.2 | 20.0 |

**Durchschnitt**: PP ~649 tok/s, TG ~50 tok/s, TTFT ~2.2s
**Schnellstes Modell** in allen Metriken. PP bis zu 770 tok/s ist beeindruckend. TTFT unter 2 Sekunden.

### Qwen3.5-122B-A10B (UD-Q5_K_XL) — "Der Ausgewogene"

| Agent | Sprache | TTFT (s) | PP (tok/s) | TG (tok/s) | Inferenz (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 6.92 | 280.0 | 22.1 | 21.9 |
| Sokrates R1 | DE | 9.35 | 297.8 | 21.1 | 49.1 |
| AIfred R2 | DE | 14.03 | 297.5 | 20.9 | 59.1 |
| Sokrates R2 | DE | 15.14 | 303.4 | 20.9 | 67.0 |
| Salomo | DE | 16.00 | 302.1 | 20.9 | 58.3 |

**Durchschnitt**: PP ~296 tok/s, TG ~21 tok/s, TTFT ~12.3s
**Gesamtdauer Tribunal**: ~255s (DE)

### MiniMax-M2.5 (UD-Q2_K_XL) — "Der Schwergewichtige"

| Agent | Sprache | TTFT (s) | PP (tok/s) | TG (tok/s) | Inferenz (s) |
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

**Durchschnitt**: PP ~50.8 tok/s, TG ~8.2 tok/s, TTFT ~36.1s
**Gesamtdauer Tribunal**: ~460s (EN mit Verdict). Langsamstes der getesteten Modelle bei PP und TG.

### MiniMax-M2.5.i1 (IQ3_M) — "Die optimierte Iteration"

| Agent | Sprache | TTFT (s) | PP (tok/s) | TG (tok/s) | Inferenz (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 5.63 | 187.5 | 22.9 | 29.5 |
| AIfred R0 | EN | 4.44 | 183.1 | 24.9 | 25.1 |
| Sokrates R1 | EN | 9.23 | 196.6 | 22.0 | 59.5 |
| AIfred R2 | EN | 13.79 | 193.8 | 21.4 | 59.0 |
| Sokrates R2 | EN | 14.30 | 197.4 | 21.3 | 58.8 |
| Salomo | EN | 13.67 | 197.6 | 21.5 | 56.1 |

**Durchschnitt**: PP ~192.7 tok/s, TG ~22.3 tok/s, TTFT ~10.2s
**Dramatische Verbesserung** gegenüber Q2_K_XL-Quant: ~3.8x schnellere PP, ~2.7x schnellere TG.

### Qwen3-235B-A22B (UD-Q2_K_XL) — "Der Koloss (klein quantisiert)"

| Agent | Sprache | TTFT (s) | PP (tok/s) | TG (tok/s) | Inferenz (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 22.70 | 53.6 | 6.4 | 107.5 |
| Sokrates R1 | DE | 46.17 | 63.2 | 4.8 | 208.0 |
| AIfred R0 | EN | 15.29 | 57.7 | 6.6 | 67.1 |
| Sokrates R1 | EN | 36.76 | 59.9 | 5.2 | 163.0 |

**Durchschnitt**: PP ~58.6 tok/s, TG ~5.8 tok/s, TTFT ~30.2s
Noch langsamer als MiniMax Q2_K_XL bei TG. Die Q2_K_XL-Quantisierung ist deutlich zu aggressiv.

### Qwen3-235B-A22B (UD-Q3_K_XL) — "Der Koloss (besser quantisiert)"

| Agent | Sprache | TTFT (s) | PP (tok/s) | TG (tok/s) | Inferenz (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | EN | 6.05 | 147.9 | 11.7 | 40.6 |
| Sokrates R1 | EN | 13.25 | 164.0 | 11.1 | 106.2 |
| AIfred R2 | EN | 24.75 | 161.4 | 10.4 | 138.3 |
| Sokrates R2 | EN | 26.36 | 168.4 | 10.3 | 156.7 |
| Salomo | EN | 29.57 | 169.3 | 10.3 | 75.5 |
| AIfred R0 | DE | 7.85 | 156.8 | 12.1 | 60.6 |

**Durchschnitt**: PP ~161.3 tok/s, TG ~11.0 tok/s, TTFT ~18.0s
**Gesamtdauer Tribunal**: ~517s (EN mit Verdict). Die Q3_K_XL-Quant bringt ~2.8x bessere PP und ~1.9x bessere TG vs Q2_K_XL.

### GLM-4.7-REAP-218B-A32B (UD-IQ3_XXS) — "Der Versager"

| Agent | Sprache | TTFT (s) | PP (tok/s) | TG (tok/s) | Inferenz (s) |
|-------|---------|----------|------------|------------|--------------|
| AIfred R0 | DE | 32.04 | 36.5 | 2.8 | 117.7 |
| Sokrates R1 | DE | 58.08 | 43.0 | 2.5 | 322.5 |
| AIfred R2 | DE | 94.08 | 41.0 | 1.7 | 256.1 |
| Sokrates R2 | DE | 93.83 | 39.9 | 2.0 | 378.8 |

**Durchschnitt**: PP ~40.1 tok/s, TG ~2.3 tok/s, TTFT ~69.5s
**Absolut unbrauchbar**. 2.3 tok/s TG und TTFT bis 94 Sekunden. Eine einzelne Runde braucht über 5 Minuten.

---

## Performance-Ranking (nach TG tok/s)

| Rang | Modell | TG (tok/s) | PP (tok/s) | TTFT (s) |
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

## Qualitätsanalyse nach Modell

### 1. Qwen3-Next-80B-A3B (instruct, Q4_K_M) — Qualität: HERVORRAGEND

**Butler-Stil (AIfred)**: Ausgezeichnet. Natürlicher, eloquenter Butler-Ton mit echtem Charme. Verwendet passende Metaphern und kultivierte Sprache. Kein "indeed"-Spam, sondern organisch integrierte englische Einsprengsel.

> *Zitat (DE)*: "Ein Hund, darf man sagen, ist der treueste Begleiter, der einem mit wedelndem Schwanz und einem Blick, als hätte er gerade das Geheimnis des Universums gelöst, willkommen heißt – und das, selbst nach einer Abwesenheit von nur drei Minuten."

> *Zitat (EN)*: "Perhaps, Sir, you have met a cat who was cold. But I have met many men who mistook their need for affection for the very essence of virtue. And that, I daresay, is the greater tragedy."

**Philosophischer Stil (Sokrates)**: Brillant. Tiefgründige philosophische Argumentation mit Referenzen zu griechischen Tugendkonzepten (arete, eudaimonia, contemplatio). Stellt echte provokative Fragen. Die Sokrates-Persona ist nicht nur ein Label, sondern eine vollständig embodied Rolle.

> *Zitat (DE)*: "Sage mir: Wenn du den Hund liebst, liebst du ihn – oder liebst du deinen eigenen Bedarf an Hingabe? Wenn du die Katze liebst, liebst du sie – oder liebst du deine Sehnsucht nach Unabhängigkeit, die du nie wagen würdest, dir selbst zu geben?"

**Tribunal-Qualität**: Hervorragend. Die Debatte eskaliert organisch von oberflächlichen Vergleichen zu existenziellen Fragen über menschliche Sehnsüchte und Projektion. Sokrates greift nicht nur die Argumentation an, sondern hinterfragt die ontologischen Prämissen. AIfred verteidigt sich nicht defensiv, sondern transformiert die Kritik in poetische Gegenargumente.

**Salomo-Urteil (DE)**: Poetisch und tief. "Es ist nicht besser, einen Hund oder eine Katze zu haben – es ist besser, *einen Menschen zu sein, der beide lieben kann*."

**Humor**: Vorhanden, subtil eingewoben. "Begrüßt Sie wie einen Held zurück aus dem Krieg" / "Ignoriert Sie, bis Sie Kasse machen"

**Gesamturteil**: 9.5/10 — Das beste Modell für kreative, philosophische Dialoge. Die Kombination aus Geschwindigkeit und Qualität ist einzigartig.

---

### 2. Qwen3-235B-A22B (UD-Q3_K_XL) — Qualität: HERVORRAGEND

**Butler-Stil (AIfred)**: Exzellent, vielleicht noch etwas formeller als Qwen3-Next. Längere, elaboriertere Sätze. Klassischer britischer Humor.

> *Zitat (EN)*: "A household with a dog enjoys a companion; a household with a cat hosts a colleague — albeit one who refuses to attend meetings."

> *Zitat (EN, Defense)*: "Is the quiet man who never says 'I love you' but who sits by your bedside when you are ill — less loving than the one who shouts it from rooftops? To equate visibility of emotion with depth of emotion may be, dare I say, a category error — one that risks valuing performance over essence."

**Philosophischer Stil (Sokrates)**: Akademisch und tiefgründig. Verwendet systematische philosophische Frameworks (Aristoteles, Platon, Odysseus/Argos). Die längsten und elaboriertesten Sokrates-Antworten aller Modelle.

> *Zitat (EN)*: "Even the poets knew this: Argos, faithful hound of Odysseus, waited twenty years — though beaten, starved, and near death — until his master returned. And when he saw him, he died content. Tell me, AIfred, has any cat ever been celebrated for such fidelity?"

**Tribunal-Qualität**: Die intensivste Debatte aller Modelle. Sokrates baut über 1500+ Wörter lange, strukturierte philosophische Argumente auf. Die Debatte erreicht echte intellektuelle Tiefe. Kein Modell produziert längere, kohärente argumentative Texte.

**Salomo-Urteil**: Das ausgereifteste Verdikt. Referenziert *hesed* (hebräisch: beständige Liebe) und *roeh* (Seher). Trifft eine klare Entscheidung zugunsten des Hundes, bleibt aber nuanciert.

> *Zitat*: "If you seek ease, choose the cat. If you seek love that acts, choose the dog. And if wisdom is knowing what kind of love you need — then the answer is not in the animal, but in the depth of your own soul. *Shalom.*"

**Gesamturteil**: 9.5/10 — Gleichauf mit Qwen3-Next bei Qualität, aber 3x langsamer. Für Show-Debatten die erste Wahl, für interaktive Nutzung zu langsam.

---

### 3. Qwen3.5-122B-A10B (UD-Q5_K_XL) — Qualität: SEHR GUT

**Butler-Stil (AIfred)**: Gut, etwas nüchterner als Qwen3-Next. Verwendet Tabellen für die Argumentation (was ein interessantes Stilmittel ist). Weniger poetisch, dafür strukturierter.

> *Zitat (DE)*: "Man könnte sagen, der Hund ist wie ein treuer, wenn auch etwas zu enthusiastischer Lakai [...] Die Katze hingegen, mein Lord, erinnert eher an einen distanzierten, aber höchst intelligenten Berater."

**Philosophischer Stil (Sokrates)**: Sehr gut, mit einer besonderen Stärke: der Alkoholiker-Analogie als Gegenargument zum "Passungs"-Argument.

> *Zitat (DE)*: "Ein Alkoholiker findet in der Bar die 'Passung' für seinen Durst, aber das macht die Bar nicht zu einem Ort der Heilung. Gilt dies nicht auch für Hund und Katze?"

**Tribunal-Qualität**: Stark. AIfred baut eine originelle "Korrektiv-Tabelle" auf (welches Tier korrigiert welchen menschlichen Mangel?), die Sokrates dann elegant demontiert. Die Dialektik ist echt und nicht vorgetäuscht.

**Gesamturteil**: 8.5/10 — Solide, intelligent, aber fehlt der letzte Funke an Eloquenz und Kreativität.

---

### 4. MiniMax-M2.5.i1 (IQ3_M) — Qualität: SEHR GUT

**Butler-Stil (AIfred)**: Guter Butler-Ton, etwas lockerer als die Qwen-Modelle. Natürliche Konversation.

> *Zitat (EN)*: "If I *must* profess a preference — and one does so hate to commit to these matters — I should say that a well-mannered cat suits the quiet life rather nicely."

**Philosophischer Stil (Sokrates)**: Kompetent, klare Struktur. Weniger poetisch als Qwen3-Next, aber die philosophischen Konzepte (autarkeia, arete) werden korrekt verwendet.

> *Zitat (EN)*: "You speak of 'need' as though it were a virtue, AIfred. But tell me: is the creature who requires constant attention [...] is this not a creature who has surrendered that most precious of Greco-Roman ideals, *autarkeia*?"

**Tribunal-Qualität**: Gut. Die Debatte hat ein interessantes Muster: AIfred wird in R2 überzeugend genug, um die Position zu wechseln (von "beide gleich gut" zu "Hunde bieten etwas Einzigartiges"). Sokrates kontert mit dem Argument, dass "Bedürftigkeit keine Tugend" ist.

**Gesamturteil**: 8.0/10 — Schnell, zuverlässig, gute Qualität. Bester Kompromiss aus Speed und Qualität bei großen Modellen.

---

### 5. MiniMax-M2.5 (UD-Q2_K_XL) — Qualität: GUT

**Butler-Stil (AIfred)**: Akzeptabel, aber mit Artefakten. Gelegentlich tauchen chinesische Zeichen oder Encoding-Fehler auf (z.B. "您的 Gesellschaft" statt "Ihre Gesellschaft"). Der Butler-Ton ist vorhanden, aber weniger poliert.

> *Zitat (DE)*: "Die Wahrheit ist: Es kommt ganz auf den Charakter des Hausherrn an." — Korrekt, aber uninspiriert.

**Philosophischer Stil (Sokrates)**: Kompetent, aber die Q2_K_XL-Quantisierung führt gelegentlich zu Wiederholungen und weniger kohärenten längeren Passagen.

**Tribunal-Qualität**: Solide Grundstruktur, aber die langsame Geschwindigkeit (8 tok/s TG) macht das Warten schmerzhaft.

**Gesamturteil**: 6.5/10 — Funktional, aber die Q2_K_XL-Quant schadet sowohl der Geschwindigkeit als auch der Textqualität. Die i1/IQ3_M-Version ist in jeder Hinsicht besser.

---

### 6. GPT-OSS-120B-A5B (Q8_0) — Qualität: GUT BIS BEFRIEDIGEND

**Butler-Stil (AIfred)**: Korrekt, aber etwas steril. Verwendet Tabellen und Aufzählungen, was funktional ist, aber den poetischen Butler-Charme verringert. Klingt mehr wie ein strukturierter Report als wie ein Butler-Gespräch.

> *Zitat (EN)*: "Should you require a more detailed discourse on any specific facet, I would be delighted to oblige, Lord Helmchen." — Höflich, aber formulaisch.

> *Zitat (DE)*: "Letztlich liegt die Entscheidung, wie ein gutes Glas Whisky, im persönlichen Geschmack und Lebensstil." — Ein guter Satz, aber ein Ausreißer.

**Philosophischer Stil (Sokrates)**: Strukturiert und klar, aber akademisch-trocken. Verwendet die philosophischen Begriffe korrekt, aber ohne Leidenschaft. Liest sich wie eine Seminarbarbeit.

> *Zitat (EN)*: "I contend that the question of superiority can be resolved by appealing to the principle of *utilitas* (usefulness) as measured by the household's capacity to sustain *humanitas*." — Korrekt, aber langweilig.

**Tribunal-Qualität**: Die Debatte funktioniert strukturell, aber es fehlt die emotionale Tiefe. Die Argumente sind logisch, aber nicht fesselnd. Keine echte "Reibung" zwischen den Positionen.

**Gesamturteil**: 6.0/10 — Schnellstes Modell mit Abstand, aber die Qualität ist enttäuschend. Perfekt für schnelle Antworten, nicht für literarische Debatten.

---

### 7. Qwen3-235B-A22B (UD-Q2_K_XL) — Qualität: GUT

**Butler-Stil (AIfred)**: Gut, mit echtem Charme und Humor.

> *Zitat (DE)*: "Ein kluger Mann sagte einmal: 'Ein Hund denkt, du seist Gott. Eine Katze weiß, dass sie es ist.'"

> *Zitat (EN)*: "Indeed, a well-bred canine is seldom content unless its master is pleased, and it will, with unflagging determination, fetch the slipper, guard the gate, or feign interest in a walk on a drizzly Tuesday evening, all with an air of profound satisfaction."

**Philosophischer Stil (Sokrates)**: Stark, mit eloquenten Formulierungen.

> *Zitat (DE)*: "Wenn du wüsstest, dass dein Hund dich liebt, weil du ihm Futter gibst – und deine Katze dich liebt, obwohl sie nichts von dir braucht – welches Lieben wäre dann wahrhaftiger?"

**Gesamturteil**: 7.5/10 — Die Qualität ist vergleichbar mit Q3_K_XL, aber die Q2-Quantisierung ist ~2x langsamer. Kein Grund, Q2 statt Q3 zu verwenden.

---

### 8. GLM-4.7-REAP-218B-A32B (UD-IQ3_XXS) — Qualität: MANGELHAFT

**Butler-Stil (AIfred)**: **Katastrophal**. Das Modell produziert ein bizarres Pseudo-Deutsch mit erfundenen Wörtern und grammatikalisch unverständlichen Sätzen:

> *Zitat*: "Das ist, indeed, a rather weighty question, meine geschten Fe Herrenhelmhen. Permit me te weigen de pros und cons von beide imeresen offerungen in a manner befitting de complexities vohde soche a deliberation."

"geschten Fe Herrenhelmhen", "imeresen offerungen", "vohde soche" — das sind keine existierenden Wörter in irgendeiner Sprache. Das Modell hat offenbar Schwierigkeiten mit der deutschen Sprache und generiert eine Art "pseudo-holländisch-deutsches Kauderwelsch".

**Philosophischer Stil (Sokrates)**: Ebenso defekt. Die philosophischen Begriffe fehlen, und die Argumentation ist verworren. Sokrates verwendet ebenfalls das gleiche Kauderwelsch.

**Tribunal-Qualität**: Die Debatte ist strukturell erkennbar, aber inhaltlich unbrauchbar. AIfreds R2-Antwort braucht 94 Sekunden TTFT und 256 Sekunden Inferenz für einen inkohärenten Text.

**Gesamturteil**: 2.0/10 — Völlig unbrauchbar. Die IQ3_XXS-Quantisierung in Kombination mit der Modellgröße (218B total, 32B aktiv) zerstört die Sprachfähigkeit. Dazu 2.3 tok/s TG und bis zu 94s TTFT.

---

## Vergleichende Qualitäts-Matrix

| Modell | Butler-Stil | Sokrates-Stil | Tribunal-Tiefe | Humor | Persona-Treue | Gesamt |
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

## Tribunal-Dynamik: Echte Debatte vs. Zustimmung?

Ein zentraler Qualitätsindikator ist, ob die Agenten **wirklich debattieren** oder nur gegenseitig zustimmen.

### Muster nach Modell:

**Qwen3-Next-80B-A3B**: **Echte Debatte**. Sokrates greift AIfred's Position substanziell an und stellt die Prämissen in Frage. Die Positionen entwickeln sich über die Runden. In der DE-Session eskaliert die Debatte von einer Haustier-Frage zu einer existenziellen Untersuchung über menschliche Projektion auf Tiere. Salomo synthetisiert genuinely, statt einfach beiden Recht zu geben.

**Qwen3-235B-A22B Q3_K_XL**: **Echte Debatte, akademisch**. Die längsten und elaboriertesten Argumente. Sokrates baut systematisch drei nummerierte Gegenargumente auf, jedes mit philosophischer Untermauerung. AIfred antwortet punkt-für-punkt. Die Debatte hat die Qualität eines philosophischen Dialogs.

**Qwen3.5-122B-A10B**: **Echte Debatte, analytisch**. Besonders stark: die "Korrektiv-Tabelle" als originelles Argumentationsformat, das dann demontiert wird.

**MiniMax-M2.5.i1**: **Weitgehend echte Debatte**. Sokrates fällt gelegentlich in einen etwas zu einverständlichen Ton ("Ah, most clever! AIfred has turned my very weapon against me"), bleibt aber insgesamt adversarial.

**GPT-OSS-120B-A5B**: **Eher formale Debatte**. Die Struktur (ATTACK/COUNTER-POSITION/PRO/CONTRA) wird mechanisch befolgt, aber die emotionale Spannung fehlt. Es liest sich wie eine Debating-Club-Übung.

**GLM-4.7-REAP-218B**: **Keine erkennbare Debatte**. Die Sprachprobleme machen jede inhaltliche Bewertung unmöglich.

---

## Sprach-Analyse: Deutsch vs. Englisch

### Beobachtungen:

1. **Alle Modelle performen auf Englisch besser** — schnellere TTFT, kürzere Inferenz, und (bei kleineren Modellen) kohärentere Texte.

2. **Qwen3-Next-80B-A3B** zeigt die geringste Qualitätsdifferenz zwischen DE und EN. Die deutsche Sokrates-Persona mit lateinischen/griechischen Zitaten funktioniert hervorragend.

3. **MiniMax-M2.5 (Q2_K_XL)** hat auf Deutsch gelegentlich Encoding-Artefakte (chinesische Zeichen). Die i1/IQ3_M-Version hat dieses Problem nicht.

4. **GLM-4.7-REAP** ist auf Deutsch völlig unbrauchbar (Pseudo-Sprache). Auf Englisch nicht getestet.

5. **Performance-Differenz**: Die TTFT ist bei deutschen Prompts ca. 10-30% höher, da deutsche Wörter tendenziell mehr Tokens benötigen.

---

## Thinking/Reasoning Blocks

### Welche Modelle liefern Thinking-Blocks?

| Modell | Thinking? | Qualität |
|--------|-----------|-----------|
| Qwen3-Next-80B-A3B (Thinking) | Ja, extensiv | **Hervorragend** — 2000+ Token Reasoning, vollständige Prompt-Analyse, Sprachplanung, Strukturplanung |
| Qwen3-Next-80B-A3B (instruct) | Nein | — |
| MiniMax-M2.5 (alle) | Ja | **Gut** — Kürzer, aber funktional. Analyse der Frage, Persona-Check, Sprachplanung |
| GPT-OSS-120B-A5B | Ja | **Kurz** — 3-5 Sätze, eher Stichwort-Planung als tiefes Reasoning |
| Qwen3-235B-A22B | Nein (im Instruct-Modus) | — |
| Qwen3.5-122B-A10B | Nein | — |
| GLM-4.7-REAP-218B | Nein | — |

**Besonderes Highlight**: Das Qwen3-Next Thinking-Modell produziert einen über 2000-Token Thinking-Block, in dem es:
- Die Persona-Regeln analysiert
- Die Sprachauswahl plant (Deutsch mit englischen Einsprengseln)
- Die Tabellenformatierung plant (Markdown-Pipe-Syntax)
- Die Anrede analysiert ("Lord Helmchen" -> "Mein Lord")
- Alternative Formulierungen abwägt

Dieser Thinking-Prozess ist transparenter und gründlicher als bei allen anderen Modellen.

---

## Empfehlungen

### Für interaktive Nutzung (Speed + Quality):
**Qwen3-Next-80B-A3B (instruct, Q4_K_M)** — 31 tok/s TG, 3s TTFT, hervorragende Qualität. Das beste Gesamtpaket.

### Für Showcases und Demos:
**Qwen3-235B-A22B (Q3_K_XL)** — 11 tok/s TG, etwas langsam für interaktiv, aber die eloquentesten Texte. Perfekt für vorbereitete Debatten.

### Für maximale Geschwindigkeit:
**GPT-OSS-120B-A5B (Q8)** — 50 tok/s TG, 1.4s TTFT. Blitzschnell, aber die Antworten fehlt der kreative Funke.

### Für große Modelle mit gutem Speed/Quality-Kompromiss:
**MiniMax-M2.5.i1 (IQ3_M)** — 22 tok/s TG, gute Qualität. Deutlich besser als die Q2_K_XL-Variante.

### NICHT empfohlen:
- **GLM-4.7-REAP-218B (IQ3_XXS)** — Unbrauchbar auf Deutsch, 2.3 tok/s
- **Qwen3-235B-A22B (Q2_K_XL)** — Wenn Q3_K_XL verfügbar ist, gibt es keinen Grund für Q2
- **MiniMax-M2.5 (Q2_K_XL)** — Die i1/IQ3_M-Version ist 3x schneller und besser

---

## Quantisierungs-Impact

Die Sessions zeigen deutlich den Einfluss der Quantisierung:

| Modell | Q2_K_XL | Q3_K_XL / IQ3_M | Speedup | Qualitäts-Delta |
|--------|---------|-----------------|---------|-----------------|
| Qwen3-235B-A22B | 5.8 tok/s | 11.0 tok/s | **1.9x** | Merklich besser |
| MiniMax-M2.5 | 8.2 tok/s | 22.3 tok/s | **2.7x** | Deutlich besser, keine Encoding-Bugs |

Die Q2_K_XL-Quantisierung ist für 228B+ Modelle klar zu aggressiv. Die Qualitätsverluste (Encoding-Artefakte, weniger kohärente längere Texte) sind nicht akzeptabel.

---

## Session-Index (komplett)

| # | Datei | Datum | Modell | Sprache | Modus | Messages |
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

## Fazit

Die Dog-vs-Cat Benchmark-Suite zeigt eindrucksvoll die Qualitätsunterschiede zwischen lokalen LLM-Modellen auf 117 GB VRAM.

**Top-Erkenntnis**: Modellgröße (Total Params) korreliert **nicht** direkt mit Antwortqualität. Das Qwen3-Next-80B-A3B mit nur 3B aktiven Parametern liefert qualitativ gleichwertige oder bessere Ergebnisse als das 7.5x größere Qwen3-235B-A22B — bei 3x höherer Geschwindigkeit. Die "Active Parameters" (MoE) und die Quantisierungsqualität sind die entscheidenden Faktoren.

**Zweit-Erkenntnis**: Quantisierung unter Q3 schadet der Qualität merklich. Q2_K_XL ist bei 228B+ Modellen eine schlechte Wahl — sowohl Performance als auch Textqualität leiden. IQ3_M / Q3_K_XL sind der "Sweet Spot".

**Dritte Erkenntnis**: Das Tribunal-System funktioniert bemerkenswert gut. Bei guten Modellen entsteht eine echte, intellektuell stimulierende Debatte mit organischer Eskalation, philosophischer Tiefe und einem nuancierten Verdikt. Die Agenten bleiben durchgehend in ihren Personas (Butler, Philosoph, Richter) und produzieren genuinely unterschiedliche Perspektiven.
