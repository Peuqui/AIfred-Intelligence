"""E-Mail client for IMAP (read) and SMTP (send).

All operations are synchronous (imaplib/smtplib) — wrapped in
asyncio.to_thread() by the tool executors.

Credentials are accessed exclusively through the CredentialBroker.

Two mailboxes: AIfred's own (service ``email``) and a read-only second one
(service ``email_readonly``). check_inbox, read_email and search_emails take the
account as a required argument and work through _ReadOnlyMailbox; everything
that sends or changes mail has no account parameter and reaches only AIfred's own
mailbox through _imap_connect.
"""

import base64
import email
import email.header
import email.utils
import imaplib
import quopri
import smtplib
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from ....lib.credential_broker import broker
from ....lib.logging_utils import log_message
from .config import (
    EMAIL_IMAP_PORT_DEFAULT,
    EMAIL_MAX_BODY_CHARS,
    EMAIL_MAX_FETCH,
    EMAIL_SENT_FOLDER_DEFAULT,
)


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
    auth_results: str  # SPF/DKIM/DMARC verdict: pass / fail / none
    attachments: list[str] = field(default_factory=list)  # Attachment filenames


@dataclass(frozen=True)
class ImapAccount:
    """Where and as whom to log in."""

    host: str
    port: int
    user: str
    password: str = field(repr=False)


# Broker service of the read-only second mailbox
_READONLY_SERVICE = "email_readonly"

# Fields the read-only mailbox cannot work without; the port has a default.
_READONLY_REQUIRED_KEYS = ("imap_host", "user", "password")

# PEEK leaves \Seen alone, so reading never marks a mail as read. These are the
# only FETCH forms of the read functions (see _ReadOnlyMailbox).
_FETCH_SUMMARY = "(FLAGS BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.400>)"
_FETCH_HEADERS = "(FLAGS BODY.PEEK[HEADER])"
_FETCH_MESSAGE = "(BODY.PEEK[])"

# IMAP socket timeout (seconds) for tool operations. Without it a hung/slow
# server blocks a to_thread worker indefinitely and can exhaust the pool.
_IMAP_TIMEOUT_SECONDS = 30


def _safe_decode(payload: bytes, charset: str | None) -> str:
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


def _decode_header(raw: str | None) -> str:
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


def _safe_parsedate(date_raw: str) -> datetime | None:
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


def _decode_preview(preview_raw: bytes, msg: email.message.Message) -> str:
    """Best-effort decode of a ``BODY[TEXT]`` prefix for the inbox preview.

    Non-multipart mails with base64/quoted-printable transfer encoding
    would otherwise show raw encoding garbage. Multipart bodies (prefix =
    MIME boundary + part headers) stay raw — a real decode would need a
    BODYSTRUCTURE roundtrip, which a 200-char preview doesn't justify.
    Decode failure degrades VISIBLY to the raw prefix (never fatal).
    """
    if not msg.get_content_type().startswith("multipart"):
        cte = (msg.get("Content-Transfer-Encoding") or "").strip().lower()
        charset = msg.get_content_charset()
        try:
            if cte == "base64":
                stripped = b"".join(preview_raw.split())
                stripped = stripped[: len(stripped) - len(stripped) % 4]
                return _safe_decode(base64.b64decode(stripped), charset)
            if cte == "quoted-printable":
                return _safe_decode(quopri.decodestring(preview_raw), charset)
        except Exception:  # noqa: BLE001 — preview only, raw prefix shows instead
            pass
    return preview_raw.decode("utf-8", errors="replace")


def _broker_account(service: str) -> ImapAccount:
    """The account stored under one broker service."""
    return ImapAccount(
        host=broker.get(service, "imap_host"),
        port=int(broker.get(service, "imap_port") or EMAIL_IMAP_PORT_DEFAULT),
        user=broker.get(service, "user"),
        password=broker.get(service, "password"),
    )


def agent_mailbox_account() -> ImapAccount:
    """AIfred's own mailbox."""
    return _broker_account("email")


def readonly_mailbox_configured() -> bool:
    """Whether the second mailbox has its own credentials. Only its own fields
    count: the fields of AIfred's mailbox never stand in for missing ones."""
    return all(broker.is_set(_READONLY_SERVICE, key) for key in _READONLY_REQUIRED_KEYS)


def readonly_mailbox_account() -> ImapAccount:
    """The read-only second mailbox."""
    return _broker_account(_READONLY_SERVICE)


