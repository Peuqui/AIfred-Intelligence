"""Scheduled jobs for the Vue app's jobs page (lib/scheduler.py): the jobs with their next run, one created or
changed (the rules of lib/scheduler.job_problem, the same the agents' scheduler tool follows; an expression
without a next run is refused), switched on or off, deleted, its last runs, and the next run of an expression
before it is saved. The page composes the expression; the server takes nothing it would have to guess."""

from typing import Literal

from fastapi import Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from ..scheduler import Job
from ..security import JOB_DEFAULT_TIER, TIER_ADMIN, TIER_READONLY
from ..user_error import Invalid, Missing
from .app import api_app
from .schemas import ChoiceOption


class CronPreset(BaseModel):
    key: str = Field(description="Its i18n key")
    minute: str
    hour: str
    dom: str
    month: str
    dow: str


class JobOptions(BaseModel):
    schedule_types: list[str]
    delivery_modes: list[str]
    audio_types: list[str]
    tiers: list[int]
    default_tier: int = Field(description="The tier a new job starts with")
    cron_presets: list[CronPreset]
    agents: list[ChoiceOption] = Field(description="The selectable agents (none switched off)")
    channels: list[ChoiceOption] = Field(description="The channel plugins an announcement can go through")
    rooms_channels: list[str] = Field(description="Channels that announce in rooms: they take an audio type")


class JobRow(BaseModel):
    job_id: int
    name: str
    schedule_type: str
    schedule_expr: str
    message: str
    agent: str
    delivery: str
    channel: str
    recipient: str
    webhook_url: str
    fixed_text: bool
    audio_type: str
    max_tier: int
    enabled: bool
    last_run: str
    next_run: str | None
    retry_count: int
    ends_at: str = Field(description="End of a cron or interval job: YYYY-MM-DD (that day still runs) or YYYY-MM-DDTHH:MM; empty = no end")


class JobWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    schedule_type: str
    schedule_expr: str = Field(min_length=1)
    message: str = Field(min_length=1)
    agent: str
    delivery: str = "review"
    channel: str = ""
    recipient: str = ""
    webhook_url: str = ""
    fixed_text: bool = False
    audio_type: str = "notification"
    max_tier: int = Field(JOB_DEFAULT_TIER, ge=TIER_READONLY, le=TIER_ADMIN)
    ends_at: str = Field("", description="YYYY-MM-DD (that day still runs) or YYYY-MM-DDTHH:MM; empty = no end")


class JobSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class JobRun(BaseModel):
    ran_at: str
    text: str


class SchedulePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule_type: Literal["cron", "interval", "once"]
    schedule_expr: str


class NextRun(BaseModel):
    next_run: str


def _row(job: Job) -> JobRow:
    payload = job.payload
    return JobRow(
        job_id=job.job_id, name=job.name, schedule_type=job.schedule_type, schedule_expr=job.schedule_expr,
        message=payload.get("message", ""), agent=payload.get("agent", "aifred"), delivery=payload.get("delivery", "review"),
        channel=payload.get("channel", ""), recipient=payload.get("recipient", ""), webhook_url=payload.get("webhook_url", ""),
        fixed_text=bool(payload.get("fixed_text")), audio_type=payload.get("audio_type", "notification"),
        max_tier=job.max_tier, enabled=job.enabled, last_run=job.last_run or "", next_run=job.next_run or None,
        retry_count=job.retry_count, ends_at=job.ends_at,
    )


def _payload(job: JobWrite) -> dict[str, object]:
    """What the job does, with the fields of its delivery only (as the agents' tool stores it)."""
    from ..agent_config import selectable_agents
    from ..message_processor import announces_in_rooms
    from ..scheduler import job_problem

    if job.agent not in selectable_agents():
        raise Invalid("job_agent_unfit", agent=job.agent)
    payload: dict[str, object] = {"message": job.message, "agent": job.agent, "delivery": job.delivery}
    if job.fixed_text:   # asked for with any delivery: job_problem says where it can go
        payload["fixed_text"] = True
    if job.delivery == "announce":
        payload |= {key: value for key, value in (("channel", job.channel), ("recipient", job.recipient)) if value}
        if announces_in_rooms(job.channel):
            payload["audio_type"] = job.audio_type
    elif job.delivery == "webhook" and job.webhook_url:
        payload["webhook_url"] = job.webhook_url
    problem = job_problem(job.schedule_type, payload, job.ends_at)
    if problem:
        raise problem
    return payload


def _job(job_id: int) -> Job:
    from ..scheduler import get_job_store

    job = get_job_store().get(job_id)
    if job is None:
        raise Missing("no_job", job=job_id)
    return job


