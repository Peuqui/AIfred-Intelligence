"""Drive API v3 basics shared by the agents' tools, their folder boundary and
the document-manager source."""

from __future__ import annotations

DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"


def escape_drive_term(term: str) -> str:
    r"""Escape a user/LLM-supplied term for use inside '...' in a Drive query.

    Drive query strings escape backslash and single quote with a backslash —
    without this, a term like ``L'atelier`` breaks the query and a crafted
    term can inject arbitrary query operators.
    """
    return term.replace("\\", "\\\\").replace("'", "\\'")
