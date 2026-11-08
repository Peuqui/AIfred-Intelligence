"""Agent trigger (webhook), OAuth routes and agent bundle export/import."""

import html as _html
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_exception, log_message
from ..user_error import BadRequest, Invalid, Missing, NotDone, UserError
from .app import api_app
from .schemas import CallerName

if TYPE_CHECKING:
    from ..agent_config import AgentConfig

# ============================================================
# AGENT TRIGGER (Webhook API)
# ============================================================

class AgentTriggerRequest(BaseModel):
    """Request to trigger an agent action."""
    sender: CallerName
    message: str = Field(..., description="Message/prompt for the agent")
    agent: str = Field(default="aifred", description="Agent to use (aifred, sokrates, salomo)")
    token: str = Field(..., description="Auth token (configured in WEBHOOK_API_TOKEN env var)")
    max_tier: int = Field(default=0, description="Max security tier (0=readonly, 1=communicate)")
    delivery: str = Field(default="review", description="Delivery mode: review, announce, webhook")
    channel: str = Field(default="", description="Target channel for announce delivery")
    recipient: str = Field(default="", description="Recipient for announce delivery")
    webhook_url: str = Field(default="", description="URL for webhook delivery")


class AgentTriggerResponse(BaseModel):
    """Response from agent trigger."""
    success: bool
    session_id: str = ""
    message: str = ""


@api_app.post("/agent/trigger", response_model=AgentTriggerResponse, tags=["Agent"])
async def trigger_agent(request: AgentTriggerRequest, background_tasks: BackgroundTasks):
    """
    Trigger an agent action from an external system.

    Runs in an isolated session with security tier enforcement.
    The result is delivered based on the delivery mode.
    Auth via token (WEBHOOK_API_TOKEN env var).
    """
    # Auth check (constant-time, fail-closed — zentrale Prüfung in lib/auth)
    from ..auth import require_service_token
    require_service_token("webhook", request.token)

    # Tier cap: webhooks max tier 1 by default
    from ..security import DEFAULT_TIER_BY_SOURCE
    max_allowed = DEFAULT_TIER_BY_SOURCE.get("webhook", 0)
    effective_tier = min(request.max_tier, max_allowed)

    log_message(f"API: agent/trigger — '{request.message[:50]}...' (agent={request.agent}, tier={effective_tier})")

    # Pre-allocate session + routing so the synchronous response can
    # already return the session_id while the engine call runs in the
    # background. process_inbound picks up the existing session via
    # routing_table.get_route().
    import secrets

    from ..config import MESSAGE_HUB_OWNER
    from ..routing_table import routing_table
    from ..session_storage import create_empty_session

    session_id = secrets.token_hex(16)
    channel_id = secrets.token_hex(8)
    if not create_empty_session(session_id, owner=MESSAGE_HUB_OWNER):
        raise NotDone("session_not_created")
    routing_table.set_route("webhook", channel_id, session_id)

    async def _run():
        from datetime import datetime

        from ..envelope import InboundMessage
        from ..message_processor import process_inbound
        from ..scheduler import Job, _deliver_result

        msg = InboundMessage(
            channel="webhook",
            channel_id=channel_id,
            sender=request.sender,
            text=request.message,
            timestamp=datetime.now(),
            metadata={
                "wake_agent": request.agent,
                # security.py honors max_tier on the webhook channel only.
                "max_tier": effective_tier,
            },
            target_agent=request.agent,
        )

        outbound = await process_inbound(msg)

        if outbound is None or not outbound.text:
            return

        # Create a minimal Job for delivery (delivery config comes from the
        # webhook request, not from the scheduler store).
        job = Job(
            job_id=0,
            name="webhook_trigger",
            schedule_type="once",
            schedule_expr="",
            payload={
                "delivery": request.delivery,
                "channel": request.channel,
                "recipient": request.recipient,
                "webhook_url": request.webhook_url,
            },
        )
        await _deliver_result(job, outbound.text, session_id)

    background_tasks.add_task(_run)

    return AgentTriggerResponse(
        success=True,
        session_id=session_id,
        message="Agent triggered, running in background",
    )


