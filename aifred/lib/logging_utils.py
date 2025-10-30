"""
Logging Utilities - Reflex Edition

Portiert von Gradio-Legacy mit Anpassungen für Reflex State Management
"""

import os
import time
from datetime import datetime
from typing import List

# ============================================================
# DEBUG-LOG-FILE Configuration
# ============================================================
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_AIFRED_DIR = os.path.dirname(_LIB_DIR)
_PROJECT_ROOT = os.path.dirname(_AIFRED_DIR)
_LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")

# Logs-Ordner erstellen falls nicht vorhanden
os.makedirs(_LOGS_DIR, exist_ok=True)

DEBUG_LOG_FILE = os.path.join(_LOGS_DIR, "aifred_debug.log")
_debug_log_initialized = False
DEBUG_LOG_MAX_SIZE_MB = 1  # Max 1 MB, danach rotieren

# ============================================================
# CONSOLE STATE (für Reflex UI)
# ============================================================
_console_messages: List[str] = []  # Thread-safe list für Console-Output
MAX_CONSOLE_MESSAGES = 200
_message_callback = None  # Optional callback when new message is added

# Queue für Thread-to-UI Kommunikation (Pipe!)
import queue
_message_queue: queue.Queue = queue.Queue(maxsize=500)  # Thread-safe Pipe!


