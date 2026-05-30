# Discord Channel Plugin

**File:** `aifred/plugins/channels/discord_channel/`

Channel plugin that connects AIfred to Discord as a bot (via `discord.py`). It listens
for messages in configured server channels and direct messages, dispatches them through
the Message Hub pipeline, and posts the reply back. The bot always replies to messages it
receives (`always_reply = True`).

## Tools (for the LLM)

| Tool | Description | Tier |
|------|------------|------|
| `discord_send` | Send a message to a Discord channel or DM | COMMUNICATE |

### `discord_send` parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `message` | yes | The message text to send |
| `channel_id` | no | Discord channel ID; falls back to the first configured channel if empty |

Messages longer than Discord's 2000-character limit are automatically split into chunks.

## Features

- **WebSocket/Gateway:** Permanent connection via the Discord Gateway API
- **Channel + DM:** Receives messages from server channels and direct messages. DMs are
  always accepted; server channels are filtered against the configured channel IDs (empty
  list = all channels)
- **`/clear` slash command:** Purges all messages in the current channel. Only works in
  server channels and requires the invoking user to have the `Manage Messages` permission
- **Markdown:** Outbound text is passed through unchanged — Discord renders Markdown
  (bold/italic/code/links) natively

## Configuration

Credentials are managed via the credential broker (configurable in the AIfred settings UI
or via `.env`):

| Credential | Description |
|------------|-------------|
| `DISCORD_BOT_TOKEN` | Bot token from the Discord Developer Portal (secret) |
| `DISCORD_CHANNEL_IDS` | Comma-separated channel IDs to watch (empty = all channels) |

Setup:

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications)
   and copy its token.
2. Enable the **Message Content Intent** for the bot (required to read message text).
3. Invite the bot to your server with permission to read and send messages.
4. To watch specific channels, copy their IDs (enable Developer Mode in Discord, then
   right-click a channel → "Copy ID") and enter them comma-separated as `DISCORD_CHANNEL_IDS`.
   Leave empty to listen on all channels.