# ============================================================
# OAuth routes
# ============================================================

@api_app.get("/oauth/{provider}/callback", response_class=HTMLResponse, include_in_schema=False)
async def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    """OAuth 2.0 callback endpoint.  Google redirects here after user login."""
    from ..oauth import oauth_broker

    if error:
        return HTMLResponse(
            f"<html><body><h2>❌ OAuth abgebrochen</h2><p>{_html.escape(error)}</p>"
            "<p>Du kannst dieses Fenster schließen.</p></body></html>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse(
            "<html><body><h2>❌ Ungültiger Callback</h2>"
            "<p>Fehlende Parameter. Starte den Login-Flow neu.</p></body></html>",
            status_code=400,
        )

    try:
        await oauth_broker.handle_callback(state, code, expected_provider=provider)
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;padding:2rem'>"
            f"<h2>✅ {_html.escape(provider.capitalize())} verbunden!</h2>"
            "<p>Tokens gespeichert. Du kannst dieses Fenster schließen.</p>"
            "</body></html>"
        )
    except Exception as exc:  # noqa: BLE001 — the login window shows why; the log keeps the traceback
        log_exception(f"OAuth callback for {provider} failed", exc)
        return HTMLResponse(
            f"<html><body><h2>❌ Fehler</h2><p>{_html.escape(str(exc))}</p></body></html>",
            status_code=400,
        )


@api_app.get("/oauth/{provider}/status")
async def oauth_status(provider: str) -> dict:
    """Check whether a provider is connected (tokens stored)."""
    from ..oauth import oauth_broker
    return {"provider": provider, "connected": oauth_broker.is_connected(provider)}


@api_app.get("/oauth/{provider}/auth-url")
async def oauth_auth_url(provider: str, redirect_uri: str, scopes: str = "") -> dict:
    """Generate an authorization URL to start the OAuth flow.

    ``scopes``: comma-separated list of OAuth scopes.
    ``redirect_uri``: the callback URL (must match Google Cloud Console registration).
    """
    from ..oauth import oauth_broker
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    return {"auth_url": oauth_broker.get_auth_url(provider, scope_list, redirect_uri)}


@api_app.delete("/oauth/{provider}")
async def oauth_disconnect(provider: str) -> dict:
    """Remove stored tokens for a provider."""
    from ..oauth import oauth_broker
    await oauth_broker.disconnect(provider)
    return {"provider": provider, "disconnected": True}


# ============================================================
# Agent Bundle Export / Import
# ============================================================


class AgentSummary(BaseModel):
    id: str
    display_name: str
    emoji: str
    role: str


@api_app.get("/agents/summary", response_model=list[AgentSummary], tags=["Agents"])
async def agents_summary() -> list[AgentSummary]:
    """The agents a chat can address: no system agent, none switched off (agent_config.selectable_agents)."""
    from ..agent_config import selectable_agents

    return [
        AgentSummary(id=agent_id, display_name=config.display_name, emoji=config.emoji, role=config.role)
        for agent_id, config in selectable_agents().items()
    ]


class AgentCard(BaseModel):
    """An agent of the agents page: every agent a chat can address, switched on or off."""

    id: str
    display_name: str
    emoji: str
    description: str
    enabled: bool
    base: bool = Field(description="The base agent: never switched off, never deleted")
    delete_refusal: str | None = Field(description="Why it cannot be deleted; null when it can")


class AgentSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _card(agent_id: str) -> AgentCard:
    from ..agent_config import BASE_AGENT, deletion_refusal, get_agent_config
    from ..prompt_loader import get_language

    config = get_agent_config(agent_id)
    if config is None:
        raise Missing("unknown_agent", agent=agent_id)
    refusal = deletion_refusal(agent_id)
    return AgentCard(
        id=agent_id, display_name=config.display_name, emoji=config.emoji, description=config.description,
        enabled=config.enabled, base=agent_id == BASE_AGENT,
        delete_refusal=refusal.message(get_language()) if refusal else None,
    )


