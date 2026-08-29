"""
Server-side Session Storage for AIfred

Stores chat history and session data per user account.
Uses username from cookie for identification.

Storage structure:
- data/accounts.json - Username → Password-Hash mapping
- data/sessions/<session_id>.json - Individual chat sessions

Each session belongs to a user (owner field).
Users can access their sessions from any device via username + password.
"""

import os
import json
import hashlib
import hmac
import re
import secrets
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

import bcrypt

from .config import DATA_DIR


# Session directory (subdirectory of data/)
SESSION_DIR = DATA_DIR / "sessions"

# SSOT: Session-ID format — exactly 32 lowercase hex chars (128 bit).
# Shared by sandbox.py and vision_utils.py for path-safety checks.
SESSION_ID_RE = re.compile(r"^[a-f0-9]{32}$")

# M4: Session files are written from MULTIPLE threads — the Reflex main
# loop (browser autosave), the message-hub worker thread (its own event
# loop), and the debug bus. Every read-modify-write on a session file
# MUST hold this lock for the WHOLE load→mutate→save sequence, otherwise
# the later writer silently overwrites the earlier one (lost update).
# One global RLock, deliberately not per-session: session ops are
# ms-scale file I/O, strictly serial is the simplest correct model
# (same decision as the EPIM DB serialization, EP6). RLock because the
# multi-step writers (save_user_to_session etc.) nest into the locked
# helpers here. New RMW functions MUST take this lock.
#
# Known limit (fixed by the Unified Inference Pipeline, not by locking):
# the browser's _save_current_session writes its full in-memory history —
# if the hub appended after the browser's last mtime-sync, that append
# is overwritten regardless of the lock (stale-state overwrite, not an
# RMW race).
session_rmw_lock = threading.RLock()

# Accounts file (username → password_hash mapping)
ACCOUNTS_FILE = DATA_DIR / "accounts.json"

# Whitelist file (list of allowed usernames)
WHITELIST_FILE = DATA_DIR / "allowed_users.json"


def _ensure_session_dir() -> None:
    """Create session directory if it doesn't exist."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Whitelist Management
# ============================================================

def _load_whitelist() -> List[str]:
    """
    Load whitelist of allowed usernames.

    Returns:
        List of allowed usernames (lowercase)
    """
    if not WHITELIST_FILE.exists():
        return []

    try:
        with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Normalize to lowercase
            return [u.lower() for u in data if isinstance(u, str)]
    except (json.JSONDecodeError, IOError):
        return []


def _save_whitelist(whitelist: List[str]) -> bool:
    """
    Save whitelist to file.

    Args:
        whitelist: List of allowed usernames

    Returns:
        True on success
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump(whitelist, f, ensure_ascii=False, indent=2)
        return True
    except IOError:
        return False


def is_username_allowed(username: str) -> bool:
    """
    Check if username is on the whitelist.

    If whitelist file doesn't exist or is empty, nobody can register.

    Args:
        username: Username to check

    Returns:
        True if username is allowed to register
    """
    whitelist = _load_whitelist()
    if not whitelist:
        return False
    return username.lower() in whitelist


def get_whitelist() -> List[str]:
    """
    Get list of allowed usernames.

    Returns:
        List of allowed usernames
    """
    return _load_whitelist()


def add_to_whitelist(username: str) -> bool:
    """
    Add username to whitelist.

    Args:
        username: Username to add (case-insensitive)

    Returns:
        True on success, False if already exists or error
    """
    if not username:
        return False

    whitelist = _load_whitelist()
    username_lower = username.lower()

    if username_lower in whitelist:
        return False  # Already on whitelist

    whitelist.append(username_lower)
    return _save_whitelist(whitelist)


