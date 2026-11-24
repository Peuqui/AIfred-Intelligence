"""Horarium plugin — create, list, update, and delete scheduled jobs via chat.

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
from ....lib.security import JOB_DEFAULT_TIER, TIER_ADMIN, TIER_READONLY, TIER_WRITE_DATA
from ....lib.plugin_base import BackgroundPart, PluginContext, load_tool_description, load_tool_parameters
from ....lib.scheduler import DELIVERY_MODES, SCHEDULE_TYPES
from ....lib.task_plan import (
    TaskPlan,
    parts_with_added,
    parts_with_replaced,
    parts_without,
    plan_parameters_schema,
)
from ....lib.user_error import Invalid


async def _not_chosen_by_owner(recipient: str, webhook_url: str, ctx: PluginContext) -> str:
    """The error for a mail address or webhook the owner did not choose, empty when both are theirs. A job delivers
    later with nobody watching, so an instruction injected into what the model read (a web page, a mail) must not
    set where its results go: an address counts when the owner wrote it in this conversation
    (security.named_by_owner) or keeps it in an address book (lib.address_books); a webhook only when written."""
    import asyncio

    from ....lib.address_books import known_mail_address
    from ....lib.security import extract_sender_email, named_by_owner

    address = extract_sender_email(recipient) if "@" in recipient else ""
    if address and not named_by_owner(address, ctx) and not await asyncio.to_thread(known_mail_address, address):
        return json.dumps({"error": f"recipient {address} was not chosen by the user: it is not in their contacts and "
                                    "they did not write it in this conversation. Ask the user to write the address."})
    if webhook_url and not named_by_owner(webhook_url, ctx):
        return json.dumps({"error": "webhook_url was not written by the user in this conversation. Ask the user "
                                    "to write the URL."})
    return ""


@dataclass
class HorariumPlugin:
    # MUST equal the folder name (see plugin_registry) — otherwise the plugin
    # is invisible to the Plugin-Manager UI (no gear/lightbulb).
    name: str = "horarium"

    def is_available(self) -> bool:
        return True

    def background_parts(self) -> dict[str, BackgroundPart]:
        """The loop that runs the jobs: while the plugin is off, no job starts."""
        from ....lib.scheduler import scheduler_loop

        return {"scheduler": BackgroundPart(scheduler_loop, in_hub=True)}

    def on_switched_on(self) -> None:
        """Appointments that fell into the time off lapse: nothing is made up."""
        from ....lib.scheduler import get_job_store

        get_job_store().lapse_missed_runs()

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

    @staticmethod
    def _parameters(tool_name: str) -> dict[str, Any]:
        """The tool's schema from its params file, plus the plan fields from the one description every tool with a plan
        shares (lib/task_plan.py). In update they replace what the job has; the part operations take one part as
        the plan describes it."""
        schema = load_tool_parameters(
            __file__, tool_name, enums={"schedule_type": list(SCHEDULE_TYPES), "delivery": list(DELIVERY_MODES)},
        )
        plan = plan_parameters_schema()
        properties = schema["properties"]
        if tool_name == "horarium_create":
            properties |= plan
        else:
            properties |= {key: plan[key] for key in ("frame", "columns")}
            for operation in ("part_add", "part_update"):
                properties[operation] = {**plan["parts"]["items"], "description": properties[operation]["description"]}
        return schema

    @staticmethod
    def _plan_summary(payload: dict[str, Any]) -> dict[str, Any]:
        """What list shows of a job's plan: all of it (frame, the parts with task and positions, columns), so a
        part_update can send the part back with only the wanted change. Only what the job has."""
        plan = TaskPlan.from_payload(payload).to_payload()
        return {key: plan[key] for key in ("frame", "parts", "columns") if plan.get(key)}

    @staticmethod
    def _with_plan_changes(
        payload: dict[str, Any], frame: str | None, columns: list[Any] | None,
        part_add: dict[str, Any] | None, part_update: dict[str, Any] | None, part_remove: str,
    ) -> dict[str, Any]:
        """The payload with the plan operations of an update applied in a fixed order (frame, columns, remove, update,
        add); ``Invalid`` for a part id that exists or is unknown. The result is checked as a whole afterwards
        (``_problem``): no operation checks half of it."""
        changed = dict(payload)
        if frame is not None:
            changed["frame"] = frame
        if columns is not None:
            changed["columns"] = columns
        if part_remove:
            changed["parts"] = parts_without(changed, part_remove)
        if part_update is not None:
            changed["parts"] = parts_with_replaced(changed, part_update)
        if part_add is not None:
            changed["parts"] = parts_with_added(changed, part_add)
        return changed

    @staticmethod
    def _job_tier(requested: int | None, creator_tier: int, default: int) -> int | str:
        """The tier a job runs with: the default, or the requested one up to the creator's own tier; else error JSON."""
        if requested is None:
            return default
        if not TIER_READONLY <= requested <= min(creator_tier, TIER_ADMIN):
            return json.dumps({"error": f"max_tier {requested} is outside what you may grant (0-{creator_tier})"})
        return requested

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from ....lib.logging_utils import log_message
        from ....lib.tool_args import InvalidValue, parse_bool, parse_id

        def checked_job_id(raw: Any) -> int | str:
            """The job id of a tool call as a whole number (the model may send "41"), or the error JSON for the model:
            one parse for every tool that takes a job id, so the lock below compares like with like (the run's metadata
            carry the id as int, scheduler._execute_job)."""
            try:
                return parse_id(raw, "job_id")
            except InvalidValue as exc:
                return json.dumps({"error": str(exc)})

        def own_job(job_id: int) -> bool:
            """Whether this call comes from the run of the job itself: a run proposes changes to its own job in the
            report, the owner approves them in the chat."""
            return ctx.source == "scheduler" and ctx.metadata.get("job_id") == job_id

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
            max_tier: int | None = None,
            frame: str = "",
            parts: list[Any] | None = None,
            columns: list[Any] | None = None,
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
            payload |= {key: value for key, value in
                        {"frame": frame, "parts": parts, "columns": columns}.items() if value}
            try:
                if parse_bool(fixed_text, "fixed_text"):
                    payload["fixed_text"] = True
            except InvalidValue as exc:
                return json.dumps({"error": str(exc)})
            error = self._problem(schedule_type, payload, end_date) or await _not_chosen_by_owner(
                recipient, webhook_url, ctx,
            )
            if error:
                return error

            job_tier = self._job_tier(max_tier, ctx.max_tier, default=min(ctx.max_tier, JOB_DEFAULT_TIER))
            if isinstance(job_tier, str):
                return job_tier
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
                log_message(f"Horarium: job '{name}' rejected — {exc}", "warning")
                return json.dumps({"error": f"{exc} — job not created"})
            log_message(f"Horarium: job '{name}' created (id={job.job_id}, next={job.next_run})")
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
                        **self._plan_summary(j.payload),
                    }
                    for j in jobs
                ]
            })

        async def _update(
            job_id: Any,
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
            max_tier: int | None = None,
            frame: str | None = None,
            columns: list[Any] | None = None,
            part_add: dict[str, Any] | None = None,
            part_update: dict[str, Any] | None = None,
            part_remove: str = "",
        ) -> str:
            """Change a scheduled job in place; empty arguments stay unchanged (end_date: "" removes the end)."""
            from ....lib.scheduler import get_job_store

            job_id = checked_job_id(job_id)
            if isinstance(job_id, str):
                return job_id
            if own_job(job_id):
                return json.dumps({"error": str(Invalid("own_job_locked"))})
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
                payload = self._with_plan_changes(payload, frame, columns, part_add, part_update, part_remove)
            except Invalid as exc:
                return json.dumps({"error": f"{exc} — job {job_id} unchanged"})
            try:
                if fixed_text is not None:
                    payload["fixed_text"] = parse_bool(fixed_text, "fixed_text")
            except InvalidValue as exc:
                return json.dumps({"error": str(exc)})
            new_end = job.ends_at if end_date is None else end_date
            new_tier = self._job_tier(max_tier, ctx.max_tier, default=job.max_tier)
            if isinstance(new_tier, str):
                return new_tier
            error = self._problem(new_type, payload, new_end) or await _not_chosen_by_owner(
                recipient, webhook_url, ctx,
            )
            if error:
                return error
            try:
                updated = store.update(
                    job_id, name or job.name, new_type,
                    schedule_expr or job.schedule_expr, payload, new_tier, new_end,
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
            log_message(f"Horarium: job '{updated.name}' (id={job_id}) updated")
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

        async def _delete(job_id: Any) -> str:
            """Delete a scheduled job with its folder in the workspace."""
            from ....lib.scheduler import delete_job, get_job_store

            job_id = checked_job_id(job_id)
            if isinstance(job_id, str):
                return job_id
            if own_job(job_id):
                return json.dumps({"error": str(Invalid("own_job_locked"))})
            store = get_job_store()
            job = store.get(job_id)
            if not job:
                return json.dumps({"error": f"Job {job_id} not found"})
            name = job.name
            await delete_job(job_id)
            log_message(f"Horarium: job '{name}' (id={job_id}) deleted")
            return json.dumps({"success": True, "deleted": job_id, "name": name})

        return [
            Tool(
                name="horarium_create",
                tier=TIER_WRITE_DATA,
                description=(
                    load_tool_description(__file__, "horarium_create")
                ),
                parameters=self._parameters("horarium_create"),
                executor=_create,
            ),
            Tool(
                name="horarium_list",
                tier=TIER_READONLY,
                description=load_tool_description(__file__, "horarium_list"),
                parameters=load_tool_parameters(__file__, "horarium_list"),
                executor=_list,
            ),
            Tool(
                name="horarium_update",
                tier=TIER_WRITE_DATA,
                description=load_tool_description(__file__, "horarium_update"),
                parameters=self._parameters("horarium_update"),
                executor=_update,
            ),
            Tool(
                name="horarium_delete",
                tier=TIER_WRITE_DATA,
                description=load_tool_description(__file__, "horarium_delete"),
                parameters=load_tool_parameters(__file__, "horarium_delete"),
                executor=_delete,
            ),
        ]

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        # Kein Hardcoding — atomare Fragmente in prompts/<de|en>/ beim Plugin.
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        from ....lib.i18n import t
        if tool_name == "horarium_create":
            return t("tool_horarium_create", lang=lang, name=tool_args.get("name", ""))
        if tool_name == "horarium_update":
            return t("tool_horarium_update", lang=lang, job_id=tool_args.get("job_id", ""))
        if tool_name == "horarium_delete":
            return t("tool_horarium_delete", lang=lang, job_id=tool_args.get("job_id", ""))
        if tool_name == "horarium_list":
            return t("tool_horarium_list", lang=lang)
        return ""


plugin = HorariumPlugin()
