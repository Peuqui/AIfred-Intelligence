# Plugin Development Guide

AIfred uses a unified plugin system. All plugins live in `aifred/plugins/`. System code (interfaces, registry, security) lives in `aifred/lib/`.

> **Security:** Full security architecture: [docs/en/architecture/security.md](../architecture/security.md).
> TL;DR: Every Tool needs a `tier`, credentials only via `broker.get()`.

---

## Plugin Types

### Tool Plugins (`plugins/tools/`)

Provide tools the LLM can call during conversations (web search, EPIM, sandbox, etc.).

**Required interface** (`ToolPlugin` protocol in `aifred/lib/plugin_base.py`):

```python
from dataclasses import dataclass
from aifred.lib.function_calling import Tool
from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import TIER_READONLY

@dataclass
class MyPlugin:
    name: str = "my_plugin"
    display_name: str = "My Plugin"

    def is_available(self) -> bool:
        """Check if this plugin can run (config, services, etc.)."""
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        """Return Tool instances for the LLM."""
        async def _execute(query: str) -> str:
            return f"Result for {query}"

        return [Tool(
            name="my_tool",
            tier=TIER_READONLY,  # REQUIRED: declare security tier
            description="What this tool does",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
            executor=_execute,
        )]

    def get_prompt_instructions(self, lang: str) -> str:
        """Prompt text injected into the LLM system prompt. Empty = none."""
        return ""

    def get_ui_status(self, tool_name: str, tool_args: dict, lang: str) -> str:
        """UI status text shown while this tool executes. Empty = not owned."""
        return ""

# Module-level instance (discovered by registry)
plugin = MyPlugin()
```

**Key points:**
- File goes in `aifred/plugins/tools/`
- Must expose a module-level `plugin` attribute
- **Every Tool MUST declare a `tier`** using named constants from `security.py`
- `PluginContext` provides: `agent_id`, `lang`, `session_id`, `state`, `user_query`, `max_tier`, `source`
- Tool executors are async functions returning strings (JSON for errors)
- Prompt instructions are loaded from `prompts/` files (not hardcoded)
- **Credentials via broker**, never via `os.environ` or `config.py`

### Channel Plugins (`plugins/channels/`)

Receive and send messages via external services (email, Discord, Telegram, etc.).

Each channel plugin is a **self-contained directory**:

```
aifred/plugins/channels/my_channel/
    __init__.py     # Plugin code (BaseChannel subclass + module-level instance)
    i18n.json       # Translations for credential labels (min. DE/EN)
    settings.json   # Auto-generated: non-secret settings (ports, hosts, etc.)
```

**Delete the folder = plugin is completely gone.** No leftover entries in `.env` or central `i18n.py`.

**Required interface** (`BaseChannel` ABC in `aifred/lib/plugin_base.py`):

```python
from ....lib.plugin_base import BaseChannel, CredentialField
from ....lib.credential_broker import broker

class MyChannel(BaseChannel):
    # ── Identity (required) ──────────────────────────
    @property
    def name(self) -> str: return "my_channel"

    @property
    def display_name(self) -> str: return "My Channel"

    @property
    def icon(self) -> str: return "message-circle"  # Lucide icon name

    # ── Credentials (required) ───────────────────────
    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            # Secrets → .env (is_password=True implies is_secret=True)
            CredentialField(env_key="MY_TOKEN", label_key="my_token_label", is_password=True),
            # Config → plugin settings.json (is_secret=False, the default)
            CredentialField(env_key="MY_PORT", label_key="my_port_label", placeholder="8080"),
        ]

    def is_configured(self) -> bool:
        return broker.is_set("my_channel", "token")

    def apply_credentials(self, values: dict[str, str]) -> None:
        broker.set_runtime("my_channel", "enabled", "true")
        broker.set_runtime("my_channel", "token", values.get("MY_TOKEN", ""))

    # ── Listener (required) ──────────────────────────
    async def listener_loop(self) -> None:
        """Long-running loop. Handle CancelledError for clean shutdown."""
        token = broker.get("my_channel", "token")  # Get credential at use time
        ...

    # ── Reply (required) ─────────────────────────────
    async def send_reply(self, outbound, original) -> None:
        """Send a reply to an incoming message.

        Important: agents produce Markdown. Run the text through
        `self.format_outbound(outbound.text)` before sending so the
        recipient sees a format their client can render — see below.
        """
        rendered = self.format_outbound(outbound.text)
        # ... use rendered["text"] (and rendered["html"] for email) ...

    # ── Context (required) ───────────────────────────
    def build_context(self, message) -> str:
        """Build LLM prompt context. Use prompts/ files, not hardcoded text."""
        from ....lib.prompt_loader import load_prompt
        return load_prompt("shared/channel_my_channel", sender=message.sender, text=message.text)

    # ── Outbound formatting (optional override) ──────
    def format_outbound(self, text: str) -> dict[str, str]:
        """Convert agent Markdown into channel-specific format(s).

        Default behaviour (passthrough): return ``{"text": text}``.
        Override for channels whose recipients cannot render Markdown.
        Use the shared converters in ``aifred/lib/markdown_render.py``:
            * ``md_to_html(text)`` — full HTML (e.g. for email's text/html part)
            * ``md_to_plain(text)`` — Markdown markers stripped

        Conventions for the returned dict:
            * ``{"text": ...}``               — plain/rendered representation
            * ``{"text": ..., "html": ...}``  — email-style multipart
            * ``{"text": ..., "parse_mode": ...}`` — Telegram-style hint
        """
        # Discord example (markdown is native — no conversion):
        return {"text": text}

        # Email example (multipart/alternative):
        # from ....lib.markdown_render import md_to_html, md_to_plain
        # return {"text": md_to_plain(text), "html": md_to_html(text)}

        # Telegram/EPIM example (plain text, robust):
        # from ....lib.markdown_render import md_to_plain
        # return {"text": md_to_plain(text)}

    # ── Optional ─────────────────────────────────────
    def build_reply_metadata(self, message) -> dict:
        return {}

    def get_tools(self, ctx) -> list:
        """Optional: Tools this channel provides (e.g. discord_send)."""
        return []

# Module-level instance (discovered by registry)
MyChannel_instance = MyChannel()
```

