# Dreistufige PLE-Platzierung — Gedankenexperiment

> Stand 2026-08-31. Entwurf, nicht umgesetzt. Betrifft ausschließlich
> Qwen3.8-Flash-Next (Architektur `qwen4_exp`) unter vLLM auf dem
> 5-GPU-Mini. Der Rest der vLLM-Kalibration ist davon unberührt.

## Warum überhaupt

Qwen3.8-Flash-Next trägt eine **Per-Layer-Embedding-Tabelle (PLE)**, die
den Checkpoint dominiert. Sie ist reine Nachschlagearbeit: Ein Token
liest 16 Zeilen à 160 Byte, also 2,5 KB je Schicht, ohne nennenswerte
Arithmetik. Ihre Größe entscheidet trotzdem, ob das Modell auf die
Karten passt — und mit welcher Präzision sie gespeichert wird, ob wir
dem Modell trauen können.

Am 2026-08-30 zerfiel `RadixArk/Qwen3.8-Flash-Next-NVFP4` bei langen
Antworten sprachlich (Wortverstümmelungen, vereinzelte CJK-Zeichen im
letzten Viertel), während der fachliche Inhalt korrekt blieb. Die
Bisektion entlastete unsere Kernel und das NVFP4-Format und verortete
die Ursache in RadixArks Quantisierung — **ohne zwischen Rumpf und PLE
zu trennen**. Beide bleiben verdächtig.

## Der Befund vom Markt

Vermessen per HTTP-Range auf die Safetensors-Header, ohne Download
(Methode wie in `v100-skinny/QWEN4EXP-PORT-HANDOVER.md`):

| Upload | gesamt | PLE | Rumpf | MTP-Block |
|---|---:|---:|---:|---|
| Original `Qwen/Qwen3.8-Flash-Next` | 360,0 GB | BF16 | — | BF16 |
| RadixArk | 135,3 GB | **fp8 E4M3** 51,2 GB | 84,0 GB | BF16 4,86 GB |
| dealignai (abliteriert) | 135,3 GB | fp8 E4M3 | 84,0 GB | BF16 |
| Inferact | 182,8 GB | **BF16** 102,5 GB | 80,3 GB | **quantisiert** (F32+U8) |
| primitive-ai (beide) | 184–186 GB | BF16 102,5 GB | 84,0 GB | BF16 5,2 GB |
| provsalt / starkweatherdigital | 101,7 GB | 4 Bit ~28,9 GB | ~73 GB | NVFP4 1,49 GB |
| local-inference-lab 4p89 | 105,9 GB | 4 Bit 28,9 GB (U8+fp8) | 77,1 GB | quantisiert |
| garnermccloud SSD-Stream | 150,0 GB | BF16 | — | BF16 |

Zwei Schlüsse daraus. Erstens: **Der Rumpf ist überall etwa gleich
groß** (73–84 GB); die gesamte Größenspanne von 102 bis 186 GB entsteht
allein aus der PLE-Präzision. Zweitens: Da das Original BF16 ist, ist
auch RadixArks fp8-Tabelle bereits eine Quantisierung — der Verdacht
gegen die PLE lässt sich am Markt nicht ausräumen, nur durch einen
Checkpoint mit **unquantisierter** Tabelle.

## Was der Code heute kann

`vllm/models/qwen4_exp/amd/ple_layer.py` implementiert genau eine
Methode, `Qwen4ExpPLEFp8EmbeddingMethod`. Sie legt fp8-Parameter an und
verteilt die Tabelle **zeilenweise auf zwei Ebenen**:

1. so viele Zeilen wie möglich im VRAM der eigenen Karte,
2. der Rest in gepinntem Hostspeicher, gelesen über eine UVA-Sicht.

Der Gather-Kernel greift also bereits heute auf einen **fremden Zeiger**
zu — für ihn ist das nur eine Adresse im gemeinsamen Adressraum. Die
Zeilen sind über Hashes gleichverteilt, weshalb der Teilungspunkt laut
Doku „eine reine Kapazitätsentscheidung ist und keine Genauigkeit
kostet".

Daraus folgt: Ein Checkpoint mit BF16- oder 4-Bit-Tabelle lässt sich
**gar nicht laden**; es fehlt nicht ein Parameter, sondern die Methode.
Der Ladefehler tritt sofort auf, nicht als stille Verschlechterung.

## Die Zwickmühle

Das Ziel ist eine unquantisierte BF16-Tabelle, um den Verdacht zu
klären. Die Rechnung dagegen:

| Posten | Bedarf |
|---|---:|
| Rumpf (Inferact, NVFP4) | 80,3 GB |
| PLE in BF16 | 102,5 GB |
| **Summe** | **182,8 GB** |

