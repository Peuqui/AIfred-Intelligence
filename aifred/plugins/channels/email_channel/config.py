"""E-Mail-Channel-Konfiguration (plugin-lokal).

Nicht-geheime Tuning-Werte des E-Mail-Plugins leben hier im Plugin-
Verzeichnis, nicht in der zentralen ``aifred/lib/config.py`` — jedes
Plugin verwaltet seine eigene Konfiguration selbst (Kapselung). Secrets
(Zugangsdaten) laufen weiterhin über den ``credential_broker``.
"""

from __future__ import annotations

import os

# IMAP over TLS, for a mailbox without a configured port
EMAIL_IMAP_PORT_DEFAULT = "993"

# Proposed host of the read-only second mailbox, from mail.de's own help page
# (external e-mail clients: POP3, IMAP & SMTP settings). The port is the one above.
EMAIL_READONLY_IMAP_HOST_DEFAULT = "imap.mail.de"

# Max emails per inbox check
EMAIL_MAX_FETCH = 20

# Truncate email body for LLM context
EMAIL_MAX_BODY_CHARS = 10_000

# Default Sent-folder name if EMAIL_SENT_FOLDER is not configured
# (provider-specific: GMX = "Gesendet", many others = "Sent")
EMAIL_SENT_FOLDER_DEFAULT = "Gesendet"

# E3: how often a single mail whose fetch/dispatch keeps throwing is retried
# (once per ~30 s reconnect cycle) before it is quarantined + skipped, so a
# poison message never blocks the queue forever. Env-overridable.
EMAIL_MAX_PROCESS_ATTEMPTS = int(os.environ.get("EMAIL_MAX_PROCESS_ATTEMPTS", "5"))
