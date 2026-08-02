# Email Channel Plugin

**Datei:** `aifred/plugins/channels/email_channel/`

Channel-Plugin für E-Mail-Kommunikation via IMAP IDLE und SMTP.

## Tools (für das LLM)

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `email` | E-Mails abrufen, lesen, suchen, senden, verschieben, löschen, markieren | COMMUNICATE |

Das `email`-Tool dispatcht über einen `action`-Parameter:
`check`, `read`, `search`, `delete`, `send`, `move`, `list_folders`, `create_folder`, `mark`.

| Action | Pflicht-Parameter | Hinweise |
|--------|-------------------|----------|
| `check` | – | `n` (Default 10, max. 20), `folder` (Default INBOX) |
| `read` | `msg_id` | `folder` (Default INBOX) |
| `search` | `query` | `folder` (Default INBOX) |
| `send` | `to`, `subject`, `body` | registriert die Session-Route |
| `move` | `msg_id`, `target_folder` | `folder` = Quelle (Default INBOX) |
| `delete` | `msg_id` | `folder` (Default INBOX) |
| `mark` | `msg_id`, `flag` | `flag` ∈ `read` / `unread` / `flagged` / `unflagged` |
| `list_folders` | – | |
| `create_folder` | `folder_name` | |

Alle IMAP/SMTP-Operationen laufen in `asyncio.to_thread()` (blockierendes I/O).

## Architektur-Überblick

```
Externer Absender                      AIfred (GMX-Account)
      |                                      |
      |--- E-Mail -->  INBOX  <-- IMAP IDLE Listener (Background Worker)
      |                                      |
      |                              _process_uid()
      |                                      |
      |                              Message Processor
      |                              (Session + Routing)
      |                                      |
      |                                LLM generiert Antwort
      |                                      |
      |<-- Auto-Reply ---  SMTP  <-- send_reply()
```

## Features

- **Push-basiert:** IMAP IDLE für sofortige Benachrichtigung bei neuen E-Mails
- **Auto-Reply:** Eingehende Mails werden automatisch beantwortet
- **Startup-Recovery:** Mails die während eines Neustarts ankommen, werden beim Start nachgeholt (Checkpoint-basiert)
- **Session-Routing:** Replies werden via `In-Reply-To` Header der ursprünglichen Session zugeordnet
- **HTML + Plaintext:** Antworten werden als `multipart/alternative` gesendet (Agent-Markdown wird zu HTML gerendert, mit Plaintext-Fallback)
- **Logging:** Alle Lifecycle-Events im journalctl (`journalctl -u aifred-intelligence | grep "Email Plugin"`)

## Antwort-Verhalten

Das LLM unterscheidet automatisch zwischen zwei Szenarien:

| Eingehende Mail | AIfred's Verhalten |
|-----------------|-------------------|
| Normale Konversation ("Hallo", Fragen, Info) | Antwortet direkt per Auto-Reply |
| Irreversible Aktion ("Schick Mail an Bob", "Erstelle Termin") | Zeigt Entwurf, wartet auf Bestätigung per Reply |

Bei irreversiblen Aktionen entsteht ein Multi-Turn-Flow über E-Mail:
```
Externer → "Schick eine Mail an bob@example.com mit Inhalt XYZ"
AIfred   → Auto-Reply: "Hier was ich tun würde: ... Bitte bestätigen."
Externer → Reply: "Ja"          (landet in gleicher Session via In-Reply-To)
AIfred   → Führt Aktion aus, Auto-Reply: "Erledigt."
```

## Startup-Recovery (Checkpoint)

Der IMAP-Listener speichert nach jeder verarbeiteten Mail die UID in
`data/message_hub/imap_checkpoint.json`:

```json
{"last_uid": 146, "uidvalidity": 1278976979}
```

Beim (Neu-)Start:
- Alle UIDs > `last_uid` werden als verpasst erkannt und nachgeholt
- Bei UIDVALIDITY-Änderung (IMAP-Server hat UIDs neu vergeben): Recovery wird übersprungen
- Erster Start (kein Checkpoint): Alle bestehenden Mails als "bekannt" behandelt

## Konfiguration

Credentials werden über `.env` oder das UI-Modal eingegeben (verwaltet vom Credential-Broker):

| Feld | Default | Zweck |
|------|---------|-------|
| `EMAIL_IMAP_HOST` | – | IMAP-Server |
| `EMAIL_IMAP_PORT` | `993` | IMAP-Port (SSL) |
| `EMAIL_SMTP_HOST` | – | SMTP-Server |
| `EMAIL_SMTP_PORT` | `587` | SMTP-Port (STARTTLS) |
| `EMAIL_USER` | – | Account-Login |
| `EMAIL_PASSWORD` | – | Account-Passwort (geheim) |
| `EMAIL_FROM` | fällt auf `EMAIL_USER` zurück | Anzeigename |
| `EMAIL_ALLOWED_SENDERS` | – | Allowlist für eingehende Absender |

Das Plugin gilt als konfiguriert, wenn `enabled = true` ist und IMAP-Host, User
und Passwort gesetzt sind.

### Allowlist-Semantik (`EMAIL_ALLOWED_SENDERS`)

