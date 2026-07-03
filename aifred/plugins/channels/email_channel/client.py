"""E-Mail client for IMAP (read) and SMTP (send).

All operations are synchronous (imaplib/smtplib) — wrapped in
asyncio.to_thread() by the tool executors.

Credentials are accessed exclusively through the CredentialBroker.
"""

import email
import email.header
import email.utils
import imaplib
import smtplib
import ssl
from datetime import datetime
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from ....lib.config import EMAIL_MAX_BODY_CHARS, EMAIL_MAX_FETCH
from ....lib.credential_broker import broker
from ....lib.logging_utils import log_message


@dataclass
class EmailSummary:
    """Compact email representation for inbox listing."""

    msg_id: str
    subject: str
    sender: str
    date: str
    preview: str  # First ~200 chars of body
    is_read: bool = True


@dataclass
class EmailMessage:
    """Full email with body text."""

    msg_id: str
    subject: str
    sender: str
    to: str
    date: str
    body: str
    attachments: list[str] = field(default_factory=list)  # Attachment filenames


# IMAP socket timeout (seconds) for tool operations. Without it a hung/slow
# server blocks a to_thread worker indefinitely and can exhaust the pool.
_IMAP_TIMEOUT_SECONDS = 30


def _safe_decode(payload: bytes, charset: Optional[str]) -> str:
    """Decode bytes with a charset, tolerant of unknown/bogus charset names.

    ``bytes.decode(errors="replace")`` only replaces undecodable *bytes* — an
    unknown codec name (e.g. a crafted ``charset=nonsense``) still raises
    ``LookupError``. We catch that and fall back to utf-8 so a single malformed
    mail cannot crash the listener (which would retry the same UID forever).
    """
    try:
        return payload.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _reject_imap_arg(value: str, field_name: str) -> str:
    """Reject CR/LF in an IMAP command argument (msg_id, folder names).

    imaplib appends arguments to the command line terminated by CRLF without
    escaping, so an embedded ``\\r\\n`` from an LLM-generated tool arg would
    inject raw IMAP commands into the stream. Fail loudly instead.
    """
    if any(ch in value for ch in "\r\n"):
        raise ValueError(f"Illegal newline in IMAP argument {field_name!r}")
    return value


