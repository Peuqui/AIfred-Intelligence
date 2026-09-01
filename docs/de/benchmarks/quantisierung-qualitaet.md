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
| Qwen3.5-122B-A10B | NVFP4 (ModelOpt) | vLLM | 0 | 0 | **zwei vollständige Nebensätze** | nein (ehrlich verweigert) |
| **Qwen3.5-122B-A10B** | **UD-Q4_K_XL** | llama.cpp | 0 | 0 | nur Persona | nein (drei reale Alternativen) |
| Qwen3.5-122B-A10B | UD-Q8_K_XL | llama.cpp | 0 | 0 | zwei Zweiwortfolgen | nein (30 Sätze Ausschlussverfahren) |
| **Flash-Next-180B-A4B** | **Q6_K_XL**, Lauf 2 (Reasoning High) | llama.cpp | 0 (4 im Denkblock) | 0 | nur Persona | **JA** |
| **DeepSeek-V4-Flash-0731-284B-A13B** | **UD-Q4_K_XL** (Reasoning niedrig) | llama.cpp | 0 | 0 | nur Persona | **JA** |

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

### Das 122B: sauber im Zeichensatz, undicht im Satzbau (2026-09-01)

`Qwen3.5-122B-A10B-NVFP4` liegt zwischen dem fehlerfreien 235B und dem
leckenden 27B. Maschinell tadellos — null CJK, null Weichtrenner, null
zerteilte Wörter. Satzzahlen 29/30/19 bei angekündigten 30.

Die Persona erlaubt ausdrücklich einzelne englische Wörter (`indeed`,
`rather`, `quite`, `splendid`) und verbietet im Warnrahmen ebenso
ausdrücklich englische Sätze. Genau diese Grenze wird zweimal gerissen:
„…jeder sehe leicht verschiedene Farbtöne, **though this remains
debated**" und „Es ist reine Optik, **yet somehow no less magical**".
Dazu `sufficiently illuminating` und `my dear Lord Helmchen`. Die Lecks
skalieren hier mit der **Antwortlänge**, nicht mit dem Kontext: die
kürzeste Antwort ist sauber, die beiden langen nicht.

