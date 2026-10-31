"""E-Mail tools for LLM function calling.

Split by risk so the tier system can gate them independently:
- ``email``          (TIER_COMMUNICATE): check / read / search / send / list_folders / mark
- ``email_manage``   (TIER_WRITE_SYSTEM): delete / move / create_folder
- ``email_readonly`` (TIER_WRITE_DATA): check / read / search on the second mailbox

Destructive actions live in the higher-tier tool so an inbound (untrusted)
message can never reach them — only the browser (user present) can delete/move
mail. ``email_readonly`` changes nothing, yet carries TIER_WRITE_DATA: the mailbox
is private, and the tiers keep it away from external channels. All IMAP/SMTP
operations run in asyncio.to_thread() (blocking I/O).
"""

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path

from ....lib.formatting import format_number
from ....lib.function_calling import Tool
from ....lib.plugin_base import load_tool_description, load_tool_parameters
from ....lib.security import (
    TIER_COMMUNICATE,
    TIER_WRITE_DATA,
    TIER_WRITE_SYSTEM,
    may_send_outbound,
    resolve_trust_label,
    sanitize_outbound,
    wrap_untrusted_data,
)
from .client import (
    ImapAccount,
    agent_mailbox_account,
    check_inbox,
    read_email,
    readonly_mailbox_account,
    readonly_mailbox_configured,
    search_emails,
)
from .config import EMAIL_MAX_FETCH

# Actions offered by the safe (COMMUNICATE) tool vs. the destructive one.
# Each tool enforces its own set so the model cannot smuggle a delete through
# the low-tier ``email`` tool.
_SAFE_ACTIONS = {"check", "read", "search", "send", "list_folders", "mark"}
_MANAGE_ACTIONS = {"delete", "move", "create_folder"}


async def _check(account: ImapAccount, kwargs: dict[str, str]) -> str:
    n = int(kwargs.get("n", "10"))
    folder = kwargs.get("folder", "INBOX")
    emails = await asyncio.to_thread(
        check_inbox, account, n=max(1, min(n, EMAIL_MAX_FETCH)), folder=folder
    )
    if not emails:
        return "No emails found (0 messages)."
    lines = [f"Total: {format_number(len(emails))} emails"]
    for e in emails:
        status = "📩" if not e.is_read else "📧"
        lines.append(f"{status} [{e.msg_id}] {e.date} — {e.sender}\n   {e.subject}\n   {e.preview}")
    return wrap_untrusted_data("\n\n".join(lines), "email")


async def _read(account: ImapAccount, kwargs: dict[str, str], *, owner_verdict: bool) -> str:
    """``owner_verdict``: may a mail earn the "owner" label? Off, it stays "external"."""
    msg_id = kwargs.get("msg_id", "")
    if not msg_id:
        return "Error: msg_id required"
    folder = kwargs.get("folder", "INBOX")
    msg = await asyncio.to_thread(read_email, account, msg_id=msg_id, folder=folder)
    parts = [
        f"From: {msg.sender}",
        f"To: {msg.to}",
        f"Date: {msg.date}",
        f"Subject: {msg.subject}",
    ]
    if msg.attachments:
        parts.append(f"Attachments: {', '.join(msg.attachments)}")
    parts.append(f"\n{msg.body}")
    # Same owner verdict as for inbound mail (From + SPF/DKIM/DMARC pass).
    trust = (
        resolve_trust_label("email", msg.sender, {"auth_results": msg.auth_results})
        if owner_verdict else "external"
    )
    return wrap_untrusted_data("\n".join(parts), "email", trust)


async def _search(account: ImapAccount, kwargs: dict[str, str]) -> str:
    query = kwargs.get("query", "")
    if not query:
        return "Error: query required"
    folder = kwargs.get("folder", "INBOX")
    emails = await asyncio.to_thread(search_emails, account, query=query, folder=folder)
    if not emails:
        return f"No emails found for '{query}'."
    lines = []
    for e in emails:
        lines.append(f"[{e.msg_id}] {e.date} — {e.sender}: {e.subject}")
    return wrap_untrusted_data("\n".join(lines), "email")


