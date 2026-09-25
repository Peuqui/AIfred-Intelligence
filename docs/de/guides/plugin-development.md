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
# aifred/plugins/tools/my_plugin/__init__.py
from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import PluginContext, load_tool_description
from ....lib.security import TIER_READONLY

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
            # Text steht in prompts/tools/my_tool.txt (fehlt/leer → RuntimeError)
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
        """Anleitung für den System-Prompt, aus den Fragmenten in prompts/<de|en>/."""
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        """UI-Statustext, der während der Ausführung dieses Tools angezeigt wird. Leer = nicht zuständig."""
        return ""

# Instanz auf Modulebene (wird von der Registry gefunden)
plugin = MyPlugin()
```

**Wichtige Punkte:**
- Ein Plugin ist ein Verzeichnis `aifred/plugins/tools/<name>/` — siehe [Verzeichnisaufbau eines Plugins](#verzeichnisaufbau-eines-plugins)
- Muss ein Attribut `plugin` auf Modulebene bereitstellen; `plugin.name` muss dem Ordnernamen entsprechen (sonst loggt die Registry eine Warnung, und das Plugin ist im Plugin Manager unsichtbar)
- **Name und Beschreibung kommen aus der eigenen `i18n.json` des Plugins** (`plugin_display_name`, `plugin_description`, jeweils DE und EN) — siehe [Plugin-i18n](#plugin-i18n)
- **Jedes Tool MUSS einen `tier` deklarieren**, mit den benannten Konstanten aus `security.py`
- `PluginContext` stellt bereit: `agent_id`, `lang`, `session_id`, `state`, `user_query`, `max_tier`, `source`, `llm_history`, `metadata` (kanalspezifisch, z. B. die Telegram-`chat_id`)
- Tool-Executors sind async-Funktionen, die Strings zurückgeben (JSON bei Fehlern)
- **Kein hardcodierter LLM-Text:** Tool-Beschreibungen kommen per `load_tool_description()` aus `prompts/tools/<tool>.txt`, Prompt-Anweisungen per `load_plugin_instructions()` aus `prompts/<de|en>/` — beide Dateien liegen im Plugin-Verzeichnis
- `get_prompt_instructions()` bekommt `granted_tools` (die für den aktuellen Agenten freigeschalteten Tool-Namen, `None` = keine Whitelist) und liefert nur die Anleitung dieser Tools
- **Credentials über den Broker**, niemals über `os.environ` oder `config.py`

## Verzeichnisaufbau eines Plugins

Jedes Plugin, ob Tool oder Channel, ist ein Python-Paket. Die Dateien, die die
vorhandenen Plugins verwenden (z. B. `tools/calculator/`, `tools/vision/`,
`channels/telegram_channel/`):

```
aifred/plugins/tools/my_plugin/
    __init__.py            # Plugin-Code + Instanz auf Modulebene (Pflicht)
    i18n.json              # plugin_display_name + plugin_description, DE und EN (Pflicht)
    settings.json          # Nicht geheime Einstellungen (optional, schreibt das Settings-Modal)
    prompts/
        tools/
            my_tool.txt    # Tool-Beschreibung für das Modell, nur Englisch
        de/
            _intro.txt     # Plugin-weite Anleitung, sobald mindestens ein Tool freigeschaltet ist
            my_tool.txt    # Nur sichtbar, wenn my_tool freigeschaltet ist
            my_tool+other_tool.txt  # Nur sichtbar, wenn BEIDE Tools freigeschaltet sind
        en/
            ...            # Dieselben Dateinamen wie in de/
    tools.py, db.py, …     # Weitere Module nach Bedarf (z. B. epim, email_channel)
