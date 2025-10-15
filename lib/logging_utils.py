"""
Logging Utilities - Centralized logging functions

This module provides debug logging functionality used throughout
the AIfred Intelligence application. All debug output goes to
systemd journal via stdout.
"""

import logging
from .config import DEBUG_ENABLED

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


def debug_log(message):
    """
    Logging-basierte Debug-Ausgabe

    Alternative zu debug_print() für strukturiertes Logging.

    Args:
        message: Debug-Nachricht für Logger
    """
    if DEBUG_ENABLED:
        logger.debug(message)