def get_email_tools(session_id: str = "", source: str = "browser", lang: str = "de") -> list[Tool]:
    """Create email tools for LLM function calling.

    ``source`` is the origin of the current pipeline (browser/email/…). It gates
    the send-recipient allowlist check: only the browser may send to a recipient
    that is not on the allowlist (see the ``send`` action). Where the source may
    not send at all (a scheduled job), ``send`` is not offered.
    """
    safe_actions = _SAFE_ACTIONS if may_send_outbound(source) else _SAFE_ACTIONS - {"send"}

    async def _email(action: str, **kwargs: str) -> str:
        """Unified email dispatcher — action set is enforced by the caller."""
        action = action.lower().strip()

        if action == "check":
            return await _check(agent_mailbox_account(), kwargs)

        elif action == "read":
            return await _read(agent_mailbox_account(), kwargs, owner_verdict=True)

        elif action == "search":
            return await _search(agent_mailbox_account(), kwargs)

        elif action == "delete":
            from .client import delete_email
            msg_id = kwargs.get("msg_id", "")
            if not msg_id:
                return "Error: msg_id required"
            folder = kwargs.get("folder", "INBOX")
            result = await asyncio.to_thread(delete_email, msg_id=msg_id, folder=folder)
            return result

        elif action == "send":
            from .client import send_email
            to = kwargs.get("to", "")
            subject = kwargs.get("subject", "")
            body = kwargs.get("body", "")
            if not to or not subject or not body:
                return "Error: to, subject, body required"
            # Recipient allowlist gate: an injected instruction in an inbound
            # mail must not exfiltrate data to an arbitrary address. Only the
            # browser (user present, driving) may send to a non-allowlisted
            # recipient; from any external channel this is refused.
            if source != "browser":
                from . import _is_sender_allowed
                if not _is_sender_allowed(to):
                    return (
                        "Error: refused to send to a recipient that is not on the "
                        "allowlist from an external channel (exfiltration guard). "
                        "Ask the user to send this from the web UI, or add the "
                        "recipient to EMAIL_ALLOWED_SENDERS."
                    )
            # Redact secrets / block image-exfil URLs on the outbound path —
            # the send tool is exactly the vector an injected prompt would use.
            subject = sanitize_outbound(subject)
            body = sanitize_outbound(body)
            # Optional attachment via the cross-channel SSOT (session-isolated,
            # path-traversal safe, size-capped); the allowlist gate above is the
            # exfiltration guard.
            explicit: list[Path] = []
            attachment = kwargs.get("attachment", "")
            if attachment:
                from ....lib.vision_utils import resolve_outbound_attachment
                path, err = resolve_outbound_attachment(attachment, session_id, source)
                if err or path is None:
                    return f"Error: {err}"
                explicit.append(path)
            # The body is Markdown: images/links to /_upload/… files in it are
            # embedded/attached (same rendering as the channel reply path).
            from .media import render_mail
            try:
                mail = render_mail(body, session_id, source, explicit)
            except ValueError as exc:
                return f"Error: {exc}"
            # session_id passed to send_email for route registration (single source of truth)
            result = await asyncio.to_thread(
                send_email, to=to, subject=subject, body=mail.plain, html=mail.html,
                session_id=session_id,
                attachments=[str(path) for path in mail.attachments],
                inline_images=[(cid, str(path)) for cid, path in mail.inline_images],
            )
            return result

        elif action == "move":
            from .client import move_email
            msg_id = kwargs.get("msg_id", "")
            target = kwargs.get("target_folder", "")
            if not msg_id or not target:
                return "Error: msg_id and target_folder required"
            folder = kwargs.get("folder", "INBOX")
            result = await asyncio.to_thread(move_email, msg_id=msg_id, target_folder=target, source_folder=folder)
            return result

        elif action == "list_folders":
            from .client import list_folders
            folders = await asyncio.to_thread(list_folders)
            return f"Total: {len(folders)} folders\n" + "\n".join(f"📁 {f}" for f in folders)

        elif action == "create_folder":
            folder_name = kwargs.get("folder_name", "")
            if not folder_name:
                return "Error: folder_name required"
            from .client import create_folder
            result = await asyncio.to_thread(create_folder, folder_name=folder_name)
            return result

        elif action == "mark":
            from .client import mark_email
            msg_id = kwargs.get("msg_id", "")
            flag = kwargs.get("flag", "")
            if not msg_id or not flag:
                return "Error: msg_id and flag required (read/unread/flagged/unflagged)"
            folder = kwargs.get("folder", "INBOX")
            result = await asyncio.to_thread(mark_email, msg_id=msg_id, flag=flag, folder=folder)
            return result

        else:
            return f"Unknown action: {action}"

    async def _email_safe(action: str, **kwargs: str) -> str:
        act = action.lower().strip()
        if act == "send" and "send" not in safe_actions:
            from ....lib.prompt_loader import load_prompt
            return load_prompt("scheduler/send_refused", lang=lang).strip()
        if act not in safe_actions:
            return (
                f"Error: action {act!r} is not available in the 'email' tool. "
                f"Destructive actions (delete, move, create_folder) require the "
                f"'email_manage' tool (only usable from the web UI)."
            )
        return await _email(act, **kwargs)

    async def _email_manage(action: str, **kwargs: str) -> str:
        act = action.lower().strip()
        if act not in _MANAGE_ACTIONS:
            return (
                f"Error: action {act!r} is not available in 'email_manage'. "
                f"Valid: {', '.join(sorted(_MANAGE_ACTIONS))}."
            )
        return await _email(act, **kwargs)

    return [
        Tool(
            name="email",
            tier=TIER_COMMUNICATE,
            description=load_tool_description(__file__, "email"),
            parameters=load_tool_parameters(__file__, "email", enums={"action": sorted(safe_actions)}),
            executor=_email_safe,
        ),
        Tool(
            name="email_manage",
            tier=TIER_WRITE_SYSTEM,
            description=load_tool_description(__file__, "email_manage"),
            parameters=load_tool_parameters(__file__, "email_manage", enums={"action": sorted(_MANAGE_ACTIONS)}),
            executor=_email_manage,
        ),
    ]


