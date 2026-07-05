# Message Hub — Architektur & Implementierungsplan

**Stand:** 2026-03-28
**Status:** Paket 1-5 implementiert — E-Mail-Kanal bereit zum Testen

---

## Konzept

AIfred wird zum zentralen Dispatcher fuer alle Kommunikationskanaele.
Jeder Kanal (E-Mail, Discord, Telegram, Signal) ist ein Plugin mit eigener Identitaet.
AIfred ist ein eigenstaendiger Teilnehmer — er ueberwacht NICHT die Kanaele des Users,
sondern hat eigene Adressen (eigene E-Mail, eigener Discord-Bot, etc.).

---

## Architektur-Uebersicht

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
                          │   (zurueck ueber    │
                          │    selben Kanal)    │
                          └─────────────────────┘
```

---

## Envelope-Normalisierung (InboundMessage)

Jede eingehende Nachricht wird in ein einheitliches Format normalisiert.
Die AIfred Engine sieht nie kanal-spezifische Details — sie bekommt Text rein
und gibt Text raus. Die Kanal-Plugins kuemmern sich um den Rest.

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
    channel: str            # Zurueck ueber selben Kanal
    channel_id: str         # An selben Thread/Channel
    recipient: str          # An selben Sender
    text: str               # Antworttext
    metadata: dict          # Kanal-spezifisch (Subject fuer E-Mail, etc.)
```

---

## Routing Table (SQLite)

Einfaches Mapping: Welche Konversation auf welchem Kanal gehoert zu welcher AIfred-Session.

```sql
CREATE TABLE routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,           -- "email", "discord", etc.
    channel_id TEXT NOT NULL,        -- Thread-ID, Channel-ID
    session_id TEXT NOT NULL,        -- AIfred Session-ID
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel, channel_id)
);
```

- Neue Nachricht → Route nachschlagen → Session gefunden? Weiterleiten.
- Keine Route? → Neue Session erstellen, Route anlegen.
- Session geloescht? → Route loeschen. Bei naechster Nachricht: neue Session.

---

## Agent-Routing

Wenn eine Nachricht an einen bestimmten Agenten gerichtet ist, wird dieser aufgerufen:

- Default: `run_aifred_direct_response()`
- "@Sokrates ..." → `run_sokrates_direct_response()`
- "@Salomo ..." → `run_salomo_direct_response()`
- Custom Agents → entsprechende Funktion

Die Ziel-Agent-Erkennung laeuft LLM-basiert in `message_processor.py:
process_inbound()`. Interne Trigger koennen den Agenten hart pinnen, indem sie
`metadata["wake_agent"]` setzen (z.B. der Scheduler, siehe `scheduler.py`) —
dann greift kein Rerouting durch die Intent-Erkennung.

---

## Auto-Reply Toggle

- Default: **AUS** (Nachrichten werden in Web-UI angezeigt, User entscheidet)
- Toggle in Settings-Dropdown pro Kanal:
  - E-Mail Auto-Reply: An/Aus
  - Discord Auto-Reply: An/Aus
  - Telegram Auto-Reply: An/Aus
  - Signal Auto-Reply: An/Aus
- Wenn AN: AIfred antwortet sofort automatisch
- Wenn AUS: Nachricht erscheint in Session, User gibt Antwort frei

---

## User-Zuordnung

AIfred ist ein **Single-Owner-System** fuer den Message Hub:
- Alle eingehenden Nachrichten landen in Sessions des **Betreibers** (Owner)
- AIfred ueberwacht das Postfach des Owners, nicht die Postfaecher anderer User
- Wenn jemand dem Owner eine E-Mail schickt, ist das eine Nachricht an den Owner
- Multi-User-Routing (verschiedene User bekommen eigene Sessions) ist Zukunftsmusik

AIfred hat zwar ein User-System (accounts.json, Whitelist, Session-Owner-Binding),
aber fuer den Message Hub ist erstmal nur der Hauptnutzer relevant.

---

## Allowlist / Security

- Pro Kanal konfigurierbar: Wer darf AIfred anschreiben?
- Unbekannte Sender werden ignoriert oder bekommen Standardantwort
- Spaeter erweiterbar: Pairing-Mechanismus wie OpenClaw

---

## Implementierungspakete

### Paket 1: Background Worker Infrastruktur + Envelope ✅
- [x] `aifred/lib/message_hub.py` — Worker-Management (register, start, stop)
- [x] `aifred/lib/envelope.py` — InboundMessage / OutboundMessage Dataclasses
- [x] Lifecycle: Start mit App, Stop bei Shutdown (Reflex lifespan)
- [x] Logging ins bestehende Debug-Log

### Paket 2: Routing Table ✅
- [x] `aifred/lib/routing_table.py` — SQLite-basiert
- [x] CRUD: get_route(), set_route(), delete_route(), get_routes_for_session()
- [ ] Auto-Cleanup wenn Session geloescht wird

### Paket 3: IMAP IDLE Listener ✅
- [x] `aifred/lib/imap_listener.py` — IMAP IDLE fuer Push-Notifications
- [x] Eingehende Mails erkennen (In-Reply-To Header fuer Thread-Zuordnung)
- [x] UID-basierte Erkennung neuer Mails
- [x] Auto-Reconnect bei Verbindungsfehlern
- [x] Integration mit Message Hub als Worker registrieren
- [ ] Allowlist-Check (wer darf mailen?)

