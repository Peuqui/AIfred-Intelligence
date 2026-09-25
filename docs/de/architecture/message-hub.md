# Message Hub — Architektur & Implementierungsplan

> **English version:** [message-hub.md](../../en/architecture/message-hub.md)

**Stand:** 2026-03-28
**Status:** Paket 1-5 implementiert — E-Mail-Kanal bereit zum Testen

---

## Konzept

AIfred wird zum zentralen Dispatcher für alle Kommunikationskanäle.
Jeder Kanal (E-Mail, Discord, Telegram, Signal) ist ein Plugin mit eigener Identität.
AIfred ist ein eigenständiger Teilnehmer — er überwacht NICHT die Kanäle des Users,
sondern hat eigene Adressen (eigene E-Mail, eigener Discord-Bot, etc.).

---

## Architektur-Übersicht

```
                        ┌──────────────────────────┐
                        │      Message Hub          │
                        │   (Background Workers)    │
                        └────────────┬─────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
    ┌─────────▼────────┐  ┌─────────▼────────┐  ┌─────────▼────────┐
    │  IMAP Listener   │  │  Discord Bot     │  │  Telegram Bot    │
    │  (IMAP IDLE)     │  │  (WebSocket)     │  │  (Bot API)       │
    └─────────┬────────┘  └─────────┬────────┘  └─────────┬────────┘
              │                      │                      │
              └──────────────────────┼──────────────────────┘
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │  InboundMessage      │
                          │  (Envelope)          │
                          │  - channel           │
                          │  - channel_id        │
                          │  - sender            │
                          │  - text              │
                          │  - target_agent      │
                          │  - metadata          │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │   Routing Table     │
                          │   (SQLite)          │
                          │   channel+id → session │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │   AIfred Engine     │
                          │   (oder Sokrates/   │
                          │    Salomo je nach   │
                          │    target_agent)    │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │   Outbound Reply    │
                          │   (zurück über      │
                          │    selben Kanal)    │
                          └─────────────────────┘
```

---

## Envelope-Normalisierung (InboundMessage)

Jede eingehende Nachricht wird in ein einheitliches Format normalisiert.
Die AIfred Engine sieht nie kanal-spezifische Details — sie bekommt Text rein
und gibt Text raus. Die Kanal-Plugins kümmern sich um den Rest.

```python
@dataclass
class InboundMessage:
    channel: str            # "email", "discord", "telegram", "freeecho2", "scheduler"
    channel_id: str         # Thread-ID, Channel-ID, Conversation-ID
    sender: str             # E-Mail-Adresse, Discord-User, Telegram-User
    text: str               # Der eigentliche Nachrichteninhalt
    timestamp: datetime
    metadata: dict          # Kanal-spezifisch (Subject, Attachments, etc.)
    target_agent: str = "aifred"  # Welcher Agent soll antworten?

@dataclass
class OutboundMessage:
    channel: str            # Zurück über selben Kanal
    channel_id: str         # An selben Thread/Channel
    recipient: str          # An selben Sender
    text: str               # Antworttext
    media: str | None = None  # Optionales Bild (lokaler Pfad oder URL) als Anhang
    metadata: dict          # Kanal-spezifisch (Subject für E-Mail, etc.)
```

---

## Routing Table (SQLite)

Einfaches Mapping: Welche Konversation auf welchem Kanal gehört zu welcher AIfred-Session.

```sql
CREATE TABLE routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,           -- "email", "discord", etc.
    channel_id TEXT NOT NULL,        -- Thread-ID, Channel-ID
    session_id TEXT NOT NULL,        -- AIfred Session-ID
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, channel_id)
);
```

- Neue Nachricht → Route nachschlagen → Session gefunden? Weiterleiten.
- Keine Route? → Neue Session erstellen, Route anlegen.
- Session gelöscht? → Route löschen. Bei nächster Nachricht: neue Session.

---

## Agent-Routing

Wenn eine Nachricht an einen bestimmten Agenten gerichtet ist, antwortet dieser.
Es gibt keine Funktion pro Agent: `process_inbound()` setzt
`message.target_agent`, und `_call_engine(agent=...)` reicht ihn an
`call_llm(agent=...)` weiter — das gilt für AIfred, Sokrates, Salomo und jeden
Custom Agent aus der Agenten-Konfiguration.

