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
| `vision_query_events` | Vergangene Events (`motion` / `face_known` / `face_unsure` / `face_unknown` / `vlm_analysis`) abfragen, filterbar nach Quelle, Event-Typ und Zeitfenster. Mit `describe=true` werden fehlende Szenenbeschreibungen für das abgefragte Fenster vorab per VLM erzeugt (Cluster-Analyse, siehe unten). | READONLY |

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
  (Standard `50`, gedeckelt bei `500`), `describe` (bool, Standard `false`) — alle
  optional.

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

## Kamera-Quellen (USB & RTSP/IP)

Bildquellen sind als `FrameSource`-Plugins unter `aifred/lib/frame_sources/`
organisiert und in einer Registry registriert. Jeder Typ bringt eine
`discover()`-Funktion mit; `rescan()` ruft alle erneut auf (z.B. nach dem
Einstecken einer Webcam).

- **USB-Webcams** (`v4l2_source`) — werden automatisch erkannt (`/dev/video*`)
  und ohne Konfiguration registriert.
- **RTSP-/IP-/WLAN-Kameras** (`rtsp_source`) — werden über die `settings.json`
  konfiguriert (Schlüssel `rtsp_cameras`), nicht auto-gescannt. Pro Eintrag:

  ```json
  "rtsp_cameras": [
    {"name": "TrackMix", "host": "192.168.1.50", "port": 554,
     "path": "Preview_01_sub", "cred": "trackmix"}
  ]
  ```

  In der `settings.json` stehen **keine Zugangsdaten**. User und Passwort
  kommen über den **CredentialBroker** (`rtsp_<cred>` → Umgebungsvariablen
  `RTSP_<CRED>_USER` / `RTSP_<CRED>_PASSWORD` in der `.env`). Die fertige
  `rtsp://user:pass@host:port/path`-URL wird erst beim Verbindungsaufbau lokal
  gebaut und nie geloggt, gespeichert oder an das LLM gegeben — Konsumenten
  sehen nur den Anzeigenamen. Ohne `cred` wird ohne Authentifizierung
  verbunden; bei gesicherter Kamera schlägt das fail-closed fehl (kein Bild).

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

Mit einer **Zonen-Maske** (siehe unten) wird die Foreground-Maske vor der
Auswertung auf die beobachtete Zone beschränkt und der Schwellwert relativ zu
deren Pixelzahl berechnet.

## Zonen-Masken (ignorieren / schwärzen / ROI)

Pro Kameraquelle lässt sich eine **Zonen-Maske** malen, die Bewegungserkennung
und gespeicherte Bilder beeinflusst. Die Maske ist ein Raster (z.B. 48×36), pro
Zelle vierwertig — die drei Typen lassen sich beliebig in einem Bild mischen:

- **rot — Bewegung ignorieren:** Bewegung in der Zone löst keinen Trigger aus
  (z.B. wackelnde Bäume). Das Bild bleibt unverändert sichtbar und gespeichert.
- **schwarz — Pixel schwärzen (DSGVO):** die Pixel werden geschwärzt, bevor das
  Bild gespeichert *und* bevor es ans VLM gegeben wird — öffentlicher Raum
  (Straße, Nachbargrundstück) landet nie auf der Platte. Bewegung wird dort
  ebenfalls unterdrückt.
- **grün — Region of Interest (ROI):** sobald grüne Zellen existieren, wird
  Bewegung **ausschließlich** dort ausgewertet, alles andere ignoriert. Grün
  hat Vorrang vor Rot. ROI wirkt nur auf die Bewegungserkennung — das VLM
  bekommt weiter das Vollbild minus Schwärzung (Kontext).

**Schwellwert relativ zur beobachteten Fläche:** `motion_min_area_ratio` (z.B.
2 %) bezieht sich auf die **unmaskierte** Fläche, nicht auf das Gesamtbild —
Wegmaskieren großer Bereiche senkt also nicht die Empfindlichkeit im Rest.

**Editor:** In den Vision-Settings öffnet pro Quelle der Button „Zonen-Maske
bearbeiten" ein eigenständiges Popup-Fenster (`/api/vision/zone-editor`, reines
Vanilla-JS/Canvas, von Reflex entkoppelt). Es zeigt das **Live-MJPEG-Bild** als
Hintergrund; darüber malt man mit dem Pinsel (linke Maustaste malt den
gewählten Typ, rechte radiert; Pinselgröße und Rasterfeinheit per Slider).
Speichern schreibt die Maske nach `zone_masks[<source_id>]` der `settings.json`
und greift **sofort im laufenden Watcher** (Live-Reload, kein Neustart nötig).
Der Haken „Maske aktiv" ist ein Gesamt-Schalter für die schnelle Gegenprobe:
aus = die Maske bleibt gespeichert, wirkt aber nicht.

