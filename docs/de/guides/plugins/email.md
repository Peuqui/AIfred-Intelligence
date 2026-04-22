# Email Channel Plugin

**Datei:** `aifred/plugins/channels/email_channel/`

Channel-Plugin fuer E-Mail-Kommunikation via IMAP IDLE und SMTP.

## Tools (für das LLM)

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `email` | E-Mails abrufen, lesen, suchen, senden, verschieben, löschen, markieren | COMMUNICATE |

Das `email`-Tool verwendet einen `action`-Parameter: `check`, `read`, `search`, `delete`, `send`, `move`, `list_folders`, `create_folder`, `mark`.

## Architektur-Ueberblick

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

- **Push-basiert:** IMAP IDLE fuer sofortige Benachrichtigung bei neuen E-Mails
- **Auto-Reply:** Eingehende Mails werden automatisch beantwortet
- **Startup-Recovery:** Mails die waehrend eines Neustarts ankommen, werden beim Start nachgeholt (Checkpoint-basiert)
- **Session-Routing:** Replies werden via `In-Reply-To` Header der urspruenglichen Session zugeordnet
- **Logging:** Alle Lifecycle-Events im journalctl (`journalctl -u aifred-intelligence | grep "\[email\]"`)

## Antwort-Verhalten

Das LLM unterscheidet automatisch zwischen zwei Szenarien:

| Eingehende Mail | AIfred's Verhalten |
|-----------------|-------------------|
| Normale Konversation ("Hallo", Fragen, Info) | Antwortet direkt per Auto-Reply |
| Irreversible Aktion ("Schick Mail an Bob", "Erstelle Termin") | Zeigt Entwurf, wartet auf Bestaetigung per Reply |

Bei irreversiblen Aktionen entsteht ein Multi-Turn-Flow ueber E-Mail:
```
Externer → "Schick eine Mail an bob@example.com mit Inhalt XYZ"
AIfred   → Auto-Reply: "Hier was ich tun wuerde: ... Bitte bestaetigen."
Externer → Reply: "Ja"          (landet in gleicher Session via In-Reply-To)
AIfred   → Fuehrt Aktion aus, Auto-Reply: "Erledigt."
```

## Startup-Recovery (Checkpoint)

Der IMAP-Listener speichert nach jeder verarbeiteten Mail die UID in
`data/message_hub/imap_checkpoint.json`:

```json
{"last_uid": 146, "uidvalidity": 1278976979}
```

Beim (Neu-)Start:
- Alle UIDs > `last_uid` werden als verpasst erkannt und nachgeholt
- Bei UIDVALIDITY-Aenderung (IMAP-Server hat UIDs neu vergeben): Recovery wird uebersprungen
- Erster Start (kein Checkpoint): Alle bestehenden Mails als "bekannt" behandelt

## Konfiguration

- IMAP/SMTP-Server, Port, Credentials ueber `.env` oder UI-Modal
- TLS/SSL konfigurierbar
- Allowlist fuer eingehende Absender (`EMAIL_ALLOWED_SENDERS`)

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

### Aufloesung bei ausgehenden E-Mails (Scheduler, Announce)

1. **Recipient im Job angegeben** (z.B. `"Lord Helmchen"`) → User-Mapping → `email_out` bevorzugt, Fallback auf `email`
2. **Kein Recipient** → Erster User im Mapping → `email_out` bevorzugt
3. **Kein Mapping** → Fallback auf `EMAIL_ALLOWED_SENDERS` (Allowlist, erster Eintrag)

### Allowlist (Eingang)

Die Allowlist in `EMAIL_ALLOWED_SENDERS` kontrolliert nur **eingehende** E-Mails — wer darf AIfred anschreiben. Ausgehende E-Mails koennen an jede Adresse gesendet werden.

## Delta Chat als Messenger-Alternative

[Delta Chat](https://delta.chat) ist ein Messenger der E-Mail als Transport nutzt.
Da AIfred ueber einen E-Mail-Account kommuniziert, funktioniert Delta Chat als
Chat-artige Oberflaeche fuer die Kommunikation mit AIfred — aehnlich wie Telegram
oder Discord, aber ohne separaten Bot-Account.

### Einrichtung

1. **Delta Chat installieren** (Desktop oder Mobil)
2. **Eigenen E-Mail-Account hinzufuegen** (z.B. `markus.peuckert@mail.de`)
3. **Mehrgeraete-Modus aktivieren** (Erweitert → Mehrgeraete-Modus)
   - Dadurch ueberwacht Delta Chat den Gesendet-Ordner
   - AIfred's Antworten erscheinen dann auch als Chat-Blasen
4. **Neuen Chat starten** mit AIfred's E-Mail-Adresse (z.B. `lord.helmchen@gmx.net`)
5. **Absender-Adresse in die Allowlist eintragen** (`EMAIL_ALLOWED_SENDERS`)

### Hinweise

- Delta Chat generiert `@localhost` Message-IDs — das Session-Routing
  funktioniert trotzdem ueber `In-Reply-To` Header
- Nachrichten von Delta Chat erscheinen in AIfred als normale eingehende E-Mails
- AIfred's Antworten erscheinen in Delta Chat dank der Kopie im Gesendet-Ordner
- Mehrere Profile moeglich: Ein Profil fuer den normalen Mail-Account,
  ein weiteres fuer einen anderen Account — unabhaengig voneinander
- Delta Chat zeigt Nachrichten als Chat-Blasen mit Zeitstempel,
  was die Kommunikation mit AIfred natuerlicher wirken laesst