Die Ziel-Agent-Erkennung läuft LLM-basiert: `detect_target_agent_via_llm()` in
`message_processor.py` kapselt `detect_query_intent_and_addressee()` (dieselbe
Erkennung wie im Browser) und liefert `(agent_id, intent, detected_language,
mode_switch_updates)`. Routing-Priorität (höchste gewinnt):

1. `metadata["wake_agent"]` — interne Trigger und Kanäle pinnen den Agenten so
   hart (z.B. der Scheduler, siehe `scheduler.py`, oder das FreeEcho.2-Wake-Word).
   Die Intent-Erkennung läuft trotzdem, ihr Adressat wird aber überschrieben
   (nur wenn der Wake-Agent konfiguriert ist).
2. Der vom LLM erkannte Adressat der aktuellen Nachricht
3. `active_agent` der Session (bleibt vom vorigen Turn hängen)
4. Default `"aifred"`

---

## Auto-Reply Toggle

- Default: **AUS** (`auto_reply` in `settings.json` → `channel_toggles`)
- Schalter pro Kanal im Plugins-Tab der Einstellungsseite (siehe
  [Aktivierung](#aktivierung)). Er erscheint nur bei Kanälen ohne
  `always_reply` — derzeit E-Mail. Discord, Telegram und FreeEcho.2 setzen
  `always_reply = True` und antworten immer (kein Schalter).
- Wenn AN: AIfred antwortet sofort automatisch über den Kanal
  (`plugin.send_reply()`)
- Wenn AUS: Die Antwort wird erzeugt und in der Session gespeichert, aber nicht gesendet

---

## User-Zuordnung

AIfred ist ein **Single-Owner-System** für den Message Hub:
- Alle eingehenden Nachrichten landen in Sessions des **Betreibers** (Owner)
- AIfred überwacht das Postfach des Owners, nicht die Postfächer anderer User
- Wenn jemand dem Owner eine E-Mail schickt, ist das eine Nachricht an den Owner
- Multi-User-Routing (verschiedene User bekommen eigene Sessions) ist Zukunftsmusik

AIfred hat zwar ein User-System (accounts.json, Whitelist, Session-Owner-Binding),
aber für den Message Hub ist erstmal nur der Hauptnutzer relevant.

---

## Allowlist / Security

- Pro Kanal konfigurierbar: Wer darf AIfred anschreiben?
  - E-Mail: `EMAIL_ALLOWED_SENDERS` — Adressen oder `@domain`; leer = niemand,
    `*` = alle
  - Telegram/Discord: numerische User-IDs; leer = niemand, `*` wird blockiert
- Unbekannte Sender werden ignoriert (nur geloggt, keine Antwort)
- Später erweiterbar: Pairing-Mechanismus wie OpenClaw

---

## Implementierungspakete

### Paket 1: Background Worker Infrastruktur + Envelope ✅
- [x] `aifred/lib/message_hub.py` — Worker-Management (register, start, stop)
- [x] `aifred/lib/envelope.py` — InboundMessage / OutboundMessage Dataclasses
- [x] Lifecycle: Start mit App, Stop bei Shutdown (Reflex lifespan)
- [x] Logging ins bestehende Debug-Log

### Paket 2: Routing Table ✅
- [x] `aifred/lib/routing_table.py` — SQLite-basiert
- [x] CRUD: get_route(), set_route(), delete_route(), delete_routes_for_session()
- [x] Auto-Cleanup wenn Session gelöscht wird (`session_storage.py` ruft
  `delete_routes_for_session()`)

### Paket 3: IMAP IDLE Listener ✅
- [x] `aifred/plugins/channels/email_channel/` — IMAP IDLE für Push-Notifications
- [x] Eingehende Mails erkennen (In-Reply-To Header für Thread-Zuordnung)
- [x] UID-basierte Erkennung neuer Mails
- [x] Auto-Reconnect bei Verbindungsfehlern
- [x] Integration mit Message Hub als Worker registrieren
- [x] Allowlist-Check (wer darf mailen?) — `EMAIL_ALLOWED_SENDERS`

### Paket 4: Processing Pipeline + Auto-Reply ✅
- [x] `aifred/lib/message_processor.py` — Bridge zwischen Hub und Engine
- [x] Eingehende Nachricht → Routing Table → Session erstellen/finden
- [x] AIfred Engine direkt aufrufen (call_llm)
- [x] Antwort per SMTP zurücksenden (bei Auto-Reply AN)
- [x] Session mit Konversation aktualisieren (update_chat_data)
- [x] Agent-Routing (Sokrates/Salomo wenn im Text angesprochen)
- [x] Config: MESSAGE_HUB_OWNER (Auto-Reply später nach `channel_toggles` verlagert)

### Paket 5: Settings & UI ✅
- [x] Schalter persistiert in `settings.json` → `channel_toggles` (`monitor`,
  `listener`, `auto_reply` pro Kanal) statt der ursprünglich geplanten
  Config-Keys MESSAGE_HUB_ENABLED, EMAIL_MONITOR_ENABLED, AUTO_REPLY_*
- [x] Abschnitt "Channels" im Plugins-Tab der Einstellungsseite
  (`aifred/ui/agent_editor/plugins.py`)
- [x] Toggle pro Kanal: Plugin An/Aus, Monitor An/Aus, Auto-Reply An/Aus
- [x] Allowlist-Konfiguration (Zugangsdaten-Seite über das Zahnrad)

### Paket 6: Weitere Kanäle
- [x] Discord Bot Plugin (`plugins/channels/discord_channel/`)
- [x] Telegram Bot Plugin (`plugins/channels/telegram_channel/`)
- [x] FreeEcho.2 Voice-Kanal (`plugins/channels/freeecho2_channel/`)
- [x] Auto-Discovery + Registrierung via `message_hub.py: register_channel_workers()`
- [ ] Kanalübergreifendes Routing

---

## Design-Prinzipien

- Envelope-Normalisierung (InboundMessage/OutboundMessage)
- Eigene Identität pro Kanal (kein Mitlesen von User-Accounts)
- Allowlist/Security als First-Class-Feature
- Mention-Gating in Gruppen (Discord: nur auf @AIfred reagieren)

---

## Setup & Workflow

### Voraussetzungen

1. **E-Mail Credentials** werden aus Umgebungsvariablen gelesen. Normalerweise
   trägst du sie auf der Zugangsdaten-Seite ein (Zahnrad in der E-Mail-Zeile,
   siehe [Aktivierung](#aktivierung)): Speichern schreibt das Passwort und
   `EMAIL_ENABLED=true` in `.env`, die übrigen Felder in
   `aifred/plugins/channels/email_channel/settings.json` (wird beim Start in die
   Umgebung geladen und hat Vorrang vor `.env`). Direkt in `.env` setzen
   funktioniert ebenfalls:
   ```
   EMAIL_ENABLED=true
   EMAIL_IMAP_HOST=imap.example.com
   EMAIL_IMAP_PORT=993
   EMAIL_SMTP_HOST=smtp.example.com
   EMAIL_SMTP_PORT=587
   EMAIL_USER=aifred@example.com
   EMAIL_PASSWORD=geheim
   EMAIL_FROM=aifred@example.com
   EMAIL_ALLOWED_SENDERS=du@example.com, @familie.example
   EMAIL_SENT_FOLDER=Gesendet   # optional, das ist der Default
   ```
   Ohne `EMAIL_ALLOWED_SENDERS` wird jede Mail blockiert (leer = niemand).

2. **Message Hub Owner** (Pflicht, setzt `scripts/install-all.sh` mit dem
   ersten Benutzer):
   ```
   MESSAGE_HUB_OWNER=deinname
   ```
   Das AIfred-Konto, dem die vom Hub erstellten Sessions gehören und das bei
   Scheduler- und Webhook-Läufen als Owner gilt. Ohne den Wert startet AIfred nicht.

### Aktivierung

1. AIfred starten (oder neu starten, wenn du `.env` von Hand geändert hast)
2. In der Web-UI: Menü-Symbol (☰, oben rechts) → **Einstellungen** → Tab
   **Plugins** → Abschnitt **Channels** → Zeile **E-Mail**: Schalter ON (ist das
   Plugin noch nicht konfiguriert, öffnet sich die Zugangsdaten-Seite)
3. Darunter: **Monitor: ON** (startet den IMAP-IDLE-Listener)
4. Optional: **Auto-Reply: ON** (erscheint erst bei Monitor ON; AIfred antwortet automatisch per E-Mail)

### Ablauf: Eingehende E-Mail

```
Neue E-Mail im Postfach
  │
  ▼
IMAP IDLE Listener erkennt neue UID
  │
  ▼
E-Mail wird gefetcht → InboundMessage (Envelope)
  ├── channel: "email"
  ├── channel_id: Message-ID / In-Reply-To (Thread)
  ├── sender: Absender
  ├── text: Body (max 10.000 Zeichen)
  └── metadata: Subject, Message-ID, References
  │
  ▼
Absender-Gate: Allowlist (EMAIL_ALLOWED_SENDERS), SPF/DKIM/DMARC "fail",
maschinell erzeugte Mail (RFC 3834) → verworfen, nur geloggt
  │
  ▼
Routing Table (SQLite): Thread bekannt?
  ├── JA → Bestehende Session laden
  └── NEIN → Neue Session erstellen (owner = MESSAGE_HUB_OWNER)
  │
  ▼
Agent-Routing: "Sokrates, ..." → target_agent = "sokrates"
  │
  ▼
AIfred Engine: call_llm()
  ├── Model + Backend aus settings.json
  ├── Temperature, Thinking Mode etc. aus Settings
  └── Agent je nach target_agent
  │
  ▼
Session aktualisieren
  ├── Chat-History: "[E-Mail] sender — subject" + Body
  │   (Kanalname in der UI-Sprache, build_user_chat_content())
  └── LLM-History: User-Text + AIfred-Antwort
  │
  ▼
Auto-Reply aktiv?
  ├── JA → SMTP senden (Re: Subject, In-Reply-To Header)
  └── NEIN → Nur in Session sichtbar
```

### Ablauf: Monitor ein-/ausschalten zur Laufzeit

Der E-Mail Monitor kann über die UI **ohne Neustart** ein-/ausgeschaltet werden:

- **Einschalten:** Worker wird registriert + asyncio Task gestartet
- **Ausschalten:** Worker wird abgemeldet + Task gecancelt
- **Persistenz:** Einstellung wird in `settings.json` gespeichert,
  beim nächsten App-Start automatisch wieder aktiv

### Datenbank

Die Routing Table liegt in `data/message_hub/routing.db` (SQLite).
Wird automatisch erstellt beim ersten Zugriff.

---

## Modul-Übersicht

| Modul | Datei | Funktion |
|-------|-------|----------|
| Envelope | `aifred/lib/envelope.py` | InboundMessage / OutboundMessage Dataclasses |
| Message Hub | `aifred/lib/message_hub.py` | Worker-Lifecycle (register, start, stop) |
| Routing Table | `aifred/lib/routing_table.py` | SQLite: (channel, channel_id) → session_id |
| IMAP Listener | `aifred/plugins/channels/email_channel/__init__.py` | IMAP IDLE, erkennt neue Mails (IMAP-/SMTP-Helfer in `client.py`) |
| Processor | `aifred/lib/message_processor.py` | Session-Management, Engine-Aufruf, Auto-Reply |
| Lifespan | `aifred/aifred.py` | Startup/Shutdown Hook + Worker-Registrierung |
| Settings | `aifred/state/_settings_mixin.py` | UI-Toggles + Persistenz |
| UI | `aifred/ui/agent_editor/plugins.py` | Kanal-Toggles (Listener, Auto-Reply) im Plugins-Tab |
| Config | `aifred/lib/config.py` | MESSAGE_HUB_OWNER (Auto-Reply kommt aus `channel_toggles` in `settings.json`) |
| i18n | `aifred/lib/i18n/` | Übersetzungen (DE/EN, `de.json` / `en.json`) |

---

## Dependencies

Keine neuen System-Dependencies für Paket 1-5.
- `sqlite3` — Python Standardbibliothek
- `imaplib` — Python Standardbibliothek
- `smtplib` — Python Standardbibliothek (schon genutzt)
- `asyncio` — Python Standardbibliothek

Für spätere Pakete:
- `discord.py` — Discord Bot (pip install)
- `python-telegram-bot` — Telegram Bot (pip install)
- `signal-cli-rest-api` — Signal (Docker Container)