Implementierung: `aifred/lib/vision_filters/zone_mask.py` (Modell + Anwendung),
angewandt im `MotionDetector` (Bewegung) und im `vision_watcher` (Schwärzung
vor Speichern/VLM).

## PTZ-Steuerung (ONVIF)

Für schwenk-/neig-/zoombare IP-Kameras gibt es einen minimalen ONVIF-Client
(`aifred/lib/ptz_control.py`, rohes SOAP über `requests`, keine zusätzliche
Abhängigkeit): `continuous_move` / `stop` / `absolute_move` / `goto_preset`
plus `nudge` und `aim_at_offset` (grobes „folge der Bounding-Box"). Auth läuft
wie bei RTSP über den CredentialBroker; konfiguriert wird über dieselben
`rtsp_cameras`-Einträge (`ptz: true`, optional `onvif_port`, Standard 8000).
Stand: die Engine ist vorhanden, aber noch nicht an einen UI-/Tool-Bedienfluss
angebunden.

## Watch-Modus (Vigilantia scharf)

`vision_start_watch` startet eine Hintergrundaufgabe, die Frames mit den
konfigurierten `fps` aufnimmt, Bewegungserkennung durchführt und — wenn
`run_face_detect_on_motion` gesetzt ist — bei Motion-Events Gesichtserkennung
und -abgleich ausführt. Die Events fließen in die Vision-Datenbank:

- **`motion`** — Bewegung über dem Flächen-Schwellwert (trägt `area_ratio` +
  Bbox).
- **`face_known` / `face_unsure` / `face_unknown`** — Gesicht erkannt und (nicht)
  einer eingelernten Person zugeordnet.
- **`vlm_analysis`** — wenn kontinuierliche/Bewegungs-VLM aktiviert ist, aus
  einem manuellen `vision_analyze`-Call oder aus der Cluster-Analyse
  (Bulk-Beschreibung, siehe unten). Der beschreibende Text steht im Feld
  `classification.description`.

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
„Was war heute an der Tür?" oder „Wer war zuletzt da?" beantworten kann. Ein
**Bulk-Analyse-Button** stößt die Cluster-Beschreibung aller noch unbeschriebenen
Events an (mit Fortschritt + Abbrechen) — derselbe Lauf, den auch der Nacht-Job
fährt (siehe *Bulk-Beschreibung* unten).

Jede Zeile zeigt ein Vorschaubild (Gesichts-Crop oder verkleinertes Vollbild);
ein Klick öffnet das Vollbild groß, durch das man wie in einer Slideshow per
Pfeiltasten und Pfeil-Buttons blättert (links = älter, rechts = neuer). Ein
Aktualisieren-Button lädt die Liste manuell neu — bewusst manuell statt
Auto-Refresh, damit Filtern/Taggen/Scrollen nicht gestört wird (der Live-Strom
läuft im Vigilantia-Feed).

Eine Live-**Vigilantia-Feed**-Karte auf der Hauptseite zeigt die letzten N
Events aller Quellen (mit Thumbnail).

## Bulk-Beschreibung (Cluster-Analyse)

Bewegungs- und Gesichts-Events tragen zunächst nur Metadaten (Zeitstempel,
`area_ratio`, erkannte Person) — die eigentliche Szenenbeschreibung steht im Feld
`classification.description` und entsteht erst durch einen VLM-Call. Die
Hintergrund-Überwachung lässt das VLM bewusst **aus** (GPU-schonend), sodass
diese Beschreibungen separat nachgezogen werden. `vision_query_events` liefert
für unbeschriebene Events also nur „Bewegung um 14:03" und erkannte Gesichter,
keine Erzählung — bis die Beschreibung erzeugt wurde.

Damit das Nachziehen bezahlbar bleibt, werden Events vor dem VLM **geclustert**:
ein Call pro *Vorkommnis*, nicht pro Frame. Eine Person, die 40 s durchs Bild
läuft und 30 Motion-Events auslöst, wird einmal beschrieben. Das Clustering läuft
über Perceptual-Hash (pHash) plus ein Zeit-Bucket — visuell ähnliche Frames
innerhalb desselben Zeitfensters landen in einem Cluster, der Repräsentant
(ältestes Mitglied) wird beschrieben, und die Beschreibung wird idempotent auf
alle Mitglieder angewandt. Implementierung: `aifred/lib/vision_cluster.py`
(Clustering) + `aifred/lib/vision_bulk.py` (`run_bulk_describe` — die geteilte
Orchestrierung). Zwei Stellschrauben in `config.py`:

- `VISION_CLUSTER_BUCKET_SECONDS` (Standard `300`) — Zeitfenster, in dem ähnliche
  Frames zu einem Cluster zusammengefasst werden; danach beginnt ein neuer
  Cluster, auch bei weiter ähnlichen Frames (verhindert ewige Cluster bei
  „Person sitzt 8 h vor der Cam").
- `VISION_CLUSTER_PHASH_THRESHOLD` (Standard `5`) — Hamming-Distanz zweier
  64-bit-Hashes, unter der zwei Frames als „ähnlich" gelten.

**Der Algorithmus im Detail** (`cluster_events` in `vision_cluster.py`): Die
unbeschriebenen Events werden chronologisch durchlaufen, pro Quelle wird *ein
offener* Cluster gehalten. Für jedes Event:

1. Das gespeicherte Frame wird von der Platte gelesen und sein **64-bit
   Perceptual-Hash** berechnet. Fehlt die Datei oder lässt sich kein Hash bilden,
   bekommt das Event `cluster_id = ""` (Solo — wird einzeln beschrieben).
2. Liegt der Zeitstempel mehr als `BUCKET_SECONDS` nach dem Beginn des offenen
   Clusters, wird dieser geschlossen und ein neuer begonnen (der Zeit-Bucket-Cap
   — verhindert, dass ein Dauergeschehen alles in einen Cluster zieht).
3. Sonst wird der neue pHash gegen die Hashes der bisherigen Cluster-Mitglieder
   verglichen. Ist die **Hamming-Distanz zu *irgendeinem* Mitglied ≤
   `PHASH_THRESHOLD`**, gehört das Frame in diesen Cluster; andernfalls wird ein
   neuer Cluster aufgemacht.

Jeder Cluster bekommt eine **deterministische ID** der Form
`{kamera-slug}-{zeit-bucket}-{hash-präfix}` (der Bucket auf die
`BUCKET_SECONDS`-Grenze abgerundet). Deterministisch heißt: derselbe Frame landet
bei einem wiederholten Lauf in derselben Cluster-ID — Läufe sind also idempotent
und überschneidungsfrei. Die berechneten IDs schreibt `write_clusters` in die
`cluster_id`-Spalte jedes Events.

Beschrieben wird dann pro Cluster **nur der Repräsentant** (das erste, älteste
Mitglied) per VLM; `apply_cluster_description` verteilt den Text auf alle
Mitglieder mit derselben `cluster_id`. Solo-Events (leerer `cluster_id`) werden
einzeln beschrieben. `vision_query_events` gruppiert beim Abruf erneut nach
`cluster_id` und liefert pro Cluster eine Zeile (`frames_in_cluster` = Zahl der
Mitglieder).

Drei Wege lösen denselben Lauf aus:

1. **Casus-Button** — manueller Bulk-Lauf über die UI, mit Fortschrittsbalken,
   Abbrechen und VRAM-Vorabcheck.
2. **Nacht-Lauf** — die tägliche Garbage-Collection um `GARBAGE_COLLECTION_HOUR`
   (Standard `03:00`) beschreibt **erst** alle noch unerfassten Events, **dann**
   prunt sie. So hat jeder Frame eine Beschreibung, bevor er nach Ablauf der TTL
   gelöscht wird, und die Tageschronik ist morgens vollständig — auch ohne
   geöffnetes Vorschau-Popup. Code: `cleanup_vision_task` in
   `aifred/lib/vision_cleanup.py`.
3. **On-demand** — `vision_query_events` mit `describe=true` zieht fehlende
   Beschreibungen nur für das gerade abgefragte Zeitfenster vorab. Das deckt
   frische Daten des laufenden Tages ab, die der Nutzer abfragt, bevor der
   Nacht-Lauf greift. Der Aufwand bleibt durch Zeitfenster + Clustering
   begrenzt.

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
- **`rtsp_cameras`** — Liste der RTSP-/IP-Kameras (`name`, `host`, `port`,
  `path`, `cred`, optional `ptz` / `onvif_port`). Zugangsdaten **nicht** hier,
  sondern via CredentialBroker (`.env`).
- **`zone_masks`** — pro Quelle die gemalte Zonen-Maske (`cols`, `rows`,
  `cells` als Ziffern 0–3, `enabled`); wird über den Zonen-Editor geschrieben.

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