def _decode_header(raw: Optional[str]) -> str:
    """Decode RFC2047 encoded header."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(_safe_decode(part, charset))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    return _safe_decode(payload, part.get_content_charset())
        # Fallback: try text/html
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    return f"[HTML]\n{_safe_decode(payload, part.get_content_charset())}"
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            return _safe_decode(payload, msg.get_content_charset())
    return ""


def _get_attachments(msg: email.message.Message) -> list[str]:
    """Get list of attachment filenames."""
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                filename = part.get_filename()
                if filename:
                    attachments.append(_decode_header(filename))
    return attachments


def _safe_parsedate(date_raw: str) -> Optional[datetime]:
    """Parse an RFC 2822 Date header, tolerant of malformed values.

    ``parsedate_to_datetime`` raises on a broken Date header; a crafted mail
    must not abort a tool call. Returns None when unparseable.
    """
    if not date_raw:
        return None
    try:
        return email.utils.parsedate_to_datetime(date_raw)
    except (ValueError, TypeError):
        return None


def _imap_connect() -> imaplib.IMAP4_SSL:
    """Create authenticated IMAP connection via broker."""
    ctx = ssl.create_default_context()
    host = broker.get("email", "imap_host")
    port = int(broker.get("email", "imap_port") or "993")
    imap = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=_IMAP_TIMEOUT_SECONDS)
    imap.login(broker.get("email", "user"), broker.get("email", "password"))
    return imap


def check_inbox(n: int = EMAIL_MAX_FETCH, folder: str = "INBOX") -> list[EmailSummary]:
    """Fetch the N most recent emails from IMAP inbox."""
    _reject_imap_arg(folder, "folder")
    results: list[EmailSummary] = []

    with _imap_connect() as imap:
        imap.select(folder, readonly=True)

        _, data = imap.search(None, "ALL")
        msg_ids = data[0].split()
        recent_ids = msg_ids[-n:] if len(msg_ids) > n else msg_ids
        recent_ids.reverse()  # Newest first

        for msg_id in recent_ids:
            _, msg_data = imap.fetch(msg_id, "(FLAGS RFC822.HEADER BODY.PEEK[TEXT]<0.400>)")
            if not msg_data or not msg_data[0]:
                continue

            # Parse flags
            flags_raw = ""
            header_raw = b""
            preview_raw = b""

            for part in msg_data:
                if isinstance(part, tuple):
                    desc = part[0].decode("utf-8", errors="replace") if isinstance(part[0], bytes) else str(part[0])
                    if "FLAGS" in desc:
                        flags_raw = desc
                    if "HEADER" in desc:
                        header_raw = part[1] if len(part) > 1 else b""
                    if "TEXT" in desc or "BODY" in desc:
                        preview_raw = part[1] if len(part) > 1 else b""

            is_read = "\\Seen" in flags_raw

            if header_raw:
                msg = email.message_from_bytes(header_raw)
                subject = _decode_header(msg.get("Subject", ""))
                sender = _decode_header(msg.get("From", ""))
                date = msg.get("Date", "")
                # Parse date to readable format
                parsed_date = _safe_parsedate(date)
                date_str = parsed_date.strftime("%d.%m.%Y %H:%M") if parsed_date else date

                preview = preview_raw.decode("utf-8", errors="replace")[:200].strip()

                results.append(EmailSummary(
                    msg_id=msg_id.decode(),
                    subject=subject,
                    sender=sender,
                    date=date_str,
                    preview=preview,
                    is_read=is_read,
                ))

    log_message(f"📧 Email: fetched {len(results)} from {folder}")
    return results


def read_email(msg_id: str, folder: str = "INBOX") -> EmailMessage:
    """Read full email by message ID."""
    _reject_imap_arg(msg_id, "msg_id")
    _reject_imap_arg(folder, "folder")
    with _imap_connect() as imap:
        imap.select(folder, readonly=True)

        _, msg_data = imap.fetch(msg_id, "(RFC822)")
        if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple):
            raise ValueError(f"Email {msg_id} not found")

        msg = email.message_from_bytes(msg_data[0][1])
        body = _extract_body(msg)[:EMAIL_MAX_BODY_CHARS]
        attachments = _get_attachments(msg)

        date = msg.get("Date", "")
        parsed_date = _safe_parsedate(date)
        date_str = parsed_date.strftime("%d.%m.%Y %H:%M") if parsed_date else date

        log_message(f"📧 Email: read msg {msg_id}")
        return EmailMessage(
            msg_id=msg_id,
            subject=_decode_header(msg.get("Subject", "")),
            sender=_decode_header(msg.get("From", "")),
            to=_decode_header(msg.get("To", "")),
            date=date_str,
            body=body,
            attachments=attachments,
        )


def search_emails(query: str, folder: str = "INBOX", n: int = EMAIL_MAX_FETCH) -> list[EmailSummary]:
    """Search emails via IMAP SEARCH."""
    _reject_imap_arg(query, "query")
    _reject_imap_arg(folder, "folder")
    results: list[EmailSummary] = []

    with _imap_connect() as imap:
        imap.select(folder, readonly=True)

        # IMAP SEARCH: search in subject and from. Escape backslash/quote so a
        # crafted query (LLM tool arg) cannot break out of the quoted string and
        # rewrite the search semantics. CRLF is rejected above (would inject a
        # raw command since imaplib does not escape it).
        safe_query = query.replace("\\", "\\\\").replace('"', '\\"')
        search_criteria = f'(OR SUBJECT "{safe_query}" FROM "{safe_query}")'
        _, data = imap.search(None, search_criteria)
        msg_ids = data[0].split()[-n:]
        msg_ids.reverse()

        for msg_id in msg_ids:
            _, msg_data = imap.fetch(msg_id, "(FLAGS RFC822.HEADER)")
            if not msg_data or not msg_data[0]:
                continue

            for part in msg_data:
                if isinstance(part, tuple) and b"HEADER" in part[0]:
                    msg = email.message_from_bytes(part[1])
                    date = msg.get("Date", "")
                    parsed_date = _safe_parsedate(date)

                    results.append(EmailSummary(
                        msg_id=msg_id.decode(),
                        subject=_decode_header(msg.get("Subject", "")),
                        sender=_decode_header(msg.get("From", "")),
                        date=parsed_date.strftime("%d.%m.%Y %H:%M") if parsed_date else date,
                        preview="",
                    ))

    log_message(f"📧 Email: search '{query}' → {len(results)} results")
    return results


def send_email(
    to: str,
    subject: str,
    body: str,
    reply_to_id: Optional[str] = None,
    session_id: Optional[str] = None,
    html: Optional[str] = None,
) -> str:
    """Send an email via SMTP. Returns confirmation string.

    If session_id is provided, registers the outgoing Message-ID in the
    routing table so replies land in the same session.

    When ``html`` is given, a multipart/alternative message is built
    with both the plain-text body (``body``) and the HTML version, so
    rich-text mail clients render the HTML and lite clients fall back
    to the plain version. Without ``html``, a plain-text-only message
    is sent (no multipart wrapping).
    """
    # Reject control characters in header values (CR/LF would otherwise raise
    # an opaque HeaderParseError deep in smtplib). to/subject come from
    # LLM-generated tool args — fail loudly and early with a clear message.
    for field_name, value in (("to", to), ("subject", subject)):
        if any(ch in value for ch in "\r\n"):
            raise ValueError(f"Illegal newline in email {field_name!r}")
    # reply_to_id comes from an attacker-controlled inbound Message-ID; a CRLF
    # would otherwise raise deep in the email flattener and abort the reply.
    if reply_to_id and any(ch in reply_to_id for ch in "\r\n"):
        raise ValueError("Illegal newline in reply_to_id")

    email_user = broker.get("email", "user")
    email_from = broker.get("email", "from") or email_user

    # If EMAIL_FROM is a display name without address, combine with EMAIL_USER
    if email_from and "@" not in email_from:
        sender = f'"{email_from}" <{email_user}>'
    else:
        sender = email_from

    msg: email.message.Message
    if html:
        # multipart/alternative: text first, then html — RFC 2046 says
        # the most-faithful representation comes last, so the mail client
        # picks html when it can render it, falls back to text otherwise.
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    # Generate Message-ID so replies can be routed back
    import email.utils as _eu
    message_id = _eu.make_msgid(domain=email_user.split("@")[-1] if "@" in email_user else "local")
    msg["Message-ID"] = message_id
    if reply_to_id:
        msg["In-Reply-To"] = reply_to_id
        msg["References"] = reply_to_id
    # RFC 3834: AIfred-Mails sind maschinell erzeugt — der Header hält
    # wohlerzogene Autoresponder (Out-of-Office etc.) davon ab, auf unsere
    # Antworten wieder zu antworten (Loop-Schutz, Gegenstück zum
    # Inbound-Skip in _process_uid).
    msg["Auto-Submitted"] = "auto-replied" if reply_to_id else "auto-generated"

    smtp_host = broker.get("email", "smtp_host")
    smtp_port = int(broker.get("email", "smtp_port") or "587")
    ctx = ssl.create_default_context()
    # Port 465 = implizites TLS (SMTPS). Sonst STARTTLS — smtplib wirft
    # SMTPNotSupportedError, wenn ein MITM die STARTTLS-Capability strippt;
    # es gibt keinen stillen Klartext-Fallback (Login erst nach TLS).
    with (
        smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx)
        if smtp_port == 465
        else smtplib.SMTP(smtp_host, smtp_port)
    ) as smtp:
        if smtp_port != 465:
            smtp.starttls(context=ctx)
        smtp.login(email_user, broker.get("email", "password"))
        smtp.send_message(msg)

    # Copy to Sent folder (like any normal mail client)
    try:
        with _imap_connect() as imap:
            # GMX uses "Gesendet", other providers may use "Sent"
            sent_folder = "Gesendet"
            status, _ = imap.select(sent_folder)
            if status != "OK":
                sent_folder = "Sent"
                status, _ = imap.select(sent_folder)
            if status == "OK":
                import time
                imap.append(sent_folder, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    except Exception as exc:
        log_message(f"📧 Email: could not copy to Sent folder: {exc}", "warning")

    # Register outgoing Message-ID for session routing (single source of truth)
    if session_id:
        from ....lib.routing_table import routing_table
        routing_table.set_route("email", message_id, session_id)

    log_message(f"📧 Email: sent to {to} — {subject} (Message-ID: {message_id})")
    return f"Email sent to {to}: {subject} [msg_id:{message_id}]"


def delete_email(msg_id: str, folder: str = "INBOX") -> str:
    """Delete an email by message ID (moves to Trash)."""
    _reject_imap_arg(msg_id, "msg_id")
    _reject_imap_arg(folder, "folder")
    with _imap_connect() as imap:
        imap.select(folder)
        imap.store(msg_id, '+FLAGS', '\\Deleted')
        imap.expunge()

    log_message(f"📧 Email: deleted msg {msg_id} from {folder}")
    return f"Email {msg_id} deleted from {folder}"


def move_email(msg_id: str, target_folder: str, source_folder: str = "INBOX") -> str:
    """Move an email to a different folder via IMAP COPY + DELETE."""
    _reject_imap_arg(msg_id, "msg_id")
    _reject_imap_arg(target_folder, "target_folder")
    _reject_imap_arg(source_folder, "source_folder")
    with _imap_connect() as imap:
        imap.select(source_folder)
        # COPY to target, then delete from source
        status, _ = imap.copy(msg_id, target_folder)
        if status != "OK":
            raise ValueError(f"COPY failed: {status}")
        imap.store(msg_id, '+FLAGS', '\\Deleted')
        imap.expunge()

    log_message(f"📧 Email: moved msg {msg_id} from {source_folder} to {target_folder}")
    return f"Email {msg_id} moved to {target_folder}"


def list_folders() -> list[str]:
    """List all IMAP folders/mailboxes."""
    with _imap_connect() as imap:
        status, folder_data = imap.list()
        if status != "OK":
            return []
        folders = []
        for item in folder_data:
            if isinstance(item, bytes):
                # Format: (\\flags) "delimiter" "name"
                parts = item.decode("utf-8", errors="replace")
                # Extract folder name (last quoted string or last word)
                if '"' in parts:
                    name = parts.rstrip('"').rsplit('"', 1)[-1].strip()
                    if not name:
                        # Try second-to-last quoted segment
                        segments = parts.split('"')
                        name = segments[-2] if len(segments) >= 2 else parts.split()[-1]
                else:
                    name = parts.split()[-1]
                folders.append(name)

    log_message(f"📧 Email: listed {len(folders)} folders")
    return folders


def create_folder(folder_name: str) -> str:
    """Create a new IMAP folder/mailbox."""
    _reject_imap_arg(folder_name, "folder_name")
    with _imap_connect() as imap:
        status, response = imap.create(folder_name)
        if status != "OK":
            raise ValueError(f"CREATE failed: {status} — {response}")

    log_message(f"📧 Email: created folder '{folder_name}'")
    return f"Folder '{folder_name}' created"


def mark_email(msg_id: str, flag: str, folder: str = "INBOX") -> str:
    """Set or remove a flag on an email.

    flag: "read", "unread", "flagged", "unflagged"
    """
    flag_map = {
        "read": ("+FLAGS", "\\Seen"),
        "unread": ("-FLAGS", "\\Seen"),
        "flagged": ("+FLAGS", "\\Flagged"),
        "unflagged": ("-FLAGS", "\\Flagged"),
    }
    if flag not in flag_map:
        raise ValueError(f"Unknown flag: {flag}. Use: {list(flag_map.keys())}")

    action, imap_flag = flag_map[flag]

    _reject_imap_arg(msg_id, "msg_id")
    _reject_imap_arg(folder, "folder")
    with _imap_connect() as imap:
        imap.select(folder)
        imap.store(msg_id, action, imap_flag)

    log_message(f"📧 Email: marked msg {msg_id} as {flag}")
    return f"Email {msg_id} marked as {flag}"
