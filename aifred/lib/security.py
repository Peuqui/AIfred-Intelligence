"""Security layer for AIfred — enforced by the pipeline, not by plugins.

Provides:
- Permission tiers: each Tool declares a tier, each context has a max tier
- Inbound sanitization: strip HTML, zero-width chars, add delimiters
- Outbound sanitization: block markdown image exfiltration, secret patterns
- Audit logging: every tool execution is recorded in SQLite
"""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .function_calling import Tool

logger = logging.getLogger(__name__)

# ============================================================
# TIER CONSTANTS
# ============================================================
TIER_READONLY = 0       # calculator, web_search, epim_search, list/search_documents
TIER_COMMUNICATE = 1    # email, discord_send, telegram_send
TIER_WRITE_DATA = 2     # epim_create, epim_update, store_memory, execute_code
TIER_WRITE_SYSTEM = 3   # delete_document, epim_delete
TIER_ADMIN = 4          # Shell, unrestricted code execution (future)

# Default max tier per source context
DEFAULT_TIER_BY_SOURCE: dict[str, int] = {
    "browser": TIER_ADMIN,          # User sits in front of the screen
    "freeecho2": TIER_COMMUNICATE,  # Voice terminal — configurable via Plugin Manager
    "email": TIER_COMMUNICATE,      # External message
    "discord": TIER_COMMUNICATE,    # External message
    "telegram": TIER_COMMUNICATE,   # External message
    "cron": TIER_COMMUNICATE,       # Unattended
    "webhook": TIER_READONLY,       # Externally triggered
}

# Tier i18n keys for labels and descriptions (value → label_key, desc_key)
TIER_I18N_KEYS: list[tuple[int, str, str]] = [
    (TIER_READONLY, "tier_0_label", "tier_0_desc"),
    (TIER_COMMUNICATE, "tier_1_label", "tier_1_desc"),
    (TIER_WRITE_DATA, "tier_2_label", "tier_2_desc"),
    (TIER_WRITE_SYSTEM, "tier_3_label", "tier_3_desc"),
    (TIER_ADMIN, "tier_4_label", "tier_4_desc"),
]


# Owner tier: what the owner gets when messaging via external channels.
# Higher than the channel default, but not full admin (no shell/code).
OWNER_TIER = TIER_WRITE_DATA  # Owner can create/update data, but not delete system files


def resolve_tier_for_sender(
    channel: str, sender: str, metadata: dict | None = None,
) -> int:
    """Determine the max tier for a sender on a channel.

    Priority: user-configured tier (settings.json) > DEFAULT_TIER_BY_SOURCE.
    Owner gets max(channel_tier, OWNER_TIER).
    """
    # Check user-configured tier override from Plugin Manager
    from .settings import load_settings
    settings = load_settings() or {}
    configured_tiers = settings.get("channel_security_tiers", {})
    if channel in configured_tiers:
        channel_default = int(configured_tiers[channel])
    else:
        channel_default = DEFAULT_TIER_BY_SOURCE.get(channel, TIER_COMMUNICATE)

    if _is_owner(channel, sender, metadata or {}):
        return max(channel_default, OWNER_TIER)

    return channel_default


def _is_owner(channel: str, sender: str, metadata: dict) -> bool:
    """Check if a sender is the owner for a given channel.

    Uses the channel's allowed_users/allowed_senders list from the broker.
    The FIRST entry in the whitelist is considered the owner.
    """
    from .credential_broker import broker

    if channel == "telegram":
        allowed = broker.get("telegram", "allowed_users").strip()
        if not allowed or allowed == "*":
            return False
        first_id = allowed.split(",")[0].strip()
        # Use user_id from metadata (more reliable than display name)
        user_id = str(metadata.get("user_id", ""))
        return user_id == first_id

    if channel == "email":
        allowed = broker.get("email", "allowed_senders").strip()
        if not allowed or allowed == "*":
            return False
        first_entry = allowed.split(",")[0].strip().lower()
        # Extract email from sender like '"Name" <user@mail.de>'
        import re
        match = re.search(r'[\w.+-]+@[\w.-]+', sender.lower())
        sender_email = match.group(0) if match else sender.lower()
        return sender_email == first_entry or sender_email.endswith(first_entry)

    if channel == "discord":
        # Discord doesn't have a simple owner concept in the whitelist.
        # For now, all whitelisted Discord users get channel default.
        return False

    return False


# ============================================================
# TIER FILTERING
# ============================================================

def filter_tools_by_tier(tools: list[Tool], max_tier: int) -> list[Tool]:
    """Return only tools whose tier is at or below *max_tier*."""
    return [t for t in tools if t.tier <= max_tier]


# ============================================================
# INBOUND SANITIZATION
# ============================================================

