"""E-Mail Channel Plugin — IMAP IDLE listener + SMTP reply.

Drop-in plugin for the Message Hub channel system.
Monitors an IMAP inbox via IDLE and sends replies via SMTP.
"""

from __future__ import annotations

import asyncio
import email as email_lib
import email.utils
import imaplib
import pathlib
import ssl
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ....lib.plugin_base import BaseChannel, CredentialField
from ....lib.logging_utils import log_message

if TYPE_CHECKING:
    from ....lib.envelope import InboundMessage, OutboundMessage
    from ....lib.function_calling import Tool
    from ....lib.plugin_base import PluginContext

# How often to re-issue IDLE (RFC recommends max 29 min, we use 5 min)
_IDLE_TIMEOUT_SECONDS = 5 * 60

# After a connection error, wait before reconnecting
_RECONNECT_DELAY_SECONDS = 30

# Persistent tracking: last processed UID (survives worker restarts)
_CHECKPOINT_FILE = None  # Initialized lazily


def _get_checkpoint_file() -> pathlib.Path:
    """Get path to the IMAP checkpoint file."""
    global _CHECKPOINT_FILE
    if _CHECKPOINT_FILE is None:
        from ....lib.config import DATA_DIR
        _CHECKPOINT_FILE = DATA_DIR / "message_hub" / "imap_checkpoint.json"
        _CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    return _CHECKPOINT_FILE


