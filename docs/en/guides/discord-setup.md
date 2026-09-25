# Discord Bot Setup

> **Deutsche Version:** [discord-setup.md](../../de/guides/discord-setup.md)

## 1. Create the bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. **Bot** page: **Reset Token** → copy the token. Enable the **Message Content Intent**
3. Disable **Public Bot** — only you should be able to add it to servers
4. **OAuth2 → URL Generator:** scope `bot`, permissions *Send Messages*,
   *Read Message History*, *View Channels* (plus *Manage Messages* if `/clear`
   should also purge server channels)
5. Open the generated URL → pick your server → authorize

## 2. Channel and user IDs

1. Enable Developer Mode in Discord: *Settings → Advanced → Developer Mode*
2. Create a private channel (e.g. `#aifred`) and add the bot
3. Right-click the channel → **Copy Channel ID**
4. Right-click your own name → **Copy User ID**

## 3. Configure AIfred

In the web UI: **Plugin Manager → Discord → gear icon** — enter bot token,
channel ID(s) and allowed user IDs → *Save & Activate*. Or in `.env`:

```env
DISCORD_ENABLED=true
DISCORD_BOT_TOKEN=<bot token>
DISCORD_CHANNEL_IDS=<channel id>          # comma-separated; empty = all channels
DISCORD_ALLOWED_USERS=<your user id>      # mandatory, comma-separated
```

Restart AIfred after editing `.env` by hand.

## 4. Test

Write in the channel or send the bot a direct message. AIfred replies to every
accepted message (`always_reply = True`).

| Command | Description |
|---|---|
| `/clear` | Resets AIfred's context for this conversation; in server channels it also deletes the messages (needs *Manage Messages*; bots cannot bulk-delete in DMs) |

## Security

- **Allowlist (mandatory):** only user IDs in `DISCORD_ALLOWED_USERS` are
  processed — in server channels and DMs alike. Empty = nobody; `*` is **not**
  supported. **Adding a user:** have them message the bot once; their ID appears
  in the AIfred log (`blocked message from user <ID>`); add it via the plugin's
  gear icon → *Allowed user IDs*.
- **Tier:** Discord messages run at `TIER_COMMUNICATE` by default — no file
  system access, no code execution. Raising it is only possible in the Plugin
  Manager, never by a sender.
- **Credentials** go through the credential broker and never reach the LLM.

Plugin reference: [Discord plugin](plugins/discord.md).