**Key points:**
- Plugin lives in `aifred/plugins/channels/{name}_channel/`
- Must expose a module-level `BaseChannel` instance
- `listener_loop()` must run indefinitely and handle `asyncio.CancelledError`
- `build_context()` should load prompts from `prompts/` directory
- **Credentials via `broker.get()`**, never via `os.environ` or `config.py` globals
- Add credential mapping to `credential_broker.py: _CREDENTIAL_MAP`
- Channel tools MUST use named tier constants (typically `TIER_COMMUNICATE`)
- **Outbound formatting**: pipe `outbound.text` through `self.format_outbound()` in `send_reply()`. Agents produce Markdown — only Discord renders it natively. Email/Telegram/EPIM/etc. need conversion via `md_to_html` / `md_to_plain` from `aifred/lib/markdown_render.py`.

## Outbound Markdown Conversion

Agents in AIfred produce Markdown by default — fine for the browser UI and Discord (renders Markdown natively), but unreadable in email clients (`**bold**` shows as raw text), Telegram, EPIM and similar destinations.

The `BaseChannel.format_outbound(text) -> dict[str, str]` hook is the single seam where each channel decides how to render the agent's Markdown for its recipient. Defaults to passthrough (`{"text": text}`). Override when your channel needs conversion.

**Shared converters** (`aifred/lib/markdown_render.py`):

| Function | Purpose |
|----------|---------|
| `md_to_html(text)` | Full HTML rendering. Used by email for the `text/html` MIME part. Powered by mistune (CommonMark + GFM tables/strikethrough/task-lists). HTML embedded inside Markdown is auto-escaped (XSS-safe). |
| `md_to_plain(text)` | Strip Markdown markers, preserve content + structure (lists become `•`, links become `text (url)`, headings keep their text without `#`, fenced code keeps body without backticks). Tables are kept as-is — they remain legible in monospace. |

**Recipes by channel type:**

```python
# Channel that renders Markdown natively (Discord):
def format_outbound(self, text: str) -> dict[str, str]:
    return {"text": text}  # passthrough — default works too

# Channel that supports HTML alongside plain text (Email):
def format_outbound(self, text: str) -> dict[str, str]:
    from ....lib.markdown_render import md_to_html, md_to_plain
    return {"text": md_to_plain(text), "html": md_to_html(text)}

# Channel that wants robust plain text (Telegram, EPIM):
def format_outbound(self, text: str) -> dict[str, str]:
    from ....lib.markdown_render import md_to_plain
    return {"text": md_to_plain(text)}
```

In `send_reply()`, call `self.format_outbound(outbound.text)` and pass the appropriate field(s) to your transport (SMTP, REST API, etc.). Concrete examples: see `email_channel/__init__.py`, `telegram_channel/__init__.py`, `discord_channel/__init__.py`.

**Why the dict return type?** Different channels need different sidecars: email needs both `text` and `html` for `multipart/alternative`, Telegram historically supported a `parse_mode` flag, future channels may need other hints. A dict keeps the interface flexible without a parameter explosion.

## Credential Storage: Secrets vs Settings

`CredentialField` has an `is_secret` flag that controls where values are stored:

