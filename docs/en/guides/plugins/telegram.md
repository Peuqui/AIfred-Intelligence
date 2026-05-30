# Telegram Channel Plugin

**File:** `aifred/plugins/channels/telegram_channel/`

Channel plugin that connects a Telegram bot to the Message Hub. It listens for
incoming chat messages via the Telegram Bot API (long polling) and sends replies
back. Authorized users can also be reached proactively through the `telegram_send`
tool.

## Features

- **Long polling:** New messages are fetched without running a webhook server
  (`drop_pending_updates=True` on start, so the backlog is skipped).
- **User allowlist:** Only Telegram user IDs in `TELEGRAM_ALLOWED_USERS` are
  processed. Empty allowlist = nobody, `*` = everybody.
- **Always reply:** The channel replies to every accepted message
  (`always_reply = True`).
- **`/clear` command:** Resets the conversation by deleting the chat's routing
  table entry.
- **Plain-text output:** Outbound Markdown is flattened via `md_to_plain` —
  Telegram's legacy Markdown / MarkdownV2 escaping is brittle, so replies are
  sent as plain text.
- **Auto-chunking:** Messages longer than Telegram's 4096-character limit are
  split at newline boundaries.

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `telegram_send` | Send a message to a Telegram chat (`message`, `chat_id`). Used when the user asks to send something via Telegram. | COMMUNICATE |

`telegram_send` sends with `parse_mode="Markdown"` and chunks long messages.
It returns an error if the bot is not configured or `chat_id` is missing.

## Configuration

Credentials are managed through the credential broker (UI plugin settings) and
persisted to `.env`:

| Key | Description |
|-----|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) (stored as password). |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram **user IDs**. `*` = allow all, empty = allow none. The **first entry is the owner** and gets elevated permissions. |

Find your user ID by messaging [@userinfobot](https://t.me/userinfobot) on
Telegram. The channel only starts once `enabled` is set and a bot token is
present (`is_configured`).

Detailed setup guide: [telegram-setup.md](../telegram-setup.md)
