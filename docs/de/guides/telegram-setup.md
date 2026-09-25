# Telegram Bot Setup

> **English version:** [telegram-setup.md](../../en/guides/telegram-setup.md)

## 1. Bot erstellen

1. Telegram öffnen, `@BotFather` anschreiben
2. `/newbot` senden
3. Bot-Name und Username vergeben
4. **Bot-Token** kopieren (z.B. `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

## 2. User-ID herausfinden

1. Telegram öffnen, `@userinfobot` anschreiben
2. `/start` senden
3. **User-ID** notieren (z.B. `123456789`)

Für mehrere erlaubte User: Jede User-ID kommagetrennt.

## 3. AIfred konfigurieren

In `.env` (oder über die Settings-UI):

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_ALLOWED_USERS=123456789
```

AIfred neu starten. In der Web-UI: **Settings > Telegram > Monitor: ON**.

## 4. Testen

Dem Bot eine Nachricht schicken. AIfred antwortet automatisch (`always_reply = True`).

### Befehle

| Befehl | Beschreibung |
|--------|-------------|
| `/clear` | Konversation leeren: Kontext zurücksetzen + alle erfassten Chat-Nachrichten löschen (Telegram-Grenzen: nur Nachrichten, die der Bot gesehen/gesendet hat und die jünger als 48 h sind) |

## Security

- **Whitelist (Pflicht):** Nur User-IDs in `TELEGRAM_ALLOWED_USERS` dürfen schreiben.
  Leer = niemand; die `*`-Wildcard wird **nicht** unterstützt.
  **User hinzufügen:** Lass ihn den Bot einmal anschreiben — der Versuch wird
  abgelehnt, aber seine numerische User-ID erscheint im AIfred-Log
  (`blocked message from <ID>`); trag diese ID in die Allowlist ein (Zahnrad des
  Telegram-Plugins → *Erlaubte User-IDs*).
- **Tier:** Eingehende Telegram-Nachrichten bekommen `max_tier=1` (TIER_COMMUNICATE). Kein Dateisystem-Zugriff, keine Code-Ausführung.
- **Credentials:** Bot-Token wird über den Credential Broker verwaltet, nie im LLM-Kontext.
- **Sanitization:** Alle ein-/ausgehenden Nachrichten werden durch die Security-Pipeline geschleust.
