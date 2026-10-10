"""Scheduler plugin — create, list, update, and delete scheduled jobs via chat.

Allows the user to say things like:
- "Fasse jeden Morgen um 7 meine E-Mails zusammen und schick es an Telegram"
- "Erinnere mich morgen um 10 an den Arzttermin"
- "Zeig mir meine geplanten Jobs"
- "Loesche Job 3"
"""

import json
from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.security import TIER_WRITE_DATA, TIER_READONLY
from ....lib.plugin_base import PluginContext, load_tool_description, load_tool_parameters


@dataclass
class SchedulerPlugin:
    # MUST equal the folder name (see plugin_registry) — otherwise the plugin
    # is invisible to the Plugin-Manager UI (no gear/lightbulb).
    name: str = "scheduler_tool"

    def is_available(self) -> bool:
        return True

    # Eine Wahrheit für beide Wertemengen: Executor-Validierung UND
    # JSON-Schema-Enums leiten sich hieraus ab (der SQLite-CHECK in
    # lib/scheduler.py bleibt als DB-seitige Integritätsgrenze).
    _SCHEDULE_TYPES = ("cron", "interval", "once")
    _DELIVERY_MODES = ("review", "announce", "webhook")
    # Payload fields shown by list and changed by update — one place.
    _PAYLOAD_FIELDS = ("message", "agent", "delivery", "channel", "recipient", "webhook_url")

    def _invalid_choice(self, schedule_type: str, delivery: str) -> str | None:
        """Error JSON for an unknown schedule type or delivery mode, else None."""
        if schedule_type not in self._SCHEDULE_TYPES:
            return json.dumps({"error": f"Invalid schedule_type: {schedule_type}. Use: {', '.join(self._SCHEDULE_TYPES)}"})
        if delivery not in self._DELIVERY_MODES:
            return json.dumps({"error": f"Invalid delivery: {delivery}. Use: {', '.join(self._DELIVERY_MODES)}"})
        return None

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from ....lib.logging_utils import log_message

        async def _create(
            name: str,
            schedule_type: str,
            schedule_expr: str,
            message: str,
            agent: str = "aifred",
            delivery: str = "review",
            channel: str = "",
            recipient: str = "",
            webhook_url: str = "",
        ) -> str:
            """Create a new scheduled job."""
            from ....lib.scheduler import get_job_store

            error = self._invalid_choice(schedule_type, delivery)
            if error:
                return error

            store = get_job_store()
            payload: dict[str, Any] = {
                "message": message,
                "agent": agent,
                "delivery": delivery,
            }
            if channel:
                payload["channel"] = channel
            if recipient:
                payload["recipient"] = recipient
            if webhook_url:
                payload["webhook_url"] = webhook_url

            # Jobs laufen unbeaufsichtigt (channel="scheduler") — Cap auf
            # TIER_COMMUNICATE, nicht auf das Tier des Erstellers. Bewusst
            # über dem Scheduler-Default TIER_READONLY: per Chat erstellte
            # Jobs sollen z.B. announce/Webhook nutzen können. Enforcement
            # läuft über den metadata["max_tier"]-Override in security.py.
            from ....lib.security import TIER_COMMUNICATE
            job_tier = TIER_COMMUNICATE

            job = store.add(
                name=name,
                schedule_type=schedule_type,
                schedule_expr=schedule_expr,
                payload=payload,
                max_tier=job_tier,
            )
            if job.next_run is None:
                # Fail-loud: without next_run the job would NEVER fire
                # (get_due_jobs filters next_run IS NOT NULL) — an invalid
                # cron expression must not be reported as success.
                store.delete(job.job_id)
                log_message(f"Scheduler: job '{name}' rejected — invalid {schedule_type} expression '{schedule_expr}'", "warning")
                return json.dumps({
                    "error": f"Invalid {schedule_type} expression: '{schedule_expr}' — job not created"
                })
            log_message(f"Scheduler: job '{name}' created (id={job.job_id}, next={job.next_run})")
            return json.dumps({
                "success": True,
                "job_id": job.job_id,
                "name": job.name,
                "schedule_type": job.schedule_type,
                "schedule_expr": job.schedule_expr,
                "next_run": job.next_run,
                "delivery": delivery,
            })

        async def _list() -> str:
            """List all scheduled jobs."""
            from ....lib.scheduler import get_job_store
            store = get_job_store()
            jobs = store.list_all()
            if not jobs:
                return json.dumps({"total_count": 0, "jobs": [], "message": "No scheduled jobs"})
            return json.dumps({
                "total_count": len(jobs),
                "jobs": [
                    {
                        "job_id": j.job_id,
                        "name": j.name,
                        "type": j.schedule_type,
                        "expr": j.schedule_expr,
                        "enabled": j.enabled,
                        "next_run": j.next_run,
                        "last_run": j.last_run,
                        "delivery": j.payload.get("delivery", "review"),
                        **{k: j.payload[k] for k in self._PAYLOAD_FIELDS
                           if k != "delivery" and j.payload.get(k)},
                    }
                    for j in jobs
                ]
            })

        async def _update(
            job_id: int,
            name: str = "",
            schedule_type: str = "",
            schedule_expr: str = "",
            message: str = "",
            agent: str = "",
            delivery: str = "",
            channel: str = "",
            recipient: str = "",
            webhook_url: str = "",
            enabled: bool | None = None,
        ) -> str:
            """Change a scheduled job in place; empty arguments stay unchanged."""
            from ....lib.scheduler import get_job_store

            store = get_job_store()
            job = store.get(job_id)
            if not job:
                return json.dumps({"error": f"Job {job_id} not found"})
            new_type = schedule_type or job.schedule_type
            new_delivery = delivery or job.payload.get("delivery", "review")
            error = self._invalid_choice(new_type, new_delivery)
            if error:
                return error

            changes = {
                "message": message, "agent": agent, "delivery": delivery,
                "channel": channel, "recipient": recipient, "webhook_url": webhook_url,
            }
            payload = {**job.payload, **{k: v for k, v in changes.items() if v}}
            try:
                updated = store.update(
                    job_id, name or job.name, new_type,
                    schedule_expr or job.schedule_expr, payload, job.max_tier,
                )
            except ValueError as exc:
                return json.dumps({"error": f"{exc} — job {job_id} unchanged"})
            if updated is None:
                return json.dumps({"error": f"Job {job_id} not found"})
            # Enable after update: update() recalculates next_run from now, so a
            # re-enabled job does not fire for a run that fell due while it was off.
            if enabled is not None:
                store.enable(job_id, enabled)
                updated.enabled = enabled
            log_message(f"Scheduler: job '{updated.name}' (id={job_id}) updated")
            return json.dumps({
                "success": True,
                "job_id": updated.job_id,
                "name": updated.name,
                "schedule_type": updated.schedule_type,
                "schedule_expr": updated.schedule_expr,
                "enabled": updated.enabled,
                "next_run": updated.next_run,
                "delivery": updated.payload.get("delivery", "review"),
            })

        async def _delete(job_id: int) -> str:
            """Delete a scheduled job."""
            from ....lib.scheduler import get_job_store
            store = get_job_store()
            job = store.get(job_id)
            if not job:
                return json.dumps({"error": f"Job {job_id} not found"})
            name = job.name
            store.delete(job_id)
            log_message(f"Scheduler: job '{name}' (id={job_id}) deleted")
            return json.dumps({"success": True, "deleted": job_id, "name": name})

        return [
            Tool(
                name="scheduler_create",
                tier=TIER_WRITE_DATA,
                description=(
                    load_tool_description(__file__, "scheduler_create")
                ),
                parameters=load_tool_parameters(__file__, "scheduler_create", enums={"schedule_type": list(self._SCHEDULE_TYPES), "delivery": list(self._DELIVERY_MODES)}),
                executor=_create,
            ),
            Tool(
                name="scheduler_list",
                tier=TIER_READONLY,
                description=load_tool_description(__file__, "scheduler_list"),
                parameters=load_tool_parameters(__file__, "scheduler_list"),
                executor=_list,
            ),
            Tool(
                name="scheduler_update",
                tier=TIER_WRITE_DATA,
                description=load_tool_description(__file__, "scheduler_update"),
                parameters=load_tool_parameters(__file__, "scheduler_update", enums={"schedule_type": list(self._SCHEDULE_TYPES), "delivery": list(self._DELIVERY_MODES)}),
                executor=_update,
            ),
            Tool(
                name="scheduler_delete",
                tier=TIER_WRITE_DATA,
                description=load_tool_description(__file__, "scheduler_delete"),
                parameters=load_tool_parameters(__file__, "scheduler_delete"),
                executor=_delete,
            ),
        ]

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        # Kein Hardcoding — atomare Fragmente in prompts/<de|en>/ beim Plugin.
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        from ....lib.i18n import t
        if tool_name == "scheduler_create":
            return t("tool_scheduler_create", lang=lang, name=tool_args.get("name", ""))
        if tool_name == "scheduler_update":
            return t("tool_scheduler_update", lang=lang, job_id=tool_args.get("job_id", ""))
        if tool_name == "scheduler_delete":
            return t("tool_scheduler_delete", lang=lang, job_id=tool_args.get("job_id", ""))
        if tool_name == "scheduler_list":
            return t("tool_scheduler_list", lang=lang)
        return ""


plugin = SchedulerPlugin()