def remove_from_whitelist(username: str) -> bool:
    """
    Remove username from whitelist.

    Args:
        username: Username to remove (case-insensitive)

    Returns:
        True on success, False if not found or error
    """
    if not username:
        return False

    whitelist = _load_whitelist()
    username_lower = username.lower()

    if username_lower not in whitelist:
        return False  # Not on whitelist

    whitelist.remove(username_lower)
    return _save_whitelist(whitelist)


# ============================================================
# Account Management (Username + Password)
# ============================================================

def _load_accounts() -> Dict[str, str]:
    """
    Load accounts file.

    Returns:
        Dict mapping username → password_hash
    """
    if not ACCOUNTS_FILE.exists():
        return {}

    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            result: Dict[str, str] = json.load(f)
            return result
    except (json.JSONDecodeError, IOError):
        return {}


def _save_accounts(accounts: Dict[str, str]) -> bool:
    """
    Save accounts file atomically (tmp + os.replace).

    A crash mid-write would otherwise leave accounts.json truncated, which
    locks every user out on the next start.

    Args:
        accounts: Dict mapping username → password_hash

    Returns:
        True on success
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = ACCOUNTS_FILE.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
        # Passwort-Hashes: nur der Service-User darf lesen (0600). Auf der
        # tmp-Datei gesetzt, damit die Rechte das os.replace atomar überleben.
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, ACCOUNTS_FILE)
        return True
    except IOError:
        try:
            ACCOUNTS_FILE.with_suffix(".json.tmp").unlink(missing_ok=True)
        except OSError:
            pass
        return False


def account_exists(username: str) -> bool:
    """
    Check if username already exists.

    Args:
        username: Username to check

    Returns:
        True if username exists
    """
    accounts = _load_accounts()
    return username.lower() in accounts


def create_account(username: str, password: str) -> bool:
    """
    Create new user account.

    Only succeeds if username is on the whitelist (allowed_users.json).

    Args:
        username: Unique username (case-insensitive)
        password: Password (will be hashed)

    Returns:
        True on success, False if username not allowed, exists, or error
    """
    if not username or not password:
        return False

    # Check whitelist first
    if not is_username_allowed(username):
        return False

    username_lower = username.lower()
    accounts = _load_accounts()

    if username_lower in accounts:
        return False  # Username already exists

    accounts[username_lower] = hash_password(password)
    return _save_accounts(accounts)


def verify_account(username: str, password: str) -> bool:
    """
    Verify username + password combination.

    On success, transparently upgrades legacy SHA-256 hashes to bcrypt
    so old accounts migrate without the user having to reset.

    Args:
        username: Username (case-insensitive)
        password: Password to verify

    Returns:
        True if credentials are correct
    """
    if not username or not password:
        return False

    accounts = _load_accounts()
    password_hash = accounts.get(username.lower())

    if not password_hash:
        return False

    if not verify_password(password, password_hash):
        return False

    if _is_legacy_hash(password_hash):
        try:
            accounts[username.lower()] = hash_password(password)
            _save_accounts(accounts)
        except (OSError, ValueError):
            # Upgrade is best-effort; authentication has already succeeded.
            pass

    return True


def list_accounts() -> List[str]:
    """
    List all usernames.

    Returns:
        List of usernames
    """
    return list(_load_accounts().keys())


def delete_account(username: str, delete_sessions: bool = False) -> bool:
    """
    Delete user account.

    Args:
        username: Username to delete (case-insensitive)
        delete_sessions: If True, also delete all sessions owned by this user

    Returns:
        True on success, False if not found or error
    """
    if not username:
        return False

    username_lower = username.lower()
    accounts = _load_accounts()

    if username_lower not in accounts:
        return False  # Account doesn't exist

    # Delete sessions if requested
    if delete_sessions:
        _ensure_session_dir()
        for session_file in SESSION_DIR.glob("*.json"):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("owner", "").lower() == username_lower:
                    session_file.unlink()
            except (json.JSONDecodeError, IOError):
                continue

    # Delete account
    del accounts[username_lower]
    return _save_accounts(accounts)


def generate_session_id() -> str:
    """
    Generate new unique Session-ID.

    Returns:
        32 character hex string (128 bit entropy)
    """
    return secrets.token_hex(16)  # 128 bits for secure session IDs


def _sanitize_session_id(session_id: str) -> str:
    """
    Validate Session-ID format (hex string with 32 characters).

    Only allows lowercase hex characters (a-f0-9) exactly 32 characters long.
    Prevents path traversal attacks through strict format checking.

    Args:
        session_id: Session-ID to validate

    Returns:
        Validated Session-ID

    Raises:
        ValueError: If format is invalid or session_id is None
    """
    # Check for None or empty
    if not session_id:
        raise ValueError("session_id cannot be None or empty")

    # Only allow lowercase hex: exactly 32 characters (128 bit)
    if not SESSION_ID_RE.match(session_id):
        raise ValueError(
            f"Invalid session_id format: Expected 32 hex chars, got '{str(session_id)[:50]}'"
        )

    return session_id


def get_session_path(session_id: str) -> Path:
    """
    Return path to session file with path traversal protection.

    Args:
        session_id: Session identifier

    Returns:
        Path to session JSON file

    Raises:
        ValueError: On invalid session_id or path traversal attempt
    """
    safe_id = _sanitize_session_id(session_id)
    path = (SESSION_DIR / f"{safe_id}.json").resolve()

    # Ensure path is within SESSION_DIR
    try:
        path.relative_to(SESSION_DIR.resolve())
    except ValueError:
        raise ValueError(f"Path traversal attempt detected: {session_id}")

    return path


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Load session for Session-ID.

    Note: Does NOT update last_seen timestamp (read-only operation).
    last_seen is written by save_session() when content is saved, and by
    touch_session() when the user deliberately switches to a session.

    Args:
        session_id: Session identifier

    Returns:
        Session dict or None if not found
    """
    _ensure_session_dir()

    try:
        session_path = get_session_path(session_id)
    except ValueError:
        return None

    if not session_path.exists():
        return None

    try:
        with open(session_path, "r", encoding="utf-8") as f:
            session: Dict[str, Any] = json.load(f)

        return session

    except (json.JSONDecodeError, IOError, KeyError):
        return None


