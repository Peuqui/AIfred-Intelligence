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
from ....lib.scheduler import DELIVERY_MODES, SCHEDULE_TYPES


@dataclass
class SchedulerPlugin:
    # MUST equal the folder name (see plugin_registry) — otherwise the plugin
    # is invisible to the Plugin-Manager UI (no gear/lightbulb).
    name: str = "scheduler_tool"

    def is_available(self) -> bool:
        return True

    # Payload fields shown by list and changed by update — one place. What a job may be
    # (schedule types, delivery modes, the fixed-text rule) is lib/scheduler.py's: job_problem.
    _PAYLOAD_FIELDS = (
        "message", "agent", "delivery", "channel", "recipient", "webhook_url", "fixed_text", "audio_type",
    )

    @staticmethod
    def _problem(schedule_type: str, payload: dict[str, Any], ends_at: str = "") -> str | None:
        """Error JSON for a job lib/scheduler.py refuses, else None."""
        from ....lib.scheduler import job_problem

        problem = job_problem(schedule_type, payload, ends_at)
        return json.dumps({"error": str(problem)}) if problem else None

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from ....lib.logging_utils import log_message
        from ....lib.tool_args import InvalidValue, parse_bool

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
            fixed_text: bool = False,
            audio_type: str = "",
            end_date: str = "",
        ) -> str:
            """Create a new scheduled job."""
            from ....lib.scheduler import get_job_store

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
            if audio_type:
                payload["audio_type"] = audio_type
            try:
                if parse_bool(fixed_text, "fixed_text"):
                    payload["fixed_text"] = True
            except InvalidValue as exc:
                return json.dumps({"error": str(exc)})
            error = self._problem(schedule_type, payload, end_date)
            if error:
                return error

            # Jobs laufen unbeaufsichtigt (channel="scheduler") — Cap auf
            # TIER_COMMUNICATE, nicht auf das Tier des Erstellers. Bewusst
            # über dem Scheduler-Default TIER_READONLY: per Chat erstellte
            # Jobs sollen z.B. announce/Webhook nutzen können. Enforcement
            # läuft über den metadata["max_tier"]-Override in security.py.
            from ....lib.security import TIER_COMMUNICATE
            job_tier = TIER_COMMUNICATE

            try:
                job = store.add(
                    name=name,
                    schedule_type=schedule_type,
                    schedule_expr=schedule_expr,
                    payload=payload,
                    max_tier=job_tier,
                    ends_at=end_date,
                )
            except ValueError as exc:
                # Fail-loud: without next_run the job would NEVER fire — an invalid
                # cron expression must not be reported as success.
                log_message(f"Scheduler: job '{name}' rejected — {exc}", "warning")
                return json.dumps({"error": f"{exc} — job not created"})
            log_message(f"Scheduler: job '{name}' created (id={job.job_id}, next={job.next_run})")
            return json.dumps({
                "success": True,
                "job_id": job.job_id,
                "name": job.name,
                "schedule_type": job.schedule_type,
                "schedule_expr": job.schedule_expr,
                "next_run": job.next_run,
                "end_date": job.ends_at,
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
                        "end_date": j.ends_at,
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
            fixed_text: bool | None = None,
            audio_type: str = "",
            end_date: str | None = None,
        ) -> str:
            """Change a scheduled job in place; empty arguments stay unchanged (end_date: "" removes the end)."""
            from ....lib.scheduler import get_job_store

            store = get_job_store()
            job = store.get(job_id)
            if not job:
                return json.dumps({"error": f"Job {job_id} not found"})
            new_type = schedule_type or job.schedule_type

            changes = {
                "message": message, "agent": agent, "delivery": delivery,
                "channel": channel, "recipient": recipient, "webhook_url": webhook_url,
                "audio_type": audio_type,
            }
            payload = {**job.payload, **{k: v for k, v in changes.items() if v}}
            try:
                if fixed_text is not None:
                    payload["fixed_text"] = parse_bool(fixed_text, "fixed_text")
            except InvalidValue as exc:
                return json.dumps({"error": str(exc)})
            new_end = job.ends_at if end_date is None else end_date
            error = self._problem(new_type, payload, new_end)
            if error:
                return error
            try:
                updated = store.update(
                    job_id, name or job.name, new_type,
                    schedule_expr or job.schedule_expr, payload, job.max_tier, new_end,
                )
            except ValueError as exc:
                return json.dumps({"error": f"{exc} — job {job_id} unchanged"})
            if updated is None:
                return json.dumps({"error": f"Job {job_id} not found"})
            # Enable after update: update() recalculates next_run from now, so a
            # re-enabled job does not fire for a run that fell due while it was off.
            if enabled is not None:
                try:
                    store.enable(job_id, enabled)
                except ValueError as exc:   # the schedule has run to its end
                    return json.dumps({"error": f"{exc} — job {job_id} changed but not switched on"})
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
                "end_date": updated.ends_at,
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
                parameters=load_tool_parameters(__file__, "scheduler_create", enums={"schedule_type": list(SCHEDULE_TYPES), "delivery": list(DELIVERY_MODES)}),
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
                parameters=load_tool_parameters(__file__, "scheduler_update", enums={"schedule_type": list(SCHEDULE_TYPES), "delivery": list(DELIVERY_MODES)}),
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
