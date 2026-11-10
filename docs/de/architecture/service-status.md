# Statusleiste — Modell, Bildbeschreiber, STT und TTS mit Restzeit

> **English version:** [service-status.md](../../en/architecture/service-status.md)

Stand: 04.11.2026. Die einklappbare Leiste unter der Titelzeile zeigt, was gerade geladen ist
und wie lange noch: das Sprachmodell (Zylinder-Icon), den Bildbeschreiber (Kamera), die
Spracherkennung (STT) und die Sprachausgabe (TTS). Im zugeklappten Kopf steht je Dienst ein Icon
mit Statuspunkt (grün geladen und sanft pulsierend, gelb lädt und schneller pulsierend, grau
nichts geladen, rot nicht erreichbar); aufgeklappt eine Zeile je Dienst mit Namen und der Zeit
bis zum Entladen und einem Knopf zum Entladen bzw. Laden (siehe unten). Der Knopf rechts fragt STT
und TTS frisch ab. Embedding-Server laufen auf der CPU und werden nicht gezeigt.

Code: [`service_status.py`](../../../aifred/lib/service_status.py) (SSOT),
[`api/service_status.py`](../../../aifred/lib/api/service_status.py) (Endpunkt + Push),
[`service_control.py`](../../../aifred/lib/service_control.py) (Laden und Entladen),
[`ui/service_status_bar.py`](../../../aifred/ui/service_status_bar.py) (leerer Rahmen),
`serviceStatus*` in [`assets/custom.js`](../../../assets/custom.js) (Darstellung, Countdown).

## Quellen und wie sie aktuell bleiben

| Dienst | Zustand | Restzeit |
|---|---|---|
| llama-swap | SSE-Strom `/api/events` (`modelStatus`, `inflight`), vom Monitor dauerhaft gehalten. `inflight` meldet seit llama-swap v269 Änderungen je Anfrage: `snapshot` (alle laufenden beim Verbinden, ohne Liste, wenn keine läuft), `upsert` (Beginn und Fortschritt, beim Streamen etwa alle 250 ms), `remove` (Ende). Der Monitor meldet nur, wenn sich die Zahl laufender Anfragen je Modell ändert. Eine Meldung in unbekannter Form (llama-swap hat sie geändert) behandelt er wie einen Verbindungsabbruch: Warnung im Log, llama-swap rot als nicht erreichbar, nach `SERVICE_STATUS_SSE_RECONNECT_S` neue Verbindung | berechnet: `ttl` (aus `/running`) ab dem Ende der letzten Anfrage, wie llama-swap selbst zählt. `exact: false` = Modell war schon geladen, als AIfred zu lauschen begann (Obergrenze, Anzeige mit `≤`) |
| STT-Suite | `GET /status` | `gpu_ttl_remaining_seconds` des Dienstes (CPU-Modell bleibt geladen) |
| TTS-Suite | `GET /engines` | `idle_remaining_s` je laufender GPU-Engine (Leerlauf-Timer des Containers) |

STT und TTS melden die Restzeit selbst, pushen aber nichts. AIfred liest sie deshalb nach jeder
eigenen Änderung (`notify_changed`, ausgelöst im Client beim Ende einer Transkription bzw. beim
ersten Ton einer Lesung (sie kann die Engine gerade gestartet haben) und bei ihrem Summary-Frame, bei
jeder Freigabe der Suiten durch AIfred und im GPU-Orc-Client, sobald GPU-Orc für AIfred etwas freigegeben hat), kurz vor dem gemeldeten Ablauf (`SERVICE_STATUS_RECHECK_MARGIN_S`;
ein anderer Client kann die Uhr inzwischen zurückgesetzt haben), bei überfälligem Ablauf alle
`SERVICE_STATUS_OVERDUE_POLL_S` (die Container prüfen ihren Leerlauf nur alle 30 bis 120 s) und auf
Knopfdruck. Die Restzeit liegt dadurch höchstens zu kurz, nie zu lang. Gibt GPU-Orc eine Karte
für einen anderen Aufrufer frei (Agent-Orc, vllm-research), sieht AIfred das erst beim nächsten
geplanten Nachschauen oder auf Knopfdruck.
Die geöffnete Seite fragt außerdem selbst alle 30 s mit `?refresh=true` nach (nur bei sichtbarem Tab, und
beim Zurückkehren in den Tab; `REFRESH_EVERY_MS` in `ServiceStatusBar.vue`), sieht diese Änderungen also auch ohne Knopf.