Verfügbar sind 160 GB auf den vier kalibrierbaren Karten (2× RTX 8000
à 48 GB, 2× V100 à 32 GB); die fünfte V100 trägt TTS und Vision. Der
Systemspeicher fasst 30 GiB, realistisch etwa 20 GiB für die
Auslagerung. Es fehlen also rund 3 GB — und der KV-Cache hat noch
keinen Platz.

**Die naheliegende Lösung scheidet aus.** Die fünfte Karte als fünfte
Pipeline-Stufe einzuhängen wäre ohne jeden Codeeingriff möglich, opfert
aber das Gitter: Gemessen am 27B liest TP2×PP2 lange Prompts mit
833 tok/s ein, reines Pipeline-Parallel deutlich langsamer. Wir würden
Speicher gewinnen und den Prefill-Vorteil verlieren, der die
Gitter-Topologie überhaupt erst zum Betriebspunkt gemacht hat.

## Nachtrag 2026-09-01: gemessen, und die Annahmen korrigiert

**Die PLE liegt auf EINER Schicht, nicht verteilt.** `config.json` nennt
`ple_layer_ids: [2]` bei 48 Schichten, und alle `ple_embedding`-Tensoren
haengen an `layers.1`. Es ist eine gemeinsame Tabelle, die genau einmal
frueh im Netz gelesen wird; die Schicht-Nummer geht nur als Hash-Saat ein
(`base_seed = seed + _PLE_LAYER_PRIME * ple_dense_layer_id`). Damit
verteilt sich ihr Speicherbedarf NICHT ueber die Stufen — er lastet
vollstaendig auf der Stufe, die diese eine Schicht besitzt. Die Rechnung
oben unterschaetzt den Druck also; er sitzt konzentriert.

**Der Peer-Weg ist auf dieser Maschine tot.** Gemessen mit
`tools/peer_ple_probe*.py`:

| Quelle | 2.560 Byte (ein Token) |
|---|---:|
| lokales VRAM | 3,45 µs |
| gepinnter Host | 2,99 µs |
| Platte, zufaellig, O_DIRECT | 185 µs |
| Nachbarkarte (Peer) | **49.362 µs** |

Die 49 ms sind eine FESTE Grundgebuehr je Kopiervorgang — 2,5 KB und 1 MB
kosten gleich viel, erst ab 16 MB waechst es. Die Platte ist damit rund
267-mal schneller als der Peer-Pfad.

Zwei Nebenbefunde: Die Peer-Matrix erlaubt Zugriff nur INNERHALB einer
Architektur (RTX↔RTX, V100↔V100), nie darueber hinweg, obwohl
`nvidia-smi topo -m` fuer jedes Paar `PHB` meldet — es ist also eine
Treiber-, keine Verdrahtungsgrenze. Und der Vergleich VRAM gegen Host bei
2,5 KB sagt nichts aus: Beide liegen an der Startkosten-Schwelle von rund
3 µs. Bei 512 MB stehen 381 GB/s gegen 3,1 GB/s, also Faktor 123. Fuer die
PLE ist das gleichgueltig, weil ihr Zugriffsmuster latenz- und nicht
bandbreitenbegrenzt ist — genau deshalb funktioniert die Auslagerung.

**Offene Spur: IOMMU.** Der IOMMU laeuft im Uebersetzungsmodus (`DMA-FQ`,
kein `iommu=`-Parameter gesetzt), was Peer-Transaktionen durch den
Root-Complex zwingt — die dokumentierte Ursache genau solcher Werte. Der
Test waere `iommu=pt`. Risiko: Eine Karte haengt am USB4-Tunnel, dessen
DMA-Schutz auf eben diesem Modus beruht; die Aenderung senkt also die
Absicherung gegen DMA-Angriffe ueber diesen Port. Entscheidung und
Neustart gehoeren dem Betreiber.

**Der klassenuebergreifende Weg ist zu.** Der tinygrad-Patch an den
offenen NVIDIA-Kernelmodulen setzt Turing oder neuer voraus; Volta faellt
heraus, und zwei Treiberstapel nebeneinander gibt es nicht.

## Der Entwurf

**Eine dritte Ebene zwischen VRAM und Host: der Speicher einer Karte,
die nicht rechnet.**

Die Kaskade würde dann lauten — lokales VRAM, dann Nachbarkarte, dann
Host. Rechnen weiterhin vier Karten im Gitter; die fünfte hält
ausschließlich Tabellenzeilen und führt keine Schicht aus.

Kapazität:

