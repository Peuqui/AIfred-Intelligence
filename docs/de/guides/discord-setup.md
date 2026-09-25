# Discord Bot Setup

> **English version:** [discord-setup.md](../../en/guides/discord-setup.md)

## 1. Bot erstellen

1. Das [Discord Developer Portal](https://discord.com/developers/applications) öffnen → **New Application**
2. Seite **Bot**: **Reset Token** → Token kopieren. **Message Content Intent** aktivieren
3. **Public Bot** deaktivieren — nur du sollst ihn zu Servern hinzufügen können
4. **OAuth2 → URL Generator:** Scope `bot`, Berechtigungen *Send Messages*,
   *Read Message History*, *View Channels* (plus *Manage Messages*, wenn `/clear`
   auch Server-Kanäle leeren soll)
5. Die erzeugte URL öffnen → deinen Server auswählen → autorisieren

## 2. Kanal- und User-IDs

1. Entwicklermodus in Discord aktivieren: *Einstellungen → Erweitert → Entwicklermodus*
2. Einen privaten Kanal anlegen (z.B. `#aifred`) und den Bot hinzufügen
3. Rechtsklick auf den Kanal → **Kanal-ID kopieren**
4. Rechtsklick auf deinen eigenen Namen → **Benutzer-ID kopieren**

## 3. AIfred konfigurieren

In der Web-UI: **Plugin Manager → Discord → Zahnrad-Symbol** — Bot-Token,
Kanal-ID(s) und erlaubte User-IDs eintragen → *Save & Activate*. Oder in `.env`:

```env
DISCORD_ENABLED=true
DISCORD_BOT_TOKEN=<bot token>
DISCORD_CHANNEL_IDS=<channel id>          # comma-separated; empty = all channels
DISCORD_ALLOWED_USERS=<your user id>      # mandatory, comma-separated
```

Nach manueller Bearbeitung von `.env` AIfred neu starten.

## 4. Testen

Im Kanal schreiben oder dem Bot eine Direktnachricht schicken. AIfred antwortet auf jede
akzeptierte Nachricht (`always_reply = True`).

| Befehl | Beschreibung |
|---|---|
| `/clear` | Setzt AIfreds Kontext für diese Konversation zurück; in Server-Kanälen löscht er zusätzlich die Nachrichten (braucht *Manage Messages*; Bots können in DMs nicht massenhaft löschen) |

## Security

- **Allowlist (Pflicht):** Nur User-IDs in `DISCORD_ALLOWED_USERS` werden
  verarbeitet — in Server-Kanälen wie in DMs. Leer = niemand; `*` wird **nicht**
  unterstützt. **User hinzufügen:** Lass ihn dem Bot einmal schreiben; seine ID erscheint
  im AIfred-Log (`blocked message from user <ID>`); trag sie über das Zahnrad-Symbol des Plugins
  → *Allowed user IDs* ein.
- **Tier:** Discord-Nachrichten laufen standardmäßig mit `TIER_COMMUNICATE` — kein Dateisystem-Zugriff,
  keine Code-Ausführung. Anheben lässt sich das nur im Plugin
  Manager, niemals durch einen Absender.
- **Credentials** laufen über den Credential Broker und erreichen nie das LLM.

Plugin-Referenz: [Discord-Plugin](plugins/discord.md).