@api_app.get("/agents", response_model=list[AgentCard], tags=["Agents"])
async def agents_cards() -> list[AgentCard]:
    """Every agent a chat can address, the ones switched off too (no system agent), in agents.json's order."""
    from ..agent_config import ROLE_SYSTEM, load_agents

    return [_card(agent_id) for agent_id, config in load_agents().items() if config.role != ROLE_SYSTEM]


@api_app.patch("/agents/{agent_id}", response_model=AgentCard, tags=["Agents"])
async def agents_switch(agent_id: str, switch: AgentSwitch) -> AgentCard:
    """Switch an agent on or off. One switched off vanishes from every choice; a turn, job or channel that
    names it is refused out loud (422 for the base agent and system agents)."""
    from ..agent_config import set_agent_enabled

    _card(agent_id)
    set_agent_enabled(agent_id, switch.enabled)
    log_message(f"🎛️ Agent '{agent_id}' switched {'on' if switch.enabled else 'off'}")
    return _card(agent_id)


@api_app.delete("/agents/{agent_id}", status_code=204, tags=["Agents"])
async def agents_delete(agent_id: str) -> Response:
    """Delete an agent with its prompt files (422 with the reason when it cannot be, see delete_refusal)."""
    from ..agent_config import delete_agent

    _card(agent_id)
    delete_agent(agent_id)
    log_message(f"🗑️ Agent '{agent_id}' deleted")
    return Response(status_code=204)


# ── Persona, prompts, rights, a new agent ────────────────────────────────


class AgentPersona(BaseModel):
    id: str
    display_name: str
    emoji: str
    description: str
    aliases: list[str] = Field(description="Names it is also addressed by (STT spellings), lowercase")
    prompt_keys: list[str] = Field(description="Its prompt layers (identity, personality, task, direct …)")
    languages: list[str] = Field(description="The languages there are prompts in")


class PersonaChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(None, min_length=1)
    emoji: str | None = Field(None, min_length=1)
    description: str | None = None
    aliases: list[str] | None = None


class PromptText(BaseModel):
    text: str
    exists: bool = Field(description="The file is there in this language (an EN-only prompt has none in DE)")


class PromptWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class ToolRight(BaseModel):
    name: str
    tier: int
    allowed: bool


class ToolGroup(BaseModel):
    name: str
    tools: list[ToolRight]


class AgentRights(BaseModel):
    all: bool = Field(description="No whitelist: every tool, also of plugins switched on later")
    groups: list[ToolGroup] = Field(description="What can be granted right now (plugins switched off offer nothing)")


class RightsChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: list[str] | None = Field(description="The tools granted; null grants every tool")


class NewAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Lowercase letters, digits and _; names its prompt folder")
    display_name: str = Field(min_length=1)
    emoji: str = Field(min_length=1)
    description: str = ""


def _chat_agent(agent_id: str) -> "AgentConfig":
    """An agent of the agents page; 404 for an unknown one and for a system agent."""
    from ..agent_config import ROLE_SYSTEM, get_agent_config

    config = get_agent_config(agent_id)
    if config is None or config.role == ROLE_SYSTEM:
        raise Missing("no_chat_agent", agent=agent_id)
    return config


def _persona(agent_id: str) -> AgentPersona:
    from ..prompt_loader import prompt_languages

    config = _chat_agent(agent_id)
    return AgentPersona(
        id=agent_id, display_name=config.display_name, emoji=config.emoji, description=config.description,
        aliases=config.aliases, prompt_keys=list(config.prompts), languages=prompt_languages(),
    )