def _open_imap(account: ImapAccount, timeout: float = _IMAP_TIMEOUT_SECONDS) -> imaplib.IMAP4_SSL:
    """Authenticated TLS connection to ``account``."""
    ctx = ssl.create_default_context()
    imap = imaplib.IMAP4_SSL(account.host, account.port, ssl_context=ctx, timeout=timeout)
    imap.login(account.user, account.password)
    return imap


def _imap_connect(timeout: float = _IMAP_TIMEOUT_SECONDS) -> imaplib.IMAP4_SSL:
    """Connection to AIfred's own mailbox, the only one that may be changed:
    listener and every writing function. It takes no account on purpose.

    ``timeout``: Tools nutzen den 30-s-Default; der IDLE-Listener übergibt
    sein längeres Fenster (IDLE-Phase ist legitim minutenlang still).
    """
    return _open_imap(agent_mailbox_account(), timeout)


class _ReadOnlyMailbox:
    """A folder opened with EXAMINE, the whole IMAP surface of the read functions.

    They hold this object, never the connection: STORE, COPY, APPEND, EXPUNGE and
    a FETCH without PEEK do not exist for them, on any account. EXAMINE lets the
    server refuse flag changes on top, but it would still allow COPY and APPEND,
    so it cannot carry the guarantee alone.
    """

    def __init__(self, imap: imaplib.IMAP4_SSL, folder: str) -> None:
        status, detail = imap.select(folder, readonly=True)
        # Refused, imaplib stays connected but unselected: without this check the
        # next command would fail with a state error that hides the real reason.
        if status != "OK":
            raise ValueError(f"Cannot open IMAP folder {folder!r}: {detail}")
        self._imap = imap

    def search(self, criteria: str) -> list[bytes]:
        """UIDs matching ASCII search criteria, ascending."""
        _, data = self._imap.uid("SEARCH", None, criteria)  # type: ignore[arg-type]
        return data[0].split() if data and data[0] else []

    def search_header(self, header: str, text: str) -> list[bytes]:
        """UIDs whose ``header`` contains non-ASCII ``text``. imaplib encodes
        command args as ASCII, so the text travels as an IMAP literal (length-
        prefixed, no escaping needed) with CHARSET UTF-8; imaplib supports only
        one literal per command, hence one header per call."""
        self._imap.literal = text.encode("utf-8")  # type: ignore[assignment]
        _, data = self._imap.uid("SEARCH", "CHARSET", "UTF-8", header)
        return data[0].split() if data and data[0] else []

    def fetch_summary(self, uid: str) -> list:
        """Flags, headers and the start of the body."""
        return self._imap.uid("FETCH", uid, _FETCH_SUMMARY)[1]

    def fetch_headers(self, uid: str) -> list:
        """Flags and headers."""
        return self._imap.uid("FETCH", uid, _FETCH_HEADERS)[1]

    def fetch_message(self, uid: str) -> list:
        """The complete message."""
        return self._imap.uid("FETCH", uid, _FETCH_MESSAGE)[1]


@contextmanager
def _examine(account: ImapAccount, folder: str) -> Iterator[_ReadOnlyMailbox]:
    """Open ``folder`` of ``account`` for reading, for the length of the block."""
    with _open_imap(account) as imap:
        yield _ReadOnlyMailbox(imap, folder)


def check_inbox(account: ImapAccount, n: int = EMAIL_MAX_FETCH, folder: str = "INBOX") -> list[EmailSummary]:
    """Fetch the N most recent emails from IMAP inbox.

    Returned ``msg_id`` values are IMAP UIDs — stable across connections
    (unlike sequence numbers, which shift on every expunge), so a later
    read/delete/move on a separate connection hits the same mail.
    """
    _reject_imap_arg(folder, "folder")
    # n <= 0 would slice to the ENTIRE mailbox (msg_ids[-0:] == all)
    n = max(1, n)
    results: list[EmailSummary] = []

    with _examine(account, folder) as mailbox:
        msg_ids = [u.decode() for u in mailbox.search("ALL")]
        recent_ids = msg_ids[-n:]
        recent_ids.reverse()  # Newest first

        for msg_id in recent_ids:
            msg_data = mailbox.fetch_summary(msg_id)
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
                    if "BODY[HEADER]" in desc:
                        header_raw = part[1] if len(part) > 1 else b""
                    if "BODY[TEXT]" in desc:
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

                preview = _decode_preview(preview_raw, msg)[:200].strip()

                results.append(EmailSummary(
                    msg_id=msg_id,
                    subject=subject,
                    sender=sender,
                    date=date_str,
                    preview=preview,
                    is_read=is_read,
                ))

    log_message(f"📧 Email: fetched {len(results)} from {folder}")
    return results


