# Plugin Development Guide

> **Deutsche Version:** [plugin-development.md](../../de/guides/plugin-development.md)

AIfred uses a unified plugin system. All plugins live in `aifred/plugins/`. System code (interfaces, registry, security) lives in `aifred/lib/`.

> **Security:** Full security architecture: [docs/en/architecture/security.md](../architecture/security.md).
> TL;DR: Every Tool needs a `tier`, credentials only via `broker.get()`.

---

## Plugin Types

### Tool Plugins (`plugins/tools/`)

Provide tools the LLM can call during conversations (web search, EPIM, sandbox, etc.).

**Required interface** (`ToolPlugin` protocol in `aifred/lib/plugin_base.py`):

```python
# aifred/plugins/tools/my_plugin/__init__.py
from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import PluginContext, load_tool_description
from ....lib.security import TIER_READONLY

@dataclass
class MyPlugin:
    name: str = "my_plugin"  # MUST equal the folder name
    # No display_name/description attributes: both live in i18n.json (see Plugin i18n)

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
            # Text lives in prompts/tools/my_tool.txt (missing/empty file → RuntimeError)
            description=load_tool_description(__file__, "my_tool"),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
            executor=_execute,
        )]

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        """System-prompt instructions, built from prompts/<de|en>/ fragments."""
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        """UI status text shown while this tool executes. Empty = not owned."""
        return ""

# Module-level instance (discovered by registry)
plugin = MyPlugin()
```