def _prompt_file(agent_id: str, lang: str, key: str) -> Path:
    from ..prompt_loader import prompt_path

    config = _chat_agent(agent_id)
    if key not in config.prompts:
        raise Missing("no_prompt", agent=config.display_name, prompt=key)
    path = prompt_path(lang, config.prompts[key])
    if path is None:
        raise Invalid("no_prompt_file", prompt=key, language=lang)
    return path


@api_app.get("/agents/{agent_id}/persona", response_model=AgentPersona, tags=["Agents"])
async def agents_persona(agent_id: str) -> AgentPersona:
    """Name, emoji, description, aliases and the prompt layers of a chat agent."""
    return _persona(agent_id)


@api_app.patch("/agents/{agent_id}/persona", response_model=AgentPersona, tags=["Agents"])
async def agents_persona_change(agent_id: str, change: PersonaChange) -> AgentPersona:
    """Change the fields sent; aliases are stored lowercase without empty ones."""
    from ..agent_config import update_agent

    _chat_agent(agent_id)
    updates = change.model_dump(exclude_none=True)
    if "aliases" in updates:
        updates["aliases"] = [alias.strip().lower() for alias in updates["aliases"] if alias.strip()]
    update_agent(agent_id, updates)
    log_message(f"🎭 Agent '{agent_id}' persona changed: {', '.join(updates)}")
    return _persona(agent_id)


@api_app.get("/agents/{agent_id}/prompts/{lang}/{key}", response_model=PromptText, tags=["Agents"])
async def agents_prompt(agent_id: str, lang: str, key: str) -> PromptText:
    """A prompt layer's text in one language (empty, ``exists`` false, when the file is not there)."""
    path = _prompt_file(agent_id, lang, key)
    return PromptText(text=path.read_text(encoding="utf-8") if path.is_file() else "", exists=path.is_file())


@api_app.put("/agents/{agent_id}/prompts/{lang}/{key}", response_model=PromptText, tags=["Agents"])
async def agents_prompt_write(agent_id: str, lang: str, key: str, prompt: PromptWrite) -> PromptText:
    """Write a prompt layer's text in one language; it takes effect from the next call on."""
    path = _prompt_file(agent_id, lang, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt.text, encoding="utf-8")
    log_message(f"🎭 Agent '{agent_id}' prompt {lang}/{key} saved ({len(prompt.text)} chars)")
    return PromptText(text=prompt.text, exists=True)


def _rights(agent_id: str) -> AgentRights:
    from ..plugin_base import PluginContext
    from ..plugin_registry import tool_groups_on_offer
    from ..prompt_loader import get_language

    allowed = _chat_agent(agent_id).tools
    lang = get_language()
    groups = tool_groups_on_offer(PluginContext(agent_id=agent_id, lang=lang, session_id=""))
    return AgentRights(all=allowed is None, groups=[
        ToolGroup(name=group[f"name_{lang}"], tools=[
            ToolRight(name=tool["name"], tier=tool["tier"], allowed=allowed is None or tool["name"] in allowed)
            for tool in group["tools"]
        ])
        for group in groups
    ])


@api_app.get("/agents/{agent_id}/tools", response_model=AgentRights, tags=["Agents"])
async def agents_rights(agent_id: str) -> AgentRights:
    """The tools a chat agent may use, grouped by plugin as they are on offer right now."""
    return _rights(agent_id)


@api_app.put("/agents/{agent_id}/tools", response_model=AgentRights, tags=["Agents"])
async def agents_rights_change(agent_id: str, change: RightsChange) -> AgentRights:
    """Grant tools: null grants every tool. A list keeps what the whitelist held for tools not on offer now
    (a plugin switched off): switching it on again brings them back as they were."""
    from ..agent_config import update_agent

    previous = _chat_agent(agent_id).tools
    if change.allowed is None:
        tools = None
    else:
        on_offer = {tool.name for group in _rights(agent_id).groups for tool in group.tools}
        kept = [name for name in previous or [] if name not in on_offer]
        tools = sorted(set(kept) | set(change.allowed))
    update_agent(agent_id, {"tools": tools})
    log_message(f"🔐 Agent '{agent_id}' tools: {'all' if tools is None else len(tools)}")
    return _rights(agent_id)


