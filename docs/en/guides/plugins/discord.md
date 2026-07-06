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
- **Channel + DM:** Receives messages from server channels and direct messages. Every
  sender is checked against the mandatory allowlist first (see below); server channels
  are additionally filtered against the configured channel IDs (empty list = all channels)
- **`/clear` slash command:** Clears the conversation — always resets AIfred's context
  (route); in server channels it additionally purges all messages (requires the invoking
  user to have the `Manage Messages` permission; in DMs bots cannot bulk-delete)
- **Markdown:** Outbound text is passed through unchanged — Discord renders Markdown
  (bold/italic/code/links) natively

## Configuration

Credentials are managed via the credential broker (configurable in the AIfred settings UI
or via `.env`):

| Credential | Description |
|------------|-------------|
| `DISCORD_BOT_TOKEN` | Bot token from the Discord Developer Portal (secret) |
| `DISCORD_CHANNEL_IDS` | Comma-separated channel IDs to watch (empty = all channels) |
| `DISCORD_ALLOWED_USERS` | **Mandatory** sender allowlist: comma-separated numeric user IDs. Empty = nobody; the `*` wildcard is **not** supported. **Adding a user:** have them message the bot once — their user ID appears in the AIfred log (`blocked message from user <ID>`); add it via the plugin's gear icon → *Allowed user IDs* |

Setup:

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications)
   and copy its token.
2. Enable the **Message Content Intent** for the bot (required to read message text).
3. Invite the bot to your server with permission to read and send messages.
4. To watch specific channels, copy their IDs (enable Developer Mode in Discord, then
   right-click a channel → "Copy ID") and enter them comma-separated as `DISCORD_CHANNEL_IDS`.
   Leave empty to listen on all channels.
