"""The debug log file (data/logs/aifred_debug.log):
- log_message(): every log line, when FILE_DEBUG_ENABLED
- log_exception(): a line with the traceback, for a boundary that catches broadly
- log_raw_messages() and log_system_prompt(): prompts sent to LLMs

What a session's debug console shows goes through the debug bus (lib/debug_bus.py).
"""

import os
import traceback
from datetime import datetime

from .config import DATA_DIR, FILE_DEBUG_ENABLED

# ============================================================
# DEBUG-LOG-FILE Configuration
# ============================================================
_LOGS_DIR = DATA_DIR / "logs"

# Create logs directory if not exists
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

DEBUG_LOG_FILE = str(_LOGS_DIR / "aifred_debug.log")
_debug_log_initialized = False
DEBUG_LOG_MAX_SIZE_MB = 1  # Max 1 MB, then rotate

def initialize_debug_log(force_reset: bool = False) -> None:
    """
    Initialize debug log file.

    Mode: 'w' (overwrite) - fresh log on each service start
    Change to 'a' (append) when debugging across restarts
    """
    global _debug_log_initialized

    # CLI tools (e.g. llama-swap-build-config) import the AIfred lib but must
    # never truncate the running app's live debug log. They export
    # AIFRED_CLI_MODE — treat the log as already initialized without touching it.
    if os.environ.get("AIFRED_CLI_MODE"):
        _debug_log_initialized = True
        return

    if _debug_log_initialized and not force_reset:
        return

    try:
        if os.path.exists(DEBUG_LOG_FILE):
            file_size_mb = os.path.getsize(DEBUG_LOG_FILE) / (1024 * 1024)
            if file_size_mb > DEBUG_LOG_MAX_SIZE_MB:
                # Rotate: Old file → .old
                old_file = DEBUG_LOG_FILE + ".old"
                if os.path.exists(old_file):
                    os.remove(old_file)
                os.rename(DEBUG_LOG_FILE, old_file)
                print(f"🔄 Debug log rotated: {file_size_mb:.1f} MB → {old_file}", flush=True)

        # MODE: 'w' = overwrite (default), 'a' = append (for debugging)
        with open(DEBUG_LOG_FILE, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write(f"=== AIfred Intelligence - Session: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            f.write("=" * 60 + "\n\n")

        _debug_log_initialized = True
        print(f"✅ Debug log initialized: {DEBUG_LOG_FILE}", flush=True)

    except OSError as e:
        print(f"⚠️ Debug log initialization failed: {e}", flush=True)


def log_message(message: str, category: str = "info") -> None:
    """A line in the debug log file (with FILE_DEBUG_ENABLED), stamped HH:MM:SS.mmm; the first call initializes
    the file. ``message`` comes without timestamp."""
    # Auto-initialize on first call
    if not _debug_log_initialized:
        initialize_debug_log()

    # ============================================================
    # FILE DEBUG (if enabled)
    # ============================================================
    if FILE_DEBUG_ENABLED and not os.environ.get("AIFRED_CLI_MODE"):
        try:
            timestamp_file = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # HH:MM:SS.mmm
            with open(DEBUG_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"{timestamp_file} | {message}\n")
        except OSError as e:
            print(f"⚠️ Debug log file error: {e}", flush=True)


def log_exception(message: str, error: BaseException) -> None:
    """Log ``message`` with the error and its traceback, for a boundary that catches broadly:
    without the traceback such a failure cannot be traced back to its origin."""
    trace = "".join(traceback.format_exception(error)).rstrip()
    log_message(f"{message}: {error!r}\n{trace}", "error")


def log_raw_messages(agent_name: str, messages: list, token_counter=None,
                     toolkit: object = None) -> None:
    """
    Log RAW messages AND tool definitions sent to an LLM (debug.log only).

    Only logs when DEBUG_LOG_RAW_MESSAGES is True in config.py.
    Logs full message content, token counts, and toolkit definitions.

    Args:
        agent_name: Name of the agent/LLM (e.g., "AUTOMATIK-LLM", "Sokrates")
        messages: List of message objects with 'role' and 'content' attributes/keys
        token_counter: Optional function to count tokens (receives [{"content": str}])
        toolkit: Optional ToolKit with tool definitions sent to the LLM
    """
    from .config import DEBUG_LOG_RAW_MESSAGES

    if not DEBUG_LOG_RAW_MESSAGES:
        return

    log_message("=" * 80)
    log_message(f"📤 [RAW] {agent_name}")
    log_message("=" * 80)

    total_tokens = 0
    for i, msg in enumerate(messages):
        # Support both dict and object with attributes
        if hasattr(msg, 'role'):
            role = msg.role
            content = msg.content
        else:
            role = msg.get("role", "?")
            content = msg.get("content", "")

        # Count tokens if counter provided
        msg_tokens = 0
        if token_counter and content:
            msg_tokens = token_counter([{"content": content}])
            total_tokens += msg_tokens

        token_info = f", tokens={msg_tokens}" if token_counter else ""
        log_message(f"[{i}] role={role}{token_info}")
        log_message("-" * 40)
        log_message(content)
        log_message("-" * 40)

    if token_counter:
        log_message(f"TOTAL: {len(messages)} messages, {total_tokens} tokens")
    else:
        log_message(f"TOTAL: {len(messages)} messages")

    # Log toolkit names (compact — full JSON schemas only with
    # DEBUG_LOG_TOOLKIT_DEFINITIONS=True, since 70+ tools spam the log).
    if toolkit and hasattr(toolkit, 'definitions'):
        from .config import DEBUG_LOG_TOOLKIT_DEFINITIONS
        log_message("-" * 40)
        names = [
            td.get("function", {}).get("name", "?") for td in toolkit.definitions
        ]
        log_message(f"🔧 TOOLKIT: {len(names)} tools — {names}")
        if DEBUG_LOG_TOOLKIT_DEFINITIONS:
            import json
            for tool_def in toolkit.definitions:
                log_message(json.dumps(tool_def, ensure_ascii=False, indent=2))
        log_message("-" * 40)

    log_message("=" * 80)


def log_system_prompt(agent_label: str, source: str, messages: list) -> None:
    """Log the complete system prompt of an LLM call with its channel (debug.log only).

    Only logs when DEBUG_LOG_SYSTEM_PROMPT is True in config.py.
    """
    from .config import DEBUG_LOG_SYSTEM_PROMPT

    if not DEBUG_LOG_SYSTEM_PROMPT:
        return

    for msg in messages:
        role = msg.role if hasattr(msg, 'role') else msg.get("role")
        if role != "system":
            continue
        content = msg.content if hasattr(msg, 'content') else msg.get("content", "")
        log_message("=" * 80)
        log_message(f"🧾 [SYSTEM PROMPT] {agent_label} | source={source} | {len(content)} chars")
        log_message("-" * 80)
        log_message(content)
        log_message("=" * 80)


# Console Separator constant (centrally defined, used everywhere)
CONSOLE_SEPARATOR = "─" * 20