# Zero-width and invisible Unicode characters to strip
_INVISIBLE_CHARS = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f"   # zero-width joiners/marks
    "\u2060\u2061\u2062\u2063\u2064"     # invisible operators
    "\ufeff"                              # BOM / zero-width no-break space
    "\u00ad"                              # soft hyphen
    "\u034f"                              # combining grapheme joiner
    "\u061c"                              # Arabic letter mark
    "\u115f\u1160"                        # Hangul fillers
    "\u17b4\u17b5"                        # Khmer vowel inherent
    "\u180e"                              # Mongolian vowel separator
    "\uffa0"                              # Halfwidth Hangul filler
    "]"
)


class _HTMLTextExtractor(HTMLParser):
    """Extract visible text from HTML, discarding all tags."""

    def __init__(self) -> None:
        super().__init__()
        self._buf = StringIO()

    def handle_data(self, data: str) -> None:
        self._buf.write(data)

    def get_text(self) -> str:
        return self._buf.getvalue()


def _strip_html(text: str) -> str:
    """Remove HTML tags, keep only visible text content."""
    if "<" not in text:
        return text
    extractor = _HTMLTextExtractor()
    extractor.feed(text)
    return extractor.get_text()


def sanitize_inbound(text: str) -> str:
    """Clean external message text before it enters the pipeline.

    - Strip HTML tags (keep visible text only)
    - Remove zero-width / invisible Unicode characters
    - NFC-normalize Unicode
    """
    text = _strip_html(text)
    text = _INVISIBLE_CHARS.sub("", text)
    text = unicodedata.normalize("NFC", text)
    return text


def wrap_external_message(
    text: str, sender: str, channel: str, trust_level: str,
) -> str:
    """Wrap text in security delimiters for the LLM context."""
    return (
        f'<external_message sender="{sender}" channel="{channel}" trust="{trust_level}">\n'
        f"{text}\n"
        f"</external_message>"
    )


# ============================================================
# OUTBOUND SANITIZATION
# ============================================================

# Markdown image syntax: ![alt](url) — blocks external URLs
_MD_IMAGE_EXTERNAL = re.compile(
    r"!\[([^\]]*)\]\(https?://[^)]+\)",
    re.IGNORECASE,
)

# Common secret patterns (API keys, tokens, etc.)
_SECRET_PATTERNS = re.compile(
    r"(?:"
    r"sk-[a-zA-Z0-9_-]{20,}"          # OpenAI API keys
    r"|sk-proj-[a-zA-Z0-9_-]{20,}"    # OpenAI project keys
    r"|ghp_[a-zA-Z0-9]{36,}"          # GitHub personal access tokens
    r"|gho_[a-zA-Z0-9]{36,}"          # GitHub OAuth tokens
    r"|github_pat_[a-zA-Z0-9_]{20,}"  # GitHub fine-grained PATs
    r"|xoxb-[a-zA-Z0-9-]+"            # Slack bot tokens
    r"|xoxp-[a-zA-Z0-9-]+"            # Slack user tokens
    r"|AKIA[0-9A-Z]{16}"              # AWS access key IDs
    r"|glpat-[a-zA-Z0-9_-]{20,}"      # GitLab PATs
    r"|Bearer\s+[a-zA-Z0-9._~+/=-]{30,}"  # Bearer tokens
    r")"
)


def sanitize_outbound(text: str) -> str:
    """Sanitize LLM output before sending to external channels.

    - Replace markdown images with external URLs (exfiltration vector)
    - Redact detected secret patterns
    """
    text = _MD_IMAGE_EXTERNAL.sub(r"![image blocked by security policy]", text)
    text = _SECRET_PATTERNS.sub("[REDACTED]", text)
    return text


def sanitize_tool_output(text: str) -> str:
    """Sanitize tool output before it goes back into the LLM context window.

    Strips credentials that might leak through error messages or tool results.
    Lighter than sanitize_outbound — focuses on credential patterns only.
    """
    return _SECRET_PATTERNS.sub("[REDACTED]", text)


# ============================================================
# TOOL-CHAIN & RATE LIMITING
# ============================================================

class ToolChainLimitReached(Exception):
    """Raised when tool call chain depth exceeds the configured maximum."""


class RateLimitReached(Exception):
    """Raised when tool call rate exceeds the configured maximum."""


# ============================================================
# ACTION CONFIRMATION (Rule of Two)
# ============================================================
# A tool call from an external source that writes data requires confirmation.
# This is the "Rule of Two": max 2 of 3 (untrusted input, sensitive access,
# state change). When all 3 apply, the call is blocked.

def needs_confirmation(source: str, tool_tier: int, max_tier: int = -1) -> bool:
    """Check if a tool call needs human confirmation.

    Returns True when an external source tries to use a write-tier tool
    that was NOT explicitly allowed by the tier resolution (owner override).

    If max_tier is provided and tool_tier <= max_tier, the tool was
    explicitly allowed (e.g. owner sending via Telegram) — no confirmation.
    """
    if source == "browser":
        return False
    # If the tool was explicitly allowed by resolve_tier_for_sender, trust it
    if max_tier >= 0 and tool_tier <= max_tier:
        return False
    return tool_tier >= TIER_WRITE_DATA


