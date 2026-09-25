# Discord Channel Plugin

> **English version:** [discord.md](../../../en/guides/plugins/discord.md)

**Datei:** `aifred/plugins/channels/discord_channel/`

Channel-Plugin, das AIfred als Bot (über `discord.py`) mit Discord verbindet. Es lauscht
auf Nachrichten in konfigurierten Server-Kanälen und Direktnachrichten, leitet sie durch
die Message-Hub-Pipeline und schickt die Antwort zurück. Der Bot antwortet immer auf
empfangene Nachrichten (`always_reply = True`).

## Tools (für das LLM)

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `discord_send` | Nachricht in einen Discord-Kanal oder eine DM senden | COMMUNICATE |

### Parameter von `discord_send`

| Parameter | Pflicht | Beschreibung |
|-----------|---------|--------------|
| `message` | ja | Der zu sendende Nachrichtentext |
| `channel_id` | nein | Discord-Channel-ID; fällt auf den ersten konfigurierten Kanal zurück, wenn leer |

Nachrichten, die das 2000-Zeichen-Limit von Discord überschreiten, werden automatisch in
Teilstücke aufgeteilt.

## Features

- **WebSocket/Gateway:** Permanente Verbindung über die Discord Gateway API
- **Channel + DM:** Empfängt Nachrichten aus Server-Kanälen und Direktnachrichten. Jeder
  Absender wird zuerst gegen die Pflicht-Allowlist geprüft (siehe unten); Server-Kanäle
  werden zusätzlich gegen die konfigurierten Channel-IDs gefiltert (leere Liste = alle Kanäle)
- **`/clear` Slash-Command:** Leert die Konversation — setzt immer AIfreds Kontext (Route)
  zurück; in Server-Kanälen löscht er zusätzlich alle Nachrichten (dafür braucht der
  aufrufende Benutzer die Berechtigung `Nachrichten verwalten`; in DMs können Bots nicht
  massenhaft löschen)
- **Markdown:** Ausgehender Text wird unverändert durchgereicht — Discord rendert Markdown
  (fett/kursiv/Code/Links) nativ

## Konfiguration

Zugangsdaten werden über den Credential-Broker verwaltet (konfigurierbar in der
AIfred-Einstellungs-UI oder über `.env`):

| Credential | Beschreibung |
|------------|--------------|
| `DISCORD_BOT_TOKEN` | Bot-Token aus dem Discord Developer Portal (geheim) |
| `DISCORD_CHANNEL_IDS` | Kommagetrennte Channel-IDs, die überwacht werden (leer = alle Kanäle) |
| `DISCORD_ALLOWED_USERS` | **Pflicht-Allowlist** der Absender: kommagetrennte numerische User-IDs. Leer = niemand; `*` wird **nicht** unterstützt. **Nutzer hinzufügen:** ihn den Bot einmal anschreiben lassen — seine User-ID erscheint im AIfred-Log (`blocked message from user <ID>`); über das Zahnrad des Plugins → *Erlaubte User-IDs* eintragen |

Einrichtung:

1. Einen Bot im [Discord Developer Portal](https://discord.com/developers/applications)
   erstellen und dessen Token kopieren.
2. Das **Message Content Intent** für den Bot aktivieren (nötig, um Nachrichtentext zu lesen).
3. Den Bot mit Lese- und Schreibberechtigung auf den Server einladen.
4. Um bestimmte Kanäle zu überwachen, deren IDs kopieren (Entwicklermodus in Discord
   aktivieren, dann Rechtsklick auf einen Kanal → „ID kopieren") und kommagetrennt als
   `DISCORD_CHANNEL_IDS` eintragen. Leer lassen, um auf allen Kanälen zu lauschen.
