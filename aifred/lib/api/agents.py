"""Agent trigger (webhook), OAuth routes and agent bundle export/import."""

import html as _html
import time

from fastapi import HTTPException, BackgroundTasks, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from ..logging_utils import log_message
from .app import api_app


# ============================================================
# AGENT TRIGGER (Webhook API)
# ============================================================

class AgentTriggerRequest(BaseModel):
    """Request to trigger an agent action."""
    message: str = Field(..., description="Message/prompt for the agent")
    agent: str = Field(default="aifred", description="Agent to use (aifred, sokrates, salomo)")
    token: str = Field(..., description="Auth token (configured in WEBHOOK_API_TOKEN env var)")
    max_tier: int = Field(default=0, description="Max security tier (0=readonly, 1=communicate)")
    delivery: str = Field(default="log", description="Delivery mode: log, announce, review, webhook")
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
    from ..session_storage import create_empty_session
    from ..routing_table import routing_table

    session_id = secrets.token_hex(16)
    channel_id = secrets.token_hex(8)
    if not create_empty_session(session_id, owner=MESSAGE_HUB_OWNER):
        raise HTTPException(status_code=500, detail="Failed to create session")
    routing_table.set_route("webhook", channel_id, session_id)

    async def _run():
        from datetime import datetime
        from ..envelope import InboundMessage
        from ..message_processor import process_inbound
        from ..scheduler import _deliver_result, Job

        msg = InboundMessage(
            channel="webhook",
            channel_id=channel_id,
            sender=MESSAGE_HUB_OWNER,
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
    except Exception as exc:
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
    try:
        url = oauth_broker.get_auth_url(provider, scope_list, redirect_uri)
        return {"auth_url": url}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@api_app.delete("/oauth/{provider}")
async def oauth_disconnect(provider: str) -> dict:
    """Remove stored tokens for a provider."""
    from ..oauth import oauth_broker
    await oauth_broker.disconnect(provider)
    return {"provider": provider, "disconnected": True}


# ============================================================
# Agent Bundle Export / Import
# ============================================================


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
        raise HTTPException(status_code=400, detail="No agents selected")

    try:
        data = export_bundle(agent_ids)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log_message(f"export_bundle failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    if len(agent_ids) == 1:
        filename = f"{agent_ids[0]}.aifred-agent.zip"
    else:
        filename = f"aifred-agents-{int(time.time())}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_app.post("/agents/import/peek", tags=["Agents"])
async def peek_agent_bundle(file: UploadFile = File(...)) -> dict:
    """Inspect a bundle without writing — returns the list of agents inside
    plus a flag per agent indicating whether it already exists locally.
    """
    from ..agent_bundle import peek_bundle

    try:
        zip_bytes = await file.read()
        return peek_bundle(zip_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Bundle nicht lesbar: {exc}")


@api_app.post("/agents/import", tags=["Agents"])
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
        raise HTTPException(status_code=400, detail=f"Invalid conflict strategy: {conflict}")

    selected = [a.strip() for a in ids.split(",") if a.strip()] or None

    try:
        zip_bytes = await file.read()
        effective_ids, warnings = import_bundle(
            zip_bytes, selected_ids=selected, conflict=conflict,  # type: ignore[arg-type]
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log_message(f"import_bundle failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"success": True, "agent_ids": effective_ids, "warnings": warnings}
