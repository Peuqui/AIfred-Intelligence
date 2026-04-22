# Discord Channel Plugin

**File:** `aifred/plugins/channels/discord_channel/`

Channel plugin for Discord bot integration with channel and DM support.

## Tools (for the LLM)

| Tool | Description | Tier |
|------|------------|------|
| `discord_send` | Send a message to a Discord channel or DM | COMMUNICATE |

## Features

- **WebSocket/Gateway:** Permanent connection via Discord Gateway API
- **Channel + DM:** Receives messages from server channels and direct messages
- **/clear command:** Slash command to reset the conversation
- **Auto-reply:** Automatic replies configurable per channel

## Configuration

- Discord bot token via `.env`
- Bot must be created in the Discord Developer Portal and invited to the server