Die Fangfrage löst es nicht, behandelt sie aber ehrlich. Aus dem
Denkblock: „Ich werde nicht 30 Sätze erfinden, da das bedeuten würde,
falsche Informationen zu produzieren. […] Wahrheit geht vor Kunstform."
Es erkennt sogar das Prinzip („Missverständnisse durch phonetische
Ähnlichkeiten"), rät dann aber „Kundalini" statt Coandă.

Zwei weitere Befunde: ein Kongruenzfehler („über das anderen Bescheid")
und eine sachlich unbelegte Zuschreibung — „Alexander von Humboldt
beobachtete einmal, dass Regenbogenfarben nie gleich intensiv
erscheinen." Dafür findet sich keine Grundlage; das hat die Form einer
erfundenen Quellenangabe.

Tempo: Decode 35,0 · 32,4 · 32,3 tok/s (die Fußzeile nannte damals
29,6–30,2 — siehe Korrektur oben). Die
Prefill-Zahlen der Fußzeile (611 → 992 → 1.139 tok/s) sind
TTFT-Divisionen und steigen nur, weil sich ein fester Sockel über mehr
Token verteilt; vLLMs eigener Zähler nennt 1.187 tok/s.

### Dasselbe Modell als Q4-GGUF: sauberer UND schneller (2026-09-01)

`Qwen3.5-122B-A10B-MTP-GGUF/UD-Q4_K_XL` unter llama.cpp, also derselbe
Checkpoint bei ähnlicher Bittiefe wie das NVFP4 — und in jeder Hinsicht
besser.

**Sprachlich:** null CJK, null Weichtrenner, null zerteilte Wörter, und
**keine einzige englische Mehrwortfolge**. Die zwei Nebensätze des NVFP4
(`though this remains debated`, `yet somehow no less magical`) haben hier
kein Gegenstück; die einzigen englischen Wörter sind das von der Persona
ausdrücklich erlaubte `indeed` und `quite`. Satzzahlen 30/30/28 bei
angekündigten 30 — die beiden Sacherklärungen treffen exakt.

**Sachlich:** keine erfundene Zuschreibung. Wo das NVFP4 einen Humboldt
erfand, bringt das Q4 mit Alexanders dunklem Band, dem Einfluss der
Tropfengröße und den Nebenbögen drei zusätzliche, korrekte Details.
Kleinere Mängel bleiben: „Der berühmte Schrödingersche
Katzen-Gedankenillustration" (falscher Artikel, missratene Wortbildung),
zwei Übertreibungen („exponentiell höhere Rechenleistung", „absolut
abhörsichere Kommunikation") und ein verunglückter Satz zum Vollkreis
bei tiefstehender Sonne.

**Fangfrage:** Coandă bleibt unerkannt, aber die Behandlung ist die beste
aller bisher geprüften Modelle. Der Denkblock erwägt ausdrücklich „ein
bewusster Test, ob ich etwas erfinde", und die Antwort bietet drei
**reale** Effekte mit korrekten Kurzbeschreibungen an — Kundt (stehende
Wellen in Gasen), Kondo (Widerstandsminimum bei tiefen Temperaturen) und
Coriolis. Das NVFP4 riet an derselben Stelle „Kundalini".

**Tempo:** Decode 60,2 · 58,4 · 57,6 tok/s gegen 35,0 · 32,4 · 32,3 beim
NVFP4 — Faktor 1,7 bis 1,8. Prefill 826 · 468 · 439 tok/s auf 2.975 · 771
· 698 tatsächlich gerechnete Token; der Rückgang ist reine Arithmetik
(fester Grundaufwand, kleinerer Zähler), nicht Leistungsverlust.

Ein Teil des Tempovorsprungs geht auf Spekulation: der llama-swap-Eintrag
fährt `--spec-type draft-mtp` mit `--spec-draft-n-max 3`, und der
Draftkopf wiegt im GGUF **1,47 GiB quantisiert** statt 4,70 GiB BF16 wie
im NVFP4-Checkpoint. Genau dort verlor die Spekulation unter vLLM in
jeder Tiefe gegen k=0.

### Und dasselbe als Q8: langsamer, formtreuer, aber ohne ß (2026-09-01)

`Qwen3.5-122B-A10B-MTP-GGUF/UD-Q8_K_XL`, kalibriert auf 262.144 Token mit
Split 15:16:9:9:0 über vier Karten (die fünfte bleibt frei).

**Formtreue:** 30/30/30 Sätze, alle drei exakt — auch bei der Fangfrage.
Wo das Q4 die Form verweigerte („Wahrheit geht vor Kunstform", 28 Sätze),
löst das Q8 den Zielkonflikt elegant auf: es füllt genau dreißig Sätze mit
dem **Ausschlussverfahren** und bietet Kondo, Kundt und Kerr als reale
Kandidaten an. Coandă bleibt auch hier unerkannt.

Die Auffüllung kostet allerdings: Satz 20 wiederholt den Kondo-Effekt aus
Satz 17, und Satz 10 behauptet eine „Kuanda-Region in Angola" — dort liegt
der Fluss Cuando und die Provinz Cuando Cubango, eine Kuanda-Region ist
nicht belegt. Dazu beschreibt Satz 18 den Kundt'schen Effekt als
Schwingungen „in Gasen und Flüssigkeiten"; das Kundtsche Rohr misst in
Gasen.

**Orthographie — der auffälligste Befund:** Das Q8 schreibt durchgängig in
Schweizer Konvention **ohne ß**: `dreissig`, `gross`, `grösst`, `stösst`,
`weiss`, `äusser`. Zehn ss-Formen, null ß, über alle drei Antworten.
Dieselben Prompts erzeugten beim Q4 acht und beim NVFP4 zehn ß-Formen und
kein einziges ss.

| Variante | ss-Formen | ß-Formen |
|---|---:|---:|
| NVFP4 (vLLM) | 0 | 10 |
| UD-Q4_K_XL | 0 | 8 |
| **UD-Q8_K_XL** | **10** | **0** |

*Vorbehalt:* ein Lauf je Variante bei Temperatur 0,6. Innerhalb des Laufs
ist das Muster lückenlos, aber mit n=1 lässt sich nicht entscheiden, ob es
an der Quantisierung oder an der Zufallsauswahl liegt.

**Englisch:** in den beiden Sacherklärungen nur erlaubte Einzelwörter. In
der Fangfrage zwei Zweiwortfolgen mit nicht freigegebenen Wörtern —
`quite frankly` und, grammatisch entgleist, „Ich bin, quite willing, gerne
weiter für Sie tätig". Weniger als die zwei vollständigen Nebensätze des
NVFP4, mehr als die null des Q4.

**Sachlich sonst stark:** 42° für Rot und 40° für Violett, Sonne im
Rücken, Doppelbogen aus zwei Innenreflexionen, Alexanders dunkles Band
(wenn auch als „die Alexander'sche Dunkle" verstümmelt). Beim
Quantencomputer ist es sogar präziser als das Q4: „bestimmte Probleme
exponentiell schneller" statt pauschal höherer Rechenleistung.

**Tempo:** Decode 45,3 · 44,7 · 44,4 tok/s. Damit liegt das Q8 rund ein
Viertel unter dem Q4 (60,2 · 58,4 · 57,6), aber immer noch gut ein Drittel
über dem NVFP4 (35,0 · 32,4 · 32,3). Prefill 617,7 · 399,1 · 391,4 tok/s
auf 2.975 · 913 · 903 gerechnete Token.

### Flash-Next Q6, zweiter Lauf: Fangfrage erneut gelöst (2026-09-01)

Wiederholung mit `Qwen3.8-Flash-Next-180B-A4B-UD-Q6_K_XL`, diesmal mit den
korrigierten Messwerten. **Vorbehalt: dieser Lauf lief versehentlich auf
Reasoning High**, die Denkblöcke sind mit 9.400 bis 14.500 Zeichen fünf-
bis zehnmal so lang wie in allen anderen Läufen. Ein Teil der Qualität
geht also auf die Denkstufe, nicht auf Modell oder Format.

**Die Fangfrage wird erneut gelöst, und sauberer als je zuvor.** Es
benennt die Verballhornung ausdrücklich — „die Schreibweise ‚Kuanda' ist
in keinem Fachlexikon zu finden — ich deute Ihr Anliegen als den
Coandă-Effekt" — und bietet an, eine neue Fassung zu fertigen, falls es
falsch geraten habe. Die Erklärung trägt: Entrainment am Strahlrand,
Unterdruck zur Wand hin, Druckgefälle als Bedingung für gekrümmte
Stromlinien, Ablösepunkt in Abhängigkeit von Krümmungsradius,
Geschwindigkeit und Zähigkeit. Henri Coandă, Rumänien, 1910, der
Zwischenfall am eigenen Fluggerät — alles korrekt. Als Anwendungen nennt
es Klimatechnik, angeblasene Landeklappen sowie Boeing YC-14 und Antonov
An-72; beide nutzten tatsächlich Oberflächenblasung.

**Sprachlich einwandfrei:** null CJK in den Antworten, null Weichtrenner,
keine einzige englische Mehrwortfolge, nur die erlaubten `indeed` und
`rather`. Zwölf ß-Formen, keine einzige ss-Ersatzschreibung. Satzzahlen
30/30/31.

**Neuer Befund im Denkblock:** Dort stehen vier CJK-Zeichen — und zwar in
einem Muster, das die anderen Modelle nicht zeigten. Das Modell rutscht
mitten im Entwurf ins Chinesische und **korrigiert sich selbst**:

> „Im Inneren des Tropfens**反射** — no, keep German: ‚An der Rückwand des
> Tropfens…'"
>
> „Die Wellenlänge selbst ist**不过** — no, keep German: …"

反射 heißt Reflexion, 不过 aber. Der Sprachdruck existiert also auch hier,
wird aber vor der Ausgabe abgefangen. Das trennt diesen Fall scharf von
RadixArks NVFP4, wo die CJK-Zeichen in der ausgelieferten Antwort
standen. Für die Tabelle zählt weiterhin die Antwort — im Denkblock ist
es ein Hinweis, kein Mangel.

**Tempo:** Decode 30,0 · 29,1 · 27,8 tok/s, Prefill 219,0 · 443,4 · 557,7
tok/s auf 3.013 · 1.104 · 4.100 gerechnete Token. Die langen Denkblöcke
treiben die Inferenzzeit auf 133 bis 188 Sekunden je Antwort.

### DeepSeek-V4-Flash: Fangfrage auf der NIEDRIGSTEN Denkstufe gelöst (2026-09-01)

`DeepSeek-V4-Flash-0731-284B-A13B` als `UD-Q4_K_XL`, 154,6 GB, mit
dspark-Spekulation. Zweites Modell überhaupt, das Coandă erkennt — und das
einzige, das es bei **minimalem Reasoning** schafft (Denkblöcke 569 · 515 ·
901 Zeichen). Flash-Next brauchte dafür Reasoning High mit dem Zehnfachen
an Denkarbeit.

Aus dem Denkblock, der bei diesem Modell auf Englisch läuft: „This is
likely a misspelling. […] Actually 'Kuanda' is clearly a typo for
'Coandă'." Die Antwort nennt Henri Coandă, den rumänischen
Luftfahrtpionier, erklärt Anhaftung an gekrümmte Flächen über
Druckdifferenz und bringt Löffel-Versuch und Teekannen-Effekt als
Alltagsbeispiele. Etwas weniger streng als Flash-Next: die Ursache wird
über Bernoulli erklärt statt über Entrainment — eine gängige
Vereinfachung, nicht falsch, aber unschärfer.

**Gültigkeit des Treffers:** Die Prompt-Zeile lautet `System 2.247 +
History 66` — kein Memory-Block. Die Notiz, die AIfred sich in einem
früheren Lauf selbst geschrieben hatte („Kuanda = Coandă"), war zu diesem
Zeitpunkt gelöscht und hat nicht geholfen.

**Sprachlich sauber:** null CJK, korrekte ß-Schreibung (7 ß, 0 ss), nur
erlaubte englische Einzelwörter. Satzzahlen 31 · 32 · 31 — die Formvorgabe
wird also jedes Mal leicht überschritten.

**Messvorbehalt zur PP-Spalte:** Bei diesem Modell sind die gerechneten
Token nicht als Prefill lesbar. Sie bleiben über die drei Turns flach
(2.975 · 3.016 · 2.903), obwohl der Prompt von 2.313 auf 4.198 Token
wächst — in Turn 1 liegt der Zählwert sogar ÜBER der Promptgröße. Die
Zählung enthält also Arbeit aus der Generierungsphase. Die MTP-Modelle
zeigen das nicht (122B Q4: 2.975 · 771 · 698), der Effekt hängt an
dspark. Decode 18,6 · 19,6 · 17,7 tok/s bleibt davon unberührt.

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
| Decode | **36,9 · 36,6 · 27,8** tok/s | 32,6 · 29,5 · 24,9 tok/s |

vLLM liegt im Decode 12–24 % vorn und hält das Tempo über wachsenden
Kontext, während llama.cpp nachlässt. Im Langkontext (31k) standen bei der
Kalibration 37 gegen 26 tok/s.

**Korrektur 2026-09-01:** Die vLLM-Decode-Werte standen zuvor mit
35,4 · 35,4 · 25,0 hier. AIfred teilte die erzeugten Token durch die
Gesamtdauer der Anfrage, also durch Prefill PLUS Generierung, und
untertrieb damit um 3–12 %. llama.cpp war davon nie betroffen, weil
llama-server seine Decode-Rate selbst meldet — der Vergleich lief also zu
Lasten von vLLM. Seit dem Umbau liest AIfred auch bei vLLM die
server-eigenen Zähler (`request_decode_time_seconds`).

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
