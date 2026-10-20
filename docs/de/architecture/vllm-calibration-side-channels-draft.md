# Entwurf: vLLM-Kalibration mit Budgets für die Zusatzmodelle (Weg A)

Stand 20.10.2026, zweite Fassung nach der Prüfung durch Mini:vllm-research (15:12), noch nicht gebaut.
Beschlüsse von Peuqui: Ablauf genau wie bei llama.cpp; keine freigehaltene Karte;
fester, konfigurierbarer Restpuffer statt Prozent oder Faktor; Speicheranteil (GMU)
senken und PLE auslagern nur, wenn wirklich alles voll ist.

## Heute

- `side_channel_uuids()` nimmt die Sammelkarte ganz aus der Topologie, sobald im Picker
  ein Paar angehakt ist; sonst rechnet die Kalibration auf allen Karten.
- Geschrieben wird nur die Basis `<entry>` (und `-speed`). Die Varianten
  `-tts-<engine>`, `-vlm-<key>` und die Kombination entstehen von Hand als Kopie der Basis.
- `--num-gpu-blocks-override` setzt AIfred nie; das Boot-Log wird nur bei Fehlern gelesen.
- Pro Karte bleibt eine feste Reserve von 1024 MiB außerhalb der GMU (`VLLM_VRAM_RESERVE_MB`),
  bei OOM steigt sie in Schritten von 1024 MiB (zweimal), danach halbiert sich der Kontext.

## Neu

### 1. Basis

Die Topologie-Leiter läuft über alle Karten (keine Reserve für Seitenkanäle). Messung,
Auswahl (Kontext vor Tempo, dann Lang-Decode) und k-Sweep bleiben wie sie sind.

Jeder Boot läuft mit `--num-gpu-blocks-override` (ohne Override nennt das Log die Blockzahl
nicht, und beim DSv4 ist die automatische Blockzahl auf den knappen Stufen negativ). Gelesen
wird aus dem EngineCore-Log, je Worker, nicht dedupliziert:

- `GPU startup budget bytes: requested=… weights=… … kv=…` (gpu_worker.py:728),
- `Overriding num_gpu_blocks=<A_i> with num_gpu_blocks_override=<N>` (kv_cache_utils.py:3343),
- einmal: `GPU KV cache size: X tokens` und `Maximum concurrency for <mml> tokens per request: Y`.

Daraus: `N_auto = min(A_i)` (was unter der GMU auf jeder Stufe Platz hätte) und
`N_min ≈ N / Y` (kleinste Blockzahl für einen vollen Kontext). Die Basis nimmt
`max(N_auto, N_min)`: so viel Cache, wie der Platz hergibt, mindestens einen vollen Kontext
(beim DSv4 liegt `N_min` über `N_auto`, der Override erzwingt ihn wie heute die 3000).
`N_auto`, `N_min` und `Y` stehen im Betriebspunkt (`meta.kv`).

