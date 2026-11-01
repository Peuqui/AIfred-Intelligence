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
from textwrap import wrap
from typing import Any

from ....lib import file_manager
from ....lib.config import LIST_DEFAULT, READ_MEMORY_GUARD_BYTES
from ....lib.context_manager import estimate_tokens
from ....lib.document_store import document_text
from ....lib.formatting import format_number
from ....lib.function_calling import Tool
from ....lib.line_window import LineWindow
from ....lib.pdf_pages import page_selection, render_scanned_pages
from ....lib.plugin_base import load_tool_description, load_tool_parameters
from ....lib.sandbox import SANDBOX_IMAGE_URL_MARKER, SANDBOX_VISION_FOCUS_MARKER
from ....lib.security import (
    TIER_COMMUNICATE,
    TIER_WRITE_DATA,
    TIER_WRITE_SYSTEM,
    extract_sender_email,
    may_send_outbound,
    resolve_trust_label,
    sanitize_outbound,
    wrap_untrusted_data,
)
from ....lib.tool_args import parse_bool
from ....lib.tool_output_cap import CHARS_PER_TOKEN, budget_var
from ....lib.vision_utils import get_image_url, is_image_file, save_session_upload
from .client import (
    EmailMessage,
    EmailSummary,
    ImapAccount,
    agent_mailbox_account,
    check_inbox,
    own_address,
    read_attachment,
    read_emails,
    readonly_mailbox_account,
    readonly_mailbox_configured,
    search_emails,
)

# Actions offered by the safe (COMMUNICATE) tool vs. the destructive one.
# Each tool enforces its own set so the model cannot smuggle a delete through
# the low-tier ``email`` tool.
_SAFE_ACTIONS = {"check", "read", "attachment", "search", "send", "list_folders", "mark"}
_MANAGE_ACTIONS = {"delete", "move", "create_folder", "index_attachment"}


def _mail_list(emails: list[EmailSummary], total: int, own_left_out: int = 0) -> str:
    """What the list tells about a mail. No content: to know what a mail says, read it."""
    lines = [f"Showing {format_number(len(emails))} of {format_number(total)} emails"]
    if own_left_out:
        lines[0] += f", {format_number(own_left_out)} of your own left out"
    for e in emails:
        line = f"{'📩' if not e.is_read else '📧'} [{e.msg_id}] {e.date} — {e.sender}: {e.subject}"
        lines.append(f"{line} | folder: {e.folder}" if e.folder else line)
    return wrap_untrusted_data("\n".join(lines), "email")


async def _check(account: ImapAccount, kwargs: dict[str, Any]) -> str:
    unread = parse_bool(kwargs["unread"], "unread") if "unread" in kwargs else False
    n = int(kwargs["n"]) if "n" in kwargs else None if unread else LIST_DEFAULT
    folder = kwargs.get("folder", "INBOX")
    emails, total = await asyncio.to_thread(check_inbox, account, n=n, folder=folder, unread_only=unread)
    own_left_out = 0
    if "exclude_own" in kwargs and parse_bool(kwargs["exclude_own"], "exclude_own"):
        others = [e for e in emails if extract_sender_email(e.sender) != own_address()]
        own_left_out = len(emails) - len(others)
        emails = others
        total -= own_left_out
    if not emails:
        nothing = "No unread emails." if unread else "No emails found (0 messages)."
        return f"{nothing} {format_number(own_left_out)} of your own left out." if own_left_out else nothing
    return _mail_list(emails, total, own_left_out)


def _lines(text: str, kwargs: dict[str, Any]) -> tuple[str, str]:
    """The asked-for lines of a text, as much as the model's budget holds, and the range
    in words. Like a file: what does not fit ends at a line, the next call goes on there."""
    budget = budget_var.get()
    max_chars = budget * CHARS_PER_TOKEN if budget > 0 else READ_MEMORY_GUARD_BYTES
    # A line longer than the budget would stop every range in front of it.
    broken = (piece for line in text.rstrip().split("\n")
              for piece in (wrap(line, max_chars) if len(line) > max_chars else [line]))
    window = LineWindow(int(kwargs.get("line_start", 0)), int(kwargs.get("line_end", 0)), max_chars=max_chars)
    window.feed_text("\n".join(broken))
    body, _total, lines = window.result()
    return body, lines


def _render_mail(msg: EmailMessage, kwargs: dict[str, Any], *, owner_verdict: bool) -> str:
    """``owner_verdict``: may a mail earn the "owner" label? Off, it stays "external"."""
    body, lines = _lines(msg.body, kwargs)
    parts = [
        f"From: {msg.sender}",
        f"To: {msg.to}",
        f"Date: {msg.date}",
        f"Subject: {msg.subject}",
        f"Lines: {lines}",
    ]
    if msg.attachments:
        parts.append(f"Attachments: {', '.join(msg.attachments)}")
    parts.append(f"\n{body}")
    # Same owner verdict as for inbound mail (From + SPF/DKIM/DMARC pass).
    trust = (
        resolve_trust_label("email", msg.sender, {"auth_results": msg.auth_results})
        if owner_verdict else "external"
    )
    return wrap_untrusted_data("\n".join(parts), "email", trust)


def _within_budget(rendered: list[tuple[str, str]], budget: int) -> list[str]:
    """Whole mails as far as the model's budget goes, then the IDs of the others for the
    next step: left to the generic cap, one mail would end mid-sentence and the mails
    behind it would be gone. The first mail always comes."""
    if budget <= 0:
        return [text for _msg_id, text in rendered]
    kept: list[str] = []
    used = 0
    for index, (_msg_id, text) in enumerate(rendered):
        rest = load_tool_description(__file__, "email_read_rest").format(
            ids=", ".join(msg_id for msg_id, _text in rendered[index:]))
        used += estimate_tokens([{"content": text}])
        if kept and used + estimate_tokens([{"content": rest}]) > budget:
            return [*kept, rest]
        kept.append(text)
    return kept


