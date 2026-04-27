"""Scheduler plugin — create, list, and delete scheduled jobs via chat.

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
from ....lib.plugin_base import PluginContext
from ....lib.prompt_loader import load_prompt


@dataclass
class SchedulerPlugin:
    name: str = "scheduler"
    display_name: str = "Scheduler"
    description: str = "Plant Aufgaben und Reminder zu bestimmten Uhrzeiten oder als wiederkehrende Cron-Jobs."

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        from ....lib.logging_utils import log_message

        async def _create(
            name: str,
            schedule_type: str,
            schedule_expr: str,
            message: str,
            agent: str = "aifred",
            delivery: str = "log",
            channel: str = "",
            recipient: str = "",
            webhook_url: str = "",
        ) -> str:
            """Create a new scheduled job."""
            from ....lib.scheduler import get_job_store

            if schedule_type not in ("cron", "interval", "once"):
                return json.dumps({"error": f"Invalid schedule_type: {schedule_type}. Use: cron, interval, once"})

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

            # Jobs run as cron — cap at cron default tier, not the creator's tier
            from ....lib.security import DEFAULT_TIER_BY_SOURCE
            job_tier = DEFAULT_TIER_BY_SOURCE.get("cron", 1)

            job = store.add(
                name=name,
                schedule_type=schedule_type,
                schedule_expr=schedule_expr,
                payload=payload,
                max_tier=job_tier,
            )
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
                        "delivery": j.payload.get("delivery", "log"),
                    }
                    for j in jobs
                ]
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
                    "Create a scheduled job. AIfred will execute the message at the scheduled time "
                    "and deliver the result via the specified mode. "
                    "Schedule types: 'cron' (e.g. '0 8 * * *' = daily 8am), "
                    "'interval' (seconds, e.g. '3600' = every hour), "
                    "'once' (ISO timestamp, e.g. '2026-03-30T10:00:00'). "
                    "Delivery modes: 'log' (default), 'announce' (send to channel), "
                    "'review' (show in UI), 'webhook' (HTTP POST)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short descriptive name for the job",
                        },
                        "schedule_type": {
                            "type": "string",
                            "enum": ["cron", "interval", "once"],
                            "description": "Type of schedule",
                        },
                        "schedule_expr": {
                            "type": "string",
                            "description": "Cron expression, interval in seconds, or ISO timestamp",
                        },
                        "message": {
                            "type": "string",
                            "description": "The prompt/message AIfred will process at the scheduled time",
                        },
                        "agent": {
                            "type": "string",
                            "description": "Agent to use (default: aifred)",
                            "default": "aifred",
                        },
                        "delivery": {
                            "type": "string",
                            "enum": ["log", "announce", "review", "webhook"],
                            "description": "How to deliver the result (default: log)",
                            "default": "log",
                        },
                        "channel": {
                            "type": "string",
                            "description": "Target channel for 'announce' delivery (e.g. telegram, discord, email)",
                        },
                        "recipient": {
                            "type": "string",
                            "description": "Recipient for 'announce' delivery (email address, etc.)",
                        },
                        "webhook_url": {
                            "type": "string",
                            "description": "URL for 'webhook' delivery",
                        },
                    },
                    "required": ["name", "schedule_type", "schedule_expr", "message"],
                },
                executor=_create,
            ),
            Tool(
                name="scheduler_list",
                tier=TIER_READONLY,
                description="List all scheduled jobs with their status, next run time, and delivery mode.",
                parameters={
                    "type": "object",
                    "properties": {},
                },
                executor=_list,
            ),
            Tool(
                name="scheduler_delete",
                tier=TIER_WRITE_DATA,
                description="Delete a scheduled job by its ID. Use scheduler_list to find the ID first.",
                parameters={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "integer",
                            "description": "ID of the job to delete",
                        },
                    },
                    "required": ["job_id"],
                },
                executor=_delete,
            ),
        ]

    def get_prompt_instructions(self, lang: str) -> str:
        return load_prompt("shared/scheduler_instructions", lang=lang)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "scheduler_create":
            return f"Creating job: {tool_args.get('name', '')}"
        if tool_name == "scheduler_delete":
            return f"Deleting job {tool_args.get('job_id', '')}"
        if tool_name == "scheduler_list":
            return "Loading scheduled jobs..."
        return ""


plugin = SchedulerPlugin()