def _load_checkpoint() -> tuple[int, int]:
    """Load last processed UID and UIDVALIDITY from disk.

    Returns (last_uid, uidvalidity). Both 0 if no checkpoint exists.
    """
    import json
    path = _get_checkpoint_file()
    if not path.exists():
        return 0, 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("last_uid", 0)), int(data.get("uidvalidity", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0, 0


def _save_checkpoint(last_uid: int, uidvalidity: int) -> None:
    """Persist the last processed UID and UIDVALIDITY."""
    import json
    path = _get_checkpoint_file()
    path.write_text(
        json.dumps({"last_uid": last_uid, "uidvalidity": uidvalidity}),
        encoding="utf-8",
    )


class EmailChannel(BaseChannel):
    """E-Mail channel via IMAP IDLE + SMTP."""

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "email"

    @property
    def display_name(self) -> str:
        return "E-Mail"

    @property
    def description(self) -> str:
        return "IMAP-IDLE-Listener für E-Mail-Eingang plus optionaler Auto-Reply per SMTP."

    @property
    def icon(self) -> str:
        return "mail"

    # ── Credentials ───────────────────────────────────────────

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="EMAIL_IMAP_HOST",
                label_key="email_cred_imap_host",
                placeholder="imap.example.com",
                group="imap",
                width_ratio=3,
            ),
            CredentialField(
                env_key="EMAIL_IMAP_PORT",
                label_key="email_cred_imap_port",
                default="993",
                group="imap",
                width_ratio=1,
            ),
            CredentialField(
                env_key="EMAIL_SMTP_HOST",
                label_key="email_cred_smtp_host",
                placeholder="smtp.example.com",
                group="smtp",
                width_ratio=3,
            ),
            CredentialField(
                env_key="EMAIL_SMTP_PORT",
                label_key="email_cred_smtp_port",
                default="587",
                group="smtp",
                width_ratio=1,
            ),
            CredentialField(
                env_key="EMAIL_USER",
                label_key="email_cred_user",
                placeholder="user@example.com",
            ),
            CredentialField(
                env_key="EMAIL_PASSWORD",
                label_key="email_cred_password",
                is_password=True,  # is_secret=True implied by is_password
            ),
            CredentialField(
                env_key="EMAIL_FROM",
                label_key="email_cred_from",
                placeholder="Display Name",
            ),
            CredentialField(
                env_key="EMAIL_ALLOWED_SENDERS",
                label_key="email_cred_allowed_senders",
                placeholder="user@example.com, @family.de",
            ),
        ]

    def is_configured(self) -> bool:
        from ....lib.credential_broker import broker
        return (
            broker.get("email", "enabled").lower() == "true"
            and broker.is_set("email", "imap_host")
            and broker.is_set("email", "user")
            and broker.is_set("email", "password")
        )

    def apply_credentials(self, values: dict[str, str]) -> None:
        """Update runtime credentials via the broker."""
        from ....lib.credential_broker import broker

        broker.set_runtime("email", "enabled", "true")

        # Map UI field env_keys to broker (service, key) pairs
        _field_to_broker = {
            "EMAIL_IMAP_HOST": ("email", "imap_host"),
            "EMAIL_IMAP_PORT": ("email", "imap_port"),
            "EMAIL_SMTP_HOST": ("email", "smtp_host"),
            "EMAIL_SMTP_PORT": ("email", "smtp_port"),
            "EMAIL_USER": ("email", "user"),
            "EMAIL_PASSWORD": ("email", "password"),
            "EMAIL_FROM": ("email", "from"),
            "EMAIL_ALLOWED_SENDERS": ("email", "allowed_senders"),
        }
        for env_key, (service, key) in _field_to_broker.items():
            val = values.get(env_key, "")
            if val:
                broker.set_runtime(service, key, val)

        # Default EMAIL_FROM to EMAIL_USER
        if not values.get("EMAIL_FROM"):
            broker.set_runtime("email", "from", values.get("EMAIL_USER", ""))

    # ── Listener ──────────────────────────────────────────────

    async def listener_loop(self) -> None:
        """IMAP IDLE loop — runs until cancelled."""
        from ....lib.credential_broker import broker

        if not self.is_configured():
            self.channel_log("Email Plugin: not configured, not starting", "warning")
            return

        self.channel_log("Email Plugin: starting IMAP IDLE listener...")

        while True:
            imap: imaplib.IMAP4_SSL | None = None
            try:
                imap, uidvalidity = await asyncio.to_thread(
                    _connect_imap,
                    broker.get("email", "imap_host"),
                    int(broker.get("email", "imap_port") or "993"),
                    broker.get("email", "user"),
                    broker.get("email", "password"),
                )

                # ── Startup Recovery ─────────────────────────────
                # Check for emails that arrived while we were down.
                saved_uid, saved_uv = _load_checkpoint()
                all_uids = await asyncio.to_thread(_get_existing_uids, imap)
                max_uid = max(int(u) for u in all_uids) if all_uids else 0

                if saved_uv != 0 and saved_uv != uidvalidity:
                    self.channel_log("Email Plugin: UIDVALIDITY changed, skipping recovery")
                    missed_uids: set[bytes] = set()
                elif saved_uid > 0:
                    missed_uids = {u for u in all_uids if int(u) > saved_uid}
                else:
                    # First run ever — no checkpoint, treat all as known
                    missed_uids = set()

                # Advance checkpoint BEFORE processing to prevent
                # parallel workers from re-processing the same mails.
                if max_uid > saved_uid:
                    _save_checkpoint(max_uid, uidvalidity)

                if missed_uids:
                    self.channel_log(f"Email Plugin: recovering {len(missed_uids)} missed email(s)...")
                    for uid in sorted(missed_uids, key=lambda u: int(u)):
                        await self._process_uid(imap, uid)

                known_uids = all_uids
                self.channel_log(f"Email Plugin: connected, {len(known_uids)} existing messages"
                            f" (checkpoint UID {max_uid})")

                while True:
                    # Wrap entire IDLE cycle in timeout — if ANY part hangs
                    # (dead socket, stale connection), we break out and reconnect.
                    try:
                        await asyncio.wait_for(
                            self._idle_cycle(imap),
                            timeout=_IDLE_TIMEOUT_SECONDS + 60,  # IDLE timeout + margin for DONE/drain
                        )
                    except asyncio.TimeoutError:
                        self.channel_log("Email Plugin: IDLE cycle timeout, reconnecting...")
                        raise OSError("IDLE cycle timeout")  # triggers reconnect

                    # Check for new messages after IDLE wakeup
                    current_uids = await asyncio.to_thread(_get_existing_uids, imap)
                    new_uids = current_uids - known_uids

                    for uid in sorted(new_uids, key=lambda u: int(u)):
                        await self._process_uid(imap, uid)

                    # Update checkpoint to highest UID
                    known_uids = current_uids
                    if known_uids:
                        max_uid = max(int(u) for u in known_uids)
                        _save_checkpoint(max_uid, uidvalidity)

            except asyncio.CancelledError:
                self.channel_log("Email Plugin: shutting down")
                if imap:
                    try:
                        imap.logout()
                    except Exception:
                        pass
                return

            except OSError as exc:
                if "IDLE" in str(exc) and "timeout" in str(exc):
                    # Normal IDLE timeout — not an error, just reconnect immediately
                    if imap:
                        try:
                            imap.logout()
                        except Exception:
                            pass
                    continue
                self.channel_log(f"Email Plugin: error — {exc}, reconnecting in {_RECONNECT_DELAY_SECONDS}s", "error")
                if imap:
                    try:
                        imap.logout()
                    except Exception:
                        pass
                await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
            except Exception as exc:
                self.channel_log(f"Email Plugin: error — {exc}, reconnecting in {_RECONNECT_DELAY_SECONDS}s", "error")
                if imap:
                    try:
                        imap.logout()
                    except Exception:
                        pass
                await asyncio.sleep(_RECONNECT_DELAY_SECONDS)

    # ── IDLE cycle ────────────────────────────────────────────

    async def _idle_cycle(self, imap: imaplib.IMAP4_SSL) -> None:
        """Single IDLE enter → wait → exit cycle.

        Wrapped in asyncio.wait_for by the caller so a dead connection
        triggers a timeout instead of hanging forever.
        """
        tag = b"A001"
        await asyncio.to_thread(imap.send, tag + b" IDLE\r\n")  # type: ignore[arg-type]

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(_read_idle_response, imap),
                timeout=_IDLE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self.channel_log("Email Plugin: IDLE timeout (no events), re-entering IDLE")
            response = b""

        if response:
            self.channel_log(f"Email Plugin: IDLE wakeup — {response.strip().decode(errors='replace')}")

        # Exit IDLE — DONE + drain can hang on dead connections.
        # Use a short timeout so we don't wait for the outer watchdog.
        try:
            await asyncio.wait_for(
                asyncio.to_thread(imap.send, b"DONE\r\n"),  # type: ignore[arg-type]
                timeout=10,
            )
            await asyncio.wait_for(
                asyncio.to_thread(_drain_idle_response, imap, tag),
                timeout=10,
            )
        except asyncio.TimeoutError:
            # DONE/drain hung — connection is dead, let outer loop reconnect
            raise OSError("IDLE exit timeout")

    # ── Single-mail processing ─────────────────────────────────

    async def _process_uid(self, imap: imaplib.IMAP4_SSL, uid: bytes) -> None:
        """Fetch, validate, and dispatch a single email by UID.

        Writes checkpoint after processing (including blocked mails)
        so no UID is retried endlessly.
        """
        inbound = await asyncio.to_thread(_fetch_email_as_inbound, imap, uid)
        if not inbound:
            self._update_checkpoint(uid)
            return
        if not _is_sender_allowed(inbound.sender):
            self.channel_log(f"Email Plugin: blocked mail from {inbound.sender} (not in whitelist)")
            self._update_checkpoint(uid)
            return
        self.channel_log(f"Email Plugin: new mail from {inbound.sender} — {inbound.metadata.get('subject', '?')}")
        await _dispatch_inbound(inbound)
        self._update_checkpoint(uid)

    @staticmethod
    def _update_checkpoint(uid: bytes) -> None:
        """Advance checkpoint to this UID."""
        uidvalidity = _load_checkpoint()[1]
        _save_checkpoint(int(uid), uidvalidity)

    # ── Reply ─────────────────────────────────────────────────

    async def send_reply(self, outbound: "OutboundMessage", original: "InboundMessage") -> None:
        """Send an email reply via SMTP."""
        from .client import send_email
        from ....lib.routing_table import routing_table

        subject = outbound.metadata.get("subject", "Re: AIfred")
        reply_to_id = outbound.metadata.get("in_reply_to")

        # Look up session_id so send_email can register the route
        route = routing_table.get_route("email", original.channel_id)
        sid = route.session_id if route else None

        send_email(
            to=outbound.recipient,
            subject=subject,
            body=outbound.text,
            reply_to_id=reply_to_id,
            session_id=sid,
        )
        from ....lib.debug_bus import debug
        debug(f"📤 Auto-reply sent to {outbound.recipient}")

    # ── Context ───────────────────────────────────────────────

    def build_context(self, message: "InboundMessage") -> str:
        """Prepare email for LLM with sender/subject context."""
        from ....lib.prompt_loader import load_prompt

        subject = message.metadata.get("subject", "?")
        return load_prompt(
            "shared/channel_email",
            sender=message.sender,
            subject=subject,
            text=message.text,
        )

    # ── Tools (LLM can read/send/search emails) ────────────────

    def get_tools(self, ctx: "PluginContext") -> list["Tool"]:
        """Email tools for LLM function calling."""
        from .tools import get_email_tools
        return get_email_tools(session_id=ctx.session_id)

    def build_reply_metadata(self, message: "InboundMessage") -> dict:
        """Build email-specific reply headers (In-Reply-To, References)."""
        subject = message.metadata.get("subject", "")
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        return {
            "subject": subject,
            "in_reply_to": message.metadata.get("message_id", ""),
            "references": message.metadata.get("message_id", ""),
        }