Die Allowlist kontrolliert nur **eingehende** E-Mails — wer darf AIfred anschreiben.
Ausgehende E-Mails können an jede Adresse gesendet werden.

- **Leer** → niemand erlaubt (sicherer Default)
- **`*`** → alle erlaubt
- **Kommagetrennte** Adressen/Domains: `user@mail.de, @family.de`
  - `@domain.de` matcht jede Adresse dieser Domain
  - eine reine Adresse matcht exakt

### Absender-Authentifizierung (SPF/DKIM/DMARC)

Der From-Header ist trivial fälschbar, deshalb liest der Listener zusätzlich
das Urteil, das dein Mail-Provider beim Empfang in den obersten
`Authentication-Results`-Header (RFC 8601) stempelt:

- **`fail`** (Provider sagt SPF/DKIM/DMARC fehlgeschlagen → vermutlich
  gespooft): Die Mail wird verworfen, bevor sie die Pipeline erreicht, mit
  Log-Warnung
- **`pass`**: Voraussetzung für die **Owner-Elevation** — nur dann bekommt der
  erste Allowlist-Eintrag `OWNER_TIER` (siehe Security-Architektur-Doku)
- **`none`** (Provider stempelt keine solchen Header): Die Mail wird normal
  verarbeitet, erhält aber niemals Owner-Rechte

### Poison-Message-Handling (Bounded Retry)

Wirft die Verarbeitung einer Mail dauerhaft Fehler (Fetch/Dispatch), wird die
UID einmal pro Reconnect-Zyklus (~30 s) erneut versucht — transiente Fehler
wie ein kurz nicht erreichbares LLM-Backend erholen sich von selbst. Nach
`EMAIL_MAX_PROCESS_ATTEMPTS` Fehlschlägen (Plugin-Config, Default 5, per Env
überschreibbar) wird die Mail **auf dem Server geflaggt** (`\Flagged` —
erscheint als Stern im Mail-Client, so siehst du, was hängen blieb) und
übersprungen — eine einzelne Poison-Message kann die Queue nie blockieren.
Die Mail selbst bleibt im Postfach.

## User-Mapping und E-Mail-Routing

AIfred unterscheidet zwischen **eingehenden** und **ausgehenden** E-Mail-Adressen pro User.
Die Zuordnung wird in `data/user_mapping.json` konfiguriert:

```json
{
  "Lord Helmchen": {
    "telegram": ["8669153916"],
    "discord": [],
    "email": ["empfang@gmx.net"],
    "email_out": ["versand@mail.de"]
  }
}
```

### Routing-Logik

| Feld | Zweck | Beispiel |
|------|-------|---------|
| `email` | **Eingang:** Von dieser Adresse darf der User AIfred anschreiben | `empfang@gmx.net` |
| `email_out` | **Ausgang:** Hierhin sendet AIfred Ergebnisse (Scheduler, Tool-Calls) | `versand@mail.de` |

### Auflösung bei ausgehenden E-Mails (Scheduler, Announce)

1. **Recipient im Job angegeben** (z.B. `"Lord Helmchen"`) → User-Mapping → `email_out` bevorzugt, Fallback auf `email`
2. **Kein Recipient** → Erster User im Mapping → `email_out` bevorzugt
3. **Kein Mapping** → Fallback auf `EMAIL_ALLOWED_SENDERS` (Allowlist, erster Eintrag)

## Delta Chat als Messenger-Alternative

[Delta Chat](https://delta.chat) ist ein Messenger der E-Mail als Transport nutzt.
Da AIfred über einen E-Mail-Account kommuniziert, funktioniert Delta Chat als
Chat-artige Oberfläche für die Kommunikation mit AIfred — ähnlich wie Telegram
oder Discord, aber ohne separaten Bot-Account.

### Einrichtung

1. **Delta Chat installieren** (Desktop oder Mobil)
2. **Eigenen E-Mail-Account hinzufügen** (z.B. `markus.peuckert@mail.de`)
3. **Mehrgeräte-Modus aktivieren** (Erweitert → Mehrgeräte-Modus)
   - Dadurch überwacht Delta Chat den Gesendet-Ordner
   - AIfred's Antworten erscheinen dann auch als Chat-Blasen
4. **Neuen Chat starten** mit AIfred's E-Mail-Adresse (z.B. `lord.helmchen@gmx.net`)
5. **Absender-Adresse in die Allowlist eintragen** (`EMAIL_ALLOWED_SENDERS`)

### Hinweise

- Delta Chat generiert `@localhost` Message-IDs — das Session-Routing
  funktioniert trotzdem über `In-Reply-To` Header
- Nachrichten von Delta Chat erscheinen in AIfred als normale eingehende E-Mails
- AIfred's Antworten erscheinen in Delta Chat dank der Kopie im Gesendet-Ordner
- Mehrere Profile möglich: Ein Profil für den normalen Mail-Account,
  ein weiteres für einen anderen Account — unabhängig voneinander
- Delta Chat zeigt Nachrichten als Chat-Blasen mit Zeitstempel,
  was die Kommunikation mit AIfred natürlicher wirken lässt