def initialize_debug_log(force_reset: bool = False) -> None:
    """
    Initialisiert Debug-Log-Datei (überschreibt bei Service-Start)

    Args:
        force_reset: Wenn True, wird Log immer zurückgesetzt (für Reflex-Start)
    """
    global _debug_log_initialized

    if _debug_log_initialized and not force_reset:
        return

    try:
        if os.path.exists(DEBUG_LOG_FILE):
            file_size_mb = os.path.getsize(DEBUG_LOG_FILE) / (1024 * 1024)
            if file_size_mb > DEBUG_LOG_MAX_SIZE_MB:
                # Rotiere: Alte Datei → .old
                old_file = DEBUG_LOG_FILE + ".old"
                if os.path.exists(old_file):
                    os.remove(old_file)
                os.rename(DEBUG_LOG_FILE, old_file)
                print(f"🔄 Debug-Log rotiert: {file_size_mb:.1f} MB → {old_file}", flush=True)

        # Neue Session: Datei überschreiben oder neu erstellen
        with open(DEBUG_LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(f"=== AIfred Intelligence Debug Log ===\n")
            f.write(f"=== Service gestartet: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")

        _debug_log_initialized = True
        print(f"✅ Debug-Log initialisiert: {DEBUG_LOG_FILE}", flush=True)

    except Exception as e:
        print(f"⚠️ Debug-Log-Initialisierung fehlgeschlagen: {e}", flush=True)


def debug_print(message: str) -> None:
    """
    Debug-Ausgabe in Log-Datei

    Args:
        message: Debug-Nachricht
    """
    # Auto-initialize on first call
    if not _debug_log_initialized:
        initialize_debug_log()

    try:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # HH:MM:SS.mmm

        # Append-Modus
        with open(DEBUG_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{timestamp} | {message}\n")
    except Exception as e:
        print(f"⚠️ Debug-Log-File-Fehler: {e}", flush=True)


def debug_print_prompt(prompt_type: str, prompt: str, model_name: str) -> None:
    """
    Logs prompt with standardized formatting

    Args:
        prompt_type: Type of prompt (e.g., "DECISION", "URL RATING")
        prompt: The actual prompt text
        model_name: Name of the model receiving the prompt
    """
    debug_print("=" * 60)
    debug_print(f"📋 {prompt_type} PROMPT an {model_name}:")
    debug_print("-" * 60)
    debug_print(prompt)
    debug_print("-" * 60)
    debug_print(f"Prompt-Länge: {len(prompt)} Zeichen, ~{len(prompt.split())} Wörter")
    debug_print("=" * 60)


def debug_print_messages(messages: list, model_name: str, context: str = "", **llm_params) -> None:
    """
    Logs LLM messages array with standardized formatting

    Args:
        messages: List of message dicts with 'role' and 'content'
        model_name: Name of the model receiving the messages
        context: Additional context (e.g., "(Decision)", "(Query-Opt)")
        **llm_params: Additional LLM parameters to log (temperature, num_ctx, etc.)
    """
    debug_print("=" * 60)
    debug_print(f"📨 MESSAGES an {model_name} {context}:")
    debug_print("-" * 60)
    for i, msg in enumerate(messages):
        debug_print(f"Message {i+1} - Role: {msg['role']}")
        content = msg['content']

        # Preview first 500 chars for system prompts, full content for user messages
        if len(content) > 500 and msg['role'] == 'system':
            preview = content[:500]
            debug_print(f"Content (erste 500 Zeichen): {preview}")
            debug_print(f"... [noch {len(content) - 500} Zeichen]")
        else:
            debug_print(f"Content: {content}")
        debug_print("-" * 60)

    # Log additional parameters if provided
    if llm_params:
        param_str = ", ".join([f"{k}: {v}" for k, v in llm_params.items()])
        debug_print(f"Total Messages: {len(messages)}, {param_str}")
    else:
        debug_print(f"Total Messages: {len(messages)}")
    debug_print("=" * 60)


def console_print(message: str, category: str = "info") -> None:
    """
    Schreibt Message ins Debug-Log UND in die Console-State für UI

    Args:
        message: Die Nachricht
        category: Kategorie für Filterung ("startup", "llm", "decision", "stats", "info")
    """
    global _console_messages

    # Timestamp hinzufügen
    timestamp = time.strftime("%H:%M:%S")
    formatted_msg = f"{timestamp} | {message}"

    # Ins Debug-Log schreiben
    debug_print(message)

    # In Console-State anhängen
    _console_messages.append(formatted_msg)

    # Limit einhalten (FIFO)
    if len(_console_messages) > MAX_CONSOLE_MESSAGES:
        _console_messages = _console_messages[-MAX_CONSOLE_MESSAGES:]

    # In Queue schreiben (Pipe für UI-Thread!)
    try:
        _message_queue.put_nowait(formatted_msg)  # Non-blocking
    except queue.Full:
        pass  # Queue voll, ignorieren (alte Messages sind schon in _console_messages)

    # Trigger callback if registered (optional, alt)
    if _message_callback:
        try:
            _message_callback(formatted_msg)
        except Exception:
            pass  # Ignore callback errors


def console_separator() -> None:
    """Fügt eine horizontale Trennlinie in die Console ein"""
    global _console_messages, _message_queue
    separator = "─" * 80
    _console_messages.append(separator)

    # In Queue schreiben (Pipe für UI-Thread!)
    try:
        _message_queue.put_nowait(separator)
    except queue.Full:
        pass  # Queue voll, ignorieren

    # Limit einhalten
    if len(_console_messages) > MAX_CONSOLE_MESSAGES:
        _console_messages = _console_messages[-MAX_CONSOLE_MESSAGES:]


def get_console_messages() -> List[str]:
    """
    Gibt alle Console-Messages als Liste zurück (für Reflex UI)

    Returns:
        Liste aller Console-Messages
    """
    return _console_messages.copy()


def clear_console() -> None:
    """Löscht alle Console-Messages und Queue"""
    global _console_messages, _message_queue
    _console_messages = []

    # Queue leeren (alle Messages verwerfen)
    while not _message_queue.empty():
        try:
            _message_queue.get_nowait()
        except queue.Empty:
            break


def set_message_callback(callback):
    """
    Registriert einen Callback, der bei jeder neuen Console-Message aufgerufen wird.

    Args:
        callback: Funktion mit Signatur callback(message: str) -> None
    """
    global _message_callback
    _message_callback = callback


def get_new_messages() -> List[str]:
    """
    Liest alle neuen Messages aus der Queue (non-blocking).

    Returns:
        Liste der neuen Messages seit dem letzten Aufruf
    """
    new_messages = []
    try:
        while True:
            msg = _message_queue.get_nowait()  # Non-blocking
            new_messages.append(msg)
    except queue.Empty:
        pass  # Keine Messages mehr
    return new_messages
