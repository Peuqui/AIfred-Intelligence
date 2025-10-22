"""
Logging Utilities - Centralized logging functions

This module provides debug logging functionality used throughout
the AIfred Intelligence application. All debug output goes to
systemd journal via stdout.
"""

import logging
import time
from .config import DEBUG_ENABLED

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

    Output geht ins systemd journal via stdout. Nutzt print() statt
    logger.info() um doppelte Messages zu vermeiden.

    Args:
        message: Debug-Nachricht
        **kwargs: Zusätzliche print() Parameter (z.B. end, sep)
    """
    if DEBUG_ENABLED:
        # Nur print nutzen - systemd loggt stdout automatisch ins journal
        # logger.info() würde zu doppelten Messages führen!
        print(message, flush=True, **kwargs)


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
