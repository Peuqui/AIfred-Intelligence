# Leitfaden zur Plugin-Entwicklung

> **English version:** [plugin-development.md](../../en/guides/plugin-development.md)

AIfred nutzt ein einheitliches Plugin-System. Alle Plugins liegen in `aifred/plugins/`. System-Code (Interfaces, Registry, Security) liegt in `aifred/lib/`.

> **Sicherheit:** Vollständige Sicherheitsarchitektur: [docs/de/architecture/security.md](../architecture/security.md).
> TL;DR: Jedes Tool braucht einen `tier`, Credentials nur über `broker.get()`.

---

## Plugin-Typen

### Tool-Plugins (`plugins/tools/`)

Stellen Tools bereit, die das LLM während einer Unterhaltung aufrufen kann (Websuche, EPIM, Sandbox usw.).

**Pflicht-Interface** (`ToolPlugin`-Protocol in `aifred/lib/plugin_base.py`):

```python
from dataclasses import dataclass
from aifred.lib.function_calling import Tool
from aifred.lib.plugin_base import PluginContext
from aifred.lib.security import TIER_READONLY

@dataclass
class MyPlugin:
    name: str = "my_plugin"  # MUSS dem Ordnernamen entsprechen
    # Keine display_name/description-Attribute: beide stehen in i18n.json (siehe Plugin-i18n)

    def is_available(self) -> bool:
        """Prüft, ob dieses Plugin laufen kann (Config, Dienste usw.)."""
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        """Gibt Tool-Instanzen für das LLM zurück."""
        async def _execute(query: str) -> str:
            return f"Result for {query}"

        return [Tool(
            name="my_tool",
            tier=TIER_READONLY,  # PFLICHT: Security-Tier deklarieren
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
        """Prompt-Text, der in den System-Prompt des LLM eingefügt wird. Leer = keiner."""
        return ""

    def get_ui_status(self, tool_name: str, tool_args: dict, lang: str) -> str:
        """UI-Statustext, der während der Ausführung dieses Tools angezeigt wird. Leer = nicht zuständig."""
        return ""

# Instanz auf Modulebene (wird von der Registry gefunden)
plugin = MyPlugin()
```

