"""Generic file/folder browser backend — sandboxed under a configurable root.

Provides the data layer for the Reflex file-picker UI. Capability-flags
let callers decide which operations are allowed (read-only browse,
create-folder, delete, rename, upload). Different consumers get
different sandboxes:

- Document-Manager → root = data/documents/
- Audio-Source picker → root = data/media/audio/
- Future video-source picker → root = data/media/video/

Symlinks are followed for content listing (so NAS-mount symlinks are
transparent), but path-traversal protection works on the *user-facing*
relative path, not the resolved target — a user who clicks
``data/media/audio/nas_music/Klassik/...`` stays inside the sandbox
even though the actual files live on `/mnt/auto/vuplus/...`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional


@dataclass
class BrowseCapabilities:
    """What operations the picker UI is allowed to expose."""
    can_create_folder: bool = False
    can_delete: bool = False
    can_rename: bool = False
    can_upload: bool = False


@dataclass
class BrowseRequest:
    root: str                   # absolute path; everything must stay under this
    rel_path: str = ""          # current location, relative to root
    file_filter: list[str] = field(default_factory=list)  # extensions like [".mp3"]; empty = all
    show_files: bool = True     # False → folder-only mode
    show_hidden: bool = False   # show entries starting with "."
    sort_by: str = "name"       # "name" | "mtime" | "size"


@dataclass
class BrowseEntry:
    name: str
    rel_path: str               # relative to root
    abs_path: str               # resolved absolute path (target if symlink)
    is_dir: bool
    is_symlink: bool
    size: Optional[int] = None
    mtime: Optional[int] = None
    error: Optional[str] = None  # set if entry can't be inspected (broken symlink etc.)


@dataclass
class BrowseResult:
    success: bool
    rel_path: str               # the listing's location, relative to root
    entries: list[BrowseEntry] = field(default_factory=list)
    error: str = ""             # populated when success=False


# Pseudo-filesystems that should never be entered (huge fake files,
# circular symlinks, kernel state). Only relevant when root='/'.
_DANGEROUS_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/var/run", "/var/lock")


def _is_safe_to_enter(abs_path: Path) -> bool:
    """Reject pseudo-filesystems and similar danger zones."""
    s = str(abs_path)
    return not any(s == p or s.startswith(p + "/") for p in _DANGEROUS_PREFIXES)


def safe_resolve(root: str, rel_path: str) -> tuple[Optional[Path], Optional[str]]:
    """Validate that rel_path stays inside root.

    Path-traversal logic uses POSIX joining + lexical check on the
    user-facing relative path. We do NOT call Path.resolve() on the
    user-facing path because that would follow symlinks into their
    target — and a symlink under root pointing to /mnt/foo is exactly
    what we want to allow (admin-controlled mount). The sandbox is
    enforced on the *prefix*, not the resolved target.

    Returns (resolved_path, None) on success or (None, error_msg).
    """
    root_p = Path(root).expanduser().resolve()
    if not root_p.is_dir():
        return None, f"root not found or not a directory: {root}"

    rel_norm = (rel_path or "").strip().lstrip("/")
    if not rel_norm:
        return root_p, None

    # Block traversal components in the user-supplied path
    parts = PurePosixPath(rel_norm).parts
    if ".." in parts:
        return None, f"path traversal denied: {rel_path!r}"

    user_path = root_p / rel_norm
    # We do NOT resolve() here — symlinks under root may point outside,
    # which is fine. We only verify that the *lexical* prefix matches.
    if not user_path.exists():
        return None, f"path not found: {rel_path}"

    return user_path, None


def browse(req: BrowseRequest) -> BrowseResult:
    """List a folder under the request's sandbox root."""
    target, err = safe_resolve(req.root, req.rel_path)
    if err or target is None:
        return BrowseResult(success=False, rel_path=req.rel_path, error=err or "unknown error")

    if not _is_safe_to_enter(target):
        return BrowseResult(
            success=False, rel_path=req.rel_path,
            error=f"refusing to enter pseudo-filesystem: {target}",
        )

    if not target.is_dir():
        return BrowseResult(success=False, rel_path=req.rel_path, error="not a directory")

    entries: list[BrowseEntry] = []
    extensions = {ext.lower() for ext in req.file_filter} if req.file_filter else None

    try:
        children = list(target.iterdir())
    except OSError as exc:
        return BrowseResult(success=False, rel_path=req.rel_path, error=f"listing failed: {exc}")

    for child in children:
        if not req.show_hidden and child.name.startswith("."):
            continue

        # Determine type without crashing on broken symlinks
        is_symlink = child.is_symlink()
        is_dir = False
        size: Optional[int] = None
        mtime: Optional[int] = None
        entry_error: Optional[str] = None

        try:
            stat = child.stat()  # follows symlinks
            is_dir = child.is_dir()
            if not is_dir:
                size = stat.st_size
            mtime = int(stat.st_mtime)
        except OSError as exc:
            entry_error = f"stat failed: {exc}"
            # For broken symlinks, fall back to lstat-based info
            try:
                lstat = child.lstat()
                mtime = int(lstat.st_mtime)
            except OSError:
                pass

        if not is_dir and entry_error is None:
            # File: apply extension filter and show_files toggle
            if not req.show_files:
                continue
            if extensions and child.suffix.lower() not in extensions:
                continue

        rel = (
            str(PurePosixPath(req.rel_path) / child.name)
            if req.rel_path else child.name
        )

        try:
            abs_resolved = str(child.resolve())
        except (OSError, RuntimeError):
            abs_resolved = str(child)

        entries.append(BrowseEntry(
            name=child.name,
            rel_path=rel,
            abs_path=abs_resolved,
            is_dir=is_dir,
            is_symlink=is_symlink,
            size=size,
            mtime=mtime,
            error=entry_error,
        ))

    # Sort: directories first, then by chosen criterion
    if req.sort_by == "mtime":
        entries.sort(key=lambda e: (not e.is_dir, -(e.mtime or 0)))
    elif req.sort_by == "size":
        entries.sort(key=lambda e: (not e.is_dir, -(e.size or 0)))
    else:  # default: name
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))

    return BrowseResult(success=True, rel_path=req.rel_path, entries=entries)


