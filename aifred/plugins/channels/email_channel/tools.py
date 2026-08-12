"""E-Mail tools for LLM function calling.

Two tools, split by risk so the tier system can gate them independently:
- ``email``        (TIER_COMMUNICATE): check / read / search / send / list_folders / mark
- ``email_manage`` (TIER_WRITE_SYSTEM): delete / move / create_folder

Destructive actions live in the higher-tier tool so an inbound (untrusted)
message can never reach them — only the browser (user present) can delete/move
mail. All IMAP/SMTP operations run in asyncio.to_thread() (blocking I/O).
"""

import asyncio

from ....lib.function_calling import Tool
from ....lib.security import TIER_COMMUNICATE, TIER_WRITE_SYSTEM, sanitize_outbound
from ....lib.plugin_base import load_tool_description

# Actions offered by the safe (COMMUNICATE) tool vs. the destructive one.
# Each tool enforces its own set so the model cannot smuggle a delete through
# the low-tier ``email`` tool.
_SAFE_ACTIONS = {"check", "read", "search", "send", "list_folders", "mark"}
_MANAGE_ACTIONS = {"delete", "move", "create_folder"}


def get_email_tools(session_id: str = "", source: str = "browser") -> list[Tool]:
    """Create email tools for LLM function calling.

    ``source`` is the origin of the current pipeline (browser/email/…). It gates
    the send-recipient allowlist check: only the browser may send to a recipient
    that is not on the allowlist (see the ``send`` action).
    """

    async def _email(action: str, **kwargs: str) -> str:
        """Unified email dispatcher — action set is enforced by the caller."""
        action = action.lower().strip()

        if action == "check":
            from .client import check_inbox
            from .config import EMAIL_MAX_FETCH
            n = int(kwargs.get("n", "10"))
            folder = kwargs.get("folder", "INBOX")
            emails = await asyncio.to_thread(
                check_inbox, n=max(1, min(n, EMAIL_MAX_FETCH)), folder=folder
            )
            if not emails:
                return "No emails found (0 messages)."
            lines = [f"Total: {len(emails)} emails"]
            for e in emails:
                status = "📩" if not e.is_read else "📧"
                lines.append(f"{status} [{e.msg_id}] {e.date} — {e.sender}\n   {e.subject}\n   {e.preview}")
            return "\n\n".join(lines)

        elif action == "read":
            from .client import read_email
            msg_id = kwargs.get("msg_id", "")
            if not msg_id:
                return "Error: msg_id required"
            folder = kwargs.get("folder", "INBOX")
            msg = await asyncio.to_thread(read_email, msg_id=msg_id, folder=folder)
            parts = [
                f"From: {msg.sender}",
                f"To: {msg.to}",
                f"Date: {msg.date}",
                f"Subject: {msg.subject}",
            ]
            if msg.attachments:
                parts.append(f"Attachments: {', '.join(msg.attachments)}")
            parts.append(f"\n{msg.body}")
            return "\n".join(parts)

        elif action == "search":
            from .client import search_emails
            query = kwargs.get("query", "")
            if not query:
                return "Error: query required"
            folder = kwargs.get("folder", "INBOX")
            emails = await asyncio.to_thread(search_emails, query=query, folder=folder)
            if not emails:
                return f"No emails found for '{query}'."
            lines = []
            for e in emails:
                lines.append(f"[{e.msg_id}] {e.date} — {e.sender}: {e.subject}")
            return "\n".join(lines)

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
            attachment_path: str | None = None
            attachment = kwargs.get("attachment", "")
            if attachment:
                from ....lib.vision_utils import resolve_outbound_attachment
                path, err = resolve_outbound_attachment(attachment, session_id, source)
                if err:
                    return f"Error: {err}"
                attachment_path = str(path)
            # session_id passed to send_email for route registration (single source of truth)
            result = await asyncio.to_thread(
                send_email, to=to, subject=subject, body=body, session_id=session_id,
                attachment=attachment_path,
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
        if act not in _SAFE_ACTIONS:
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
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        # Aus dem Python-Enforcement-Set abgeleitet — eine Wahrheit
                        "enum": sorted(_SAFE_ACTIONS),
                        "description": "Action to perform",
                    },
                    "msg_id": {"type": "string", "description": "Message ID (for read)"},
                    "query": {"type": "string", "description": "Search term (for search)"},
                    "n": {"type": "string", "description": "Number of emails to fetch (for check, default 10)"},
                    "to": {"type": "string", "description": "Recipient email address (for send)"},
                    "subject": {"type": "string", "description": "Email subject (for send)"},
                    "body": {"type": "string", "description": "Email body (for send)"},
                    "attachment": {
                        "type": "string",
                        "description": (
                            "Optional (for send): URL of a file from THIS conversation to "
                            "attach (an uploaded image, or generated sandbox output like a "
                            "PDF — its /_upload/... URL)."
                        ),
                    },
                    "folder": {"type": "string", "description": "IMAP folder (default INBOX)"},
                    "flag": {
                        "type": "string",
                        "enum": ["read", "unread", "flagged", "unflagged"],
                        "description": "Flag for mark action",
                    },
                },
                "required": ["action"],
            },
            executor=_email_safe,
        ),
        Tool(
            name="email_manage",
            tier=TIER_WRITE_SYSTEM,
            description=load_tool_description(__file__, "email_manage"),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        # Aus dem Python-Enforcement-Set abgeleitet — eine Wahrheit
                        "enum": sorted(_MANAGE_ACTIONS),
                        "description": "Destructive mailbox action",
                    },
                    "msg_id": {"type": "string", "description": "Message ID (for delete, move)"},
                    "folder": {"type": "string", "description": "Source IMAP folder (default INBOX)"},
                    "target_folder": {"type": "string", "description": "Target folder for move action"},
                    "folder_name": {"type": "string", "description": "Folder name for create_folder action"},
                },
                "required": ["action"],
            },
            executor=_email_manage,
        ),
    ]
