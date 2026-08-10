"""
Logging Utilities - Reflex Edition

Ported from Gradio legacy with adaptations for Reflex State Management

UNIFIED LOGGING SYSTEM:
- log_message(): Central function for all logging requirements
- Config-controlled via CONSOLE_DEBUG_ENABLED and FILE_DEBUG_ENABLED
- debug_print_prompt() and debug_print_messages(): Specialized formatting
"""

import os
import time
from datetime import datetime
from typing import List
from .config import CONSOLE_DEBUG_ENABLED, FILE_DEBUG_ENABLED, DATA_DIR

# ============================================================
# DEBUG-LOG-FILE Configuration
# ============================================================
_LOGS_DIR = DATA_DIR / "logs"

# Create logs directory if not exists
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

DEBUG_LOG_FILE = str(_LOGS_DIR / "aifred_debug.log")
_debug_log_initialized = False
DEBUG_LOG_MAX_SIZE_MB = 1  # Max 1 MB, then rotate

# ============================================================
# CONSOLE STATE (for Reflex UI)
# ============================================================
_console_messages: List[str] = []  # Thread-safe list for Console-Output
MAX_CONSOLE_MESSAGES = 200

# Queue for Thread-to-UI communication (Pipe!)
import queue  # noqa: E402
_message_queue: queue.Queue = queue.Queue(maxsize=500)  # Thread-safe Pipe!


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
    """
    CENTRAL LOGGING FUNCTION - Unified System

    Writes messages based on config:
    - FILE_DEBUG_ENABLED: To debug log file
    - CONSOLE_DEBUG_ENABLED: To queue for UI debug console

    Args:
        message: The message to log (without timestamp!)
        category: Category for filtering ("startup", "llm", "decision", "stats", "info")

    Behavior:
        - Automatically adds timestamp (HH:MM:SS.mmm for file, HH:MM:SS for console)
        - Auto-initializes debug log on first call
        - Thread-safe queue for UI communication
    """
    global _console_messages, _debug_log_initialized

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

    # ============================================================
    # CONSOLE DEBUG (if enabled)
    # ============================================================
    if CONSOLE_DEBUG_ENABLED:
        timestamp_console = time.strftime("%H:%M:%S")  # HH:MM:SS (like legacy)
        formatted_msg = f"{timestamp_console} | {message}"

        # Append to console state
        _console_messages.append(formatted_msg)

        # Maintain limit (FIFO) — in-place mutation to avoid race with concurrent appends
        if len(_console_messages) > MAX_CONSOLE_MESSAGES:
            del _console_messages[:len(_console_messages) - MAX_CONSOLE_MESSAGES]

        # Write to queue (for state polling!)
        try:
            _message_queue.put_nowait(formatted_msg)  # Non-blocking
        except queue.Full:
            pass  # Queue full, old messages are in _console_messages


def debug_print_prompt(prompt_type: str, prompt: str, model_name: str) -> None:
    """
    Logs prompt with standardized formatting

    Args:
        prompt_type: Type of prompt (e.g., "DECISION", "QUERY_OPT")
        prompt: The actual prompt text
        model_name: Name of the model receiving the prompt
    """
    log_message("=" * 60)
    log_message(f"📋 {prompt_type} PROMPT to {model_name}:")
    log_message("-" * 60)
    log_message(prompt)
    log_message("-" * 60)
    log_message(f"Prompt length: {len(prompt)} chars, ~{len(prompt.split())} words")
    log_message("=" * 60)


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
            try:
                msg_tokens = token_counter([{"content": content}])
                total_tokens += msg_tokens
            except Exception:
                pass

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


def debug_print_messages(messages: list, model_name: str, context: str = "", **llm_params) -> None:
    """
    Logs LLM messages array with standardized formatting

    Args:
        messages: List of message dicts with 'role' and 'content'
        model_name: Name of the model receiving the messages
        context: Additional context (e.g., "(Decision)", "(Query-Opt)")
        **llm_params: Additional LLM parameters to log (temperature, num_ctx, etc.)
    """
    log_message("=" * 60)
    log_message(f"📨 MESSAGES to {model_name} {context}:")
    log_message("-" * 60)
    for i, msg in enumerate(messages):
        log_message(f"Message {i+1} - Role: {msg['role']}")
        content = msg['content']

        # Preview first 500 chars for system prompts, full content for user messages
        if len(content) > 500 and msg['role'] == 'system':
            preview = content[:500]
            log_message(f"Content (first 500 chars): {preview}")
            log_message(f"... [{len(content) - 500} more chars]")
        else:
            log_message(f"Content: {content}")
        log_message("-" * 60)

    # Log additional parameters if provided
    if llm_params:
        param_str = ", ".join([f"{k}: {v}" for k, v in llm_params.items()])
        log_message(f"Total Messages: {len(messages)}, {param_str}")
    else:
        log_message(f"Total Messages: {len(messages)}")
    log_message("=" * 60)


# Console Separator constant (centrally defined, used everywhere)
CONSOLE_SEPARATOR = "─" * 20

def console_separator() -> None:
    """Adds a horizontal separator line to the console"""
    global _console_messages, _message_queue
    _console_messages.append(CONSOLE_SEPARATOR)

    # Write to queue (pipe for UI thread!)
    try:
        _message_queue.put_nowait(CONSOLE_SEPARATOR)
    except queue.Full:
        pass  # Queue full, ignore

    # Maintain limit — in-place mutation to avoid race with concurrent appends
    if len(_console_messages) > MAX_CONSOLE_MESSAGES:
        del _console_messages[:len(_console_messages) - MAX_CONSOLE_MESSAGES]