@api_app.post("/agents", response_model=AgentCard, status_code=201, tags=["Agents"])
async def agents_create(new: NewAgent) -> AgentCard:
    """A new agent with the base prompts of prompts/<lang>/agent-template/ (its name filled in);
    422 for an id taken or no valid folder name."""
    from ..agent_config import create_agent

    create_agent(agent_id=new.id, display_name=new.display_name, emoji=new.emoji, description=new.description)
    log_message(f"✨ Agent '{new.id}' created")
    return _card(new.id)


@api_app.get("/agents/export", tags=["Agents"])
async def export_agents_endpoint(ids: str) -> Response:
    """Download a ZIP bundle containing one or more agents.

    ``ids`` is a comma-separated list of agent IDs (e.g. ``?ids=<id1>,<id2>``).
    A GET endpoint is used so the UI can trigger a plain browser download
    via ``<a href>`` without orchestrating a fetch+blob roundtrip.
    """
    from ..agent_bundle import export_bundle

    agent_ids = [a.strip() for a in ids.split(",") if a.strip()]
    if not agent_ids:
        raise BadRequest("no_agents_selected")

    try:
        data = export_bundle(agent_ids)
    except UserError:
        raise
    except Exception as exc:  # the voices or the archive failed: the caller gets the reason, the log the traceback
        log_exception("export_bundle failed", exc)
        raise NotDone("bundle_export_failed", reason=str(exc)) from exc

    if len(agent_ids) == 1:
        filename = f"{agent_ids[0]}.aifred-agent.zip"
    else:
        filename = f"aifred-agents-{int(time.time())}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class BundleAgent(BaseModel):
    agent_id: str
    display_name: str
    description: str
    exists_locally: bool


class BundlePeek(BaseModel):
    format_version: int
    exported_at: int | None = Field(description="Unix time of the export")
    agents: list[BundleAgent]


class BundleImported(BaseModel):
    success: bool
    agent_ids: list[str] = Field(description="The ids the agents got (a renamed one has its new id)")
    warnings: list[str]


@api_app.post("/agents/import/peek", response_model=BundlePeek, tags=["Agents"])
async def peek_agent_bundle(file: UploadFile = File(...)) -> dict:
    """Inspect a bundle without writing — returns the list of agents inside
    plus a flag per agent indicating whether it already exists locally.
    """
    from ..agent_bundle import peek_bundle

    return peek_bundle(await file.read())


@api_app.post("/agents/import", response_model=BundleImported, tags=["Agents"])
async def import_agents_endpoint(
    file: UploadFile = File(...),
    ids: str = Form(""),
    conflict: str = Form("abort"),
) -> dict:
    """Import selected agents from a bundle ZIP.

    ``ids`` (comma-separated) selects which agents to import; empty means
    all agents in the bundle. ``conflict`` is one of abort|overwrite|rename.
    """
    from ..agent_bundle import import_bundle

    if conflict not in ("abort", "overwrite", "rename"):
        raise BadRequest("conflict_strategy", value=conflict)

    selected = [a.strip() for a in ids.split(",") if a.strip()] or None

    zip_bytes = await file.read()
    try:
        effective_ids, warnings = import_bundle(
            zip_bytes, selected_ids=selected, conflict=conflict,  # type: ignore[arg-type]
        )
    except UserError:
        raise
    except Exception as exc:  # writing the agents failed midway: the caller gets the reason, the log the traceback
        log_exception("import_bundle failed", exc)
        raise NotDone("bundle_import_failed", reason=str(exc)) from exc

    return {"success": True, "agent_ids": effective_ids, "warnings": warnings}
