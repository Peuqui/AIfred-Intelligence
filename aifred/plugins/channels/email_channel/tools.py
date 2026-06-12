"""E-Mail tool for LLM function calling.

Single `email` tool with action parameter to reduce tool count.
All IMAP/SMTP operations run in asyncio.to_thread() (blocking I/O).
"""

import asyncio

from ....lib.function_calling import Tool
from ....lib.security import TIER_COMMUNICATE
from ....lib.plugin_base import load_tool_description


def get_email_tools(session_id: str = "") -> list[Tool]:
    """Create email tool for LLM function calling."""

    async def _email(action: str, **kwargs: str) -> str:
        """Unified email tool — dispatches by action."""
        action = action.lower().strip()

        if action == "check":
            from .client import check_inbox
            n = int(kwargs.get("n", "10"))
            folder = kwargs.get("folder", "INBOX")
            emails = await asyncio.to_thread(check_inbox, n=min(n, 20), folder=folder)
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
            # session_id passed to send_email for route registration (single source of truth)
            result = await asyncio.to_thread(
                send_email, to=to, subject=subject, body=body, session_id=session_id,
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
            return f"Unknown action: {action}. Valid: check, read, search, delete, send, move, list_folders, create_folder, mark"

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
                        "enum": ["check", "read", "search", "delete", "send", "move", "list_folders", "create_folder", "mark"],
                        "description": "Action to perform",
                    },
                    "msg_id": {
                        "type": "string",
                        "description": "Message ID (for read, delete)",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search term (for search)",
                    },
                    "n": {
                        "type": "string",
                        "description": "Number of emails to fetch (for check, default 10)",
                    },
                    "to": {
                        "type": "string",
                        "description": "Recipient email address (for send)",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject (for send)",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body (for send)",
                    },
                    "folder": {
                        "type": "string",
                        "description": "IMAP folder (default INBOX)",
                    },
                    "target_folder": {
                        "type": "string",
                        "description": "Target folder for move action",
                    },
                    "folder_name": {
                        "type": "string",
                        "description": "Folder name for create_folder action",
                    },
                    "flag": {
                        "type": "string",
                        "enum": ["read", "unread", "flagged", "unflagged"],
                        "description": "Flag for mark action",
                    },
                },
                "required": ["action"],
            },
            executor=_email,
        ),
    ]
