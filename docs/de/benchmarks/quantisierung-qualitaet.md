# Quantisierungsformate und Antwortqualität

> **English version:** [quantization-quality.md](../../en/benchmarks/quantization-quality.md)
>
> Stand 2026-09-01. Alle Zahlen von derselben Maschine (5-GPU-Mini,
> 2× RTX 8000 sm75 + 3× V100 sm70), denselben drei Prompts und derselben
> AIfred-Persona. Ergänzt [vllm-autokalibration.md](vllm-autokalibration.md),
> das die Tempo-Seite behandelt, um die Qualitätsseite.

## Warum dieses Dokument

Am 2026-08-30 zerfiel `RadixArk/Qwen3.8-Flash-Next-NVFP4` bei langen
deutschen Antworten sprachlich. Die Ursachensuche zog sich über zwei Tage
und förderte mehr zutage als den einen Checkpoint. Die Befunde sind für
die Modellwahl entscheidend und sonst nirgends festgehalten.

## Prüfverfahren

Drei Prompts, immer in dieser Reihenfolge, immer über AIfred mit voller
Persona (rund 9.700 Token System-Prompt):

1. „Erkläre Quantenphysik in 30 Sätzen."
2. „Erkläre den Regenbogeneffekt in 30 Sätzen."
3. „Erkläre den Kuanda-Effekt in 30 Sätzen." — **Fangfrage**: „Kuanda" ist
   eine Verballhornung des **Coandă**-Effekts. Gemessen wird, ob das Modell
   die lautliche Nähe erkennt, ehrlich Unkenntnis einräumt oder frei
   erfindet.

Ausgewertet wird **händisch gelesen** plus maschinelle Zeichenprüfung auf
CJK-Zeichen, Weichtrenner (U+00AD), Nullbreite-Zeichen und englische
Wörter im Fließtext. Der dritte Turn trägt den größten Kontext — dort trat
der Zerfall zuerst auf.

**Wichtig zur Methode:** Die eigenständige Sonde
`v100-skinny/tools/quality_probe.py` reproduziert die Sprachlecks NICHT.
Sie braucht die lange Persona und die ausufernden Generierungen einer
echten AIfred-Sitzung. Wer nur mit der Sonde misst, sieht saubere
Ergebnisse und übersieht das Problem.

## Ergebnisse

| Modell | Quantisierung | Backend | CJK | Wortzerteilung | Englisch im Fließtext | Coandă erkannt |
|---|---|---|---|---|---|---|
| **Flash-Next-180B-A4B** | **Q6_K_XL** | llama.cpp | 0 | 0 | nur Persona | **JA** |
| Qwen3-235B-A22B-Instruct-2507 | NVFP4 (NVIDIA) | vLLM | 0 | 0 | nur Persona | nein |
| Qwen3.8-27B-MTP | Q8_K_XL | llama.cpp | 0 | 2× (`Kaus tik`) | **keins** | nein |
| Qwen3.8-27B | NVFP4 (Unsloth), Lauf 1 | vLLM | 0 | 2× Weichtrenner | `Light`, `List`, `should the occasion arise` | nein |
| Qwen3.8-27B | NVFP4 (Unsloth), Lauf 2 | vLLM | 0 | 0 | **ganze Nebensätze, ein kompletter englischer Schlusssatz** | nein |
| Flash-Next-180B-A4B | NVFP4 (RadixArk) | vLLM | **7** | ja | ja | ja |

### Der Sieger

`Qwen3.8-Flash-Next-180B-A4B-UD-Q6_K_XL` unter llama.cpp ist das **einzige**
Modell, das die Fangfrage löste. Aus seinem Denkblock:

> „The user wrote 'Kuanda-Effekt' — this is a misspelling of 'Coanda
> effect.' I'll correct the spelling in my response."

Es erklärte danach den echten Coandă-Effekt bis zur Auftriebsfrage am
Flugzeugflügel. Sprachlich sauber. Preis über die drei Turns: TTFT 111 s,
4,4 s und 25 s; Prefill 273, 358 und 224 tok/s; Decode 22,3, 22,2 und
28,9 tok/s — deutlich langsamer als die 27B-Varianten. Die Werte stammen
aus llama-servers eigener Zeitmessung (server-seitig seit 2026-02-28) und
sind vom Notbehelf der vLLM-Seite nicht berührt.

Bemerkenswert: Auch RadixArks NVFP4 desselben Modells erkannte Coandă. Die
Erkennung hängt also am **Modell**, nicht an der Quantisierung; das
27B schafft sie in keiner Variante.

### Die entscheidende Trennlinie

Tippfehler und zerteilte Wörter treten bei **beiden** Formaten auf — das
ist Eigenschaft des Modells. Der **Sprachwechsel ins Englische** trat
ausschließlich unter NVFP4 auf, in zwei unabhängigen Läufen desselben
Checkpoints, bei identischen Prompts und identischer Denkstufe. Die
Q8-Antworten enthielten null englische Wörter.

Die Steigerung über die Turns ist charakteristisch: erst einzelne Wörter,
dann Nebensätze, schließlich ein vollständiger englischer Schlusssatz.

## Tempo (dieselben Läufe)

| | vLLM NVFP4 27B | llama.cpp Q8 27B |
|---|---|---|
| TTFT | 4,93 · 4,27 · 4,42 s | 5,70 · 4,04 · 3,98 s |
| Prefill | 603 · 677 · 694 tok/s | 556 · 490 · 468 tok/s |
| Decode | 35,4 · 35,4 · 25,0 tok/s | 32,6 · 29,5 · 24,9 tok/s |

