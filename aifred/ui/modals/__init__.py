"""Modal dialogs for AIfred UI (Paket-Split aus dem frueheren ui/modals.py)."""

from .help import (
    multi_agent_help_modal,
    reasoning_thinking_help_modal,
    research_help_modal,
    model_lifecycle_help_modal,
)
from .login import login_dialog
from .crop import crop_modal
from .lightbox import image_lightbox_modal
from .documents import document_manager_page
from .credentials import channel_credentials_page
from .audit import audit_log_modal
from .bundles import bundle_export_modal, bundle_import_modal

__all__ = [
    "multi_agent_help_modal",
    "reasoning_thinking_help_modal",
    "research_help_modal",
    "model_lifecycle_help_modal",
    "login_dialog",
    "crop_modal",
    "image_lightbox_modal",
    "document_manager_page",
    "channel_credentials_page",
    "audit_log_modal",
    "bundle_export_modal",
    "bundle_import_modal",
]