# ── Sender whitelist ──────────────────────────────────────────

def _is_sender_allowed(sender: str) -> bool:
    """Check if an email sender is in the whitelist.

    Whitelist from EMAIL_ALLOWED_SENDERS (via broker):
    - Empty = nobody allowed (safe default)
    - "*" = everyone allowed
    - Comma-separated addresses/domains: "user@mail.de, @family.de"
      - "@domain.de" matches any address at that domain
    """
    from ....lib.credential_broker import broker
    whitelist_raw = broker.get("email", "allowed_senders").strip()

    if not whitelist_raw:
        return False

    if whitelist_raw == "*":
        return True

    # Extract email address from sender string like '"Lord Helmchen" <user@mail.de>'
    sender_lower = sender.lower()
    import re
    match = re.search(r'[\w.+-]+@[\w.-]+', sender_lower)
    sender_email = match.group(0) if match else sender_lower

    entries = [e.strip().lower() for e in whitelist_raw.split(",") if e.strip()]
    for entry in entries:
        if entry.startswith("@"):
            # Domain match
            if sender_email.endswith(entry):
                return True
        else:
            # Exact address match
            if sender_email == entry:
                return True

    return False


# ── IMAP helpers (moved from imap_listener.py) ───────────────


def _connect_imap(host: str, port: int, user: str, password: str) -> tuple[imaplib.IMAP4_SSL, int]:
    """Create a fresh IMAP connection and select INBOX.

    Returns (imap_connection, uidvalidity).
    """
    ctx = ssl.create_default_context()
    imap = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    imap.login(user, password)
    status, data = imap.select("INBOX")
    # UIDVALIDITY from SELECT response (data[0] is message count, not uidvalidity)
    # Parse from imap.response('UIDVALIDITY') or use a STATUS command
    uidvalidity = 0
    try:
        _, uv_data = imap.status("INBOX", "(UIDVALIDITY)")
        if uv_data and uv_data[0]:
            import re
            match = re.search(rb"UIDVALIDITY\s+(\d+)", uv_data[0])
            if match:
                uidvalidity = int(match.group(1))
    except Exception:
        pass
    return imap, uidvalidity


