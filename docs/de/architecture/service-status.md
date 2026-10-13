# Statusleiste — Modell, Bildbeschreiber, STT und TTS mit Restzeit

> **English version:** [service-status.md](../../en/architecture/service-status.md)

Stand: 13.10.2026. Die einklappbare Leiste unter der Titelzeile zeigt, was gerade geladen ist
und wie lange noch: das Sprachmodell (Zylinder-Icon), den Bildbeschreiber (Kamera), die
Spracherkennung (STT) und die Sprachausgabe (TTS). Im zugeklappten Kopf steht je Dienst ein Icon
mit Statuspunkt (grün geladen und sanft pulsierend, gelb lädt und schneller pulsierend, grau
nichts geladen, rot nicht erreichbar); aufgeklappt eine Zeile je Dienst mit Namen und der Zeit
bis zum Entladen. Der Knopf daneben fragt STT und TTS frisch ab. Embedding-Server laufen auf der
CPU und werden nicht gezeigt.

Code: [`service_status.py`](../../../aifred/lib/service_status.py) (SSOT),
[`api/service_status.py`](../../../aifred/lib/api/service_status.py) (Endpunkt + Push),
[`ui/service_status_bar.py`](../../../aifred/ui/service_status_bar.py) (leerer Rahmen),
`serviceStatus*` in [`assets/custom.js`](../../../assets/custom.js) (Darstellung, Countdown).

## Quellen und wie sie aktuell bleiben

| Dienst | Zustand | Restzeit |
|---|---|---|
| llama-swap | SSE-Strom `/api/events` (`modelStatus`, `inflight`), vom Monitor dauerhaft gehalten | berechnet: `ttl` (aus `/running`) ab dem Ende der letzten Anfrage, wie llama-swap selbst zählt. `exact: false` = Modell war schon geladen, als AIfred zu lauschen begann (Obergrenze, Anzeige mit `≤`) |
| STT-Suite | `GET /status` | `gpu_ttl_remaining_seconds` des Dienstes (CPU-Modell bleibt geladen) |
| TTS-Suite | `GET /engines` | `idle_remaining_s` je laufender GPU-Engine (Leerlauf-Timer des Containers) |

STT und TTS melden die Restzeit selbst, pushen aber nichts. AIfred liest sie deshalb nach jeder
eigenen Nutzung (`notify_used`, ausgelöst im Client beim Ende einer Transkription bzw. beim
Summary-Frame einer Lesung), kurz vor dem gemeldeten Ablauf (`SERVICE_STATUS_RECHECK_MARGIN_S`;
ein anderer Client kann die Uhr inzwischen zurückgesetzt haben), bei überfälligem Ablauf alle
`SERVICE_STATUS_OVERDUE_POLL_S` (die Container prüfen ihren Leerlauf nur alle 30 bis 120 s) und auf
Knopfdruck. Die Anzeige liegt dadurch höchstens zu kurz, nie zu lang.

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