class CircuitBreakerTripped(Exception):
    """Raised when a channel's circuit breaker is open."""


class _RateTracker:
    """Track tool call counts per source within a sliding time window.

    Also implements circuit breaker: if rate limit is exceeded N times
    within a window, the channel is temporarily blocked.
    """

    # After this many rate limit violations, the circuit breaker trips
    _BREAKER_THRESHOLD = 3
    # Channel is blocked for this many seconds after tripping
    _BREAKER_COOLDOWN_SEC = 300  # 5 minutes

    def __init__(self) -> None:
        self._calls: list[tuple[float, str]] = []  # (timestamp, source)
        self._violations: dict[str, list[float]] = {}  # source → [violation_timestamps]
        self._tripped: dict[str, float] = {}  # source → tripped_until timestamp

    def record_and_check(self, source: str, now: float) -> bool:
        """Record a call and return True if within limit, False if exceeded."""
        from .config import SECURITY_RATE_LIMIT_WINDOW_SEC, SECURITY_RATE_LIMITS

        limit = SECURITY_RATE_LIMITS.get(source, 0)
        if limit <= 0:
            return True  # Unlimited

        # Prune old entries
        cutoff = now - SECURITY_RATE_LIMIT_WINDOW_SEC
        self._calls = [(t, s) for t, s in self._calls if t > cutoff]

        # Count calls from this source
        count = sum(1 for _, s in self._calls if s == source)
        self._calls.append((now, source))

        if count >= limit:
            self._record_violation(source, now)
            return False
        return True

    def is_tripped(self, source: str, now: float) -> bool:
        """Check if the circuit breaker is currently open for a source."""
        tripped_until = self._tripped.get(source, 0)
        if now < tripped_until:
            return True
        # Auto-reset after cooldown
        if source in self._tripped:
            del self._tripped[source]
            from .logging_utils import log_message
            log_message(f"Security: circuit breaker reset for '{source}'")
        return False

    def _record_violation(self, source: str, now: float) -> None:
        """Track rate limit violations and trip breaker if threshold exceeded."""
        if source not in self._violations:
            self._violations[source] = []
        # Prune old violations
        cutoff = now - 60  # Violations within last 60 seconds
        self._violations[source] = [t for t in self._violations[source] if t > cutoff]
        self._violations[source].append(now)

        if len(self._violations[source]) >= self._BREAKER_THRESHOLD:
            self._tripped[source] = now + self._BREAKER_COOLDOWN_SEC
            self._violations[source] = []
            from .logging_utils import log_message
            log_message(
                f"Security: CIRCUIT BREAKER TRIPPED for '{source}' — "
                f"blocked for {self._BREAKER_COOLDOWN_SEC}s",
                "error",
            )


# Singleton rate tracker
_rate_tracker = _RateTracker()


def check_rate_limit(source: str) -> None:
    """Check rate limit and circuit breaker. Raises on violation."""
    import time
    now = time.time()

    # Circuit breaker check first
    if _rate_tracker.is_tripped(source, now):
        raise CircuitBreakerTripped(
            f"Channel '{source}' is temporarily blocked (circuit breaker)"
        )

    if not _rate_tracker.record_and_check(source, now):
        raise RateLimitReached(
            f"Rate limit exceeded for source '{source}'"
        )


# ============================================================
# AUDIT LOG
# ============================================================

_audit_db_path: Path | None = None
_audit_db_initialized = False


def _get_audit_db_path() -> Path:
    global _audit_db_path
    if _audit_db_path is None:
        from .config import SECURITY_AUDIT_DB
        _audit_db_path = SECURITY_AUDIT_DB
    return _audit_db_path


def _ensure_audit_db(conn: sqlite3.Connection) -> None:
    global _audit_db_initialized
    if _audit_db_initialized:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime')),
            session_id TEXT NOT NULL,
            source TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            tool_tier INTEGER NOT NULL,
            tool_args_preview TEXT,
            result_preview TEXT,
            success INTEGER NOT NULL,
            duration_ms REAL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_session
        ON tool_audit(session_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp
        ON tool_audit(timestamp)
    """)
    conn.commit()
    _audit_db_initialized = True


def audit_log(
    *,
    session_id: str,
    source: str,
    tool_name: str,
    tool_tier: int,
    tool_args_preview: str = "",
    result_preview: str = "",
    success: bool = True,
    duration_ms: float = 0.0,
) -> None:
    """Record a tool execution in the audit database. Fire-and-forget."""
    try:
        db_path = _get_audit_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path), timeout=5) as conn:
            _ensure_audit_db(conn)
            conn.execute(
                """INSERT INTO tool_audit
                   (session_id, source, tool_name, tool_tier,
                    tool_args_preview, result_preview, success, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    source,
                    tool_name,
                    tool_tier,
                    tool_args_preview[:500],
                    result_preview[:500],
                    1 if success else 0,
                    round(duration_ms, 1),
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("Audit log write failed: %s", exc)