def _write_session_file(path: Path, session: Dict[str, Any]) -> bool:
    """
    Write session dict to file (internal helper).

    Atomic write via tempfile + os.replace — crashing mid-write leaves the
    original file intact instead of truncating it.

    Args:
        path: Path to session file
        session: Session dict

    Returns:
        True on success
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except IOError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def create_empty_session(session_id: str, owner: str, channel: str = "") -> bool:
    """
    Create an empty session file for a new device.

    This is called when a user creates a new chat.
    Creates the file immediately so the API can inject messages right away.

    New sessions start with DEFAULT_SESSION_CONFIG — always clean defaults,
    not inherited from previous sessions or global settings.

    Args:
        session_id: Session identifier
        owner: Username who owns this session
        channel: Origin channel ("" = interactive browser session, otherwise
                 a background channel like "scheduler"/"email"). Purely
                 descriptive: it records where a session came from and is
                 reported by list_sessions().

    Returns:
        True on success, False on error
    """
    return save_session(
        session_id,
        {
            "data": {"config": dict(DEFAULT_SESSION_CONFIG)},
            "owner": owner.lower(),
            "channel": channel,
        },
    )


def save_session(
    session_id: str,
    session_data: Dict[str, Any],
    owner: Optional[str] = None
) -> bool:
    """
    Save session for Session-ID.

    Args:
        session_id: Session identifier
        session_data: Complete session dict
        owner: Username who owns this session (required for new sessions)

    Returns:
        True on success, False on error
    """
    _ensure_session_dir()

    try:
        session_path = get_session_path(session_id)
    except ValueError:
        return False

    with session_rmw_lock:
        # Ensure timestamps
        now = datetime.now().isoformat()
        if "created_at" not in session_data:
            session_data["created_at"] = now
        session_data["last_seen"] = now
        session_data["session_id"] = session_id

        # Set owner (only on creation, don't overwrite existing)
        if owner and "owner" not in session_data:
            session_data["owner"] = owner.lower()

        return _write_session_file(session_path, session_data)


def update_chat_data(
    session_id: str,
    chat_history: List[Dict[str, Any]],
    chat_summaries: Optional[List[str]] = None,
    llm_history: Optional[List[Dict[str, str]]] = None,
    debug_messages: Optional[List[str]] = None,
    is_generating: Optional[bool] = None,
    owner: Optional[str] = None
) -> bool:
    """
    Update chat data of a session.

    Creates new session if not present (requires owner for new sessions).
    More efficient than save_session() when only chat data changes.

    Args:
        session_id: Session identifier
        chat_history: List of ChatMessage dicts (UI - vollständig)
        chat_summaries: Optional - List of summary strings
        llm_history: Optional - List of {"role": ..., "content": ...} dicts (LLM - komprimiert)
        debug_messages: Optional - List of debug log entries (last N entries)
        is_generating: Optional - Current generation status (for API polling)
        owner: Optional - Username for new sessions (required if session doesn't exist)

    Returns:
        True on success
    """
    with session_rmw_lock:
        # Load existing session or create new one
        session = load_session(session_id)

        if session is None:
            # Session doesn't exist - create with owner (owner is REQUIRED)
            if not owner:
                raise ValueError(f"Cannot create session {session_id}: owner is required")
            session = {
                "created_at": datetime.now().isoformat(),
                "data": {"config": dict(DEFAULT_SESSION_CONFIG)},
                "owner": owner.lower()
            }

        # Update chat data (Dict-based format - no conversion needed)
        session["data"]["chat_history"] = chat_history

        if chat_summaries is not None:
            session["data"]["chat_summaries"] = list(chat_summaries)

        # DUAL-HISTORY (v2.13.0+): Store llm_history separately
        if llm_history is not None:
            session["data"]["llm_history"] = llm_history

        # DEBUG-PERSISTENCE (v2.14.0+): Store last N debug entries
        if debug_messages is not None:
            session["data"]["debug_messages"] = debug_messages

        # API STATUS (v2.15.9+): Store is_generating for API polling
        if is_generating is not None:
            session["data"]["is_generating"] = is_generating

        return save_session(session_id, session)


# ============================================================
# Session Config (per-session agent/mode persistence)
# ============================================================

# Clean defaults for new sessions — explicitly hardcoded, not inherited
# from global settings. Every new session starts from this state.
DEFAULT_SESSION_CONFIG: Dict[str, Any] = {
    "active_agent": "aifred",
    "multi_agent_mode": "standard",
    "symposion_agents": [],
    "research_mode": "automatik",
}


def get_session_config(session_id: str) -> Dict[str, Any]:
    """
    Get the config block of a session.

    Returns DEFAULT_SESSION_CONFIG if session has no config block
    (new session or session without config yet).

    Args:
        session_id: Session identifier

    Returns:
        Config dict with keys: active_agent, multi_agent_mode,
        symposion_agents, research_mode
    """
    session = load_session(session_id)
    if not session:
        return dict(DEFAULT_SESSION_CONFIG)
    config = session.get("data", {}).get("config")
    if not isinstance(config, dict):
        return dict(DEFAULT_SESSION_CONFIG)
    # Merge with defaults so callers always get all expected keys
    merged = dict(DEFAULT_SESSION_CONFIG)
    merged.update(config)
    return merged


def update_session_config(session_id: str, **config_updates: Any) -> bool:
    """
    Update the config block of a session.

    Only the fields passed as kwargs are updated — the rest stays.
    Used by browser handlers, API endpoints, and message processors
    to persist agent/mode choices per session.

    Args:
        session_id: Session identifier
        **config_updates: Fields to update (active_agent, multi_agent_mode,
            symposion_agents, research_mode)

    Returns:
        True on success, False if session not found or write failed
    """
    if not config_updates:
        return True

    with session_rmw_lock:
        session = load_session(session_id)
        if session is None:
            return False

        data = session.setdefault("data", {})
        config = data.setdefault("config", dict(DEFAULT_SESSION_CONFIG))
        config.update(config_updates)
        return save_session(session_id, session)


def set_session_active_agent(session_id: str, agent_id: str) -> bool:
    """
    Set the active agent for a session — universal entry point for routing.

    All routing pathways (Wake-Word-Override, Voice-Mode-Switch,
    Inline-Address detection, UI agent picker) should funnel through
    this function. Validates ``agent_id`` against the agent registry;
    unknown ids are silently ignored to avoid corrupting the session
    config (callers can decide whether to fall back).

    Args:
        session_id: Session identifier
        agent_id: Lowercase agent id (matched against ``agent_config``)

    Returns:
        True on success, False if session/agent not found or write failed
    """
    if not agent_id or not session_id:
        return False
    from .agent_config import get_agent_config
    if get_agent_config(agent_id) is None:
        return False
    return update_session_config(session_id, active_agent=agent_id)


def delete_session(session_id: str, expected_owner: Optional[str] = None) -> bool:
    """
    Delete session completely, including associated images and audio.

    Args:
        session_id: Session identifier
        expected_owner: If set, only delete when the session's owner field
            matches this username (case-insensitive). Returns False on mismatch.

    Returns:
        True on success, False on owner mismatch / not found / error.
    """
    try:
        session_path = get_session_path(session_id)

        # Load session data before deleting to find referenced HTML files
        if session_path.exists():
            try:
                session_data = json.loads(session_path.read_text(encoding="utf-8"))
                if expected_owner is not None:
                    actual_owner = str(session_data.get("owner", "")).lower()
                    if actual_owner != expected_owner.lower():
                        return False
                from .formatting import cleanup_session_html
                cleanup_session_html(session_data)
            except (json.JSONDecodeError, OSError):
                # If we cannot read the file but an owner check was requested,
                # refuse to delete — fail safe.
                if expected_owner is not None:
                    return False
            session_path.unlink()

        # Clean up orphan .pending flag (if any) — prevents stale message injection
        # after the session is gone.
        try:
            safe_id = _sanitize_session_id(session_id)
            (SESSION_DIR / f"{safe_id}.pending").unlink(missing_ok=True)
        except ValueError:
            pass

        # Also cleanup associated images
        from .vision_utils import cleanup_session_images
        cleanup_session_images(session_id)

        # Also cleanup associated audio
        from .audio_processing import cleanup_session_audio
        cleanup_session_audio(session_id)

        # Browser screenshots die with the session — plots and generated
        # HTML stay, they are deliverables the user curates in the storage
        # tab and often outlive the chat they were made in.
        from .sandbox import cleanup_session_screenshots
        cleanup_session_screenshots(session_id)

        # Remove routing table entries (Hub channels → this session)
        from .routing_table import routing_table
        routing_table.delete_routes_for_session(session_id)

        return True
    except (ValueError, IOError):
        return False


# ============================================================
# Password Hashing Functions
# ============================================================

_BCRYPT_PREFIX = "bcrypt:"
_LEGACY_SHA256_PREFIX = "sha256:"
_BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    """
    Hash password with bcrypt (cost=12, per-password salt).

    Args:
        password: Plaintext password

    Returns:
        Hash string in format "bcrypt:..." (legacy "sha256:..." hashes
        from previous versions are still verifiable via verify_password
        and are transparently upgraded by verify_account on next login).
    """
    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(_BCRYPT_ROUNDS))
    return f"{_BCRYPT_PREFIX}{digest.decode('ascii')}"


def _is_legacy_hash(password_hash: str) -> bool:
    """True if the stored hash uses the legacy unsalted SHA-256 scheme."""
    return bool(password_hash) and password_hash.startswith(_LEGACY_SHA256_PREFIX)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify password against stored hash.

    Supports the current bcrypt scheme and the legacy unsalted SHA-256
    scheme. Constant-time compare in both branches.
    """
    if not password or not password_hash:
        return False

    if password_hash.startswith(_BCRYPT_PREFIX):
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                password_hash[len(_BCRYPT_PREFIX):].encode("ascii"),
            )
        except (ValueError, TypeError):
            return False

    if password_hash.startswith(_LEGACY_SHA256_PREFIX):
        legacy = f"{_LEGACY_SHA256_PREFIX}{hashlib.sha256(password.encode()).hexdigest()}"
        return hmac.compare_digest(legacy, password_hash)

    return False