**Key points:**
- Plugin is a directory `aifred/plugins/tools/<name>/` — see [Plugin Directory Layout](#plugin-directory-layout)
- Must expose a module-level `plugin` attribute; `plugin.name` must equal the folder name (the registry logs a warning otherwise, and the plugin is invisible in the Plugin Manager)
- **Name and description come from the plugin's own `i18n.json`** (`plugin_display_name`, `plugin_description`, both DE and EN) — see [Plugin i18n](#plugin-i18n)
- **Every Tool MUST declare a `tier`** using named constants from `security.py`
- `PluginContext` provides: `agent_id`, `lang`, `session_id`, `state`, `user_query`, `max_tier`, `source`, `llm_history`, `metadata` (channel-specific, e.g. the Telegram `chat_id`)
- Tool executors are async functions returning strings (JSON for errors)
- **No hardcoded LLM text:** tool descriptions come from `prompts/tools/<tool>.txt` via `load_tool_description()`, prompt instructions from `prompts/<de|en>/` via `load_plugin_instructions()` — both files live inside the plugin directory
- `get_prompt_instructions()` receives `granted_tools` (the tool names enabled for the current agent, `None` = no whitelist) and returns only the instructions of those tools
- **Credentials via broker**, never via `os.environ` or `config.py`

## Plugin Directory Layout

Every plugin, tool and channel alike, is a Python package. The files the
existing plugins use (e.g. `tools/calculator/`, `tools/vision/`,
`channels/telegram_channel/`):

```
aifred/plugins/tools/my_plugin/
    __init__.py            # Plugin code + module-level instance (required)
    i18n.json              # plugin_display_name + plugin_description, DE and EN (required)
    settings.json          # Non-secret settings (optional, written by the settings modal)
    prompts/
        tools/
            my_tool.txt    # Tool description for the model, English only
        de/
            _intro.txt     # Plugin-wide instructions, shown once at least one tool is granted
            my_tool.txt    # Instructions shown only when my_tool is granted
            my_tool+other_tool.txt  # Shown only when BOTH tools are granted
        en/
            ...            # Same file names as de/
    tools.py, db.py, …     # Further modules as needed (e.g. epim, email_channel)
```

| File | Read by | Behaviour |
|------|---------|-----------|
| `prompts/tools/<tool>.txt` | `load_tool_description(__file__, "<tool>")` | Read fresh on every toolkit build; missing or empty file → `RuntimeError`, no fallback text. English only — the recipient is the model |
| `prompts/<de\|en>/*.txt` | `load_plugin_instructions(plugin, lang, granted_tools)` | File name = required tool(s), joined with `+` (AND); `_intro.txt` is prepended once at least one tool fragment applies. Transitional fallback: a flat `prompts/<de\|en>.txt`, gated plugin-wide |
| `i18n.json` | `plugin_display_name()`, `plugin_description()`, settings modal | See [Plugin i18n](#plugin-i18n) |
| `settings.json` | Channels: `BaseChannel.load_settings()` / `save_settings()`; tools: `load_plugin_settings(__file__)` / `save_plugin_settings(__file__, …)` | See [Credential Storage](#credential-storage-secrets-vs-settings) |

Larger plugins also split into sub-packages (e.g. `google_suite/` with
`calendar/`, `contacts/`, `drive/`, `tasks/`). Plugin-specific data files are
fine too (e.g. `bible/book_aliases/de.json`). Shared logic between plugins
belongs in `aifred/lib/`, never in an import from another plugin.

### Channel Plugins (`plugins/channels/`)

Receive and send messages via external services (email, Discord, Telegram, etc.).

Each channel plugin is a **self-contained directory**:

```
aifred/plugins/channels/my_channel/
    __init__.py     # Plugin code (BaseChannel subclass + module-level instance)
    i18n.json       # Name, description, credential labels (DE and EN, required)
    settings.json   # Auto-generated: non-secret settings (ports, hosts, etc.)
    prompts/tools/  # Descriptions of the channel's own tools (e.g. telegram_send.txt)
```

Code, texts and non-secret settings all live in the folder — nothing is
registered in the central UI translations (`aifred/lib/i18n/`). Only secret
fields (`is_secret=True`) are stored in `.env`.

**Required interface** (`BaseChannel` ABC in `aifred/lib/plugin_base.py`):

```python
from ....lib.plugin_base import BaseChannel, CredentialField
from ....lib.credential_broker import broker

class MyChannel(BaseChannel):
    # ── Identity (required) ──────────────────────────
    @property
    def name(self) -> str: return "my_channel"

    # Name and description: plugin_display_name / plugin_description in i18n.json

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
- Plugin lives in `aifred/plugins/channels/{name}_channel/` (`BaseChannel` derives the `settings.json` path from this folder name)
- Must expose a module-level `BaseChannel` instance (the registry picks up every `BaseChannel` instance in the module)
- `listener_loop()` must run indefinitely and handle `asyncio.CancelledError`
- `build_context()` should load prompts from `prompts/` directory
- **Credentials via `broker.get()`**, never via `os.environ` or `config.py` globals
- `broker` resolves `(service, key)` to the env var `<SERVICE>_<KEY>`; add an entry to `credential_broker.py: _CREDENTIAL_MAP` only when the env var name differs from that
- Channel tools MUST use named tier constants (typically `TIER_COMMUNICATE`) and load their description via `load_tool_description(__file__, "<tool>")`
- Optional properties: `always_reply` (default `False`; `True` hides the Auto-Reply toggle) and `has_allowlist` (default `True`; `False` hides the allowlist row, e.g. FreeEcho.2)
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

In `send_reply()`, call `self.format_outbound(outbound.text)` and pass the appropriate field(s) to your transport (SMTP, REST API, etc.). Concrete overrides: see `email_channel/__init__.py` and `telegram_channel/__init__.py`; `discord_channel` uses the default passthrough.

**Why the dict return type?** Different channels need different sidecars: email needs both `text` and `html` for `multipart/alternative`, Telegram historically supported a `parse_mode` flag, future channels may need other hints. A dict keeps the interface flexible without a parameter explosion.

## Credential Storage: Secrets vs Settings

`CredentialField` has an `is_secret` flag that controls where values are stored:

| `is_secret` | Storage | Example |
|-------------|---------|---------|
| `True` | `.env` file + `os.environ` | Passwords, API keys, bot tokens |
| `False` (default) | Plugin's `settings.json` | Ports, hosts, paths, engine selections |

`is_password=True` automatically sets `is_secret=True`.

**At boot (channels):** when the registry discovers a channel it calls `load_settings_to_env()`, which copies the non-empty values of the channel's `settings.json` into `os.environ` (they take priority over `.env`), so `credential_broker` and `is_configured()` work seamlessly.

**Tool plugins** read their `settings.json` directly via `load_plugin_settings(__file__)` from `aifred/lib/plugin_base.py` (counterpart: `save_plugin_settings(__file__, settings)`).

## Plugin i18n

Every plugin, tool and channel alike, carries its own user-facing texts in its own `i18n.json`. Two keys are **required**, each in DE and EN:

| Key | Used by |
|-----|---------|
| `plugin_display_name` | Plugin list, tool pill groups, settings modal title, Message Hub channel label, sub-agent legend |
| `plugin_description` | Lightbulb in the plugin list, sub-agent legend (`delegate_task` → `agent`) |

They are read **only** through `plugin_display_name(plugin, lang)` and `plugin_description(plugin, lang)` in `aifred/lib/plugin_base.py`. A missing file, key or language raises a `RuntimeError` (no fallback text); `tests/test_plugin_i18n.py` checks every plugin package. Debug messages use `lang="en"`.

Credential labels and tooltips live in the same file:

```json
{
  "plugin_display_name": {
    "de": "Mein Plugin",
    "en": "My Plugin"
  },
  "plugin_description": {
    "de": "Was das Plugin kann, in ein bis zwei Sätzen.",
    "en": "What the plugin does, in one or two sentences."
  },
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

The Settings modal resolves credential labels, tooltips and dropdown-option labels **only** from the plugin's own `i18n.json` (tool and channel plugins alike). A key the file does not contain is shown as written; a missing tooltip stays empty. There is no fallback to the central UI translations — those live in the package `aifred/lib/i18n/` (`de.json`, `en.json`, `TranslationManager` in `__init__.py`) and are not meant for plugin texts.

Channel plugins additionally have `translate(key, lang="de")` (see below): it returns the entry for `lang`, otherwise the `de` entry, otherwise the key itself.

## BaseChannel Helper Methods

Every channel plugin inherits these from `BaseChannel`:

| Method | Description |
|--------|-------------|
| `load_settings()` | Read plugin's `settings.json` |
| `save_settings(dict)` | Write plugin's `settings.json` |
| `load_settings_to_env()` | Copy non-empty `settings.json` values into `os.environ` (called by the registry at discovery) |
| `load_i18n()` | Read plugin's `i18n.json` |
| `translate(key, lang="de")` | Translate a key using plugin's `i18n.json` (default language `de`, falls back to `de`, then to the key) |
| `format_outbound(text)` | Convert agent Markdown for the channel (default: passthrough) |
| `channel_log(msg, level="info")` | Log to log file + stderr (journalctl), mirrored into the browser debug console inside a `session_scope` |

Tool plugins use the module-level helpers from `aifred/lib/plugin_base.py` instead: `load_plugin_settings()`, `save_plugin_settings()`, `load_plugin_i18n()`, `load_tool_description()`, `load_plugin_instructions()`.

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
- State is stored in AIfred's `data/settings.json` (`channel_toggles`)

**Tool plugins** (Calculator, Workspace, EPIM, etc.) have toggles that apply **on OK**:
- Toggling a tool plugin in the UI only changes the visual state
- Clicking OK applies all changes at once by moving plugin directories to/from `plugins/disabled/`
- The plugin directory is physically moved and prefixed with its type (`disabled/tool_<name>`, `disabled/channel_<name>`) — what's in `tools/` or `channels/` is active, what's in `disabled/` is not

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
│   │                          # + helpers (load_tool_description, load_plugin_instructions, plugin i18n/settings)
│   ├── plugin_registry.py     # Discovery, enable/disable, list
│   ├── security.py            # Tier constants, filter, sanitize, audit
│   ├── credential_broker.py   # Centralized credential management
│   ├── debug_bus.py           # debug(), session_scope, flush
│   ├── function_calling.py    # Tool, ToolKit classes
│   ├── markdown_render.py     # md_to_html, md_to_plain (outbound formatting)
│   └── i18n/                  # Central UI translations (de.json, en.json, __init__.py) — not for plugin texts
└── plugins/
    ├── channels/              # every channel: __init__.py, i18n.json, settings.json, prompts/tools/
    │   ├── discord_channel/   # Discord bot (+ discord_send)
    │   ├── email_channel/     # IMAP IDLE + SMTP (+ email tools; client.py, config.py, tools.py)
    │   ├── freeecho2_channel/ # FreeEcho.2 speakers via WebSocket (+ freeecho2_announce)
    │   └── telegram_channel/  # Telegram bot (+ telegram_send)
    ├── tools/                 # every tool: __init__.py, i18n.json, prompts/ (settings.json where needed)
    │   ├── audio_player/      # Local audio files and internet streams
    │   ├── bible/             # Bible passage lookup and search
    │   ├── calculator/        # calculate
    │   ├── epim/              # EPIM appointments, contacts, notes, tasks (db.py, tools.py)
    │   ├── google_suite/      # Google Calendar, Contacts, Tasks, Drive (sub-packages per service)
    │   ├── judaica/           # Jewish source corpus lookup and search
    │   ├── narrator/          # Narrates text files into an audio file
    │   ├── research/          # web_search, web_fetch
    │   ├── sandbox/           # execute_code, render_html
    │   ├── scheduler_tool/    # Scheduled tasks and reminders
    │   ├── subagent/          # delegate_task to sub-agents
    │   ├── system_monitor/    # system_status (CPU, RAM, VRAM, disks, …)
    │   ├── translator/        # DeepL translate, translate_file
    │   ├── vision/            # Vigilantia: snapshots, scene descriptions, watches
    │   └── workspace/         # Files in the working directory + ChromaDB document index
    └── disabled/              # Disabled plugins (moved here by the UI as tool_<name> / channel_<name>)
```