| Ebene | Kapazität |
|---|---:|
| Vier Rechenkarten (Gitter) | 160 GB |
| Fünfte Karte als Tabellenlager | 32 GB |
| Gepinnter Hostspeicher | ~20 GiB |
| **adressierbar** | **~212 GB** |

Damit passen 182,8 GB samt KV-Cache, und die Topologie bleibt
unangetastet.

**Der Eingriff ist klein — wenn die Hardware mitspielt.** Da der Kernel
den ausgelagerten Teil ohnehin über einen fremden Zeiger liest, ändert
sich seine Logik nicht. Es ändert sich, wohin allokiert wird: statt
`pin_memory()` auf dem Host eine Allokation auf der Nachbarkarte plus
Peer-Mapping. `PLEPlacement` bekäme statt zweier Zeilenzahlen drei.

## Die offene Frage: funktioniert Peer-Zugriff auf diesem Board?

`NCCL_P2P_DISABLE=1` steht in jeder Konfiguration, weil der
Ryzen-Root-Port P2P defekt implementiert. Das betraf bislang
NCCL-Kollektive. Ob **einfacher Peer-Speicherzugriff** funktioniert, ist
ungetestet. Erschwerend: Eine Karte haengt am USB4-Tunnel, die uebrigen an M.2-OCuLink-Adaptern; die
Peer-Fähigkeit könnte für manche Paare gelten und für andere nicht —
im Gitter brauchen alle vier Rechenkarten Zugriff auf die fünfte.

### Testplan (vor jeder Implementierung, GPUs müssen frei sein)

1. **Peer-Matrix**: `cudaDeviceCanAccessPeer` für alle 20 geordneten
   Paare der fünf Karten.
2. **Korrektheit vor Tempo**: Für jedes Paar mit „ja" Peer-Zugriff
   aktivieren, ein bekanntes Muster hinüberschreiben, aus einem Kernel
   der anderen Karte zurücklesen und byteweise vergleichen. „Defekt"
   könnte auch heißen, dass Zugriffe stillschweigend falsche Daten
   liefern — das wäre der schlimmste Ausgang und muss ausgeschlossen
   werden, bevor irgendetwas gebaut wird.
3. **Bandbreite unter realistischem Muster**: viele kleine
   Zufallslesevorgänge à 160 Byte, nicht ein großer sequenzieller
   Block. Vergleichsmaßstab ist der Host-Pfad über PCIe Gen3 x4.

Erst wenn Schritt 2 sauber ist, lohnt Schritt 3; erst wenn Schritt 3
den Host-Pfad nicht unterbietet, lohnt die Implementierung.

## Wann sich das lohnt — und wann nicht

**Nicht** für einen Checkpoint mit fp8-Tabelle. RadixArks Struktur
braucht 135,3 GB und passt heute schon ins Gitter, ohne fünfte Karte
und ohne dritte Ebene.

**Nur** für das eine Experiment: das 180B mit unquantisierter PLE fahren
und damit klären, ob der Sprachzerfall an der Tabelle hing oder am
Rumpf. Fällt die Antwort auf „Rumpf", genügt künftig ein besser
quantisierter fp8-Checkpoint und die dritte Ebene wird nie wieder
gebraucht. Fällt sie auf „PLE", ist die dritte Ebene die
Voraussetzung dafür, das Modell überhaupt brauchbar zu betreiben.

## Alternative ohne neuen Code

Inferacts BF16-Tabelle selbst nach fp8 quantisieren (Shards lesen,
`.ple.`-Tensoren mit Skalen casten, neu schreiben; ModelOpt-Konvention
im Handover dokumentiert). Ergebnis wäre ein Checkpoint mit RadixArks
Struktur, aber von einem anderen Quantisierer und mit quantisiertem
Draftkopf — 131,5 GB, passt ins Gitter.

**Der Haken:** Das ist genau der Schritt, den wir als möglichen
Zerfallsgrund verdächtigen. Der Test würde seine eigene Aussagekraft
untergraben. Er beantwortet nur die Frage „liegt es an RadixArks
Rumpf?", nicht die Frage „liegt es an der PLE-Quantisierung?".

## Reihenfolge, falls angegangen

1. Laufende 27B-Kalibration abwarten (GPUs müssen frei sein).
2. Peer-Matrix vermessen, Schritte 1–3 des Testplans.
3. Bei positivem Ergebnis: Inferact herunterladen (182,8 GB bei
   217 GB frei — shardweise, Platz knapp) und parallel die
   BF16-Methode plus dritte Ebene bauen.
4. Bei negativem Ergebnis: dabei bleiben, dass das 180B unter
   llama.cpp mit Q6_K_XL läuft, und die vLLM-Seite auf Modelle ohne
   PLE beschränken.