| `is_secret` | Storage | Example |
|-------------|---------|---------|
| `True` | `.env` file + `os.environ` | Passwords, API keys, bot tokens |
| `False` (default) | Plugin's `settings.json` | Ports, hosts, paths, engine selections |

`is_password=True` automatically sets `is_secret=True`.

**At boot:** Plugin's `settings.json` values are loaded into `os.environ` by the registry, so `credential_broker` and `is_configured()` work seamlessly.

**Migration:** When a plugin is first loaded and has no `settings.json`, existing `.env` values for non-secret fields are automatically migrated.

## Plugin i18n

Each channel plugin has its own `i18n.json` for credential labels and tooltips:

```json
{
  "my_token_label": {
    "de": "API Token",
    "en": "API Token"
  },
  "my_port_label": {
    "de": "Server Port",
    "en": "Server Port"
  },
  "my_port_label_tooltip": {
    "de": "Port auf dem der Server lauscht.",
    "en": "Port the server listens on."
  }
}
```

**Convention:** Tooltip keys are `{label_key}_tooltip`.

The plugin's `translate(key, lang)` method looks up translations from `i18n.json`. The Settings modal tries plugin i18n first, then falls back to central `aifred/lib/i18n.py`.

## BaseChannel Helper Methods

Every channel plugin inherits these from `BaseChannel`:

| Method | Description |
|--------|-------------|
| `load_settings()` | Read plugin's `settings.json` |
| `save_settings(dict)` | Write plugin's `settings.json` |
| `get_setting(key)` | Get a single setting value |
| `set_setting(key, value)` | Set a single setting value |
| `load_i18n()` | Read plugin's `i18n.json` |
| `translate(key, lang)` | Translate a key using plugin's `i18n.json` |
| `channel_log(msg, level)` | Log to debug-log + stderr (journalctl) |

## Debug Messages

Use the centralized Debug Bus. Works in all contexts (browser, hub, standalone):

```python
from aifred.lib.debug_bus import debug

debug("Searching...")
debug("Found 5 results")
```

Messages automatically go to:
- Log file (`data/logs/aifred_debug.log`) — always
- Browser debug console — if browser session is active
- Session file — if `session_scope` is active (Hub path)

## Enabling / Disabling

Plugins are managed via the **Plugin Manager** (Settings > Plugin Manager > gear icon).

### Channel Plugins vs Tool Plugins — Different Toggle Behavior

**Channel plugins** (Email, Discord) have toggles that apply **immediately**:
- The main toggle enables/disables the entire channel (tools + listener)
- Sub-toggles control the background listener (Monitor) and Auto-Reply
- Changes take effect instantly because they start/stop running background workers
- State is stored in `settings.json` (`channel_toggles`)

**Tool plugins** (Calculator, Documents, EPIM, etc.) have toggles that apply **on OK**:
- Toggling a tool plugin in the UI only changes the visual state
- Clicking OK applies all changes at once by moving files to/from `plugins/disabled/`
- The plugin file is physically moved — what's in the folder is active, what's in `disabled/` is not

### Channel Sub-Toggles

Channels with `always_reply = False` (e.g. Email) show additional sub-toggles:
- **Monitor**: Start/stop the background listener (IMAP IDLE, etc.)
- **Auto-Reply**: Automatically send LLM responses back via the channel

Channels with `always_reply = True` (e.g. Discord) only show the main toggle.

## File Structure

```
aifred/
├── lib/
│   ├── plugin_base.py         # Interfaces (BaseChannel, ToolPlugin, PluginContext, CredentialField)
│   ├── plugin_registry.py     # Discovery, enable/disable, list, migration
│   ├── security.py            # Tier constants, filter, sanitize, audit
│   ├── credential_broker.py   # Centralized credential management
│   ├── debug_bus.py           # debug(), session_scope, flush
│   └── function_calling.py    # Tool, ToolKit classes
└── plugins/
    ├── channels/
    │   ├── email_channel/     # E-Mail (IMAP/SMTP + email tools)
    │   │   ├── __init__.py
    │   │   └── i18n.json
    │   ├── discord_channel/   # Discord (bot + discord_send tool)
    │   │   ├── __init__.py
    │   │   └── i18n.json
    │   ├── telegram_channel/  # Telegram (bot + telegram_send tool)
    │   │   ├── __init__.py
    │   │   └── i18n.json
    │   └── freeecho2_channel/ # FreeEcho.2 voice terminal (WebSocket)
    │       ├── __init__.py
    │       └── i18n.json
    ├── tools/
    │   ├── calculator.py      # calculate (tier 0)
    │   ├── epim/              # EPIM database CRUD (tier 0/2/3)
    │   ├── research.py        # web_search, web_fetch (tier 0)
    │   └── sandbox.py         # execute_code (tier 2)
    └── disabled/              # Disabled tool plugins (moved here by UI)
```