**Wichtige Punkte:**
- Ein Plugin ist ein Verzeichnis `aifred/plugins/tools/<name>/` mit `__init__.py` und `i18n.json`
- Muss ein Attribut `plugin` auf Modulebene bereitstellen
- **Name und Beschreibung kommen aus der eigenen `i18n.json` des Plugins** (`plugin_display_name`, `plugin_description`, jeweils DE und EN) — siehe [Plugin-i18n](#plugin-i18n)
- **Jedes Tool MUSS einen `tier` deklarieren**, mit den benannten Konstanten aus `security.py`
- `PluginContext` stellt bereit: `agent_id`, `lang`, `session_id`, `state`, `user_query`, `max_tier`, `source`
- Tool-Executors sind async-Funktionen, die Strings zurückgeben (JSON bei Fehlern)
- Prompt-Anweisungen werden aus Dateien in `prompts/` geladen (nicht hardcodiert)
- **Credentials über den Broker**, niemals über `os.environ` oder `config.py`

### Channel-Plugins (`plugins/channels/`)

Empfangen und senden Nachrichten über externe Dienste (E-Mail, Discord, Telegram usw.).

Jedes Channel-Plugin ist ein **in sich geschlossenes Verzeichnis**:

```
aifred/plugins/channels/my_channel/
    __init__.py     # Plugin-Code (BaseChannel-Unterklasse + Instanz auf Modulebene)
    i18n.json       # Name, Beschreibung, Credential-Labels (DE und EN, Pflicht)
    settings.json   # Automatisch erzeugt: nicht geheime Einstellungen (Ports, Hosts usw.)
```

**Ordner löschen = Plugin ist komplett weg.** Keine Überbleibsel in `.env` oder in der zentralen `i18n.py`.

**Pflicht-Interface** (`BaseChannel`-ABC in `aifred/lib/plugin_base.py`):

```python
from ....lib.plugin_base import BaseChannel, CredentialField
from ....lib.credential_broker import broker

class MyChannel(BaseChannel):
    # ── Identität (Pflicht) ──────────────────────────
    @property
    def name(self) -> str: return "my_channel"

    # Name und Beschreibung: plugin_display_name / plugin_description in i18n.json

    @property
    def icon(self) -> str: return "message-circle"  # Name des Lucide-Icons

    # ── Credentials (Pflicht) ───────────────────────
    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            # Secrets → .env (is_password=True impliziert is_secret=True)
            CredentialField(env_key="MY_TOKEN", label_key="my_token_label", is_password=True),
            # Config → settings.json des Plugins (is_secret=False, der Default)
            CredentialField(env_key="MY_PORT", label_key="my_port_label", placeholder="8080"),
        ]

    def is_configured(self) -> bool:
        return broker.is_set("my_channel", "token")

    def apply_credentials(self, values: dict[str, str]) -> None:
        broker.set_runtime("my_channel", "enabled", "true")
        broker.set_runtime("my_channel", "token", values.get("MY_TOKEN", ""))

    # ── Listener (Pflicht) ──────────────────────────
    async def listener_loop(self) -> None:
        """Dauerhaft laufende Schleife. CancelledError für sauberes Beenden behandeln."""
        token = broker.get("my_channel", "token")  # Credential erst bei Verwendung holen
        ...

    # ── Antwort (Pflicht) ─────────────────────────────
    async def send_reply(self, outbound, original) -> None:
        """Sendet eine Antwort auf eine eingehende Nachricht.

        Wichtig: Agenten erzeugen Markdown. Den Text vor dem Senden durch
        `self.format_outbound(outbound.text)` schicken, damit der
        Empfänger ein Format sieht, das sein Client darstellen kann — siehe unten.
        """
        rendered = self.format_outbound(outbound.text)
        # ... rendered["text"] verwenden (und rendered["html"] bei E-Mail) ...

    # ── Kontext (Pflicht) ───────────────────────────
    def build_context(self, message) -> str:
        """Baut den LLM-Prompt-Kontext. Dateien aus prompts/ verwenden, keinen hardcodierten Text."""
        from ....lib.prompt_loader import load_prompt
        return load_prompt("shared/channel_my_channel", sender=message.sender, text=message.text)

    # ── Ausgangsformatierung (optional überschreibbar) ──────
    def format_outbound(self, text: str) -> dict[str, str]:
        """Wandelt Agenten-Markdown in kanalspezifische(s) Format(e) um.

        Default-Verhalten (Passthrough): gibt ``{"text": text}`` zurück.
        Überschreiben für Kanäle, deren Empfänger kein Markdown darstellen können.
        Die gemeinsamen Konverter in ``aifred/lib/markdown_render.py`` verwenden:
            * ``md_to_html(text)`` — vollständiges HTML (z. B. für den text/html-Teil einer E-Mail)
            * ``md_to_plain(text)`` — Markdown-Markierungen entfernt

        Konventionen für das zurückgegebene Dict:
            * ``{"text": ...}``               — Klartext-/gerenderte Darstellung
            * ``{"text": ..., "html": ...}``  — Multipart im E-Mail-Stil
            * ``{"text": ..., "parse_mode": ...}`` — Hinweis im Telegram-Stil
        """
        # Discord-Beispiel (Markdown ist nativ — keine Umwandlung):
        return {"text": text}

        # E-Mail-Beispiel (multipart/alternative):
        # from ....lib.markdown_render import md_to_html, md_to_plain
        # return {"text": md_to_plain(text), "html": md_to_html(text)}

        # Telegram/EPIM-Beispiel (Klartext, robust):
        # from ....lib.markdown_render import md_to_plain
        # return {"text": md_to_plain(text)}

    # ── Optional ─────────────────────────────────────
    def build_reply_metadata(self, message) -> dict:
        return {}

    def get_tools(self, ctx) -> list:
        """Optional: Tools, die dieser Kanal bereitstellt (z. B. discord_send)."""
        return []

# Instanz auf Modulebene (wird von der Registry gefunden)
MyChannel_instance = MyChannel()
```

**Wichtige Punkte:**
- Das Plugin liegt in `aifred/plugins/channels/{name}_channel/`
- Muss eine `BaseChannel`-Instanz auf Modulebene bereitstellen
- `listener_loop()` muss unbegrenzt laufen und `asyncio.CancelledError` behandeln
- `build_context()` sollte Prompts aus dem Verzeichnis `prompts/` laden
- **Credentials über `broker.get()`**, niemals über `os.environ` oder globale Variablen aus `config.py`
- Credential-Mapping in `credential_broker.py: _CREDENTIAL_MAP` ergänzen
- Channel-Tools MÜSSEN benannte Tier-Konstanten verwenden (typischerweise `TIER_COMMUNICATE`)
- **Ausgangsformatierung**: `outbound.text` in `send_reply()` durch `self.format_outbound()` leiten. Agenten erzeugen Markdown — nur Discord stellt es nativ dar. E-Mail/Telegram/EPIM usw. brauchen eine Umwandlung über `md_to_html` / `md_to_plain` aus `aifred/lib/markdown_render.py`.

## Markdown-Umwandlung für ausgehende Nachrichten

Agenten in AIfred erzeugen standardmäßig Markdown — gut für die Browser-UI und Discord (stellt Markdown nativ dar), aber unlesbar in E-Mail-Clients (`**fett**` erscheint als Rohtext), Telegram, EPIM und ähnlichen Zielen.

Der Hook `BaseChannel.format_outbound(text) -> dict[str, str]` ist die einzige Stelle, an der jeder Kanal entscheidet, wie das Markdown des Agenten für seinen Empfänger dargestellt wird. Default ist Passthrough (`{"text": text}`). Überschreiben, wenn dein Kanal eine Umwandlung braucht.

**Gemeinsame Konverter** (`aifred/lib/markdown_render.py`):

| Funktion | Zweck |
|----------|---------|
| `md_to_html(text)` | Vollständiges HTML-Rendering. Wird von E-Mail für den MIME-Teil `text/html` verwendet. Basiert auf mistune (CommonMark + GFM-Tabellen/Durchstreichen/Task-Listen). In Markdown eingebettetes HTML wird automatisch escaped (XSS-sicher). |
| `md_to_plain(text)` | Entfernt Markdown-Markierungen, erhält Inhalt + Struktur (Listen werden zu `•`, Links zu `text (url)`, Überschriften behalten ihren Text ohne `#`, Fenced Code behält den Inhalt ohne Backticks). Tabellen bleiben unverändert — in Monospace bleiben sie lesbar. |

**Rezepte nach Kanaltyp:**

```python
# Kanal, der Markdown nativ darstellt (Discord):
def format_outbound(self, text: str) -> dict[str, str]:
    return {"text": text}  # Passthrough — der Default funktioniert auch

# Kanal, der HTML neben Klartext unterstützt (E-Mail):
def format_outbound(self, text: str) -> dict[str, str]:
    from ....lib.markdown_render import md_to_html, md_to_plain
    return {"text": md_to_plain(text), "html": md_to_html(text)}

# Kanal, der robusten Klartext will (Telegram, EPIM):
def format_outbound(self, text: str) -> dict[str, str]:
    from ....lib.markdown_render import md_to_plain
    return {"text": md_to_plain(text)}
```

In `send_reply()` `self.format_outbound(outbound.text)` aufrufen und die passenden Felder an deinen Transport übergeben (SMTP, REST-API usw.). Konkrete Beispiele: siehe `email_channel/__init__.py`, `telegram_channel/__init__.py`, `discord_channel/__init__.py`.

**Warum ein Dict als Rückgabetyp?** Verschiedene Kanäle brauchen verschiedene Beigaben: E-Mail braucht sowohl `text` als auch `html` für `multipart/alternative`, Telegram unterstützte früher ein `parse_mode`-Flag, künftige Kanäle brauchen vielleicht andere Hinweise. Ein Dict hält das Interface flexibel, ohne dass die Parameter explodieren.

## Credential-Speicherung: Secrets gegen Settings

`CredentialField` hat ein Flag `is_secret`, das steuert, wo Werte gespeichert werden:

| `is_secret` | Speicherort | Beispiel |
|-------------|---------|---------|
| `True` | `.env`-Datei + `os.environ` | Passwörter, API-Keys, Bot-Tokens |
| `False` (Default) | `settings.json` des Plugins | Ports, Hosts, Pfade, Engine-Auswahl |

`is_password=True` setzt automatisch `is_secret=True`.

**Beim Start:** Die Werte aus der `settings.json` des Plugins werden von der Registry in `os.environ` geladen, sodass `credential_broker` und `is_configured()` nahtlos funktionieren.

**Migration:** Wird ein Plugin zum ersten Mal geladen und hat noch keine `settings.json`, werden vorhandene `.env`-Werte nicht geheimer Felder automatisch migriert.

## Plugin-i18n

Jedes Plugin, ob Tool oder Channel, bringt seine nutzersichtbaren Texte in seiner eigenen `i18n.json` mit. Zwei Keys sind **Pflicht**, jeweils in DE und EN:

| Key | Verwendet von |
|-----|---------|
| `plugin_display_name` | Plugin-Liste, Tool-Pill-Gruppen, Titel des Settings-Modals, Channel-Label im Message Hub, Sub-Agent-Legende |
| `plugin_description` | Glühbirne in der Plugin-Liste, Sub-Agent-Legende (`delegate_task` → `agent`) |

Sie werden **ausschließlich** über `plugin_display_name(plugin, lang)` und `plugin_description(plugin, lang)` in `aifred/lib/plugin_base.py` gelesen. Eine fehlende Datei, ein fehlender Key oder eine fehlende Sprache löst einen `RuntimeError` aus (kein Fallback-Text); `tests/test_plugin_i18n.py` prüft jedes Plugin-Paket. Debug-Meldungen verwenden `lang="en"`.

Credential-Labels und Tooltips stehen in derselben Datei:

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

**Konvention:** Tooltip-Keys heißen `{label_key}_tooltip`.

Die Methode `translate(key, lang)` des Plugins schlägt Übersetzungen in der `i18n.json` nach. Das Settings-Modal versucht zuerst die Plugin-i18n und greift dann auf die zentrale `aifred/lib/i18n.py` zurück.

## Hilfsmethoden von BaseChannel

Jedes Channel-Plugin erbt diese Methoden von `BaseChannel`:

| Methode | Beschreibung |
|--------|-------------|
| `load_settings()` | `settings.json` des Plugins lesen |
| `save_settings(dict)` | `settings.json` des Plugins schreiben |
| `get_setting(key)` | Einzelnen Einstellungswert holen |
| `set_setting(key, value)` | Einzelnen Einstellungswert setzen |
| `load_i18n()` | `i18n.json` des Plugins lesen |
| `translate(key, lang)` | Key über die `i18n.json` des Plugins übersetzen |
| `channel_log(msg, level)` | Ins Debug-Log + stderr loggen (journalctl) |

## Debug-Meldungen

Den zentralen Debug Bus verwenden. Funktioniert in allen Kontexten (Browser, Hub, Standalone):

```python
from aifred.lib.debug_bus import debug

debug("Searching...")
debug("Found 5 results")
```

Meldungen landen automatisch in:
- der Log-Datei (`data/logs/aifred_debug.log`) — immer
- der Debug-Konsole im Browser — wenn eine Browser-Session aktiv ist
- der Session-Datei — wenn `session_scope` aktiv ist (Hub-Pfad)

## Aktivieren / Deaktivieren

Plugins werden über den **Plugin Manager** verwaltet (Settings > Plugin Manager > Zahnrad-Symbol).

### Channel-Plugins und Tool-Plugins — unterschiedliches Schaltverhalten

**Channel-Plugins** (E-Mail, Discord) haben Schalter, die **sofort** wirken:
- Der Hauptschalter aktiviert/deaktiviert den gesamten Kanal (Tools + Listener)
- Unterschalter steuern den Hintergrund-Listener (Monitor) und Auto-Reply
- Änderungen wirken sofort, weil sie laufende Hintergrund-Worker starten/stoppen
- Der Zustand wird in `settings.json` gespeichert (`channel_toggles`)

**Tool-Plugins** (Calculator, Documents, EPIM usw.) haben Schalter, die **bei OK** wirken:
- Das Umschalten eines Tool-Plugins in der UI ändert nur den sichtbaren Zustand
- Ein Klick auf OK übernimmt alle Änderungen auf einmal, indem Dateien nach/aus `plugins/disabled/` verschoben werden
- Die Plugin-Datei wird physisch verschoben — was im Ordner liegt, ist aktiv, was in `disabled/` liegt, nicht

### Unterschalter von Kanälen

Kanäle mit `always_reply = False` (z. B. E-Mail) zeigen zusätzliche Unterschalter:
- **Monitor**: Hintergrund-Listener starten/stoppen (IMAP IDLE usw.)
- **Auto-Reply**: LLM-Antworten automatisch über den Kanal zurücksenden

Kanäle mit `always_reply = True` (z. B. Discord) zeigen nur den Hauptschalter.

## Dateistruktur

```
aifred/
├── lib/
│   ├── plugin_base.py         # Interfaces (BaseChannel, ToolPlugin, PluginContext, CredentialField)
│   ├── plugin_registry.py     # Discovery, Aktivieren/Deaktivieren, Auflisten, Migration
│   ├── security.py            # Tier-Konstanten, Filter, Sanitizing, Audit
│   ├── credential_broker.py   # Zentrale Credential-Verwaltung
│   ├── debug_bus.py           # debug(), session_scope, flush
│   └── function_calling.py    # Klassen Tool, ToolKit
└── plugins/
    ├── channels/
    │   ├── email_channel/     # E-Mail (IMAP/SMTP + E-Mail-Tools)
    │   │   ├── __init__.py
    │   │   └── i18n.json
    │   ├── discord_channel/   # Discord (Bot + Tool discord_send)
    │   │   ├── __init__.py
    │   │   └── i18n.json
    │   ├── telegram_channel/  # Telegram (Bot + Tool telegram_send)
    │   │   ├── __init__.py
    │   │   └── i18n.json
    │   └── freeecho2_channel/ # FreeEcho.2-Sprachterminal (WebSocket)
    │       ├── __init__.py
    │       └── i18n.json
    ├── tools/
    │   ├── calculator.py      # calculate (Tier 0)
    │   ├── epim/              # EPIM-Datenbank-CRUD (Tier 0/2/3)
    │   ├── research.py        # web_search, web_fetch (Tier 0)
    │   └── sandbox.py         # execute_code (Tier 2)
    └── disabled/              # Deaktivierte Tool-Plugins (von der UI hierher verschoben)
```