@api_app.get("/jobs/options", response_model=JobOptions, tags=["Jobs"])
async def jobs_options() -> JobOptions:
    """What a job can be set to: schedule types, deliveries, audio types, tiers, cron templates, agents, channels."""
    from ..agent_config import selectable_agents
    from ..message_processor import announces_in_rooms
    from ..plugin_base import plugin_display_name
    from ..plugin_registry import all_channels
    from ..prompt_loader import get_language
    from ..scheduler import AUDIO_TYPES, CRON_PRESETS, DELIVERY_MODES, SCHEDULE_TYPES

    lang = get_language()
    channels = sorted(all_channels().items())
    return JobOptions(
        schedule_types=list(SCHEDULE_TYPES), delivery_modes=list(DELIVERY_MODES), audio_types=list(AUDIO_TYPES),
        tiers=list(range(TIER_READONLY, TIER_ADMIN + 1)), default_tier=JOB_DEFAULT_TIER,
        cron_presets=[CronPreset(key=key, minute=minute, hour=hour, dom=dom, month=month, dow=dow)
                      for key, minute, hour, dom, month, dow in CRON_PRESETS],
        agents=[ChoiceOption(id=agent_id, label=f"{config.emoji} {config.display_name}") for agent_id, config in selectable_agents().items()],
        channels=[ChoiceOption(id=name, label=plugin_display_name(channel, lang)) for name, channel in channels],
        rooms_channels=[name for name, _channel in channels if announces_in_rooms(name)],
    )


@api_app.get("/jobs", response_model=list[JobRow], tags=["Jobs"])
async def jobs_list() -> list[JobRow]:
    """Every job, switched on or off."""
    from ..scheduler import get_job_store

    return [_row(job) for job in get_job_store().list_all()]


@api_app.post("/jobs", response_model=JobRow, status_code=201, tags=["Jobs"])
async def jobs_create(job: JobWrite) -> JobRow:
    """A new job (422 with the reason for one the scheduler refuses or an expression that never fires)."""
    from ..scheduler import get_job_store

    payload = _payload(job)
    created = get_job_store().add(job.name, job.schedule_type, job.schedule_expr, payload, job.max_tier, job.ends_at)
    return _row(created)


@api_app.put("/jobs/{job_id}", response_model=JobRow, tags=["Jobs"])
async def jobs_change(job_id: int, job: JobWrite) -> JobRow:
    """Change a job in place (it keeps its id, its runs and whether it is on); the next run is computed anew."""
    from ..scheduler import get_job_store

    _job(job_id)
    payload = _payload(job)
    changed = get_job_store().update(job_id, job.name, job.schedule_type, job.schedule_expr, payload, job.max_tier, job.ends_at)
    if changed is None:   # deleted meanwhile
        raise Missing("no_job", job=job_id)
    return _row(changed)


@api_app.patch("/jobs/{job_id}", response_model=JobRow, tags=["Jobs"])
async def jobs_switch(job_id: int, switch: JobSwitch) -> JobRow:
    """Switch a job on or off."""
    from ..scheduler import get_job_store

    _job(job_id)
    get_job_store().enable(job_id, switch.enabled)
    log_message(f"Scheduler: job {job_id} switched {'on' if switch.enabled else 'off'}")
    return _row(_job(job_id))


@api_app.delete("/jobs/{job_id}", status_code=204, tags=["Jobs"])
async def jobs_delete(job_id: int) -> Response:
    from ..scheduler import delete_job

    _job(job_id)
    await delete_job(job_id)
    return Response(status_code=204)


@api_app.get("/jobs/{job_id}/runs", response_model=list[JobRun], tags=["Jobs"])
async def jobs_runs(job_id: int, limit: int = Query(5, ge=1, le=50)) -> list[JobRun]:
    """The newest runs of a job with what they delivered."""
    from ..scheduler import get_job_store

    _job(job_id)
    return [JobRun(ran_at=ran_at, text=text) for ran_at, text in get_job_store().recent_runs(job_id, limit)]


@api_app.post("/jobs/preview", response_model=NextRun, tags=["Jobs"])
async def jobs_preview(preview: SchedulePreview) -> NextRun:
    """When an expression would fire next (422 for one that never does)."""
    from ..scheduler import next_run_of

    next_run = next_run_of(preview.schedule_type, preview.schedule_expr)
    if next_run is None:
        raise Invalid("schedule_never_fires", kind=preview.schedule_type, expression=preview.schedule_expr)
    return NextRun(next_run=next_run)