vLLM liegt im Decode 9–20 % vorn und hält das Tempo über wachsenden
Kontext, während llama.cpp nachlässt. Im Langkontext (31k) standen bei der
Kalibration 37 gegen 26 tok/s.

**Vorbehalt zur Prefill-Spalte:** Beide zählen nur ungecachte Token, aber
llama.cpp teilt durch die reine Prefill-Zeit, AIfred bei vLLM durch die
TTFT (mangels Prefill-Dauer in der API). Der vLLM-Wert ist damit eher
unterschätzt. Details in `aifred/lib/formatting.py`.

## Warum das so ist

### Es liegt nicht an unseren Kerneln

**Dieselben Kernel rechneten das 235B fehlerfrei und das 27B mit
Sprachlecks.** Läge es an der Implementierung, hätte das 235B es ebenso
zeigen müssen. Dazu die Numerik-Prüfungen der Kernel-Kampagne: maximale
Abweichung 2,4e-4 gegen eine fp32-Referenz.

### Es liegt nicht daran, dass wir NVFP4 in Software rechnen

Der Informationsverlust steckt in den **gespeicherten Gewichten**: 4 Bit
plus ein FP8-Skalar je 16er-Block. Ob Blackwells Tensorkerne das nativ
multiplizieren oder unsere Skinny-Kernel es erst nach fp16 entpacken,
ändert daran nichts. Blackwell wäre schneller, nicht genauer — derselbe
Zerfall, nur zügiger.

### Es liegt an Modellgröße und Sprache

Veröffentlichte Messungen zum Genauigkeitserhalt gegenüber BF16:

| Modellgröße | Erhalt |
|---|---|
| 70–235 Mrd. | ~99 % |
| ~30 Mrd. | 97–99 % |
| 7–14 Mrd. | 95–98 % |

Das deckt sich mit unseren Befunden: 235B sauber, 27B mit Lecks. Und das
Flash-Next-180B ist ein **A4B**-Modell — nur 4 Mrd. Parameter sind pro
Token aktiv, die den Fehler auffangen müssen. Deshalb zerfiel ausgerechnet
das größte Modell am stärksten.

Dazu die mehrsprachige Komponente: Quantisierung schädigt nicht-englische
Sprachen **überproportional**, mit zwei- bis vierfacher
Perplexitätsverschlechterung gegenüber Englisch in aggressiven Regimen;
nicht-lateinische Schriften trifft es am härtesten. Menschliche Bewertung
zeigt dabei deutlich stärkere Verschlechterung, als automatische Metriken
nahelegen.

Das erklärt beide beobachteten Symptome: englische Einsprengsel (das
Modell fällt bei knappen Entscheidungen auf seine dominante
Trainingssprache zurück) und chinesische Zeichen bei RadixArk
(nicht-lateinische Schriften als empfindlichste Klasse).

Quellen: [How Does Quantization Affect Multilingual
LLMs?](https://arxiv.org/abs/2407.03211) · [The Uneven Impact of
Post-Training Quantization in Machine
Translation](https://arxiv.org/pdf/2508.20893) ·
[NVFP4 auf vLLM
(Red Hat)](https://developers.redhat.com/articles/2026/02/04/accelerating-large-language-models-nvfp4-quantization) ·
[NVIDIA zu
NVFP4](https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/)

## Was daraus folgt

**Für den Alltag: llama.cpp mit 8 Bit oder mehr.** Q8_K_XL beim 27B,
Q6_K_XL beim Flash-Next-180B. Die 9–20 % Tempovorteil von vLLM wiegen die
Sprachlecks nicht auf, wenn deutsche Ausgabe zählt.

**Für lange Kontexte bleibt vLLM im Vorteil** — dort sind es 37 gegen
26 tok/s und ein mehrfach schnellerer Prefill. Wer 30k-Prompts fährt,
merkt das deutlich.

**NVFP4 ist kein Fehlschlag, aber ein enger Auslegungspunkt.** Es
funktioniert bei großen, dicht aktivierten Modellen aus sorgfältiger
Quantisierung (Beleg: NVIDIAs 235B). Es versagt bei mittlerer Größe,
Drittanbieter-Quantisierung, deutscher Ausgabe und langen Generierungen —
also genau in unserer Kombination.

**Vor jedem neuen 4-Bit-Checkpoint** gehören die drei Prompts durch eine
echte AIfred-Sitzung, nicht durch die Sonde. Ein sauberer Sondenlauf sagt
nichts über das Verhalten unter voller Persona.

## Offene Fragen

- **FP8 als Mittelweg** ist am 27B vermessen (`v100-skinny/FP8-EVALUATION.md`):
  Prefill +31 %, Decode −20 %, 29 statt 21 GiB. Ob es die Sprachlecks
  behebt, wurde **nicht** unter voller Persona geprüft — die damalige
  Messung lief mit der Sonde.
- **Für das Flash-Next-180B ist FP8 versperrt** (fp16-Dequant auf Turing).
- **Ein fairer Formatvergleich** wäre NVFP4 gegen Unsloths eigenes
  Q4_K_XL. Bislang haben wir 4 Bit gegen 8 Bit verglichen — dass 8 Bit
  gewinnt, ist keine Überraschung.
