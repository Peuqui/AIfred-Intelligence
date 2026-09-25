# Telegram Channel Plugin

> **English version:** [telegram.md](../../../en/guides/plugins/telegram.md)

**Datei:** `aifred/plugins/channels/telegram_channel/`

Channel-Plugin, das einen Telegram-Bot an den Message Hub anbindet. Es empfängt
eingehende Chat-Nachrichten über die Telegram Bot API (Long Polling) und sendet
Antworten zurück. Autorisierte User können über das Tool `telegram_send` auch
proaktiv angeschrieben werden.

## Features

- **Long Polling:** Neue Nachrichten werden ohne Webhook-Server abgerufen.
  Das Polling startet mit `drop_pending_updates=False`: Nachrichten, die während
  einer AIfred-Downtime eingingen, werden beim Start nachgeholt (Telegram puffert
  ausstehende Updates bis zu 24 h).
- **User-Allowlist:** Nur Telegram-User-IDs in `TELEGRAM_ALLOWED_USERS` werden
  verarbeitet; geblockte Absender landen im Log (`blocked message from <ID>`).
  Leere Allowlist = niemand; `*` wird abgelehnt (ein weltoffener Bot ließe jeden
  auf deine GPUs zugreifen).
- **Immer antworten:** Der Channel antwortet auf jede akzeptierte Nachricht
  (`always_reply = True`, daher gibt es in der UI keinen Auto-Reply-Schalter).
- **`/clear`-Befehl:** Setzt die Konversation zurück, indem der Routing-Table-
  Eintrag des Chats gelöscht wird, und löscht per Bulk-Delete die Chat-Nachrichten,
  die der Bot getrackt hat (eingehende und selbst gesendete). Es gelten Telegrams
  Grenzen: nur Nachrichten jünger als 48 h, in Gruppen nur mit Löschrecht.
- **Klartext-Ausgabe:** Ausgehendes Markdown wird über `md_to_plain` geglättet —
  Telegrams Legacy-Markdown / MarkdownV2 ist beim Escapen fehleranfällig, daher
  werden Antworten als Klartext ohne `parse_mode` gesendet.
- **Auto-Chunking:** Nachrichten über Telegrams Limit von 4096 Zeichen werden
  aufgeteilt, bevorzugt an Zeilenumbrüchen.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `telegram_send` | Eine Nachricht an einen Telegram-Chat senden (`message`, optional `chat_id`, optional `attachment`). Wird genutzt, wenn der User etwas via Telegram senden möchte. | COMMUNICATE |

`telegram_send` rendert den Text genau wie der Antwortpfad (Klartext ohne
`parse_mode`, nach `sanitize_outbound`) und chunkt lange Nachrichten. Ohne
`chat_id` geht die Nachricht an den Owner (erster Allowlist-Eintrag).
`attachment` ist die URL einer Datei aus der aktuellen Konversation: Bilder gehen
als Foto raus, andere Dateien als Dokument, der Nachrichtentext wird zur Caption.
Das Tool gibt einen Fehler zurück, wenn das Bot-Token fehlt, wenn weder `chat_id`
noch ein Owner vorhanden ist, wenn `chat_id` ungültig ist oder wenn der Ziel-Chat
nicht auf der Allowlist steht und der Aufruf aus einem externen Kanal statt aus
dem Browser kommt.

## Konfiguration

Die Zugangsdaten werden über den Credential-Broker verwaltet
(Plugin-Einstellungsseite, Zahnrad im Plugins-Tab):

| Key | Beschreibung |
|-----|--------------|
| `TELEGRAM_BOT_TOKEN` | Bot-Token von [@BotFather](https://t.me/BotFather) (Passwortfeld, gespeichert in `.env`). |
| `TELEGRAM_ALLOWED_USERS` | Kommagetrennte Telegram-**User-IDs** (gespeichert in der `settings.json` des Plugins). Leer = niemand erlaubt; `*` wird nicht unterstützt. Der **erste Eintrag ist der Owner**: Standardziel von `telegram_send` und berechtigt für die Owner-Rechteerhöhung. |

Die eigene User-ID findest du, indem du [@userinfobot](https://t.me/userinfobot)
in Telegram anschreibst. Der Channel startet erst, wenn `enabled` gesetzt ist und
ein Bot-Token vorliegt (`is_configured`).

Detaillierte Setup-Anleitung: [telegram-setup.md](../telegram-setup.md)
