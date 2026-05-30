# Vision-Plugin (Vigilantia)

**Datei:** `aifred/plugins/tools/vision/`

Das Vision-Plugin sind AIfreds Augen. Es lässt den Assistenten auf
angeschlossene Webcams und andere Bildquellen zugreifen, Fotos machen, ein VLM
das Gesehene beschreiben lassen, eingelernte Gesichter erkennen und eine
kontinuierliche Hintergrund-Überwachung laufen lassen, die Bewegungs- und
Gesichts-Ereignisse in einer dauerhaften Chronik festhält.

Das Plugin selbst ist nur die dünne, LLM-zugewandte Klammer. Die eigentliche
Arbeit liegt in den geteilten Bibliotheken unter `aifred/lib/` —
`frame_sources` (Capture), `vision_filters/motion` (Bewegungserkennung),
`vision_filters/face_detect` + `face_recognize` (InsightFace), `vision_analyzer`
(der VLM-Call), `vision_watcher` (die Watch-Hintergrundaufgabe) und
`vision_store` (die SQLite-Datenbank).

Ein zentraler Designpunkt: **VLM-Calls laufen über Ollama als Side-Channel**,
vollständig unabhängig vom aktiven Chat-Backend auf llama-swap. Eine
Snapshot-Analyse verdrängt nie das laufende Chat-Modell.

## Tools

Die Tools werden dem LLM nur präsentiert, wenn Vision aktiviert ist (siehe
*Vision-Modus* unten). Bei `vision_mode = off` liefert `get_tools` eine leere
Liste und der Assistent sieht das Plugin gar nicht.

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `vision_list_sources` | Alle registrierten Bildquellen (Webcams, IP-Kameras) mit Verfügbarkeit, Auflösung und konfiguriertem Kontext auflisten. | READONLY |
| `vision_rescan_sources` | System nach neu angeschlossenen/entfernten Bildquellen neu durchsuchen (z.B. eine gerade eingesteckte Webcam). | READONLY |
| `vision_snapshot` | Einzelbild von einer Quelle aufnehmen. Mit `save=true` (Standard) wird das Bild in die aktuelle Session gespeichert und eine `image_url` + `markdown`-Verknüpfung für die Inline-Darstellung zurückgegeben. | READONLY |
| `vision_analyze` | N Frames (1–10) aufnehmen und vom VLM beschreiben lassen. `n_frames > 1` sendet eine zeitliche Sequenz (Bewegungsbeschreibung). Loggt ein `vlm_analysis`-Event. | READONLY |
| `vision_enroll_face` | Snapshot der Quelle, prominentestes Gesicht erkennen und sein Embedding unter `name` speichern. Erneuter Aufruf mit bestehendem Namen hängt ein weiteres Embedding an (Multi-Winkel-Enrollment). | WRITE_DATA |
| `vision_start_watch` | Kontinuierliche Hintergrund-Überwachung auf einer Quelle starten: nimmt Frames mit `fps` auf, erkennt Bewegung und (wenn aktiviert) führt Gesichtserkennung durch. Events landen in der Vision-DB. | WRITE_DATA |
| `vision_stop_watch` | Laufende Überwachung auf einer Quelle stoppen. No-op, wenn nichts lief. | WRITE_DATA |
| `vision_list_active_watches` | Aktuell laufende Watch-Tasks mit Zählern pro Watch (gesehene Frames, Motion-Events, Face-Events) auflisten. | READONLY |
| `vision_query_events` | Vergangene Events (`motion` / `face_known` / `face_unsure` / `face_unknown` / `vlm_analysis`) abfragen, filterbar nach Quelle, Event-Typ und Zeitfenster. | READONLY |

### Tool-Parameter

- **`vision_snapshot`** — `source_id` (Pflicht), `save` (bool, Standard `true`).
- **`vision_analyze`** — `source_id` (Pflicht), `prompt` (optional; fällt auf den
  konfigurierten Default-Prompt zurück), `n_frames` (int, 1–10, Standard `1`).
