"""Modal dialogs for AIfred UI (Paket-Split aus dem frueheren ui/modals.py)."""

from .help import (
    multi_agent_help_modal,
    reasoning_thinking_help_modal,
    model_lifecycle_help_modal,
)
from .login import login_dialog
from .crop import crop_modal
from .documents import document_manager_page
from .credentials import channel_credentials_page
from .bundles import bundle_export_modal, bundle_import_modal

__all__ = [
    "bundle_export_modal",
    "bundle_import_modal",
    "channel_credentials_page",
    "crop_modal",
    "document_manager_page",
    "login_dialog",
    "model_lifecycle_help_modal",
    "multi_agent_help_modal",
    "reasoning_thinking_help_modal",
]