def get_readonly_mailbox_tools(lang: str = "de") -> list[Tool]:
    """The tool of the read-only second mailbox; none while its credentials are missing.

    A separate function, no action of ``email``: its handlers are all it can do, and
    ``send``, ``delete`` and the rest do not exist here. Its mails never earn the
    "owner" label: that verdict rests on the Authentication-Results header, and this
    mailbox holds mail from anyone at a provider whose stamping was never checked.
    """
    if not readonly_mailbox_configured():
        return []

    handlers: dict[str, Callable[[ImapAccount, dict[str, str]], Awaitable[str]]] = {
        "check": _check,
        "read": partial(_read, owner_verdict=False),
        "search": _search,
    }

    async def _email_readonly(action: str, **kwargs: str) -> str:
        handler = handlers.get(action.lower().strip())
        if handler is None:
            from ....lib.prompt_loader import load_prompt
            return load_prompt("shared/email_readonly_refused", lang=lang, actions=", ".join(sorted(handlers))).strip()
        return await handler(readonly_mailbox_account(), kwargs)

    return [
        Tool(
            name="email_readonly",
            read_only=True,
            tier=TIER_WRITE_DATA,
            description=load_tool_description(__file__, "email_readonly"),
            parameters=load_tool_parameters(__file__, "email_readonly", enums={"action": sorted(handlers)}),
            executor=_email_readonly,
        ),
    ]