- **`vision_enroll_face`** — `name` (Pflicht), `source_id` (Pflicht), `notes`
  (optional).
- **`vision_start_watch`** — `source_id` (Pflicht), `fps` (optional; Standard aus
  den Settings), `run_face_detect` (optional; Standard aus den Settings).
- **`vision_stop_watch`** — `source_id` (Pflicht).
- **`vision_query_events`** — `source_id`, `event_type`, `since_hours`, `limit`
  (Standard `50`, gedeckelt bei `500`) — alle optional.

Auflösung pro Kamera und ein statisches „Briefing" (Prompt-Kontext) werden bei
jedem Call aus dem `vision_store` gelesen; gesetzt wird das über das
Live-Preview-Popup in der UI. Das Briefing wird dem Analyze-Prompt vorangestellt,
damit das VLM den statischen Kontext („Eingang, Tür mit Briefkasten") vor der
variablen Anweisung sieht.

## Snapshot vs. Analyze vs. Watch

Das Plugin nutzt eine Kamera auf drei Arten:

1. **Snapshot** — ein Frame holen, optional in die Session speichern. Keine
   Modell-Inferenz. Schnell.
2. **Analyze** — 1–10 Frames holen und das VLM laufen lassen. Liefert einen
   Beschreibungstext plus VLM-Statistiken (TTFT / Inferenz / Tokens-pro-Sekunde),
   die die Chat-Bubble als ausklappbares `<vlm_output>` mit Metrik-Footer
   rendert. Das aufgenommene Frame wird in die Antwort gepinnt, damit man sieht,
   was das VLM gesehen hat. Jeder Analyze-Call wird als `vlm_analysis`-Event
   geloggt.
3. **Watch (Vigilantia scharf)** — eine kontinuierliche Hintergrundaufgabe.
   Siehe unten.

## Bewegungserkennung

Die Bewegungserkennung nutzt OpenCVs `BackgroundSubtractorMOG2` (Mixture of
Gaussians), ein zustandsbehafteter Detektor pro Quelle. Jedes Frame wird in
Graustufen mit leichtem Gaussian-Blur reduziert, um JPEG-Quantisierungsrauschen
zu dämpfen; die Foreground-Maske liefert dann eine `area_ratio` (Anteil
geänderter Pixel) und die Bounding-Box der größten Kontur.
`motion_min_area_ratio` filtert Mikrorauschen heraus (Wind im Baum,
Kompressionsartefakte), und `motion_warmup_frames` ignoriert die ersten Frames,
während sich das Hintergrundmodell stabilisiert. Reine CPU, ~5–15 ms pro Frame
bei 640×480.

## Watch-Modus (Vigilantia scharf)

`vision_start_watch` startet eine Hintergrundaufgabe, die Frames mit den
konfigurierten `fps` aufnimmt, Bewegungserkennung durchführt und — wenn
`run_face_detect_on_motion` gesetzt ist — bei Motion-Events Gesichtserkennung
und -abgleich ausführt. Die Events fließen in die Vision-Datenbank:

- **`motion`** — Bewegung über dem Flächen-Schwellwert (trägt `area_ratio` +
  Bbox).
- **`face_known` / `face_unsure` / `face_unknown`** — Gesicht erkannt und (nicht)
  einer eingelernten Person zugeordnet.
- **`vlm_analysis`** — wenn kontinuierliche/Bewegungs-VLM aktiviert ist oder aus
  einem manuellen `vision_analyze`-Call.

`min_event_interval_sec` entprellt Events, damit ein einzelner Passant das Log
nicht überflutet. Event-Frames werden bei `save_event_frames=true` auf die
Platte gespeichert, sodass die Chronik-Einträge ein Thumbnail tragen.

## Gesichtserkennung (InsightFace)

Detektion und Embedding laufen über **InsightFace `buffalo_l`**
(RetinaFace-Detection + ArcFace-Embedding in einem Pass). Beim ersten Aufruf
lädt InsightFace das Modell (~280 MB) nach `~/.insightface/models/buffalo_l/`;
die Initialisierung ist lazy, damit der Modul-Import billig bleibt.

Jedes erkannte Gesicht liefert ein 512-dim L2-normalisiertes Embedding. Der
Abgleich ist Cosine-Similarity (ein Dot-Product auf den normalisierten Vektoren),
bulk-vektorisiert mit NumPy. Eine Person kann **mehrere Embeddings** haben
(verschiedene Winkel / Beleuchtung) — der Recognizer macht Max-Pooling, sodass
die höchste Similarity *irgendeines* Embeddings dieser Person zählt. Zwei
Schwellwerte definieren drei Bänder:

- `similarity >= threshold_known` → **known** (bekannt)
- `threshold_unsure <= similarity < threshold_known` → **unsure** (bester
  Kandidat benannt, aber nicht sicher)
- unterhalb `threshold_unsure` → **unknown** (unbekannt)

Das „unsure"-Band existiert bewusst, damit ein Türsteher-Workflow Mehrdeutigkeit
als „unbekannt" behandeln kann statt als False-Positive.

## Personarium (Identitäten-Verwaltung)

Eingelernte Personen liegen in der `faces`-Tabelle des Vision-Stores. Das
**Personarium**-UI-Modal listet jede Identität mit Avatar (letzter Crop),
Embedding-Anzahl und Zeitpunkt der letzten Sichtung und erlaubt, eine Person
umzubenennen, zu löschen oder einzelne Embeddings zu entfernen. Reads und Writes
gehen direkt über den `VisionStore`; die REST-Endpoints unter
`/api/vision/face/*` sind für externe Konsumenten gedacht.

`vision_enroll_face` ist der LLM-zugewandte Single-Shot-Pfad. Erneuter Aufruf mit
bestehendem Namen hängt ein weiteres Embedding an dieselbe Person an —
iteratives Enrollment ist der vorgesehene Workflow.

## Multi-Pose-Enrollment

Für robuste Erkennung bei Kopfbewegung führt der **Multi-Pose**-UI-Assistent den
User durch mehrere Posen (Frontal / Links / Rechts / Oben / Unten). Jede Pose
wird einzeln aufgenommen — Anweisung → Live-Snapshot → Face-Detect → Embedding in
die Capture-Liste — und am Ende werden alle Embeddings als ein Sample-Bundle
geschrieben. Es kann eine neue Person anlegen oder mehr Posen zu einer
bestehenden Identität hinzufügen (aus dem Personarium gestartet). Die Pose-Info
dient nur der Anleitung; die Embeddings selbst sind pose-agnostisch und das
Pose-Label wird nicht in der DB gespeichert.

## Casus (Ereignis-Chronik)

**Casus** ist das Ereignis-Verwaltungs-UI-Modal: eine chronologische Liste aller
Vision-Events (`motion` / `face_known` / `face_unsure` / `face_unknown` /
`vlm_analysis`) mit Filtern (Quelle, Typ, Identität) und Aktionen pro Zeile —
Event löschen oder ein unbekanntes Gesicht nachträglich einer Person zuordnen.
Es liest und schreibt direkt im `VisionStore`. Dieselben Daten werden dem LLM
über `vision_query_events` zugänglich gemacht, sodass der Assistent Fragen wie
„Was war heute an der Tür?" oder „Wer war zuletzt da?" beantworten kann.

Eine Live-**Vigilantia-Feed**-Karte auf der Hauptseite zeigt die letzten N
Events aller Quellen.

## Modell-Lifecycle

Zwei Modelle stützen dieses Plugin, beide bei Bedarf geladen:

- **InsightFace `buffalo_l`** — Gesichtserkennung + Embedding. Beim ersten Aufruf
  automatisch nach `~/.insightface/models/buffalo_l/` heruntergeladen (~280 MB).
  Lazy Init, eine Instanz pro Prozess. Provider und GPU sind konfigurierbar; auf
  einem GPU-armen Host wird auf `CPUExecutionProvider` zurückgefallen.
- **VLM via Ollama** — Bildbeschreibung. Default im Code ist `qwen2.5vl:7b-q8_0`;
  die mitgelieferte `settings.json` übersteuert das auf
  `qwen3-vl:4b-instruct-q8_0`. Multi-Image-Inputs werden als zeitliche Sequenz
  gesendet. Das Modell bleibt per `keep_alive` (Standard `30m`) im VRAM; im
  `live`-Modus wird `keep_alive` auf `-1` gezwungen, sodass das Modell für
  Always-On-Überwachung dauerhaft geladen bleibt.

Die GPU-Platzierung ist automatisch (`gpu_id: "auto"`). Der Chat-LLM besitzt die
schnellste Compute-Klasse; das VLM und InsightFace teilen sich den
*Side-Channel-Tier* (die Klasse darunter), mit einem weichen Floor von Compute
Capability ≥ 7.0 (Volta+), damit eine langsame Pascal-Karte nur als Notnagel
genutzt wird. Siehe `aifred/lib/vision_gpu_select.py`.

## Vision-Modus

Ein globaler Toggle in `settings.json` (`vision_mode`) steuert das gesamte
Subsystem:

- **`off`** — Vision deaktiviert; das Plugin präsentiert keine Tools und der LLM
  sieht es nie. Keine VRAM-Reservierung während der Kalibration, keine
  Watch-Tasks werden angenommen.
- **`on-demand`** (Standard) — Snapshot/Analyze laufen on demand; Watch-Tasks
  brauchen ein explizites `vision_start_watch`. Das VLM wird mit `keep_alive`
  (typisch 30 min) gehalten.
- **`live`** — wie on-demand, plus das VLM bleibt dauerhaft geladen
  (`keep_alive=-1`) für Always-On-/Türsteher-Überwachung.

## Konfiguration

Alle Einstellungen liegen in `aifred/plugins/tools/vision/settings.json` und
werden bei jedem Call frisch geladen, sodass man sie aus dem Plugin-Manager ohne
Neustart anpassen kann. Wichtige Gruppen:

- **`vlm`** — `model`, `num_ctx`, `keep_alive`, `host`, `default_prompt`.
- **`face_recognition`** — `providers`, `gpu_id` (int / `"auto"` / `null` für
  Nur-CPU), `det_size`, `model_name` (`buffalo_l`), `threshold_known`,
  `threshold_unsure`.
- **`watch`** — `default_fps`, die Motion-Schwellwerte (`motion_min_area_ratio`,
  `motion_history_frames`, `motion_var_threshold`, `motion_warmup_frames`),
  `min_event_interval_sec`, `save_event_frames`, `run_face_detect_on_motion`,
  `run_vlm_on_motion`.
- **`snapshot`** — `jpeg_quality`, `save_to_disk`, `retention_days`.
- **`events`** — Retention pro Typ (`retention_days_motion` / `_face` / `_vlm`)
  und `default_query_limit`.

Ollama muss erreichbar sein (Standard `http://localhost:11434`) und das
konfigurierte VLM muss gepullt sein. InsightFace braucht installiertes
`insightface` + `onnxruntime` (GPU oder CPU).

## Anwendungsbeispiele

- „Mach ein Foto von der Haustür." → `vision_snapshot`
- „Was siehst du gerade auf der Webcam?" → `vision_analyze`
- „Merk dir diese Person als Alex." → `vision_enroll_face` (für mehr Winkel
  wiederholen)
- „Überwache den Eingang und sag mir Bescheid, wenn jemand auftaucht." →
  `vision_start_watch`
- „Wer war heute an der Tür?" → `vision_query_events`