## Laden und Entladen per Knopf

Hinter jeder Zeile sitzt ein Ein/Aus-Knopf (`serviceStatusActionButton` in `custom.js`, Endpunkte
`POST /api/service-status/unload` und `/load`):

| Zeile | Knopf | Weg |
|---|---|---|
| Sprachmodell, Bildbeschreiber, geladen und frei | Entladen | `POST /api/models/unload/<Modell>` an llama-swap. Nur ein Modell, das der Monitor führt (der Name landet im Pfad der URL), und nur, solange keine Anfrage läuft |
| Sprachmodell, nichts geladen | Laden | das Hauptmodell, das eine Chat-Anfrage jetzt laden würde (`get_effective_model_from_settings("aifred")`), über `LLMBackend.load_model`, also wie jede Anfrage: GPU-Orc räumt die Karten, llama-swap lädt. Nur mit llama.cpp oder vLLM als Backend |
| Bildbeschreiber, nichts geladen | Laden | die `-visiond`-Platzierung des Vision-LLMs der Einstellungen, die gerade neben das Geladene passt (`fitting_visiond`). Passt keine, wird nicht geladen und die Meldung sagt warum: der Bildbeschreiber verdrängt nie das Hauptmodell. TTS und STT auf seiner Karte räumt GPU-Orc nach der Rangfolge weg, eine laufende Vorlesung bricht dabei ab |
| STT, GPU | Entladen | `POST /gpu/release` der STT-Suite mit `force`: eine laufende Transkription bricht ab (mit `device=auto` läuft sie auf der CPU weiter) |
| TTS, lokale Engine | Entladen | `POST /gpu/release` der TTS-Suite für diese Engine mit `force`: eine laufende Vorlesung bricht ab |

STT und TTS haben keinen Lade-Knopf, sie laden mit der ersten Anfrage; das CPU-Modell der STT-Suite
entlädt nie, eine TTS-Engine auf einem anderen Rechner ist nicht Sache dieses Rechners. Entladen geht
bei den Suiten direkt an den Dienst, nicht über GPU-Orc: der räumt nur ganze Karten oder Platz für ein
Modell, und beide Suiten können auf derselben Karte liegen.

Laden antwortet sofort und läuft im Hintergrund weiter (ein großes Modell braucht Minuten, ein
geschlossener Tab bricht es nicht ab); die Zeile zeigt „lädt“ über die Ereignisse von llama-swap, ein
Fehlschlag landet in der Debug-Konsole. Eine Ablehnung (arbeitet gerade, passt nicht, nicht geladen)
kommt als 409 mit der Meldung in der Sprache der Seite zurück und steht kurz unter der Liste
(`service-status-notice`). Eine Rückfrage vor dem Entladen gibt es bewusst nicht, und eine Vorlesung
oder Transkription hält einen Klick nicht auf: der Klick ist eine bewusste Entscheidung (Peuqui,
04.11.2026). Nur ein Modell, das gerade antwortet, hat keinen Knopf.

## Weg zum Browser

Der Monitor ruft bei jeder Änderung seine Listener; `api/service_status.py` schiebt den
Snapshot als Art `service_status` über den [Browser Push Bus](browser-push-bus.md) an alle
verbundenen Seiten (nur der jeweils letzte Stand bleibt in der Warteschlange). Eine Seite holt
den Stand beim Öffnen ihres Streams über `GET /api/service-status` (mit `?refresh=true` liest der
Server STT und TTS vorher neu). Die Restzeit reist als Sekunden zum Zeitpunkt des Snapshots; der
Browser zählt selbst herunter.

Das Plugin `system_monitor` liest dieselbe SSOT (`system_status` mit `model`, `stt`, `tts`), es
pusht nichts. Den einen `/running`-Zugriff (`running_models`) nutzen auch
`tts_llm_coordination`, `backends/base.py` und der Chat-Mixin.