async def _read(account: ImapAccount, kwargs: dict[str, Any], *, owner_verdict: bool) -> str:
    msg_ids = [msg_id.strip() for msg_id in kwargs.get("msg_id", "").split(",") if msg_id.strip()]
    if not msg_ids:
        return "Error: msg_id required"
    folder = kwargs.get("folder", "INBOX")
    mails, missing = await asyncio.to_thread(read_emails, account, msg_ids, folder)
    if not mails:
        raise ValueError(f"Email {', '.join(missing)} not found")
    rendered = [(msg.msg_id, _render_mail(msg, kwargs, owner_verdict=owner_verdict)) for msg in mails]
    answers = await asyncio.to_thread(_within_budget, rendered, budget_var.get())
    if missing:
        answers.append(f"Not found: {', '.join(missing)}")
    return "\n\n".join(answers)


def _scanned_pages(data: bytes, msg_id: str, pages: str, session_id: str) -> tuple[list[tuple[int, str]], list[int]]:
    """Pages without text as images: done (page, URL) and the page numbers left."""
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        selected = page_selection(pages, len(document)) if pages else list(range(len(document)))
        return render_scanned_pages(document, selected, f"mail{msg_id}", session_id)


def _for_the_describer(answer: str, urls: list[str]) -> str:
    """Add the lines the backend acts on: it hands the images to the vision model and
    appends what it read (sandbox.describe_sandbox_screenshots)."""
    focus = load_tool_description(__file__, "email_attachment_focus").strip()
    return "\n".join([answer, f"{SANDBOX_VISION_FOCUS_MARKER}{focus}", *(f"{SANDBOX_IMAGE_URL_MARKER}{url}" for url in urls)])


async def _attachment(session_id: str, account: ImapAccount, kwargs: dict[str, Any]) -> str:
    """An attachment: its text by lines like a mail or a file, what is only a picture
    of text through the vision model."""
    msg_id, name = kwargs.get("msg_id", ""), kwargs.get("attachment", "")
    if not msg_id or not name:
        return "Error: msg_id and attachment required"
    data = await asyncio.to_thread(read_attachment, account, msg_id, name, kwargs.get("folder", "INBOX"))
    suffix = Path(name).suffix.lower()
    if is_image_file(Path(name)):
        path = await asyncio.to_thread(save_session_upload, data, session_id, f"mail{msg_id}", suffix.lstrip("."))
        return _for_the_describer(wrap_untrusted_data(f"Attachment: {name}", "email"), [get_image_url(path)])

    pages = str(kwargs.get("pages", ""))
    text = "" if pages else await asyncio.to_thread(document_text, data, name)
    rendered: list[tuple[int, str]] = []
    left: list[int] = []
    if suffix == ".pdf":
        rendered, left = await asyncio.to_thread(_scanned_pages, data, msg_id, pages, session_id)
    if not text.strip() and not rendered:
        return f"Attachment {name} contains no readable text."
    body, lines = _lines(text, kwargs)
    answer = wrap_untrusted_data(f"Attachment: {name}\nLines: {lines}\n\n{body}", "email")
    if left:
        answer += "\n" + load_tool_description(__file__, "email_attachment_pages_left").format(pages=", ".join(map(str, left)))
    return _for_the_describer(answer, [url for _page, url in rendered]) if rendered else answer


async def _index_attachment(source_key: str, account: ImapAccount, kwargs: dict[str, Any]) -> str:
    """Put an attachment into the document index. The text stays marked as external."""
    msg_id, name = kwargs.get("msg_id", ""), kwargs.get("attachment", "")
    if not msg_id or not name:
        return "Error: msg_id and attachment required"
    data = await asyncio.to_thread(read_attachment, account, msg_id, name, kwargs.get("folder", "INBOX"))
    result = await file_manager.index_received_file(data, name, source_key, f"{msg_id}/{name}")
    return result.detail if result.success else f"Error: {result.detail}"


async def _search(account: ImapAccount, kwargs: dict[str, Any]) -> str:
    query = kwargs.get("query", "")
    if not query:
        return "Error: query required"
    n = int(kwargs["n"]) if "n" in kwargs else LIST_DEFAULT
    emails, total = await asyncio.to_thread(search_emails, account, query=query, folder=kwargs.get("folder"), n=n)
    if not emails:
        return f"No emails found for '{query}'."
    return _mail_list(emails, total)


def get_email_tools(session_id: str = "", source: str = "browser", lang: str = "de", trust: str = "external") -> list[Tool]:
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

        elif action == "attachment":
            return await _attachment(session_id, agent_mailbox_account(), kwargs)

        elif action == "index_attachment":
            return await _index_attachment("email", agent_mailbox_account(), kwargs)

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
                path, err = resolve_outbound_attachment(attachment, session_id, source, trust)
                if err or path is None:
                    return f"Error: {err}"
                explicit.append(path)
            # The body is Markdown: images/links to /_upload/… files in it are
            # embedded/attached (same rendering as the channel reply path).
            from .media import render_mail
            try:
                mail = render_mail(body, session_id, source, explicit, trust)
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


def get_readonly_mailbox_tools(lang: str = "de", session_id: str = "") -> list[Tool]:
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
        "attachment": partial(_attachment, session_id),
        "index_attachment": partial(_index_attachment, "email_readonly"),
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