### Paket 4: Processing Pipeline + Auto-Reply ✅
- [x] `aifred/lib/message_processor.py` — Bridge zwischen Hub und Engine
- [x] Eingehende Nachricht → Routing Table → Session erstellen/finden
- [x] AIfred Engine direkt aufrufen (call_llm)
- [x] Antwort per SMTP zuruecksenden (bei Auto-Reply AN)
- [x] Session mit Konversation aktualisieren (update_chat_data)
- [x] Agent-Routing (Sokrates/Salomo wenn im Text angesprochen)
- [x] Config: MESSAGE_HUB_OWNER, EMAIL_MONITOR_AUTO_REPLY

### Paket 5: Settings & UI
- [ ] Config-Keys: MESSAGE_HUB_ENABLED, EMAIL_MONITOR_ENABLED, AUTO_REPLY_*
- [ ] Settings-Dropdown erweitern: Message Hub Sektion
- [ ] Toggle pro Kanal: Monitor An/Aus, Auto-Reply An/Aus
- [ ] Allowlist-Konfiguration

### Paket 6: Weitere Kanaele
- [x] Discord Bot Plugin (`plugins/channels/discord_channel/`)
- [x] Telegram Bot Plugin (`plugins/channels/telegram_channel/`)
- [x] FreeEcho.2 Voice-Kanal (`plugins/channels/freeecho2_channel/`)
- [x] Auto-Discovery + Registrierung via `message_hub.py: register_channel_workers()`
- [ ] Kanaluebergreifendes Routing

---

## Design-Prinzipien

- Envelope-Normalisierung (InboundMessage/OutboundMessage)
- Eigene Identitaet pro Kanal (kein Mitlesen von User-Accounts)
- Allowlist/Security als First-Class-Feature
- Mention-Gating in Gruppen (Discord: nur auf @AIfred reagieren)

---

## Setup & Workflow

### Voraussetzungen

1. **E-Mail Credentials** muessen als Umgebungsvariablen gesetzt sein (`.env`):
   ```
   EMAIL_ENABLED=true
   EMAIL_IMAP_HOST=imap.example.com
   EMAIL_IMAP_PORT=993
   EMAIL_SMTP_HOST=smtp.example.com
   EMAIL_SMTP_PORT=587
   EMAIL_USER=aifred@example.com
   EMAIL_PASSWORD=geheim
   EMAIL_FROM=aifred@example.com
   ```

2. **Message Hub Owner** (optional, Default: `mp`):
   ```
   MESSAGE_HUB_OWNER=mp
   ```
   Sessions die der Hub erstellt gehoeren diesem User.

### Aktivierung

1. AIfred starten (oder neu starten wenn `.env` geaendert)
2. In der Web-UI: **Settings → Message Hub → E-Mail Monitor: ON**
3. Optional: **Auto-Reply: ON** (AIfred antwortet automatisch per E-Mail)

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
Agent-Routing: "Sokrates, ..." → target_agent = "sokrates"
  │
  ▼
Routing Table (SQLite): Thread bekannt?
  ├── JA → Bestehende Session laden
  └── NEIN → Neue Session erstellen (owner = MESSAGE_HUB_OWNER)
  │
  ▼
AIfred Engine: call_llm()
  ├── Model + Backend aus settings.json
  ├── Temperature, Thinking Mode etc. aus Settings
  └── Agent je nach target_agent
  │
  ▼
Session aktualisieren
  ├── Chat-History: "[EMAIL] sender — subject" + Body
  └── LLM-History: User-Text + AIfred-Antwort
  │
  ▼
Auto-Reply aktiv?
  ├── JA → SMTP senden (Re: Subject, In-Reply-To Header)
  └── NEIN → Nur in Session sichtbar
```

### Ablauf: Monitor ein-/ausschalten zur Laufzeit

Der E-Mail Monitor kann ueber die UI **ohne Neustart** ein-/ausgeschaltet werden:

- **Einschalten:** Worker wird registriert + asyncio Task gestartet
- **Ausschalten:** Worker wird abgemeldet + Task gecancelt
- **Persistenz:** Einstellung wird in `settings.json` gespeichert,
  beim naechsten App-Start automatisch wieder aktiv

### Datenbank

Die Routing Table liegt in `data/message_hub/routing.db` (SQLite).
Wird automatisch erstellt beim ersten Zugriff.

---

## Modul-Uebersicht

| Modul | Datei | Funktion |
|-------|-------|----------|
| Envelope | `aifred/lib/envelope.py` | InboundMessage / OutboundMessage Dataclasses |
| Message Hub | `aifred/lib/message_hub.py` | Worker-Lifecycle (register, start, stop) |
| Routing Table | `aifred/lib/routing_table.py` | SQLite: (channel, channel_id) → session_id |
| IMAP Listener | `aifred/lib/imap_listener.py` | IMAP IDLE, erkennt neue Mails |
| Processor | `aifred/lib/message_processor.py` | Session-Management, Engine-Aufruf, Auto-Reply |
| Lifespan | `aifred/aifred.py` | Startup/Shutdown Hook + Worker-Registrierung |
| Settings | `aifred/state/_settings_mixin.py` | UI-Toggles + Persistenz |
| UI | `aifred/ui/settings_accordion.py` | Message Hub Sektion in Settings-Dropdown |
| Config | `aifred/lib/config.py` | MESSAGE_HUB_OWNER, EMAIL_MONITOR_AUTO_REPLY |
| i18n | `aifred/lib/i18n.py` | Uebersetzungen (DE/EN) |

---

## Dependencies

Keine neuen System-Dependencies fuer Paket 1-5.
- `sqlite3` — Python Standardbibliothek
- `imaplib` — Python Standardbibliothek
- `smtplib` — Python Standardbibliothek (schon genutzt)
- `asyncio` — Python Standardbibliothek

Fuer spaetere Pakete:
- `discord.py` — Discord Bot (pip install)
- `python-telegram-bot` — Telegram Bot (pip install)
- `signal-cli-rest-api` — Signal (Docker Container)