def create_folder(root: str, rel_path: str, name: str) -> tuple[bool, str]:
    """Create a new folder under (root/rel_path). Used when caps.can_create_folder."""
    if not name or "/" in name or name.startswith("."):
        return False, "invalid folder name"
    parent, err = safe_resolve(root, rel_path)
    if err or parent is None:
        return False, err or "invalid parent"
    target = parent / name
    if target.exists():
        return False, "already exists"
    try:
        target.mkdir(parents=False)
    except OSError as exc:
        return False, f"mkdir failed: {exc}"
    return True, str(target.relative_to(Path(root).resolve()))


def delete_entry(root: str, rel_path: str) -> tuple[bool, str]:
    """Delete a file or empty folder under root. Used when caps.can_delete."""
    if not rel_path:
        return False, "cannot delete root"
    target, err = safe_resolve(root, rel_path)
    if err or target is None:
        return False, err or "invalid path"
    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            target.rmdir()  # only empty dirs — recursive delete is callers job
        else:
            return False, "unsupported entry type"
    except OSError as exc:
        return False, f"delete failed: {exc}"
    return True, str(target)


def rename_entry(root: str, rel_path: str, new_name: str) -> tuple[bool, str]:
    """Rename a file/folder/symlink in-place. Used when caps.can_rename."""
    if not new_name or "/" in new_name or new_name.startswith("."):
        return False, "invalid new name"
    target, err = safe_resolve(root, rel_path)
    if err or target is None:
        return False, err or "invalid path"
    new_target = target.parent / new_name
    if new_target.exists():
        return False, "destination exists"
    try:
        target.rename(new_target)
    except OSError as exc:
        return False, f"rename failed: {exc}"
    return True, str(new_target.relative_to(Path(root).resolve()))


def create_symlink(
    root: str,
    rel_path: str,
    name: str,
    target_abs: str,
    target_must_be_under_root: bool = False,
) -> tuple[bool, str]:
    """Create a symlink under (root/rel_path) pointing to target_abs.

    Args:
        root: sandbox root.
        rel_path: relative path inside root where the symlink is placed.
        name: filename of the symlink (no slashes, no dot-prefix).
        target_abs: absolute path the symlink points at.
        target_must_be_under_root: if True, refuse targets that resolve
            outside the sandbox root. Use for callers that must prevent
            sandbox escape via symlink (e.g. audio-source picker).

    Returns:
        (success, message_or_relpath)
    """
    if not name or "/" in name or name.startswith("."):
        return False, "invalid symlink name"
    parent, err = safe_resolve(root, rel_path)
    if err or parent is None:
        return False, err or "invalid parent"
    link_path = parent / name
    if link_path.exists() or link_path.is_symlink():
        return False, "name already in use"
    target_p = Path(target_abs).expanduser()
    if not target_p.is_absolute():
        return False, "target must be an absolute path"
    if not target_p.is_dir():
        return False, f"target is not a directory: {target_abs}"
    if target_must_be_under_root:
        try:
            target_resolved = target_p.resolve()
            root_resolved = Path(root).expanduser().resolve()
            target_resolved.relative_to(root_resolved)
        except ValueError:
            return False, (
                f"target {target_abs} is outside the sandbox root {root} — "
                f"refusing symlink that would enable sandbox escape"
            )
    try:
        link_path.symlink_to(target_p)
    except OSError as exc:
        return False, f"symlink failed: {exc}"
    return True, str(link_path.relative_to(Path(root).resolve()))
