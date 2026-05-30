# Telegram Channel Plugin

**Datei:** `aifred/plugins/channels/telegram_channel/`

Channel-Plugin, das einen Telegram-Bot an den Message Hub anbindet. Es empfängt
eingehende Chat-Nachrichten über die Telegram Bot API (Long Polling) und sendet
Antworten zurück. Autorisierte User können über das Tool `telegram_send` auch
proaktiv angeschrieben werden.

## Features

- **Long Polling:** Neue Nachrichten werden ohne Webhook-Server abgerufen
  (`drop_pending_updates=True` beim Start, der Rückstau wird also übersprungen).
- **User-Allowlist:** Nur Telegram-User-IDs in `TELEGRAM_ALLOWED_USERS` werden
  verarbeitet. Leere Allowlist = niemand, `*` = alle.
- **Immer antworten:** Der Channel antwortet auf jede akzeptierte Nachricht
  (`always_reply = True`).
- **`/clear`-Befehl:** Setzt die Konversation zurück, indem der Routing-Table-
  Eintrag des Chats gelöscht wird.
- **Klartext-Ausgabe:** Ausgehendes Markdown wird über `md_to_plain` geglättet —
  Telegrams Legacy-Markdown / MarkdownV2 ist beim Escapen fehleranfällig, daher
  werden Antworten als Klartext gesendet.
- **Auto-Chunking:** Nachrichten über Telegrams Limit von 4096 Zeichen werden an
  Zeilenumbrüchen aufgeteilt.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `telegram_send` | Eine Nachricht an einen Telegram-Chat senden (`message`, `chat_id`). Wird genutzt, wenn der User etwas via Telegram senden möchte. | COMMUNICATE |

`telegram_send` sendet mit `parse_mode="Markdown"` und chunkt lange Nachrichten.
Es gibt einen Fehler zurück, wenn der Bot nicht konfiguriert ist oder `chat_id`
fehlt.

## Konfiguration

Die Zugangsdaten werden über den Credential-Broker (UI-Plugin-Einstellungen)
verwaltet und in `.env` persistiert:

| Key | Beschreibung |
|-----|--------------|
| `TELEGRAM_BOT_TOKEN` | Bot-Token von [@BotFather](https://t.me/BotFather) (als Passwort gespeichert). |
| `TELEGRAM_ALLOWED_USERS` | Kommagetrennte Telegram-**User-IDs**. `*` = alle erlaubt, leer = niemand erlaubt. Der **erste Eintrag ist der Owner** und erhält erhöhte Rechte. |

Die eigene User-ID findest du, indem du [@userinfobot](https://t.me/userinfobot)
in Telegram anschreibst. Der Channel startet erst, wenn `enabled` gesetzt ist und
ein Bot-Token vorliegt (`is_configured`).

Detaillierte Setup-Anleitung: [telegram-setup.md](../telegram-setup.md)