**Startwert des Overrides:** großzügig, sodass der erste Boot sicher besteht — die Blockzahl
des vorhandenen Betriebspunkts bzw. llama-swap-Eintrags (DSv4: 3000). Ohne einen solchen Wert
(neues Modell) oder wenn der erste Boot die Concurrency-Prüfung nicht besteht, liefert der
gescheiterte Boot den Rückfall: `Overriding num_gpu_blocks=<A_i>` steht trotzdem im Log, dazu der
ValueError „… (<X> GiB KV cache is needed, which is larger than the available KV cache memory
(<Y> GiB) …“ der ersten zu kleinen Stufe → nächster Versuch mit `aufgerundet(N × X / Y)` (eine
Untergrenze: spätere Stufen können mehr brauchen; X und Y sind auf zwei Stellen gerundet). Jeder
gescheiterte Boot kostet die volle Ladezeit (DSv4 ~4 min). Bestätigt ist `N_min` erst durch einen
bestandenen Boot mit `Y ≥ 1,0`; genau ist dann `N_min = N / Y`.

Bei PP setzt vLLM die Blockzahl aller Stufen auf das Minimum (kv_cache_utils.py:3403); Stufen
über dem Minimum lassen den Rest frei (Flash-Next PP4: grob 20 GB auf GPU 2, gerechnet).

**Voraussetzung:** Vor jedem vLLM-Start räumt AIfred alle Karten (TTS, STT, VLM fliegen raus),
die Zusatzmodelle kommen erst danach. Die Startprüfung von vLLM (auf jeder Karte
`frei ≥ GMU × total`) trifft deshalb nie auf ein Zusatzmodell; eine GMU-Senkung nur für die
Startprüfung ist nie nötig.

### 2. Varianten

Je angehaktem Profil (TTS-Engine, VLM, Kombination) kommen die Budgets aus den Burn-ins,
dieselben Quellen wie bei llama.cpp: `resolve_tts_reserve()` (Spitze + Kopfraum) und
`resolve_vlm_reserve()` (Karte des VLM + Spitze + Kopfraum). Die STT bleibt weich: Sie
bekommt kein eigenes Profil und nimmt, was frei bleibt (CPU als Ausweg).

Die Topologie der Basis bleibt. Freigemacht wird in dieser Reihenfolge, die erste Stufe,
die passt, gewinnt:

1. **Karte, die die Topologie nicht nutzt** (Flash-Next PP4 → fünfte Karte): Die Variante
   ist die Basis mit eigenem Namen, ohne Probe (wie der isolierte Modus bei llama.cpp).
2. **Gemessener Rest auf einer Modellkarte:** Unter Last (Lang-Kontext-Probe) wird der freie
   Speicher je Karte gemessen. Reicht er auf einer Karte für Budget + Restpuffer, ist die
   Variante die Basis.
3. **KV-Cache verkleinern:** Override `N` so weit senken, dass auf der günstigsten Karte
   Budget + Restpuffer frei werden (freier Zuwachs je Block = bytes_per_block dieser Stufe),
   höchstens bis `N_min` (Beschluss Peuqui: Untergrenze genau ein voller Kontext; Preis: beim
   Wechsel zwischen zwei langen Gesprächen wird das Präfix des anderen neu gerechnet).
   Günstigste Karte = die, die den kleinsten Block-Verzicht braucht (dieselbe Regel wie
   `pick_tts_reserve_gpu()`: erst unbenutzte Karte, dann geringste Kosten).
4. **GMU senken, nur bei PLE-Modellen und nur, wenn die gewählte Karte die PLE-Stufe ist**
   (Flash-Next PP4: GPU 0). Mit Override gibt eine GMU-Senkung sonst nichts frei. PLE wandert
   dabei in Host-RAM oder auf die Platte, etwa ein GiB je GiB; in Schritten von 0,02.
5. **Kontext verkleinern**, wie heute bei OOM.
6. Passt nichts: Profil nicht geschrieben, Fehler gemerkt (wie `capacity_exceeded` bei llama.cpp).

Gemessen wird „frei unter Last“ nach der längsten Anfrage nahe der maximalen Kontextlänge
(Projektregel; 184k/250k bei 262k-Einträgen, DSv4 bis ~300k), nicht nach der 30k-Probe: Der
Allokator behält seine Hochwassermarke, und Puffer wie der DSv4-Indexer wachsen mit dem Kontext.
Die Basis wird so einmal vermessen (Stufe 1–2 brauchen dann keinen weiteren Boot); jede Variante
aus Stufe 3–5 wird gebootet und ebenso nachgemessen: Auf der gewählten Karte muss
Budget + Restpuffer frei sein. Die Variante wird als eigener Betriebspunkt
`<entry>-tts-<engine>[-vlm-<key>]` mit eigenem `cmd` (Override, GMU, mml) persistiert und in
die llama-swap-Konfiguration geschrieben; die Handkopien entfallen.

Sitzen TTS und VLM auf derselben Karte, zählt die Summe beider Budgets (wie bei llama.cpp).
Der gemeinsame Burn-in bleibt als Prüfung, ob die Summe trägt.

### 3. Puffer

- `SIDE_CHANNEL_RESIDUAL_MB = 512` (`config.py`, gebaut, gilt für beide Backends): EIN fester
  Rest je Karte, auf der Zusatzmodelle sitzen, über deren gemessene Spitzen hinaus. Die Budgets
  selbst sind die nackten Burn-in-Spitzen (die früheren Aufschläge je Modell, 512 MiB TTS und
  500 MiB VLM, sind entfallen). Reicht er im Betrieb nicht, wird er erhöht.
- `VLLM_VRAM_RESERVE_MB` (Reserve außerhalb der GMU: CUDA-Kontexte, Hilfsprozesse wie der
  PLE-Offload-Worker, Allokator-Wachstum) bleibt bei 1024 mit seiner OOM-Leiter; auf diesem Stack
  nicht gemessen, deshalb keine Senkung ohne Messung.

### 4. Oberfläche

Die vLLM-Matrix im Kalibrier-Picker zeigt heute Burn-in-Paare. Sie wird wie bei llama.cpp zu
einer Matrix der Profile: angehakt = Variante wird kalibriert.

## Offen

- `memory.used` je Karte schreibt vllm-research in seinen A/B-Protokollen mit (Peuqui 20.10.:
  ja). Ändert den Algorithmus nicht, liefert aber echte Zahlen zur Reserve außerhalb der GMU.