def _get_existing_uids(imap: imaplib.IMAP4_SSL) -> set[bytes]:
    """Get all current message UIDs in INBOX."""
    _, data = imap.uid("SEARCH", None, "ALL")  # type: ignore[arg-type]
    if data[0]:
        return set(data[0].split())
    return set()


def _fetch_email_as_inbound(imap: imaplib.IMAP4_SSL, uid: bytes) -> "InboundMessage | None":
    """Fetch a single email by UID and convert to InboundMessage."""
    from .client import _decode_header, _extract_body
    from ....lib.config import EMAIL_MAX_BODY_CHARS
    from ....lib.envelope import InboundMessage

    _, msg_data = imap.uid("FETCH", uid, "(RFC822)")  # type: ignore[arg-type]
    if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple):
        return None

    msg = email_lib.message_from_bytes(msg_data[0][1])

    sender = _decode_header(msg.get("From", ""))
    subject = _decode_header(msg.get("Subject", ""))
    body = _extract_body(msg)[:EMAIL_MAX_BODY_CHARS]
    message_id = msg.get("Message-ID", "")
    in_reply_to = msg.get("In-Reply-To", "")
    references = msg.get("References", "")

    # Parse date
    date_raw = msg.get("Date", "")
    try:
        timestamp = email_lib.utils.parsedate_to_datetime(date_raw)
    except (ValueError, TypeError):
        timestamp = datetime.now(timezone.utc)

    # Thread-ID: use In-Reply-To or first Reference, or Message-ID
    thread_id = in_reply_to or (references.split()[0] if references else message_id)

    return InboundMessage(
        channel="email",
        channel_id=thread_id,
        sender=sender,
        text=body,
        timestamp=timestamp,
        metadata={
            "subject": subject,
            "message_id": message_id,
            "in_reply_to": in_reply_to,
            "references": references,
            "uid": uid.decode(),
        },
    )


def _read_idle_response(imap: imaplib.IMAP4_SSL) -> bytes:
    """Blocking read of one IDLE event from the server."""
    while True:
        line = imap.readline()
        if line.startswith(b"+"):
            continue  # Skip IDLE ack ("+ idling")
        return line


def _drain_idle_response(imap: imaplib.IMAP4_SSL, tag: bytes) -> None:
    """Read and discard lines until the tagged IDLE completion response."""
    for _ in range(20):
        line = imap.readline()
        if line.startswith(tag):
            return


async def _dispatch_inbound(message: "InboundMessage") -> None:
    """Hand an inbound message to the message processor."""
    from ....lib.message_processor import process_inbound

    outbound = await process_inbound(message)

    if outbound:
        log_message(
            f"Email Plugin: processed — reply "
            f"{'sent' if outbound.metadata.get('sent') else 'ready'} "
            f"for {outbound.recipient}"
        )


# Module-level instance — discovered by registry
EmailChannel_instance = EmailChannel()