```

| Datei | Gelesen von | Verhalten |
|-------|-------------|-----------|
| `prompts/tools/<tool>.txt` | `load_tool_description(__file__, "<tool>")` | Bei jedem Toolkit-Build frisch gelesen; fehlende oder leere Datei → `RuntimeError`, kein Fallback-Text. Nur Englisch — Empfänger ist das Modell |
| `prompts/<de\|en>/*.txt` | `load_plugin_instructions(plugin, lang, granted_tools)` | Dateiname = benötigte(s) Tool(s), mit `+` verknüpft (UND); `_intro.txt` wird vorangestellt, sobald mindestens ein Tool-Fragment greift. Übergangs-Fallback: eine flache `prompts/<de\|en>.txt`, plugin-weit gegated |
| `i18n.json` | `plugin_display_name()`, `plugin_description()`, Settings-Modal | Siehe [Plugin-i18n](#plugin-i18n) |
| `settings.json` | Channels: `BaseChannel.load_settings()` / `save_settings()`; Tools: `load_plugin_settings(__file__)` / `save_plugin_settings(__file__, …)` | Siehe [Credential-Speicherung](#credential-speicherung-secrets-gegen-settings) |

Größere Plugins teilen sich zusätzlich in Unterpakete auf (z. B. `google_suite/` mit
`calendar/`, `contacts/`, `drive/`, `tasks/`). Plugin-eigene Datendateien sind ebenfalls
in Ordnung (z. B. `bible/book_aliases/de.json`). Gemeinsame Logik mehrerer Plugins gehört
nach `aifred/lib/`, niemals in einen Import aus einem anderen Plugin.

### Channel-Plugins (`plugins/channels/`)

Empfangen und senden Nachrichten über externe Dienste (E-Mail, Discord, Telegram usw.).

Jedes Channel-Plugin ist ein **in sich geschlossenes Verzeichnis**:

```
aifred/plugins/channels/my_channel/
    __init__.py     # Plugin-Code (BaseChannel-Unterklasse + Instanz auf Modulebene)
    i18n.json       # Name, Beschreibung, Credential-Labels (DE und EN, Pflicht)
    settings.json   # Automatisch erzeugt: nicht geheime Einstellungen (Ports, Hosts usw.)
    prompts/tools/  # Beschreibungen der kanaleigenen Tools (z. B. telegram_send.txt)
```

Code, Texte und nicht geheime Einstellungen liegen alle im Ordner — in den zentralen
UI-Übersetzungen (`aifred/lib/i18n/`) wird nichts eingetragen. Nur geheime Felder
(`is_secret=True`) landen in der `.env`.

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
- Das Plugin liegt in `aifred/plugins/channels/{name}_channel/` (`BaseChannel` leitet den Pfad der `settings.json` aus diesem Ordnernamen ab)
- Muss eine `BaseChannel`-Instanz auf Modulebene bereitstellen (die Registry übernimmt jede `BaseChannel`-Instanz im Modul)
- `listener_loop()` muss unbegrenzt laufen und `asyncio.CancelledError` behandeln
- `build_context()` sollte Prompts aus dem Verzeichnis `prompts/` laden
- **Credentials über `broker.get()`**, niemals über `os.environ` oder globale Variablen aus `config.py`
- `broker` löst `(service, key)` zur Umgebungsvariable `<SERVICE>_<KEY>` auf; einen Eintrag in `credential_broker.py: _CREDENTIAL_MAP` brauchst du nur, wenn der Variablenname davon abweicht
- Channel-Tools MÜSSEN benannte Tier-Konstanten verwenden (typischerweise `TIER_COMMUNICATE`) und ihre Beschreibung per `load_tool_description(__file__, "<tool>")` laden
- Optionale Properties: `always_reply` (Default `False`; `True` blendet den Auto-Reply-Schalter aus) und `has_allowlist` (Default `True`; `False` blendet die Allowlist-Zeile aus, z. B. FreeEcho.2)
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

In `send_reply()` `self.format_outbound(outbound.text)` aufrufen und die passenden Felder an deinen Transport übergeben (SMTP, REST-API usw.). Konkrete Überschreibungen: siehe `email_channel/__init__.py` und `telegram_channel/__init__.py`; `discord_channel` nutzt den Default-Passthrough.

**Warum ein Dict als Rückgabetyp?** Verschiedene Kanäle brauchen verschiedene Beigaben: E-Mail braucht sowohl `text` als auch `html` für `multipart/alternative`, Telegram unterstützte früher ein `parse_mode`-Flag, künftige Kanäle brauchen vielleicht andere Hinweise. Ein Dict hält das Interface flexibel, ohne dass die Parameter explodieren.

## Credential-Speicherung: Secrets gegen Settings

`CredentialField` hat ein Flag `is_secret`, das steuert, wo Werte gespeichert werden:

| `is_secret` | Speicherort | Beispiel |
|-------------|---------|---------|
| `True` | `.env`-Datei + `os.environ` | Passwörter, API-Keys, Bot-Tokens |
| `False` (Default) | `settings.json` des Plugins | Ports, Hosts, Pfade, Engine-Auswahl |

`is_password=True` setzt automatisch `is_secret=True`.

**Beim Start (Channels):** Findet die Registry einen Kanal, ruft sie `load_settings_to_env()` auf. Das kopiert die nicht leeren Werte aus der `settings.json` des Kanals nach `os.environ` (sie haben Vorrang vor der `.env`), sodass `credential_broker` und `is_configured()` nahtlos funktionieren.

**Tool-Plugins** lesen ihre `settings.json` direkt über `load_plugin_settings(__file__)` aus `aifred/lib/plugin_base.py` (Gegenstück: `save_plugin_settings(__file__, settings)`).

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

Das Settings-Modal löst Credential-Labels, Tooltips und die Labels von Dropdown-Optionen **ausschließlich** über die eigene `i18n.json` des Plugins auf (Tool- wie Channel-Plugins). Ein Key, den die Datei nicht enthält, wird so angezeigt, wie er geschrieben ist; ein fehlender Tooltip bleibt leer. Einen Rückfall auf die zentralen UI-Übersetzungen gibt es nicht — die liegen im Paket `aifred/lib/i18n/` (`de.json`, `en.json`, `TranslationManager` in `__init__.py`) und sind nicht für Plugin-Texte gedacht.

Channel-Plugins haben zusätzlich `translate(key, lang="de")` (siehe unten): Es liefert den Eintrag für `lang`, sonst den `de`-Eintrag, sonst den Key selbst.

## Hilfsmethoden von BaseChannel

Jedes Channel-Plugin erbt diese Methoden von `BaseChannel`:

| Methode | Beschreibung |
|--------|-------------|
| `load_settings()` | `settings.json` des Plugins lesen |
| `save_settings(dict)` | `settings.json` des Plugins schreiben |
| `load_settings_to_env()` | Nicht leere Werte der `settings.json` nach `os.environ` kopieren (ruft die Registry beim Discovery auf) |
| `load_i18n()` | `i18n.json` des Plugins lesen |
| `translate(key, lang="de")` | Key über die `i18n.json` des Plugins übersetzen (Default-Sprache `de`, Rückfall auf `de`, dann auf den Key) |
| `format_outbound(text)` | Agenten-Markdown für den Kanal umwandeln (Default: Passthrough) |
| `channel_log(msg, level="info")` | In Log-Datei + stderr loggen (journalctl), innerhalb eines `session_scope` zusätzlich in die Debug-Konsole im Browser |

Tool-Plugins nutzen stattdessen die Hilfsfunktionen auf Modulebene aus `aifred/lib/plugin_base.py`: `load_plugin_settings()`, `save_plugin_settings()`, `load_plugin_i18n()`, `load_tool_description()`, `load_plugin_instructions()`.

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
- Der Zustand wird in AIfreds `data/settings.json` gespeichert (`channel_toggles`)

**Tool-Plugins** (Calculator, Workspace, EPIM usw.) haben Schalter, die **bei OK** wirken:
- Das Umschalten eines Tool-Plugins in der UI ändert nur den sichtbaren Zustand
- Ein Klick auf OK übernimmt alle Änderungen auf einmal, indem Plugin-Verzeichnisse nach/aus `plugins/disabled/` verschoben werden
- Das Plugin-Verzeichnis wird physisch verschoben und bekommt seinen Typ als Präfix (`disabled/tool_<name>`, `disabled/channel_<name>`) — was in `tools/` oder `channels/` liegt, ist aktiv, was in `disabled/` liegt, nicht

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
│   │                          # + Helfer (load_tool_description, load_plugin_instructions, Plugin-i18n/-Settings)
│   ├── plugin_registry.py     # Discovery, Aktivieren/Deaktivieren, Auflisten
│   ├── security.py            # Tier-Konstanten, Filter, Sanitizing, Audit
│   ├── credential_broker.py   # Zentrale Credential-Verwaltung
│   ├── debug_bus.py           # debug(), session_scope, flush
│   ├── function_calling.py    # Klassen Tool, ToolKit
│   ├── markdown_render.py     # md_to_html, md_to_plain (Ausgangsformatierung)
│   └── i18n/                  # Zentrale UI-Übersetzungen (de.json, en.json, __init__.py) — nicht für Plugin-Texte
└── plugins/
    ├── channels/              # jeder Kanal: __init__.py, i18n.json, settings.json, prompts/tools/
    │   ├── discord_channel/   # Discord-Bot (+ discord_send)
    │   ├── email_channel/     # IMAP IDLE + SMTP (+ E-Mail-Tools; client.py, config.py, tools.py)
    │   ├── freeecho2_channel/ # FreeEcho.2-Lautsprecher über WebSocket (+ freeecho2_announce)
    │   └── telegram_channel/  # Telegram-Bot (+ telegram_send)
    ├── tools/                 # jedes Tool: __init__.py, i18n.json, prompts/ (settings.json bei Bedarf)
    │   ├── audio_player/      # Lokale Audiodateien und Internet-Streams
    │   ├── bible/             # Bibelstellen nachschlagen und durchsuchen
    │   ├── calculator/        # calculate
    │   ├── epim/              # EPIM-Termine, -Kontakte, -Notizen, -Aufgaben (db.py, tools.py)
    │   ├── google_suite/      # Google Calendar, Contacts, Tasks, Drive (Unterpaket pro Dienst)
    │   ├── judaica/           # Jüdischen Quellenkorpus nachschlagen und durchsuchen
    │   ├── narrator/          # Liest Textdateien in eine Audiodatei ein
    │   ├── research/          # web_search, web_fetch
    │   ├── sandbox/           # execute_code, render_html
    │   ├── scheduler_tool/    # Geplante Aufgaben und Erinnerungen
    │   ├── subagent/          # delegate_task an Sub-Agenten
    │   ├── system_monitor/    # system_status (CPU, RAM, VRAM, Platten, …)
    │   ├── translator/        # DeepL translate, translate_file
    │   ├── vision/            # Vigilantia: Snapshots, Szenenbeschreibungen, Watches
    │   └── workspace/         # Dateien im Arbeitsverzeichnis + ChromaDB-Dokumentenindex
    └── disabled/              # Deaktivierte Plugins (von der UI als tool_<name> / channel_<name> hierher verschoben)
```
