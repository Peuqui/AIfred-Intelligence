"""File-Picker Mixin — generic sandboxed folder/file browser modal.

Used by callers that need a path from the user (audio source-add, document-
manager browse, future video-source-add). The picker is configured per-call
via ``picker_open_for`` — root sandbox, capability flags, file filter,
mode (pick_folder|pick_file), and a callback event name that gets dispatched
on the chosen path.

Callback dispatch uses string-based ``getattr`` on the state to call back
into the caller's mixin. The callback handler receives the picked relative
path (relative to ``picker_root``) plus any args the caller stashed in
``picker_callback_args``.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Dict, List

import reflex as rx


class FilePickerMixin(rx.State, mixin=True):
    """Generic sandboxed folder/file picker."""

    picker_open: bool = False
    picker_title: str = ""
    picker_root: str = ""               # absolute path of the sandbox
    picker_current: str = ""            # rel_path inside root
    picker_entries: List[Dict[str, Any]] = []  # serialized BrowseEntry list
    picker_caps: Dict[str, bool] = {}   # can_create_folder/delete/rename/upload/can_create_symlink
    picker_symlink_target_must_be_under_root: bool = False  # for sandbox-escape protection
    picker_file_filter: List[str] = []
    picker_show_files: bool = True
    picker_show_hidden: bool = False
    picker_sort_by: str = "name"        # "name" | "mtime" | "size"
    picker_mode: str = "pick_folder"    # "pick_folder" | "pick_file"

    # Callback (string-based event dispatch into the caller's mixin)
    picker_callback_event: str = ""
    picker_callback_args: Dict[str, str] = {}

    # UI sub-states
    picker_loading: bool = False
    picker_error: str = ""
    picker_filter_text: str = ""        # client-side live filter on entry names

    # Inline forms
    picker_creating_folder: bool = False
    picker_new_folder_name: str = ""
    picker_creating_symlink: bool = False
    picker_symlink_name: str = ""
    picker_symlink_target: str = ""

    # Path-Input (tippen + Enter zum direkten Sprung)
    picker_path_input: str = ""

    # ── Computed ─────────────────────────────────────────

    @rx.var
    def picker_breadcrumbs(self) -> List[Dict[str, str]]:
        """Breadcrumb segments [{label, rel_path}] from sandbox root → current.

        - First crumb (root): shows the actual sandbox path (e.g. '/mnt')
          so the user sees exactly where the sandbox starts. NOT a generic
          '/' which would imply filesystem root.
        - Subsequent crumbs: '/<segment>' so adjacent buttons read like
          a path without needing extra separator text.
        """
        root_label = self.picker_root or "/"
        crumbs = [{"label": root_label, "rel_path": ""}]
        if not self.picker_current:
            return crumbs
        accumulated = ""
        for part in PurePosixPath(self.picker_current).parts:
            accumulated = (
                str(PurePosixPath(accumulated) / part) if accumulated else part
            )
            crumbs.append({"label": "/" + part, "rel_path": accumulated})
        return crumbs

    @rx.var
    def picker_filtered_entries(self) -> List[Dict[str, Any]]:
        """Live-filtered version of picker_entries by picker_filter_text."""
        if not self.picker_filter_text:
            return self.picker_entries
        needle = self.picker_filter_text.lower()
        return [e for e in self.picker_entries if needle in e.get("name", "").lower()]

    # ── Open / Close ─────────────────────────────────────

    @rx.event
    def picker_open_for(
        self,
        title: str,
        root: str,
        start_at: str = "",
        mode: str = "pick_folder",
        caps: Dict[str, bool] | None = None,
        file_filter: List[str] | None = None,
        show_files: bool = True,
        callback_event: str = "",
        callback_args: Dict[str, str] | None = None,
        symlink_target_must_be_under_root: bool = False,
    ) -> None:
        """Initialize and open the picker modal.

        Args:
            symlink_target_must_be_under_root: if True, refuse symlinks
                whose target resolves outside the sandbox root. Prevents
                sandbox-escape via 'create symlink to /etc' attacks.
        """
        self.picker_title = title
        self.picker_root = root
        self.picker_current = start_at
        self.picker_mode = mode
        self.picker_caps = caps or {}
        self.picker_file_filter = file_filter or []
        self.picker_show_files = show_files if mode != "pick_folder" else show_files
        self.picker_callback_event = callback_event
        self.picker_callback_args = callback_args or {}
        self.picker_symlink_target_must_be_under_root = symlink_target_must_be_under_root
        self.picker_filter_text = ""
        self.picker_path_input = start_at
        self.picker_error = ""
        self.picker_creating_folder = False
        self.picker_new_folder_name = ""
        self.picker_creating_symlink = False
        self.picker_symlink_name = ""
        self.picker_symlink_target = ""
        self._picker_refresh()
        self.picker_open = True

    @rx.event
    def picker_close(self) -> None:
        self.picker_open = False
        self.picker_callback_event = ""
        self.picker_callback_args = {}

    # ── Navigation ───────────────────────────────────────

    def _picker_refresh(self) -> None:
        """Reload entries for current rel_path. Synchronous (sync over sync)."""
        from ..lib.file_browser import BrowseRequest, browse

        self.picker_loading = True
        self.picker_error = ""
        result = browse(BrowseRequest(
            root=self.picker_root,
            rel_path=self.picker_current,
            file_filter=self.picker_file_filter,
            show_files=self.picker_show_files,
            show_hidden=self.picker_show_hidden,
            sort_by=self.picker_sort_by,
        ))
        self.picker_loading = False
        if not result.success:
            self.picker_error = result.error
            self.picker_entries = []
            return
        # Serialize BrowseEntry to dicts (Reflex-State-friendly)
        self.picker_entries = [
            {
                "name": e.name,
                "rel_path": e.rel_path,
                "abs_path": e.abs_path,
                "is_dir": e.is_dir,
                "is_symlink": e.is_symlink,
                "size": e.size or 0,
                "mtime": e.mtime or 0,
                "error": e.error or "",
            }
            for e in result.entries
        ]

    @rx.event
    def picker_navigate(self, rel_path: str) -> None:
        """Move to a specific relative path (folder)."""
        self.picker_current = rel_path
        self.picker_path_input = rel_path
        self.picker_filter_text = ""
        self._picker_refresh()

    @rx.event
    def picker_navigate_up(self) -> None:
        if not self.picker_current:
            return
        parts = PurePosixPath(self.picker_current).parts
        new_path = str(PurePosixPath(*parts[:-1])) if len(parts) > 1 else ""
        self.picker_navigate(new_path)

    @rx.event
    def picker_jump_to_path(self) -> None:
        """Jump to picker_path_input (typed by user)."""
        self.picker_navigate(self.picker_path_input.strip().lstrip("/"))

    @rx.event
    def picker_set_path_input(self, value: str) -> None:
        self.picker_path_input = value

    @rx.event
    def picker_set_filter_text(self, value: str) -> None:
        self.picker_filter_text = value

    @rx.event
    def picker_set_sort(self, sort_by: str) -> None:
        if sort_by in ("name", "mtime", "size"):
            self.picker_sort_by = sort_by
            self._picker_refresh()

    # ── Pick (final selection → dispatch callback) ──────

    @rx.event
    def picker_pick_current(self) -> None:
        """Pick the *current* folder (used for pick_folder mode)."""
        self._dispatch_callback(self.picker_current)

    @rx.event
    def picker_pick(self, rel_path: str) -> None:
        """Pick a specific entry (used for pick_file mode or click-to-pick)."""
        self._dispatch_callback(rel_path)

    @rx.event
    def picker_entry_clicked(self, rel_path: str, is_dir: bool) -> None:
        """Default entry click handler: navigate into folders, pick files
        in pick_file mode, ignore file clicks in pick_folder mode."""
        if is_dir:
            self.picker_navigate(rel_path)
            return
        if self.picker_mode == "pick_file":
            self._dispatch_callback(rel_path)

    def _dispatch_callback(self, rel_path: str) -> None:
        """Call the registered callback event with the picked path + args."""
        cb_name = self.picker_callback_event
        cb_args = dict(self.picker_callback_args)
        # Close the picker first so the modal is gone when the caller
        # opens its own follow-up dialog (e.g. "name your symlink")
        self.picker_open = False
        if cb_name:
            handler = getattr(self, cb_name, None)
            if callable(handler):
                handler(rel_path, **cb_args)

    # ── Create folder ────────────────────────────────────

    @rx.event
    def picker_create_folder_start(self) -> None:
        if not self.picker_caps.get("can_create_folder"):
            return
        self.picker_creating_folder = True
        self.picker_new_folder_name = ""

    @rx.event
    def picker_create_folder_cancel(self) -> None:
        self.picker_creating_folder = False
        self.picker_new_folder_name = ""

    @rx.event
    def picker_create_folder_submit(self) -> None:
        if not self.picker_caps.get("can_create_folder"):
            return
        from ..lib.file_browser import create_folder
        ok, msg = create_folder(
            self.picker_root, self.picker_current, self.picker_new_folder_name
        )
        if not ok:
            self.picker_error = msg
            return
        self.picker_creating_folder = False
        self.picker_new_folder_name = ""
        self._picker_refresh()

    @rx.event
    def picker_set_new_folder_name(self, value: str) -> None:
        self.picker_new_folder_name = value

    # ── Create symlink (admin-style) ─────────────────────

    @rx.event
    def picker_create_symlink_start(self) -> None:
        if not self.picker_caps.get("can_create_symlink"):
            return
        self.picker_creating_symlink = True
        self.picker_symlink_name = ""
        self.picker_symlink_target = ""

    @rx.event
    def picker_create_symlink_cancel(self) -> None:
        self.picker_creating_symlink = False

    @rx.event
    def picker_create_symlink_submit(self) -> None:
        if not self.picker_caps.get("can_create_symlink"):
            return
        from ..lib.file_browser import create_symlink
        ok, msg = create_symlink(
            self.picker_root,
            self.picker_current,
            self.picker_symlink_name,
            self.picker_symlink_target,
            target_must_be_under_root=self.picker_symlink_target_must_be_under_root,
        )
        if not ok:
            self.picker_error = msg
            return
        self.picker_creating_symlink = False
        self.picker_symlink_name = ""
        self.picker_symlink_target = ""
        self._picker_refresh()

    @rx.event
    def picker_set_symlink_name(self, value: str) -> None:
        self.picker_symlink_name = value

    @rx.event
    def picker_set_symlink_target(self, value: str) -> None:
        self.picker_symlink_target = value

    # ── Delete ───────────────────────────────────────────

    @rx.event
    def picker_delete_entry(self, rel_path: str) -> None:
        if not self.picker_caps.get("can_delete"):
            return
        from ..lib.file_browser import delete_entry
        ok, msg = delete_entry(self.picker_root, rel_path)
        if not ok:
            self.picker_error = msg
            return
        self._picker_refresh()
