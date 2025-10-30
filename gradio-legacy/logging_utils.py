"""
Logging Utilities - Centralized logging functions

This module provides debug logging functionality used throughout
the AIfred Intelligence application. All debug output goes to
systemd journal via stdout AND to /tmp/aifred_debug.log
"""

import logging
import time
import os
from datetime import datetime
from .config import DEBUG_ENABLED

# ============================================================
# DEBUG-LOG-FILE (wird bei Service-Start überschrieben!)
# ============================================================
# Projekt-Root ermitteln (lib/ ist ein Unterordner)
_LIB_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_LIB_DIR)
_LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")

# Logs-Ordner erstellen falls nicht vorhanden
os.makedirs(_LOGS_DIR, exist_ok=True)

DEBUG_LOG_FILE = os.path.join(_LOGS_DIR, "aifred_debug.log")
_debug_log_initialized = False
DEBUG_LOG_MAX_SIZE_MB = 1  # Max 1 MB (~20-30 Anfragen), danach wird Log rotiert

# ============================================================
# GLOBAL DEBUG CONSOLE STATE (für UI)
# ============================================================
debug_console_messages = []  # Liste aller Debug-Messages für UI
MAX_CONSOLE_MESSAGES = 200   # Maximale Anzahl Messages

# Logging Setup
logging.basicConfig(
    level=logging.DEBUG if DEBUG_ENABLED else logging.INFO,
    format='%(message)s',
    force=True
)
logger = logging.getLogger(__name__)


def debug_print(message, **kwargs):
    """
    Debug-Ausgabe nur wenn DEBUG_ENABLED = True

    Output geht ins systemd journal via stdout UND in /tmp/aifred_debug.log
    Die Log-Datei wird beim ersten Aufruf überschrieben (eine Datei pro Session!)

    Args:
        message: Debug-Nachricht
        **kwargs: Zusätzliche print() Parameter (z.B. end, sep)
    """
    global _debug_log_initialized

    if DEBUG_ENABLED:
        # Journal Control Ausgabe deaktiviert - alle Logs gehen nur noch in Debug-File
        # print(message, flush=True, **kwargs)

        # In Debug-File schreiben (alle Logs, keine journald Limits!)
        try:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # HH:MM:SS.mmm

            # Beim ersten Aufruf: Datei-Rotation prüfen und ggf. überschreiben
            if not _debug_log_initialized:
                # Prüfe Dateigröße - wenn > MAX_SIZE_MB → rotiere (lösche alte, erstelle neue)
                if os.path.exists(DEBUG_LOG_FILE):
                    file_size_mb = os.path.getsize(DEBUG_LOG_FILE) / (1024 * 1024)
                    if file_size_mb > DEBUG_LOG_MAX_SIZE_MB:
                        # Rotiere: Alte Datei → .old, neue Datei erstellen
                        old_file = DEBUG_LOG_FILE + ".old"
                        if os.path.exists(old_file):
                            os.remove(old_file)  # Lösche vorherige .old
                        os.rename(DEBUG_LOG_FILE, old_file)  # Aktuell → .old
                        print(f"🔄 Debug-Log rotiert: {file_size_mb:.1f} MB → {old_file}", flush=True)

                # Neue Session: Datei überschreiben oder neu erstellen
                with open(DEBUG_LOG_FILE, 'w', encoding='utf-8') as f:
                    f.write(f"=== AIfred Intelligence Debug Log ===\n")
                    f.write(f"=== Service gestartet: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
                _debug_log_initialized = True

            # Danach: Append-Modus
            with open(DEBUG_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"{timestamp} | {message}\n")
        except Exception as e:
            # Fehler beim File-Logging nicht crashen lassen!
            print(f"⚠️ Debug-Log-File-Fehler: {e}", flush=True)


def debug_print_prompt(prompt_type: str, prompt: str, model_name: str):
    """
    Logs prompt with standardized formatting for debugging.

    Args:
        prompt_type: Type of prompt (e.g., "DECISION", "QUERY OPTIMIZATION", "URL RATING")
        prompt: The actual prompt text
        model_name: Name of the model receiving the prompt
    """
    debug_print("=" * 60)
    debug_print(f"📋 {prompt_type} PROMPT:")
    debug_print("-" * 60)
    debug_print(prompt)
    debug_print("-" * 60)
    debug_print(f"Prompt-Länge: {len(prompt)} Zeichen, ~{len(prompt.split())} Wörter")
    debug_print("=" * 60)


def debug_print_messages(messages: list, model_name: str, context: str = "", **ollama_params):
    """
    Logs Ollama messages array with standardized formatting for debugging.

    Args:
        messages: List of message dicts with 'role' and 'content'
        model_name: Name of the model receiving the messages
        context: Additional context (e.g., "(Decision)", "(Query-Opt)")
        **ollama_params: Additional Ollama parameters to log (temperature, num_ctx, etc.)
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
    if ollama_params:
        param_str = ", ".join([f"{k}: {v}" for k, v in ollama_params.items()])
        debug_print(f"Total Messages: {len(messages)}, {param_str}")
    else:
        debug_print(f"Total Messages: {len(messages)}")
    debug_print("=" * 60)


def console_print(message, category="info"):
    """
    Schreibt Debug-Message sowohl ins Journal (via debug_print) als auch in die UI-Konsole

    Args:
        message: Die Nachricht
        category: Kategorie für Filterung ("startup", "llm", "decision", "stats", "info")
    """
    global debug_console_messages

    # Timestamp hinzufügen (HH:MM:SS)
    timestamp = time.strftime("%H:%M:%S")
    formatted_msg = f"{timestamp} | {message}"

    # An Journal senden (wie bisher)
    debug_print(message)

    # An Console-State anhängen
    debug_console_messages.append(formatted_msg)

    # Limit einhalten (FIFO - älteste löschen)
    if len(debug_console_messages) > MAX_CONSOLE_MESSAGES:
        debug_console_messages = debug_console_messages[-MAX_CONSOLE_MESSAGES:]


def console_separator():
    """Fügt eine horizontale Trennlinie in die Console ein"""
    global debug_console_messages
    debug_console_messages.append("─" * 80)

    # Limit einhalten
    if len(debug_console_messages) > MAX_CONSOLE_MESSAGES:
        debug_console_messages = debug_console_messages[-MAX_CONSOLE_MESSAGES:]


def get_console_output():
    """Gibt alle Console-Messages als String zurück (für Gradio Textbox)"""
    return "\n".join(debug_console_messages)


def clear_console():
    """Löscht alle Console-Messages"""
    global debug_console_messages
    debug_console_messages = []
