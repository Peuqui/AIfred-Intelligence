# Umbau: Reflex → Vue 3 + Server

Stand: 07.11.2026, beschlossen mit Peuqui. Dieses Dokument ist der Plan; die Bestandsaufnahme der
State-Mixins liegt daneben (`inventar-*.md`).

## Ziel

AIfred wird eine reine Server-Anwendung, die ohne Browser vollständig läuft. Die Oberfläche ist ein
Client wie Telegram oder das Echo.

| Schicht | Inhalt | Ort |
|---|---|---|
| Server | Dienste ohne Oberflächen-Zustand: Sitzungen, Züge, Agenten, Einstellungen, Dokumente, TTS/STT, Vision, Kanäle, Scheduler | `aifred/lib/` |
| API | FastAPI: REST für Befehle und Abfragen, ein Ereignisstrom (SSE) je Sitzung | `aifred/lib/api/` |
| Oberfläche | Vue 3, Vite, TypeScript, Pinia, Vue Router (Hash-Modus) | `frontend/` |

Ein Zug (eine Nachricht und alles bis zur fertigen Antwort) läuft immer im Server zu Ende, egal ob
ein Tab offen ist. Mehrere Tabs oder Geräte sehen dieselbe Sitzung live.

## Entscheidungen

- **Ein Port.** Der Server liefert das gebaute Vue (`frontend/dist`), die API und den Ereignisstrom
  aus einem Prozess. Im Heimnetz oder per VPN braucht es keinen Reverse-Proxy; einen Proxy (Caddy
  mit Auto-TLS) nur für Zugriff aus dem Internet. nginx bleibt Peuquis eigener Aufbau.
- **Pfad.** Während Reflex und Vue nebeneinander laufen, liefert das Backend (8002) Vue unter
  `/api/app/` aus; nginx leitet `/api/` dort schon hin, hinter Authelia. Der Build nutzt relative
  Pfade (`base: './'`) und Hash-Routing, damit derselbe Build später unter `/aifred/` läuft. Beim
  endgültigen Wechsel zeigt nginx `/aifred/` auf den Server statt auf das Reflex-Frontend (3002).
- **Farben** bleiben in `aifred/theme.py` (`COLORS`); Vue lädt die CSS-Variablen von
  `GET /api/app/theme.css` (`css_variables()`).
- **Übersetzungen** bleiben in `aifred/lib/i18n/{de,en}.json`; Vue lädt sie von
  `GET /api/app/i18n/{lang}`.
- **Typen der API** entstehen aus den Pydantic-Schemas: OpenAPI der FastAPI-App →
  `openapi-typescript` → `frontend/src/api/schema.d.ts`. Keine doppelt gepflegten Typen.
- **Mikrofon** (Diktat) geben Browser nur über HTTPS oder `localhost` frei. In Produktion liefert
  nginx HTTPS; für andere Aufbauten gehört das in die Installationsanleitung (Tailscale serve,
  Caddy mit lokaler CA oder TLS im Server).
- **Tests:** pytest für Dienste und API, Vitest für Komponenten mit Logik, Playwright für
  Ende-zu-Ende-Abläufe.

## Altlasten, die der Umbau mit aufräumt

- **Feste Agenten** (Peuqui 07.11.2026): AIfred, Sokrates und Salomo werden normale Agenten wie alle
  später eingerichteten: konfigurierbar, abschaltbar, löschbar. Die Diskussionsmodi (Kritiker,
  Tribunal mit Richter) und der Standard-Agent einer Sitzung verweisen auf Rollen, denen man in der
  Konfiguration beliebige Agenten zuordnet, nicht auf feste Namen.
  Umsetzung in zwei Teilen, ohne denselben Code zweimal anzufassen: In Schritt 2 kennt die
  Server-Schnittstelle der Züge nur Rollen (Hauptagent, Kritiker, Richter) und eine Zuordnung
  Rolle → Agent, standardmäßig wie heute belegt; das Verhalten bleibt gleich. Abschalten,
  Löschen und die Zuordnung in der Oberfläche kommen mit der Agenten-Seite in Schritt 5.
- **Agenten-Übersicht** (Peuqui 07.11.2026): je Agent eine Karte mit Persona, Rechten und
  Einstellungen, oben rechts ein Schalter wie bei den Plugins. Jeder Agent ist zur Laufzeit
  ein- und ausschaltbar und löschbar, am Ende auch AIfred, Sokrates und Salomo.

## Schritt 2 im Einzelnen: Zug-Dienst ohne State

Befund der Bestandsaufnahme: Der Browser-Zug steckt in `send_message` (`state/_chat_mixin.py`,
rund 800 Zeilen), `lib/multi_agent.py` schreibt direkt in den State; der Weg ohne Browser
(`message_processor.process_inbound` → `llm_engine.call_llm`) ist ärmer (keine Kompression, kein
Multi-Agent, keine Bilder) und weicht ab. Reflex bleibt im Zweig unverändert, bis Vue alles kann;
der neue Dienst entsteht daneben und ersetzt beim Wechsel beide alten Pfade.

- **`lib/runtime_settings.py`**: Sicht auf `settings.json` mit den Feldern, die Modellwahl, Kontext,
  Sampling und Denken lesen (`agent_tuning`, `backend_type`, `backend_url`, `temperature_mode`,
  `ui_language`, `enable_tts`, `agent_memory_enabled`). Die vorhandenen Funktionen
  (`get_agent_setting`, `get_agent_num_ctx`, `build_llm_options`, `thinking_mode`, …) brauchen nur
  ein Objekt mit diesen Feldern. Der Aufbau von `agent_tuning` aus `settings.json` (heute im
  Backend-Mixin) und die wirksame Modell-ID (heute zweimal: `_effective_model_id` im State,
  `get_effective_model_from_settings` in `config.py`) werden je eine Funktion für alle.
- **`lib/turn_service.py`**: ein Zug als Task der Server-Schleife. Prüfungen (Kalibrier-Sperre,
  Modell), Nutzer-Eintrag, Zeitstempel, Intent und Moduswechsel, Kompression, Routing, Antwort
  des Agenten (zustandslose Fassung von `_run_agent_direct_response` und
  `_stream_agent_to_history`), Speichern unter `session_rmw_lock`, Titel, Abbruch über
  `pipeline_registry`. Ereignisse über `session_events`: `turn_started`, `agent_started`, `token`,
  `tool_status`, `debug`, `message_added`, `turn_finished` (Liste in `inventar-chat-sitzungen.md` 5.2).
- Danach in dieser Reihenfolge: Multi-Agent-Modi mit Rollen statt fester Namen, Bilder, TTS im
  Browser, Diktat; dann übernehmen Kanäle und Scheduler denselben Dienst.

## Schritte

0. Reflex 0.10.1 (Zweig `reflex-0.10`), damit der Bestand bis zum Wechsel gepflegt läuft.
1. Bestandsaufnahme der 29 State-Mixins (`inventar-*.md`).
2. Zug-Ablauf aus dem State in einen Server-Dienst lösen; der Reflex-State wird dünner Client.
3. Ereignisstrom und Chat-API.
4. Erste Vue-Seite (Chat) neben Reflex unter `/api/app/`.
5. Seite für Seite: Einstellungen, Dokumente, Agenten, Vision, Audio.
6. Reflex entfernen: Mixins, `aifred/ui/`, `scripts/patch-reflex.py`, Reflex-Patches in `CLAUDE.md`;
   Server startet direkt (Granian/Uvicorn), nginx `/aifred/` auf den Server, Einzeilen-Installer.