def read_email(account: ImapAccount, msg_id: str, folder: str = "INBOX") -> EmailMessage:
    """Read full email by UID (as returned by check_inbox/search_emails)."""
    _reject_imap_arg(msg_id, "msg_id")
    _reject_imap_arg(folder, "folder")
    with _examine(account, folder) as mailbox:
        msg_data = mailbox.fetch_message(msg_id)
        if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple):
            raise ValueError(f"Email {msg_id} not found")

        msg = email.message_from_bytes(msg_data[0][1])
        body = _extract_body(msg)[:EMAIL_MAX_BODY_CHARS]
        attachments = _get_attachments(msg)

        date = msg.get("Date", "")
        parsed_date = _safe_parsedate(date)
        date_str = parsed_date.strftime("%d.%m.%Y %H:%M") if parsed_date else date

        from . import _parse_auth_results

        log_message(f"📧 Email: read msg {msg_id}")
        return EmailMessage(
            msg_id=msg_id,
            subject=_decode_header(msg.get("Subject", "")),
            sender=_decode_header(msg.get("From", "")),
            to=_decode_header(msg.get("To", "")),
            date=date_str,
            body=body,
            auth_results=_parse_auth_results(msg),
            attachments=attachments,
        )


def search_emails(
    account: ImapAccount, query: str, folder: str = "INBOX", n: int = EMAIL_MAX_FETCH
) -> list[EmailSummary]:
    """Search emails via IMAP SEARCH. Returned ``msg_id`` values are UIDs."""
    _reject_imap_arg(query, "query")
    _reject_imap_arg(folder, "folder")
    # n <= 0 would slice to ALL matches
    n = max(1, n)
    results: list[EmailSummary] = []

    with _examine(account, folder) as mailbox:
        # IMAP SEARCH: search in subject and from. Escape backslash/quote so a
        # crafted query (LLM tool arg) cannot break out of the quoted string and
        # rewrite the search semantics. CRLF is rejected above (would inject a
        # raw command since imaplib does not escape it).
        if query.isascii():
            safe_query = query.replace("\\", "\\\\").replace('"', '\\"')
            uids = set(mailbox.search(f'(OR SUBJECT "{safe_query}" FROM "{safe_query}")'))
        else:
            # Non-ASCII ("Müller"): SUBJECT and FROM are searched separately
            # (see search_header) and the UID sets are merged.
            uids = set(mailbox.search_header("SUBJECT", query)) | set(mailbox.search_header("FROM", query))
        msg_ids = sorted((u.decode() for u in uids), key=int)[-n:]
        msg_ids.reverse()

        for msg_id in msg_ids:
            msg_data = mailbox.fetch_headers(msg_id)
            if not msg_data or not msg_data[0]:
                continue

            for part in msg_data:
                if isinstance(part, tuple) and b"HEADER" in part[0]:
                    msg = email.message_from_bytes(part[1])
                    date = msg.get("Date", "")
                    parsed_date = _safe_parsedate(date)

                    results.append(EmailSummary(
                        msg_id=msg_id,
                        subject=_decode_header(msg.get("Subject", "")),
                        sender=_decode_header(msg.get("From", "")),
                        date=parsed_date.strftime("%d.%m.%Y %H:%M") if parsed_date else date,
                        preview="",
                    ))

    log_message(f"📧 Email: search '{query}' → {len(results)} results")
    return results


def _file_part(path: str, disposition: str, content_id: str | None = None) -> email.message.Message:
    """A MIME part for a local file; ``content_id`` makes it addressable as ``cid:``."""
    import mimetypes
    from email import encoders as _encoders
    from email.mime.base import MIMEBase

    ctype, _enc = mimetypes.guess_type(path)
    maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
    part = MIMEBase(maintype, subtype or "octet-stream")
    with open(path, "rb") as fh:
        part.set_payload(fh.read())
    _encoders.encode_base64(part)
    if content_id:
        part.add_header("Content-ID", f"<{content_id}>")
    # Path(...).name strips any directory — the recipient sees just the
    # filename, never a server path.
    part.add_header("Content-Disposition", disposition, filename=Path(path).name)
    return part