# ============================================================
# Session Discovery (for API access)
# ============================================================

def get_latest_session_file() -> Optional[Path]:
    """
    Get the most recently modified session file.

    Useful for API access when session_id is not known.
    Returns the session file with the newest modification time.

    Returns:
        Path to newest session file, or None if no sessions exist
    """
    _ensure_session_dir()

    session_files = list(SESSION_DIR.glob("*.json"))
    if not session_files:
        return None

    # Sort by modification time, newest first
    session_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return session_files[0]


# Metadata cache for list_sessions(): filename → (mtime_ns, size, meta).
# Session files grow to hundreds of KB (coding sessions), and the picker
# needs only title/timestamps/count — re-parsing every file on every
# refresh made bulk deletes sluggish. A file is re-parsed only when its
# mtime or size changed. No lock: dict ops are GIL-atomic, a concurrent
# refresh at worst parses a file twice.
_session_meta_cache: Dict[str, tuple] = {}


def list_sessions(owner: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List sessions with basic info, optionally filtered by owner.

    Args:
        owner: Username to filter by (case-insensitive). If None, returns empty list.

    Returns list of dicts with:
    - session_id: Session identifier
    - title: Chat title (LLM-generated, or None if not yet set)
    - last_seen: Last activity timestamp
    - created_at: Session creation timestamp
    - message_count: Number of chat messages
    - owner: Username who owns this session
    - channel: Origin channel ("" = interactive browser session)

    Returns:
        List of session info dicts, sorted by last_seen (newest first)
    """
    _ensure_session_dir()

    owner_lower = owner.lower() if owner else None
    sessions = []
    seen_files = set()

    for session_file in SESSION_DIR.glob("*.json"):
        try:
            stat = session_file.stat()
            seen_files.add(session_file.name)

            cached = _session_meta_cache.get(session_file.name)
            if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
                meta = cached[2]
            else:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                chat_history = data.get("data", {}).get("chat_history", [])
                meta = {
                    "session_id": session_file.stem,
                    "title": data.get("data", {}).get("title"),
                    "last_seen": data.get("last_seen", ""),
                    "created_at": data.get("created_at", ""),
                    "message_count": len(chat_history),
                    "owner": data.get("owner", "").lower(),
                    "channel": data.get("channel", ""),
                }
                _session_meta_cache[session_file.name] = (
                    stat.st_mtime_ns, stat.st_size, meta,
                )

            # Filter AFTER the cache lookup — the cache is owner-agnostic
            # so one entry serves every logged-in user.
            if owner_lower and meta["owner"] != owner_lower:
                continue

            sessions.append(dict(meta))
        except (json.JSONDecodeError, IOError, OSError):
            continue

    # Evict entries for deleted files so the cache mirrors the directory.
    for stale in set(_session_meta_cache) - seen_files:
        _session_meta_cache.pop(stale, None)

    # Sort by last_seen, newest first
    sessions.sort(key=lambda s: s.get("last_seen", ""), reverse=True)
    return sessions


def update_session_title(session_id: str, title: str) -> bool:
    """
    Update the title of a session.

    Called after first Q&A pair to set an LLM-generated title.

    Args:
        session_id: Session identifier
        title: The generated chat title

    Returns:
        True on success, False on error
    """
    with session_rmw_lock:
        session = load_session(session_id)
        if session is None:
            return False

        if "data" not in session:
            session["data"] = {}

        session["data"]["title"] = title
        return save_session(session_id, session)


def touch_session(session_id: str) -> bool:
    """
    Mark a session as active now (updates last_seen).

    Called when the user switches to a session: opening a session counts as
    activity, so it ranks as the most recent one on the next login auto-load
    (see _load_latest_session()). load_session() itself stays read-only —
    only this explicit call moves a session to the top.

    Args:
        session_id: Session identifier

    Returns:
        True on success, False if the session does not exist
    """
    with session_rmw_lock:
        session = load_session(session_id)
        if session is None:
            return False

        # save_session() stamps last_seen — no field handling needed here.
        return save_session(session_id, session)


def get_session_title(session_id: str) -> Optional[str]:
    """
    Get the title of a session.

    Args:
        session_id: Session identifier

    Returns:
        Title string or None if not set
    """
    session = load_session(session_id)
    if session is None:
        return None

    title: str | None = session.get("data", {}).get("title")
    return title


# ============================================================
# API Update Flags (for Browser Auto-Reload)
# ============================================================

# NOTE: The legacy update_flag mechanism (set_update_flag / check_and_clear_update_flag)
# was removed. Browser tabs now detect session changes via mtime-watching on the
# session file directly (SSOT). See _base.py tick-handler for the implementation.


# ============================================================
# Pending Message (API → Browser Message Injection)
# ============================================================

def set_pending_message(session_id: str, message: str) -> bool:
    """
    Set pending message for browser to process.

    Called by API to inject a message into a browser session.
    Browser polls for .pending flag, reads message, triggers send_message().

    Args:
        session_id: Session identifier
        message: User message to inject

    Returns:
        True on success
    """
    _ensure_session_dir()

    try:
        safe_id = _sanitize_session_id(session_id)

        # Load existing session or create minimal structure
        session_path = get_session_path(session_id)
        if session_path.exists():
            with open(session_path, 'r', encoding='utf-8') as f:
                session = json.load(f)
        else:
            session = {"session_id": session_id, "data": {}}

        # Set pending message in session data
        if "data" not in session:
            session["data"] = {}
        session["data"]["pending_message"] = message

        # Save session (atomic write — crash mid-write leaves prior content intact)
        if not _write_session_file(session_path, session):
            return False

        # Set .pending flag file
        flag_path = SESSION_DIR / f"{safe_id}.pending"
        flag_path.touch()

        return True
    except (ValueError, IOError, json.JSONDecodeError):
        return False


def get_and_clear_pending_message(session_id: str) -> Optional[str]:
    """
    Get pending message and clear it.

    Called by browser to check for API-injected messages.
    Returns the message if present, clears it from session.

    Args:
        session_id: Session identifier

    Returns:
        Pending message string, or None if no pending message
    """
    _ensure_session_dir()

    try:
        safe_id = _sanitize_session_id(session_id)
        flag_path = SESSION_DIR / f"{safe_id}.pending"

        # Check flag first (fast path)
        if not flag_path.exists():
            return None

        # Flag exists - clear it (missing_ok: another caller may race-clear it)
        flag_path.unlink(missing_ok=True)

        # Load session and extract message
        session_path = get_session_path(session_id)
        if not session_path.exists():
            return None

        with open(session_path, 'r', encoding='utf-8') as f:
            session = json.load(f)

        pending_msg = session.get("data", {}).get("pending_message")
        if not pending_msg:
            return None

        # Clear pending_message from session (atomic write)
        session["data"]["pending_message"] = None
        _write_session_file(session_path, session)

        result: str | None = pending_msg
        return result

    except (ValueError, IOError, json.JSONDecodeError):
        return None
