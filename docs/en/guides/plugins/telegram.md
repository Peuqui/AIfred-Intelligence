# Telegram Channel Plugin

> **Deutsche Version:** [telegram.md](../../../de/guides/plugins/telegram.md)

**File:** `aifred/plugins/channels/telegram_channel/`

Channel plugin that connects a Telegram bot to the Message Hub. It listens for
incoming chat messages via the Telegram Bot API (long polling) and sends replies
back. Authorized users can also be reached proactively through the `telegram_send`
tool.

## Features

- **Long polling:** New messages are fetched without running a webhook server.
  Polling starts with `drop_pending_updates=False`, so messages received while
  AIfred was down are caught up on start (Telegram buffers pending updates for
  up to 24 h).
- **User allowlist:** Only Telegram user IDs in `TELEGRAM_ALLOWED_USERS` are
  processed; blocked senders are logged (`blocked message from <ID>`). Empty
  allowlist = nobody; `*` is rejected (a world-open bot would let anyone use
  your GPUs).
- **Always reply:** The channel replies to every accepted message
  (`always_reply = True`, so there is no auto-reply toggle in the UI).
- **`/clear` command:** Resets the conversation by deleting the chat's routing
  table entry, and bulk-deletes the chat messages the bot tracked (incoming
  messages and its own sends). Telegram limits apply: only messages younger
  than 48 h, in groups only with delete permission.
- **Plain-text output:** Outbound Markdown is flattened via `md_to_plain` —
  Telegram's legacy Markdown / MarkdownV2 escaping is brittle, so replies are
  sent as plain text without `parse_mode`.
- **Auto-chunking:** Messages longer than Telegram's 4096-character limit are
  split, preferably at newline boundaries.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `telegram_send` | Send a message to a Telegram chat (`message`, optional `chat_id`, optional `attachment`). Used when the user asks to send something via Telegram. | COMMUNICATE |

`telegram_send` renders the text exactly like the reply path (plain text
without `parse_mode`, after `sanitize_outbound`) and chunks long messages.
Without `chat_id` it sends to the owner (first allowlist entry). `attachment`
is the URL of a file from the current conversation: images are sent as a
photo, other files as a document, with the message text as caption. The tool
returns an error if the bot token is missing, if neither `chat_id` nor an owner
is available, if `chat_id` is invalid, or if the target chat is not on the
allowlist while the call comes from an external channel instead of the
browser.

## Configuration

Credentials are managed through the credential broker (plugin settings page,
gear icon in the Plugins tab):

| Key | Description |
|-----|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) (password field, stored in `.env`). |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram **user IDs** (stored in the plugin's `settings.json`). Empty = allow none; `*` is not supported. The **first entry is the owner**: default target of `telegram_send` and eligible for owner elevation. |

Find your user ID by messaging [@userinfobot](https://t.me/userinfobot) on
Telegram. The channel only starts once `enabled` is set and a bot token is
present (`is_configured`).

Detailed setup guide: [telegram-setup.md](../telegram-setup.md)
