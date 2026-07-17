"""Agent editor mixin for AIfred state.

Handles the agent editor page: agent CRUD, prompt layer editing,
tool whitelist, cloud model selection for system-role agents,
and the scheduler job editor.
"""

from __future__ import annotations

from typing import Dict, List

import reflex as rx
from reflex.event import EventSpec

from ..lib.config import TTS_DEFAULT_ENGINE


class AgentEditorMixin(rx.State, mixin=True):
    """Mixin for the agent editor page and scheduler job editing."""

    # Editor modal visibility
    agent_editor_open: bool = False

    # Editor view mode: "config", "memory" or "database"
    agent_editor_mode: str = "config"

    # Scheduler state
    scheduler_job_list: List[Dict[str, str]] = []
    scheduler_edit_id: str = ""  # Job being edited ("" = none, "new" = create)
    scheduler_edit_name: str = ""
    scheduler_edit_type: str = "cron"
    scheduler_edit_expr: str = ""
    scheduler_edit_message: str = ""
    scheduler_edit_agent: str = "aifred"
    scheduler_edit_delivery: str = "review"
    scheduler_edit_channel: str = ""
    scheduler_edit_tier: str = "1"
    scheduler_edit_webhook_url: str = ""
    scheduler_edit_recipient: str = ""
    # Structured schedule fields (compose to scheduler_edit_expr on save)
    scheduler_cron_min: str = "0"
    scheduler_cron_hour: str = "8"
    scheduler_cron_dom: str = "*"
    scheduler_cron_month: str = "*"
    scheduler_cron_dow: str = "*"
    scheduler_interval_value: str = "60"
    scheduler_interval_unit: str = "minutes"  # minutes, hours, days
    scheduler_once_date: str = ""
    scheduler_once_time: str = "10:00"

    # Agent dropdown options for the editor (["emoji name", ...])
    _agent_dropdown_items: List[str] = []
    # Mapping: display label → agent_id (for dropdown selection)
    _agent_id_by_label: Dict[str, str] = {}

    # Currently editing agent (empty = creating new)
    editor_agent_id: str = ""
    editor_display_name: str = ""
    editor_emoji: str = ""
    _editor_description: str = ""
    editor_role: str = "custom"
    editor_model: str = ""  # Cloud model id, only used for system-role agents
    # Cloud provider for system-role agents (calibration). One of
    # CLOUD_API_PROVIDERS — drives which endpoint + key the model list and
    # the calibration loop use (same SSOT as the main chat backend).
    editor_cloud_provider: str = "qwen"
    # Live model list fetched from the selected provider's /models endpoint.
    editor_cloud_models: List[str] = []
    # Reasoning toggle for system-role agents (e.g. calibration). Mirrors
    # agents.json `toggles.reasoning`. Off by default — system workflows
    # typically don't benefit enough from chain-of-thought to justify the
    # 30-120 s per-turn overhead.
    editor_system_reasoning: bool = False

    # Prompt layer editor state
    editor_prompt_tab: str = "identity"
    _editor_prompt_content: str = ""
    editor_prompt_lang: str = "de"  # Language toggle for prompt editor
    # Available prompt keys for current agent (for tab rendering)
    editor_prompt_keys: List[str] = []

    # New agent creation fields
    _editor_new_agent_id: str = ""

    # Delete confirmation
    editor_delete_confirm: str = ""
    # Memory clear confirmation
    editor_memory_confirm: str = ""

    # Emoji picker visibility
    editor_emoji_picker_open: bool = False

    # Tool whitelist editor — list of all tool names with enabled/disabled state
    editor_tools: Dict[str, bool] = {}


    @rx.var(deps=["_agent_dropdown_items"], auto_deps=False)
    def agent_dropdown_options(self) -> List[str]:
        """Agent dropdown labels for the editor (e.g. ['🎩 AIfred', '🏛️ Sokrates', ...])."""
        return self._agent_dropdown_items

    @rx.var(deps=["editor_agent_id", "_agent_dropdown_items"], auto_deps=False)
    def editor_agent_dropdown_value(self) -> str:
        """Current dropdown value matching the selected agent."""
        if not self.editor_agent_id:
            return ""
        for item in self._agent_dropdown_items:
            # Format: "emoji Name" — match by checking if it maps to this agent_id
            # We store a parallel mapping, but simpler: just find by id
            pass
        # Reconstruct the label from current editor state
        return f"{self.editor_emoji} {self.editor_display_name}" if self.editor_agent_id else ""

    # Separator + label for Automatik-LLM in agent dropdown
    _AUTOMATIK_SEPARATOR = "─────────────────"
    _AUTOMATIK_LABEL = "⚡ Automatik-LLM"

    @rx.var
    def editor_is_system_agent(self) -> bool:
        """True for system-role agents that use the locked-down editor view
        (only model + prompts editable). Excludes Automatik-LLM, which has
        its own dedicated UI path."""
        return self.editor_role == "system" and self.editor_agent_id != "automatik"

    def set_editor_model(self, value: str) -> None:
        """Set the cloud model for a system-role agent."""
        if value:
            self.editor_model = value
            self.editor_dirty = True  # type: ignore[attr-defined]

    # Static registry; explicit empty deps avoid the lazy-import auto-dep warning.
    @rx.var(deps=[], auto_deps=False)
    def editor_cloud_provider_options(self) -> List[str]:
        """Provider display labels — the SAME labels the main backend dropdown
        shows ("Qwen (DashScope)", …), via the shared cloud_api SSOT."""
        from ..backends.cloud_api import cloud_provider_labels
        return cloud_provider_labels()

    @rx.var(deps=["editor_cloud_provider"], auto_deps=False)
    def editor_cloud_provider_label(self) -> str:
        """Display label of the currently-selected provider (for the select)."""
        from ..backends.cloud_api import cloud_provider_label
        return cloud_provider_label(self.editor_cloud_provider)

    @rx.var(deps=["editor_cloud_models", "editor_model"], auto_deps=False)
    def editor_cloud_model_options(self) -> List[str]:
        """Live model list, with the currently-saved model guaranteed present
        so the select shows it even before the list has been fetched."""
        models = list(self.editor_cloud_models)
        if self.editor_model and self.editor_model not in models:
            models = [self.editor_model] + models
        return models

    async def set_editor_cloud_provider(self, label: str):
        """Switch provider (selected by display label) → reset model + reload
        the live model list. Label→id resolves through the cloud_api SSOT."""
        from ..backends.cloud_api import cloud_provider_from_label
        provider = cloud_provider_from_label(label)
        if provider != self.editor_cloud_provider:
            self.editor_cloud_provider = provider
            self.editor_model = ""  # different provider → old model invalid
            self.editor_dirty = True  # type: ignore[attr-defined]
            async for _ in self.refresh_editor_cloud_models():
                yield

    async def refresh_editor_cloud_models(self):
        """Fetch the live /models list for the selected provider via the
        shared cloud_api SSOT (CloudAPIBackend.list_models)."""
        from ..backends.cloud_api import (
            CloudAPIBackend, get_cloud_api_key, is_cloud_api_configured,
        )
        from ..lib.config import CLOUD_API_PROVIDERS
        provider = self.editor_cloud_provider
        cfg = CLOUD_API_PROVIDERS.get(provider)
        if not cfg or not is_cloud_api_configured(provider):
            self.editor_cloud_models = []
            yield
            return
        try:
            backend = CloudAPIBackend(
                base_url=cfg["base_url"],
                api_key=get_cloud_api_key(provider) or "",
                provider=provider,
            )
            models = await backend.list_models()
            await backend.close()
            self.editor_cloud_models = sorted(models)
        except Exception:  # noqa: BLE001 — network/SDK errors → empty list + hint
            self.editor_cloud_models = []
        yield

    def toggle_editor_system_reasoning(self) -> None:
        """Flip the reasoning toggle for a system-role agent."""
        self.editor_system_reasoning = not self.editor_system_reasoning
        self.editor_dirty = True  # type: ignore[attr-defined]

    def _refresh_agent_dropdown(self) -> None:
        """Refresh the agent dropdown items from config.

        Layout:
          - Regular agents (role != "system")
          - separator
          - Automatik-LLM
          - System agents from agents.json (role == "system"),
            each labelled with its emoji + display_name
        """
        from ..lib.agent_config import load_agents_raw
        raw = load_agents_raw()
        regular = {aid: d for aid, d in raw.items() if d.get("role") != "system"}
        system = {aid: d for aid, d in raw.items() if d.get("role") == "system"}

        items = [f"{d['emoji']} {d['display_name']}" for d in regular.values()]
        items.append(self._AUTOMATIK_SEPARATOR)
        items.append(self._AUTOMATIK_LABEL)
        for d in system.values():
            items.append(f"{d['emoji']} {d['display_name']}")
        self._agent_dropdown_items = items

        self._agent_id_by_label = {
            f"{d['emoji']} {d['display_name']}": aid for aid, d in raw.items()
        }
        self._agent_id_by_label[self._AUTOMATIK_LABEL] = "automatik"

    # Init-Flag fuer on_load_agent_editor: True bei Cold-Start oder wenn
    # open_agent_editor explizit gerufen wurde. False sobald ein Setup
    # gelaufen ist — verhindert, dass on_load bei jedem Page-Re-Entry
    # (z.B. zurueck von /audio-settings) den Tab-Mode auf "config"
    # zurueckschlaegt.
    _agent_editor_needs_init: bool = True

    def open_agent_editor(self):
        """Navigate to the agent-editor page.

        Trigger fuer „Agent bearbeiten"-Buttons im Chat. Setzt den
        Init-Flag, damit ``on_load_agent_editor`` den vollen Setup macht
        (Refresh-Dropdown, Load-First-Agent, DOM-Push, Tab=config).
        """
        self._agent_editor_needs_init = True
        return rx.redirect("/agent-editor")

    def on_load_agent_editor(self):
        """Page-Load-Hook fuer ``/agent-editor``.

        Vollstaendiger Setup nur beim ersten Open via Chat-Button (oder
        Cold-Start). Bei Re-Entry (z.B. zurueck von /audio-settings)
        bleibt der Editor-State unangetastet — der User landet wieder
        in dem Tab den er vor dem Detour offen hatte.
        """
        if not self._agent_editor_needs_init:
            return
        self._agent_editor_needs_init = False
        self._refresh_agent_dropdown()
        self.agent_editor_mode = "config"
        self.agent_editor_open = True
        self.editor_delete_confirm = ""
        self.editor_emoji_picker_open = False
        self.editor_dirty = False
        self.editor_dirty_confirm = False

        # Load first agent's data into state
        from ..lib.agent_config import load_agents_raw
        raw = load_agents_raw()
        if raw:
            first_id = next(iter(raw))
            self._load_agent_into_state(first_id)

        # Yield to render the page DOM first
        yield

        # Now populate DOM fields (page exists now)
        yield self._push_editor_dom()

    # Dirty flag — set on any keystroke in editor fields
    editor_dirty: bool = False
    editor_dirty_confirm: bool = False  # Show unsaved-changes dialog
    _pending_agent_label: str = ""
    _pending_close: bool = False

    def mark_editor_dirty(self) -> None:
        """Mark editor as having unsaved changes (called on any keystroke)."""
        self.editor_dirty = True

    def close_agent_editor(self):
        """Close the agent editor — reset state + navigate back to chat."""
        self.agent_editor_open = False
        self.editor_agent_id = ""
        self.editor_delete_confirm = ""
        self.editor_dirty_confirm = False
        self.editor_dirty = False
        return rx.redirect("/")

    def close_editor_with_dirty_check(self):
        """Close editor — warn if unsaved changes.

        Wenn nicht dirty (oder nicht im config-Tab): direkt close_agent_editor
        durchreichen — dessen ``rx.redirect("/")`` muss zurueckgegeben werden,
        damit Reflex die Navigation tatsaechlich ausloest. Sonst „klickt"
        der User auf X und nichts passiert (Bug nach dem Multi-Route-Split).
        """
        if not self.editor_dirty or self.agent_editor_mode != "config":
            return self.close_agent_editor()
        self._pending_close = True
        self._pending_agent_label = ""
        self.editor_dirty_confirm = True

    def select_editor_agent_with_dirty_check(self, label: str):
        """Switch agent — warn if unsaved changes."""
        if not self.editor_dirty:
            return self.select_editor_agent(label)
        self._pending_agent_label = label
        self._pending_close = False
        self.editor_dirty_confirm = True

    def confirm_discard_changes(self):
        """User confirmed discarding unsaved changes — reload current agent, stay open."""
        self.editor_dirty_confirm = False
        self.editor_dirty = False
        if self._pending_close:
            self._pending_close = False
            # Don't close — just reload the current agent to discard changes
            if self.editor_agent_id:
                self._load_agent_into_state(self.editor_agent_id)
                return self._push_editor_dom()
            return
        if self._pending_agent_label:
            label = self._pending_agent_label
            self._pending_agent_label = ""
            self.select_editor_agent(label)
            return self._push_editor_dom()


    def set_agent_editor_tab(self, tab: str):
        """Switch between config, memory and database tabs."""
        self.agent_editor_mode = tab
        if tab == "config":
            # Re-push DOM fields when switching back to config tab
            yield
            yield self._push_editor_dom()
        elif tab == "memory":
            self.open_memory_browser()
        elif tab == "database":
            self.db_clear_confirm = False
            if self.db_browser_collection:
                self._load_db_entries()
        elif tab == "storage":
            # Speicher-Tab: lokale Datei-Stores (Exporte + Sandbox) laden.
            self.load_storage_files()
        elif tab == "plugins":
            # Load tool toggles + channel allowlists for the plugins tab.
            # list_all_plugins() (not discover_tools) so DISABLED tool plugins
            # stay visible and can be re-enabled — enabled == in tools/.
            from ..lib.credential_broker import broker
            from ..lib.plugin_registry import list_all_plugins, all_channels
            self.tool_plugin_toggles = {
                p["name"]: p["enabled"]
                for p in list_all_plugins() if p["type"] == "tool"
            }
            self.channel_allowlists = {
                "email": broker.get("email", "allowed_senders") or "-",
                "telegram": broker.get("telegram", "allowed_users") or "-",
                "discord": broker.get("discord", "channel_ids") or "-",
                "freeecho2": "",
            }
            # Ensure all channels have a security tier entry
            from ..lib.security import DEFAULT_TIER_BY_SOURCE, TIER_COMMUNICATE
            tiers = dict(self.channel_security_tiers)
            for ch_name in all_channels():
                if ch_name not in tiers:
                    tiers[ch_name] = DEFAULT_TIER_BY_SOURCE.get(ch_name, TIER_COMMUNICATE)
            self.channel_security_tiers = tiers
        elif tab == "audit":
            from ..lib.security import load_audit_entries
            self.audit_log_entries = load_audit_entries()
        elif tab == "scheduler":
            self._load_scheduler_jobs()

    @staticmethod
    def _human_cron(expr: str, lang: str) -> str:
        """Convert cron expression to human-readable text."""
        parts = expr.split()
        if len(parts) != 5:
            return expr
        minute, hour, dom, month, dow = parts

        dow_names = {
            "de": {"*": "", "1-5": "Mo–Fr", "6,0": "Wochenende",
                   "0": "So", "1": "Mo", "2": "Di", "3": "Mi",
                   "4": "Do", "5": "Fr", "6": "Sa"},
            "en": {"*": "", "1-5": "Mon–Fri", "6,0": "Weekend",
                   "0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed",
                   "4": "Thu", "5": "Fri", "6": "Sat"},
        }
        month_names = {
            "de": {"*": "", "1": "Jan", "2": "Feb", "3": "Mär", "4": "Apr",
                   "5": "Mai", "6": "Jun", "7": "Jul", "8": "Aug",
                   "9": "Sep", "10": "Okt", "11": "Nov", "12": "Dez"},
            "en": {"*": "", "1": "Jan", "2": "Feb", "3": "Mar", "4": "Apr",
                   "5": "May", "6": "Jun", "7": "Jul", "8": "Aug",
                   "9": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"},
        }
        lc = lang if lang in ("de", "en") else "de"

        # Time part
        if hour == "*" and minute == "0":
            time_str = "Stündlich" if lc == "de" else "Hourly"
        elif hour == "*":
            time_str = f":{minute}"
        else:
            time_str = f"{hour}:{minute.zfill(2)}"

        # Day/week part
        dow_str = dow_names[lc].get(dow, dow)
        month_str = month_names[lc].get(month, month)

        fragments: list[str] = []
        fragments.append(time_str)

        if dow_str:
            fragments.append(dow_str)
        if dom != "*":
            tag = "Tag" if lc == "de" else "day"
            fragments.append(f"{dom}. {tag}" if lc == "de" else f"{tag} {dom}")
        if month_str:
            fragments.append(month_str)

        return " · ".join(fragments) if fragments else expr

    @staticmethod
    def _human_interval(expr: str, lang: str) -> str:
        """Convert interval seconds to human-readable text."""
        try:
            seconds = int(expr)
        except ValueError:
            return expr
        lc = lang if lang in ("de", "en") else "de"
        if seconds >= 86400 and seconds % 86400 == 0:
            n = seconds // 86400
            unit = "Tage" if lc == "de" else "days"
            prefix = "Alle" if lc == "de" else "Every"
            return f"{prefix} {n} {unit}"
        if seconds >= 3600 and seconds % 3600 == 0:
            n = seconds // 3600
            unit = "Stunden" if lc == "de" else "hours"
            if n == 1:
                unit = "Stunde" if lc == "de" else "hour"
            prefix = "Alle" if lc == "de" else "Every"
            return f"{prefix} {n} {unit}"
        n = max(1, seconds // 60)
        unit = "Minuten" if lc == "de" else "minutes"
        if n == 1:
            unit = "Minute" if lc == "de" else "minute"
        prefix = "Alle" if lc == "de" else "Every"
        return f"{prefix} {n} {unit}"

    @staticmethod
    def _human_once(expr: str, _lang: str) -> str:
        """Format ISO datetime for display."""
        if "T" in expr:
            date, time = expr.split("T", 1)
            return f"{date} {time[:5]}"
        return expr

    def _format_schedule_display(self, stype: str, expr: str) -> str:
        """Format schedule expression for human-readable display."""
        lang = self.ui_language if hasattr(self, "ui_language") else "de"
        if stype == "cron":
            return self._human_cron(expr, lang)
        if stype == "interval":
            return self._human_interval(expr, lang)
        if stype == "once":
            return self._human_once(expr, lang)
        return expr

    def _format_agent_display(self, agent_id: str) -> str:
        """Resolve agent ID to emoji + display name."""
        from ..lib.agent_config import get_agent_config
        cfg = get_agent_config(agent_id)
        if cfg:
            return f"{cfg.emoji} {cfg.display_name}"
        return agent_id

    def _format_delivery_display(self, delivery: str, channel: str) -> str:
        """Format delivery + channel for display."""
        lang = self.ui_language if hasattr(self, "ui_language") else "de"
        label = self._DELIVERY_DISPLAY.get(lang, self._DELIVERY_DISPLAY["de"]).get(
            delivery, delivery
        )
        if channel:
            return f"{label} → {channel}"
        return label

    @staticmethod
    def _format_datetime(iso_str: str) -> str:
        """Format ISO datetime to readable 'DD.MM.YYYY  HH:MM'."""
        if not iso_str or len(iso_str) < 16:
            return iso_str
        # "2026-04-13T10:00:00" → "13.04.2026  10:00"
        date_part = iso_str[:10]  # 2026-04-13
        time_part = iso_str[11:16]  # 10:00
        try:
            y, m, d = date_part.split("-")
            return f"{d}.{m}.{y}  {time_part}"
        except ValueError:
            return iso_str

    def _load_scheduler_jobs(self) -> None:
        """Load all scheduler jobs for display."""
        from ..lib.scheduler import get_job_store
        store = get_job_store()
        jobs = store.list_all()
        self.scheduler_job_list = [
            {
                "job_id": str(j.job_id),
                "name": j.name,
                "schedule_type": j.schedule_type,
                "schedule_expr": j.schedule_expr,
                "type_display": self._TYPE_DISPLAY.get(
                    self.ui_language if hasattr(self, "ui_language") else "de",
                    self._TYPE_DISPLAY["de"],
                ).get(j.schedule_type, j.schedule_type),
                "schedule_display": self._format_schedule_display(
                    j.schedule_type, j.schedule_expr
                ),
                "agent_display": self._format_agent_display(
                    j.payload.get("agent", "aifred")
                ),
                "delivery_display": self._format_delivery_display(
                    j.payload.get("delivery", ""),
                    j.payload.get("channel", ""),
                ),
                "enabled": "1" if j.enabled else "",
                "next_run": self._format_datetime(j.next_run[:19]) if j.next_run else "",
                "last_run": self._format_datetime(j.last_run[:19]) if j.last_run else "",
                "created_at": self._format_datetime(j.created_at[:19]) if j.created_at else "",
                "message": j.payload.get("message", ""),
                "agent": j.payload.get("agent", "aifred"),
                "delivery": j.payload.get("delivery", ""),
                "channel": j.payload.get("channel", ""),
                "max_tier": str(j.max_tier),
                "retry_count": str(j.retry_count),
                "webhook_url": j.payload.get("webhook_url", ""),
                "recipient": j.payload.get("recipient", ""),
            }
            for j in jobs
        ]

    def toggle_scheduler_job(self, job_id: str) -> None:
        """Toggle a scheduler job enabled/disabled."""
        from ..lib.scheduler import get_job_store
        store = get_job_store()
        job = store.get(int(job_id))
        if job:
            store.enable(int(job_id), not job.enabled)
        self._load_scheduler_jobs()

    def delete_scheduler_job(self, job_id: str) -> None:
        """Delete a scheduler job."""
        from ..lib.scheduler import get_job_store
        store = get_job_store()
        store.delete(int(job_id))
        if self.scheduler_edit_id == job_id:
            self.scheduler_edit_id = ""
        self._load_scheduler_jobs()

    def edit_scheduler_job(self, job_id: str) -> None:
        """Load a job into the edit form."""
        from ..lib.scheduler import get_job_store
        store = get_job_store()
        job = store.get(int(job_id))
        if not job:
            return
        self.scheduler_edit_id = str(job.job_id)
        self.scheduler_edit_name = job.name
        self.scheduler_edit_type = job.schedule_type
        self.scheduler_edit_expr = job.schedule_expr
        self.scheduler_edit_message = job.payload.get("message", "")
        self.scheduler_edit_agent = job.payload.get("agent", "aifred")
        self.scheduler_edit_delivery = job.payload.get("delivery", "review")
        self.scheduler_edit_channel = job.payload.get("channel", "")
        self.scheduler_edit_webhook_url = job.payload.get("webhook_url", "")
        self.scheduler_edit_recipient = job.payload.get("recipient", "")
        self.scheduler_edit_tier = str(job.max_tier)
        self._decompose_schedule_expr()

    def new_scheduler_job(self) -> None:
        """Open empty edit form for a new job."""
        self.scheduler_edit_id = "new"
        self.scheduler_edit_name = ""
        self.scheduler_edit_type = "cron"
        self.scheduler_edit_expr = ""
        self.scheduler_edit_message = ""
        self.scheduler_edit_agent = "aifred"
        self.scheduler_edit_delivery = "review"
        self.scheduler_edit_channel = ""
        self.scheduler_edit_webhook_url = ""
        self.scheduler_edit_recipient = ""
        self.scheduler_edit_tier = "1"
        self.scheduler_cron_min = "0"
        self.scheduler_cron_hour = "8"
        self.scheduler_cron_dom = "*"
        self.scheduler_cron_month = "*"
        self.scheduler_cron_dow = "*"
        self.scheduler_interval_value = "60"
        self.scheduler_interval_unit = "minutes"
        self.scheduler_once_date = ""
        self.scheduler_once_time = "10:00"


    # ── Schedule type / delivery i18n mapping ──────────────────

    _TYPE_MAP: dict[str, str] = {
        "Zeitplan": "cron", "Cron": "cron",
        "Intervall": "interval", "Interval": "interval",
        "Einmalig": "once", "Once": "once",
    }
    _TYPE_DISPLAY: dict[str, dict[str, str]] = {
        "de": {"cron": "Zeitplan", "interval": "Intervall", "once": "Einmalig"},
        "en": {"cron": "Cron", "interval": "Interval", "once": "Once"},
    }
    _DELIVERY_MAP: dict[str, str] = {
        "Vorschau": "review", "Review": "review",
        "Senden": "announce", "Send": "announce",
        "Webhook": "webhook",
    }
    _DELIVERY_DISPLAY: dict[str, dict[str, str]] = {
        "de": {"review": "Vorschau", "announce": "Senden", "webhook": "Webhook"},
        "en": {"review": "Review", "announce": "Send", "webhook": "Webhook"},
    }

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_type_options(self) -> list[str]:
        lang = "de" if self.ui_language == "de" else "en"
        return list(self._TYPE_DISPLAY[lang].values())

    @rx.var(deps=["scheduler_edit_type", "ui_language"], auto_deps=False)
    def sched_type_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._TYPE_DISPLAY[lang].get(self.scheduler_edit_type, self.scheduler_edit_type)

    def set_scheduler_type_from_label(self, label: str) -> None:
        new_type = self._TYPE_MAP.get(label, "cron")
        self.scheduler_edit_type = new_type

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_delivery_options(self) -> list[str]:
        lang = "de" if self.ui_language == "de" else "en"
        return list(self._DELIVERY_DISPLAY[lang].values())

    @rx.var(deps=["scheduler_edit_delivery", "ui_language"], auto_deps=False)
    def sched_delivery_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._DELIVERY_DISPLAY[lang].get(self.scheduler_edit_delivery, self.scheduler_edit_delivery)

    def set_scheduler_delivery_from_label(self, label: str) -> None:
        self.scheduler_edit_delivery = self._DELIVERY_MAP.get(label, "review")

    # ── Cron presets ───────────────────────────────────────────

    _CRON_PRESETS: list[tuple[str, str, str, str, str, str, str]] = [
        # (label_de, label_en, min, hour, dom, month, dow)
        ("Stündlich", "Hourly", "0", "*", "*", "*", "*"),
        ("Täglich", "Daily", "0", "8", "*", "*", "*"),
        ("Werktags", "Weekdays", "0", "8", "*", "*", "1-5"),
        ("Wöchentlich", "Weekly", "0", "8", "*", "*", "1"),
        ("Monatlich", "Monthly", "0", "8", "1", "*", "*"),
    ]

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_preset_options(self) -> list[str]:
        idx = 0 if self.ui_language == "de" else 1
        return [p[idx] for p in self._CRON_PRESETS]

    def apply_cron_preset(self, label: str) -> None:
        for p in self._CRON_PRESETS:
            if label in (p[0], p[1]):
                self.scheduler_cron_min = p[2]
                self.scheduler_cron_hour = p[3]
                self.scheduler_cron_dom = p[4]
                self.scheduler_cron_month = p[5]
                self.scheduler_cron_dow = p[6]
                return

    # ── Weekday dropdown ──────────────────────────────────────

    _DOW_OPTIONS: list[tuple[str, str, str]] = [
        # (label_de, label_en, cron_value)
        ("Jeden Tag", "Every day", "*"),
        ("Mo–Fr", "Mon–Fri", "1-5"),
        ("Wochenende", "Weekend", "6,0"),
        ("Montag", "Monday", "1"),
        ("Dienstag", "Tuesday", "2"),
        ("Mittwoch", "Wednesday", "3"),
        ("Donnerstag", "Thursday", "4"),
        ("Freitag", "Friday", "5"),
        ("Samstag", "Saturday", "6"),
        ("Sonntag", "Sunday", "0"),
    ]
    _DOW_LABEL_TO_VAL: dict[str, str] = {
        label: val for de, en, val in _DOW_OPTIONS for label in (de, en)
    }
    _DOW_VAL_TO_LABEL: dict[str, dict[str, str]] = {
        "de": {val: de for de, _en, val in _DOW_OPTIONS},
        "en": {val: en for _de, en, val in _DOW_OPTIONS},
    }

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_dow_options(self) -> list[str]:
        idx = 0 if self.ui_language == "de" else 1
        return [o[idx] for o in self._DOW_OPTIONS]

    @rx.var(deps=["scheduler_cron_dow", "ui_language"], auto_deps=False)
    def sched_dow_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._DOW_VAL_TO_LABEL[lang].get(
            self.scheduler_cron_dow, self.scheduler_cron_dow
        )

    def set_scheduler_dow_from_label(self, label: str) -> None:
        self.scheduler_cron_dow = self._DOW_LABEL_TO_VAL.get(label, "*")

    # ── Month dropdown ────────────────────────────────────────

    _MONTH_OPTIONS: list[tuple[str, str, str]] = [
        ("Jeden", "Every", "*"),
        ("Januar", "January", "1"),
        ("Februar", "February", "2"),
        ("März", "March", "3"),
        ("April", "April", "4"),
        ("Mai", "May", "5"),
        ("Juni", "June", "6"),
        ("Juli", "July", "7"),
        ("August", "August", "8"),
        ("September", "September", "9"),
        ("Oktober", "October", "10"),
        ("November", "November", "11"),
        ("Dezember", "December", "12"),
    ]
    _MONTH_LABEL_TO_VAL: dict[str, str] = {
        label: val for de, en, val in _MONTH_OPTIONS for label in (de, en)
    }
    _MONTH_VAL_TO_LABEL: dict[str, dict[str, str]] = {
        "de": {val: de for de, _en, val in _MONTH_OPTIONS},
        "en": {val: en for _de, en, val in _MONTH_OPTIONS},
    }

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_month_options(self) -> list[str]:
        idx = 0 if self.ui_language == "de" else 1
        return [o[idx] for o in self._MONTH_OPTIONS]

    @rx.var(deps=["scheduler_cron_month", "ui_language"], auto_deps=False)
    def sched_month_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._MONTH_VAL_TO_LABEL[lang].get(
            self.scheduler_cron_month, self.scheduler_cron_month
        )

    def set_scheduler_month_from_label(self, label: str) -> None:
        self.scheduler_cron_month = self._MONTH_LABEL_TO_VAL.get(label, "*")

    # ── Interval unit i18n ─────────────────────────────────────

    _UNIT_MAP: dict[str, str] = {
        "Minuten": "minutes", "Minutes": "minutes",
        "Stunden": "hours", "Hours": "hours",
        "Tage": "days", "Days": "days",
    }
    _UNIT_DISPLAY: dict[str, dict[str, str]] = {
        "de": {"minutes": "Minuten", "hours": "Stunden", "days": "Tage"},
        "en": {"minutes": "Minutes", "hours": "Hours", "days": "Days"},
    }

    @rx.var(deps=["ui_language"], auto_deps=False)
    def sched_interval_unit_options(self) -> list[str]:
        lang = "de" if self.ui_language == "de" else "en"
        return list(self._UNIT_DISPLAY[lang].values())

    @rx.var(deps=["scheduler_interval_unit", "ui_language"], auto_deps=False)
    def sched_interval_unit_display(self) -> str:
        lang = "de" if self.ui_language == "de" else "en"
        return self._UNIT_DISPLAY[lang].get(self.scheduler_interval_unit, self.scheduler_interval_unit)

    def set_scheduler_interval_unit_from_label(self, label: str) -> None:
        self.scheduler_interval_unit = self._UNIT_MAP.get(label, "minutes")

    # ── Compose / decompose schedule expression ────────────────

    def _compose_schedule_expr(self) -> str:
        """Build schedule_expr from the structured fields."""
        if self.scheduler_edit_type == "cron":
            return (
                f"{self.scheduler_cron_min} {self.scheduler_cron_hour} "
                f"{self.scheduler_cron_dom} {self.scheduler_cron_month} "
                f"{self.scheduler_cron_dow}"
            )
        if self.scheduler_edit_type == "interval":
            multiplier = {"minutes": 60, "hours": 3600, "days": 86400}
            try:
                val = int(self.scheduler_interval_value)
            except ValueError:
                val = 60
            return str(val * multiplier.get(self.scheduler_interval_unit, 60))
        if self.scheduler_edit_type == "once":
            date = self.scheduler_once_date or "2026-01-01"
            time = self.scheduler_once_time or "00:00"
            return f"{date}T{time}:00"
        return self.scheduler_edit_expr

    def _decompose_schedule_expr(self) -> None:
        """Parse schedule_expr into structured fields."""
        expr = self.scheduler_edit_expr.strip()
        if self.scheduler_edit_type == "cron":
            parts = expr.split()
            if len(parts) >= 5:
                self.scheduler_cron_min = parts[0]
                self.scheduler_cron_hour = parts[1]
                self.scheduler_cron_dom = parts[2]
                self.scheduler_cron_month = parts[3]
                self.scheduler_cron_dow = parts[4]
        elif self.scheduler_edit_type == "interval":
            try:
                seconds = int(expr)
                if seconds >= 86400 and seconds % 86400 == 0:
                    self.scheduler_interval_value = str(seconds // 86400)
                    self.scheduler_interval_unit = "days"
                elif seconds >= 3600 and seconds % 3600 == 0:
                    self.scheduler_interval_value = str(seconds // 3600)
                    self.scheduler_interval_unit = "hours"
                else:
                    self.scheduler_interval_value = str(max(1, seconds // 60))
                    self.scheduler_interval_unit = "minutes"
            except ValueError:
                self.scheduler_interval_value = "60"
                self.scheduler_interval_unit = "minutes"
        elif self.scheduler_edit_type == "once":
            if "T" in expr:
                date_part, time_part = expr.split("T", 1)
                self.scheduler_once_date = date_part
                self.scheduler_once_time = time_part[:5]

    @rx.var(auto_deps=False)
    def scheduler_agent_options(self) -> list[str]:
        """Agent display labels for scheduler dropdown."""
        from ..lib.agent_config import load_agents_raw
        agents = load_agents_raw()
        return [
            f"{data['emoji']} {data['display_name']}"
            for aid, data in agents.items() if aid != "vision"
        ]

    @rx.var(deps=["scheduler_edit_agent"], auto_deps=False)
    def scheduler_edit_agent_display(self) -> str:
        """Display label for currently selected agent in scheduler edit."""
        from ..lib.agent_config import get_agent_config
        cfg = get_agent_config(self.scheduler_edit_agent)
        return f"{cfg.emoji} {cfg.display_name}" if cfg else self.scheduler_edit_agent

    def set_scheduler_edit_agent_from_label(self, label: str) -> None:
        """Resolve agent display label back to ID."""
        from ..lib.agent_config import load_agents_raw
        for aid, data in load_agents_raw().items():
            if f"{data['emoji']} {data['display_name']}" == label:
                self.scheduler_edit_agent = aid
                return

    def cancel_scheduler_edit(self) -> None:
        """Close the edit form."""
        self.scheduler_edit_id = ""

    def save_scheduler_job(self) -> None:
        """Save (create or update) a scheduler job."""
        from ..lib.scheduler import get_job_store

        store = get_job_store()
        expr = self._compose_schedule_expr()
        payload = {
            "message": self.scheduler_edit_message,
            "agent": self.scheduler_edit_agent,
            "delivery": self.scheduler_edit_delivery,
        }
        if self.scheduler_edit_delivery == "announce":
            if self.scheduler_edit_channel:
                payload["channel"] = self.scheduler_edit_channel
            if self.scheduler_edit_recipient:
                payload["recipient"] = self.scheduler_edit_recipient
        elif self.scheduler_edit_delivery == "webhook":
            if self.scheduler_edit_webhook_url:
                payload["webhook_url"] = self.scheduler_edit_webhook_url

        if self.scheduler_edit_id == "new":
            store.add(
                name=self.scheduler_edit_name,
                schedule_type=self.scheduler_edit_type,
                schedule_expr=expr,
                payload=payload,
                max_tier=int(self.scheduler_edit_tier),
            )
        else:
            store.update(
                int(self.scheduler_edit_id),
                name=self.scheduler_edit_name,
                schedule_type=self.scheduler_edit_type,
                schedule_expr=expr,
                payload=payload,
                max_tier=int(self.scheduler_edit_tier),
            )

        self.scheduler_edit_id = ""
        self._load_scheduler_jobs()

    def select_editor_agent(self, label: str):
        """Select an agent from the dropdown by its display label."""
        if label == self._AUTOMATIK_SEPARATOR:
            return  # Ignore separator click
        agent_id = self._agent_id_by_label.get(label, "")
        if agent_id:
            self._load_agent_into_state(agent_id)
            return self._push_editor_dom()

    def _load_agent_into_state(self, agent_id: str) -> None:
        """Load an agent's config into editor state vars (no DOM touch)."""
        self.editor_delete_confirm = ""
        self.editor_emoji_picker_open = False
        self.editor_dirty = False
        self.editor_reset_confirm = False
        self.editor_prompt_lang = self.ui_language  # type: ignore[attr-defined]

        # Automatik-LLM: no AgentConfig — load prompts directly from directory
        if agent_id == "automatik":
            self._load_automatik_into_state()
            return

        from ..lib.agent_config import get_agent_config
        config = get_agent_config(agent_id)
        if config is None:
            return

        self.editor_agent_id = agent_id
        self.editor_display_name = config.display_name
        self.editor_emoji = config.emoji
        self._editor_description = config.description
        self.editor_role = config.role
        self.editor_model = getattr(config, "model", "") or ""
        self.editor_cloud_provider = getattr(config, "cloud_provider", "qwen") or "qwen"
        self.editor_cloud_models = []  # refreshed lazily via the select's on_mount
        self.editor_system_reasoning = bool(config.toggles.get("reasoning", False))
        self.editor_prompt_keys = list(config.prompts.keys())
        # Pick a sensible initial prompt tab — "identity" if available,
        # else the first defined prompt (system-role agents like
        # calibration usually only have "system").
        self.editor_prompt_tab = (
            "identity" if "identity" in config.prompts
            else (self.editor_prompt_keys[0] if self.editor_prompt_keys else "identity")
        )

        # Load tool whitelist — None means all tools allowed
        from ..lib.plugin_registry import discover_tools
        from ..lib.plugin_base import PluginContext
        # Collect all available tool names
        all_tool_names: list[str] = []
        ctx = PluginContext(agent_id=agent_id, lang="de", session_id="", llm_history=[])
        for p in discover_tools():
            if p.is_available():
                for t in p.get_tools(ctx):
                    all_tool_names.append(t.name)
        # Memory tool
        all_tool_names.append("store_memory")
        # Channel tools
        from ..lib.plugin_registry import all_channels
        for ch in all_channels().values():
            if ch.is_configured():
                for t in ch.get_tools(ctx):
                    all_tool_names.append(t.name)

        if config.tools is None:
            # None = all allowed
            self.editor_tools = {name: True for name in all_tool_names}
        else:
            allowed = set(config.tools)
            self.editor_tools = {name: name in allowed for name in all_tool_names}

        # Load TTS settings for this agent — always start with the
        # default engine (config.TTS_DEFAULT_ENGINE).
        self.editor_tts_engine = TTS_DEFAULT_ENGINE  # type: ignore[attr-defined]
        self._load_editor_tts_settings()  # type: ignore[attr-defined]

        self._load_editor_prompt(self.editor_prompt_tab)

    def _load_automatik_into_state(self) -> None:
        """Load Automatik-LLM pseudo-agent into editor (prompts only)."""
        from ..lib.prompt_loader import PROMPTS_DIR

        self.editor_agent_id = "automatik"
        self.editor_display_name = "Automatik-LLM"
        self.editor_emoji = "⚡"
        self._editor_description = "Intent Detection, Routing, Research Decisions"
        self.editor_role = "system"
        self.editor_tools = {}

        # Discover prompt files from both language directories
        prompt_keys: list[str] = []
        seen: set[str] = set()
        for lang in ("de", "en"):
            prompt_dir = PROMPTS_DIR / lang / "automatik"
            if prompt_dir.is_dir():
                for f in sorted(prompt_dir.glob("*.txt")):
                    key = f.stem  # e.g. "intent_detection"
                    if key not in seen:
                        prompt_keys.append(key)
                        seen.add(key)

        self.editor_prompt_keys = prompt_keys
        first_key = prompt_keys[0] if prompt_keys else ""
        self.editor_prompt_tab = first_key
        if first_key:
            self._load_editor_prompt(first_key)

    def _push_editor_dom(self) -> EventSpec:
        """Push current editor state values into DOM fields via JS and store initial state."""
        import json as _json
        name_js = _json.dumps(self.editor_display_name)
        desc_js = _json.dumps(self._editor_description)
        prompt_js = _json.dumps(self._editor_prompt_content)
        return rx.call_script(
            "setTimeout(() => {"
            f" const n = document.getElementById('editor-name'); if (n) n.value = {name_js};"
            f" const d = document.getElementById('editor-description'); if (d) d.value = {desc_js};"
            f" const p = document.getElementById('editor-prompt-textarea'); if (p) p.value = {prompt_js};"
            "}, 50)",
        )

    def _load_editor_prompt(self, prompt_key: str) -> None:
        """Load a prompt file's content into state (for JS population)."""
        from ..lib.prompt_loader import PROMPTS_DIR

        if self.editor_agent_id == "automatik":
            full_path = PROMPTS_DIR / self.editor_prompt_lang / "automatik" / f"{prompt_key}.txt"
        else:
            from ..lib.agent_config import get_agent_config
            config = get_agent_config(self.editor_agent_id)
            if config is None:
                return
            prompt_path = config.prompts.get(prompt_key, "")
            if not prompt_path:
                self._editor_prompt_content = ""
                return
            full_path = PROMPTS_DIR / self.editor_prompt_lang / prompt_path

        if full_path.exists():
            self._editor_prompt_content = full_path.read_text(encoding="utf-8")
        else:
            # Fallback: try the other language (EN-only prompts like intent_detection)
            fallback_lang = "en" if self.editor_prompt_lang == "de" else "de"
            if self.editor_agent_id == "automatik":
                fallback_path = PROMPTS_DIR / fallback_lang / "automatik" / f"{prompt_key}.txt"
            else:
                fallback_path = PROMPTS_DIR / fallback_lang / prompt_path if prompt_path else None  # type: ignore[assignment]
            if fallback_path and fallback_path.exists():
                content = fallback_path.read_text(encoding="utf-8")
                hint = f"[{fallback_lang.upper()} only]\n\n"
                self._editor_prompt_content = hint + content
            else:
                self._editor_prompt_content = ""

    def set_editor_prompt_tab(self, tab: str) -> None:
        """Switch prompt layer tab — load from disk and push to DOM."""
        import json as _json
        self.editor_prompt_tab = tab
        self._load_editor_prompt(tab)
        prompt_js = _json.dumps(self._editor_prompt_content)
        return rx.call_script(  # type: ignore[return-value]
            f"setTimeout(() => {{ const p = document.getElementById('editor-prompt-textarea'); if (p) p.value = {prompt_js}; }}, 50)",
        )

    def set_editor_prompt_lang(self, lang: str) -> None:
        """Switch prompt language — load from disk and push to DOM."""
        import json as _json
        self.editor_prompt_lang = lang
        self._load_editor_prompt(self.editor_prompt_tab)
        prompt_js = _json.dumps(self._editor_prompt_content)
        return rx.call_script(  # type: ignore[return-value]
            f"setTimeout(() => {{ const p = document.getElementById('editor-prompt-textarea'); if (p) p.value = {prompt_js}; }}, 50)",
        )

    def set_editor_emoji(self, value: str) -> None:
        """Update editor emoji field from picker."""
        self.editor_emoji = value
        self.editor_emoji_picker_open = False

    def set_editor_role(self, value: str) -> None:
        """Update editor role field."""
        self.editor_role = value

    def toggle_editor_tool(self, tool_name: str) -> None:
        """Toggle a single tool in the editor whitelist."""
        tools = dict(self.editor_tools)
        tools[tool_name] = not tools.get(tool_name, True)
        self.editor_tools = tools

    def set_all_editor_tools(self, enabled: bool) -> None:
        """Enable or disable all tools at once."""
        self.editor_tools = {name: enabled for name in self.editor_tools}

    def toggle_emoji_picker(self) -> None:
        """Toggle the emoji picker visibility."""
        self.editor_emoji_picker_open = not self.editor_emoji_picker_open

    def _save_editor_prompt_to_disk(self) -> None:
        """Save current prompt content to disk (editor_prompt_lang)."""
        from ..lib.prompt_loader import PROMPTS_DIR

        if not self.editor_agent_id:
            return

        if self.editor_agent_id == "automatik":
            full_path = PROMPTS_DIR / self.editor_prompt_lang / "automatik" / f"{self.editor_prompt_tab}.txt"
        else:
            from ..lib.agent_config import get_agent_config
            config = get_agent_config(self.editor_agent_id)
            if not config:
                return
            prompt_path = config.prompts.get(self.editor_prompt_tab, "")
            if not prompt_path:
                return
            full_path = PROMPTS_DIR / self.editor_prompt_lang / prompt_path

        # Strip fallback language hint if present (e.g. "[EN only]\n\n")
        content = self._editor_prompt_content
        for prefix in ("[EN only]\n\n", "[DE only]\n\n"):
            if content.startswith(prefix):
                content = content[len(prefix):]
                break

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    def save_agent_editor(self, dom_values: str = "{}") -> EventSpec | None:
        """Save agent editor — receives DOM values JSON from UI call_script callback."""
        import json
        from ..lib.agent_config import update_agent, create_agent
        from ..lib.prompt_loader import register_agent_toggles

        self.editor_dirty = False

        try:
            vals = json.loads(dom_values)
        except (json.JSONDecodeError, TypeError):
            vals = {}

        # Sync DOM values into state
        if vals.get("name"):
            self.editor_display_name = vals["name"]
        if vals.get("description") is not None:
            self._editor_description = vals["description"]
        # Always sync prompt (even if empty — user may have cleared it)
        self._editor_prompt_content = vals.get("prompt", self._editor_prompt_content)

        # Build tools whitelist from editor state
        # If all tools are enabled → save as None (= all allowed, no whitelist)
        all_enabled = all(self.editor_tools.values())
        tools_value = None if all_enabled else [
            name for name, enabled in self.editor_tools.items() if enabled
        ]

        if self.editor_agent_id == "automatik":
            # Automatik-LLM: only save prompt files, no agents.json entry
            self._save_editor_prompt_to_disk()
            self.editor_dirty_confirm = False
            self.add_debug("\u2705 Automatik-LLM prompt saved")  # type: ignore[attr-defined]
            return rx.toast.success("Automatik-LLM gespeichert", duration=2000)

        if self.editor_agent_id:
            # Update existing agent metadata
            update_payload: dict = {
                "display_name": self.editor_display_name,
                "emoji": self.editor_emoji,
                "description": self._editor_description,
                "role": self.editor_role,
                "tools": tools_value,
            }
            # System-role agents persist a Cloud model id alongside their config
            # plus their own reasoning toggle (off by default — see toggles spec).
            if self.editor_role == "system" and self.editor_agent_id != "automatik":
                update_payload["model"] = self.editor_model
                update_payload["cloud_provider"] = self.editor_cloud_provider
                update_payload["toggles"] = {
                    "personality": False,
                    "reasoning": self.editor_system_reasoning,
                    "thinking": False,
                }
            update_agent(self.editor_agent_id, update_payload)

            # Save current prompt tab content to file
            self._save_editor_prompt_to_disk()

            # Bump revision so file-IO-backed @rx.var (e.g. calibration_ai_label
            # in CalibrationMixin) re-evaluates and reflects the new value.
            self._agents_json_revision = self._agents_json_revision + 1  # type: ignore[attr-defined]

            self.add_debug(  # type: ignore[attr-defined]
                f"\u2705 Agent '{self.editor_display_name}' saved"
            )
        else:
            # Create new agent
            agent_id = vals.get("agent_id", "").strip().lower().replace(" ", "_")
            if not agent_id:
                self.add_debug("\u26a0\ufe0f Agent-ID is required")  # type: ignore[attr-defined]
                return None

            new_config = create_agent(
                agent_id=agent_id,
                display_name=self.editor_display_name,
                emoji=self.editor_emoji,
                description=self._editor_description,
                role=self.editor_role,
            )
            register_agent_toggles(agent_id, new_config.toggles)
            self.ensure_all_agents_have_tts()  # type: ignore[attr-defined]
            self.add_debug(  # type: ignore[attr-defined]
                f"\u2705 Agent '{self.editor_display_name}' created"
            )
            # Select the newly created agent in the dropdown
            self._refresh_agent_dropdown()
            self._load_agent_into_state(agent_id)
            return self._push_editor_dom()

        # Existing agent saved — refresh dropdown, show toast, stay open
        self._refresh_agent_dropdown()
        self.editor_dirty_confirm = False
        return rx.toast.success(f"{self.editor_display_name} gespeichert", duration=2000)

    def delete_agent_editor(self, agent_id: str) -> None:
        """Delete an agent (with confirmation)."""
        if self.editor_delete_confirm != agent_id:
            # First click: ask for confirmation
            self.editor_delete_confirm = agent_id
            return

        # Second click: actually delete
        from ..lib.agent_config import delete_agent
        from ..lib.prompt_loader import unregister_agent_toggles

        try:
            delete_agent(agent_id)
            unregister_agent_toggles(agent_id)
            self.ensure_all_agents_have_tts()  # type: ignore[attr-defined]
            self.add_debug(f"\U0001f5d1\ufe0f Agent '{agent_id}' deleted")  # type: ignore[attr-defined]
        except ValueError as e:
            self.add_debug(f"\u26a0\ufe0f {e}")  # type: ignore[attr-defined]

        self.editor_delete_confirm = ""
        self._refresh_agent_dropdown()

        # Select first remaining agent
        from ..lib.agent_config import load_agents_raw
        raw = load_agents_raw()
        if raw:
            self._select_agent_for_editor(next(iter(raw)))

    def clear_agent_memory(self, agent_id: str) -> EventSpec | None:
        """Clear an agent's long-term memory (confirm on first click, delete on second)."""
        import reflex as rx
        from ..lib.agent_memory import get_agent_memory

        if self.editor_memory_confirm != agent_id:
            self.editor_memory_confirm = agent_id
            return None

        self.editor_memory_confirm = ""

        memory = get_agent_memory()
        if not memory:
            return rx.toast.error("AgentMemory unavailable", duration=3000, position="top-center")

        # Get display name for logs/toasts
        from ..lib.multi_agent import get_agent_config
        agent_cfg = get_agent_config(agent_id)
        agent_name = agent_cfg.display_name if agent_cfg else agent_id.capitalize()

        try:
            col = memory._collection(agent_id)
            count = col.count()
            if count == 0:
                return rx.toast.info(f"{agent_name}: memory already empty", duration=3000, position="top-center")
            all_ids = col.get(include=[])["ids"]
            col.delete(ids=all_ids)
            self.add_debug(f"🗑️ {agent_name}: {count} memories cleared")  # type: ignore[attr-defined]
            return rx.toast.success(f"{agent_name}: {count} memories cleared", duration=3000, position="top-center")
        except Exception as e:
            return rx.toast.error(f"Error: {e}", duration=3000, position="top-center")

    # Reset confirm state
    editor_reset_confirm: bool = False

    def request_reset_editor_prompt(self) -> None:
        """First click on reset — show confirmation."""
        self.editor_reset_confirm = True

    def confirm_reset_editor_prompt(self) -> EventSpec:
        """Second click — actually reset prompt to file on disk."""
        self.editor_reset_confirm = False
        self.editor_dirty = False
        import json as _json
        self._load_editor_prompt(self.editor_prompt_tab)
        prompt_js = _json.dumps(self._editor_prompt_content)
        return rx.call_script(
            f"setTimeout(() => {{ const p = document.getElementById('editor-prompt-textarea'); if (p) p.value = {prompt_js}; }}, 50)",
        )

    def start_new_agent(self) -> None:
        """Switch editor to 'create new agent' mode (empty form)."""
        self.editor_agent_id = ""
        self.editor_display_name = ""
        self.editor_emoji = "\U0001f916"
        self._editor_description = ""
        self.editor_role = "custom"
        self._editor_new_agent_id = ""
        self.editor_prompt_tab = "identity"
        self._editor_prompt_content = ""
        self.editor_prompt_keys = []
        self.editor_delete_confirm = ""
        # Clear DOM fields
        return rx.call_script(  # type: ignore[return-value]
            "setTimeout(() => {"
            " ['editor-name','editor-description','editor-agent-id','editor-prompt-textarea']"
            "  .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });"
            "}, 50)",
        )