def send_email(
    to: str,
    subject: str,
    body: str,
    reply_to_id: str | None = None,
    session_id: str | None = None,
    html: str | None = None,
    attachments: list[str] | None = None,
    inline_images: list[tuple[str, str]] | None = None,
) -> str:
    """Send an email via SMTP. Returns confirmation string.

    If session_id is provided, registers the outgoing Message-ID in the
    routing table so replies land in the same session.

    When ``html`` is given, a multipart/alternative message is built
    with both the plain-text body (``body``) and the HTML version, so
    rich-text mail clients render the HTML and lite clients fall back
    to the plain version. Without ``html``, a plain-text-only message
    is sent (no multipart wrapping).

    ``inline_images`` are ``(content_id, local path)`` pairs the HTML refers
    to as ``cid:<content_id>``: the body becomes a multipart/related with the
    images as inline parts (needs ``html``). ``attachments`` are local file
    paths: the message above becomes the first part of a multipart/mixed
    container with the files attached. The caller is responsible for
    resolving/validating the paths (see resolve_outbound_attachment).
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

    body_part: email.message.Message
    if html:
        # multipart/alternative: text first, then html — RFC 2046 says
        # the most-faithful representation comes last, so the mail client
        # picks html when it can render it, falls back to text otherwise.
        body_part = MIMEMultipart("alternative")
        body_part.attach(MIMEText(body, "plain", "utf-8"))
        body_part.attach(MIMEText(html, "html", "utf-8"))
    else:
        body_part = MIMEText(body, "plain", "utf-8")

    msg: email.message.Message
    if inline_images:
        if not html:
            raise ValueError("Inline images need an HTML body")
        # multipart/related: the body first, the images it refers to by cid.
        related = MIMEMultipart("related")
        related.attach(body_part)
        for content_id, image_path in inline_images:
            related.attach(_file_part(image_path, "inline", content_id))
        body_part = related

    if attachments:
        # multipart/mixed: the body (text, alternative or related) as the
        # first part, the files as attachment parts.
        mixed = MIMEMultipart("mixed")
        mixed.attach(body_part)
        for attachment_path in attachments:
            mixed.attach(_file_part(attachment_path, "attachment"))
        msg = mixed
    else:
        msg = body_part
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    # Generate Message-ID so replies can be routed back
    message_id = email.utils.make_msgid(domain=email_user.split("@")[-1] if "@" in email_user else "local")
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

    # Copy to Sent folder (like any normal mail client). Folder name is
    # configurable (EMAIL_SENT_FOLDER, provider-specific) — no guessing chain.
    sent_folder = broker.get("email", "sent_folder") or EMAIL_SENT_FOLDER_DEFAULT
    try:
        with _imap_connect() as imap:
            status, _ = imap.select(sent_folder)
            if status == "OK":
                import time
                imap.append(sent_folder, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            else:
                log_message(
                    f"📧 Email: Sent folder '{sent_folder}' not found — copy skipped "
                    f"(set EMAIL_SENT_FOLDER in the credentials dialog)", "warning"
                )
    except Exception as exc:
        log_message(f"📧 Email: could not copy to Sent folder '{sent_folder}': {exc}", "warning")

    # Register outgoing Message-ID for session routing (single source of truth)
    if session_id:
        from ....lib.routing_table import routing_table
        routing_table.set_route("email", message_id, session_id)

    log_message(f"📧 Email: sent to {to} — {subject} (Message-ID: {message_id})")
    return f"Email sent to {to}: {subject} [msg_id:{message_id}]"


def delete_email(msg_id: str, folder: str = "INBOX") -> str:
    """Delete an email by UID.

    UID commands — sequence numbers shift when any client expunges between
    the listing and this call, which would delete the WRONG mail.
    """
    _reject_imap_arg(msg_id, "msg_id")
    _reject_imap_arg(folder, "folder")
    with _imap_connect() as imap:
        imap.select(folder)
        imap.uid("STORE", msg_id, "+FLAGS", "\\Deleted")
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
        # UID COPY to target, then delete from source (UIDs are stable
        # across connections, sequence numbers are not)
        status, _ = imap.uid("COPY", msg_id, target_folder)
        if status != "OK":
            raise ValueError(f"COPY failed: {status}")
        imap.uid("STORE", msg_id, "+FLAGS", "\\Deleted")
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
        imap.uid("STORE", msg_id, action, imap_flag)

    log_message(f"📧 Email: marked msg {msg_id} as {flag}")
    return f"Email {msg_id} marked as {flag}"
