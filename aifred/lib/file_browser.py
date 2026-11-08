"""Files and folders under a root, for the file requester (lib/api/files.py, VueFinder) and the audio sources.

Every path a client sends is relative to the area's root and checked lexically: no ``..``, nothing outside. A
symlink under the root may point anywhere (an admin's NAS mount is exactly that); a new one only to a folder inside
the root, else it would open the whole filesystem. Deleting a symlink removes the link, never its target; deleting
a folder removes its content without following a symlink inside it. Errors are UserErrors with the reason in words.
"""

from __future__ import annotations

import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .user_error import Conflict, Invalid, Missing, NotDone

# Pseudo-filesystems never entered (huge fake files, circular links, kernel state); only reachable with root "/".
_DANGEROUS_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/var/run", "/var/lock")


@dataclass(frozen=True)
class Entry:
    name: str
    rel_path: str          # relative to the root, "/"-separated
    is_dir: bool
    is_symlink: bool
    size: int | None       # None: folders and entries that cannot be read (a broken link)
    mtime: int | None
    writable: bool


def root_folder(root: str) -> Path:
    folder = Path(root).expanduser().resolve()
    if not folder.is_dir():
        raise Invalid("sandbox_not_folder", path=root)
    return folder


def resolve(root: str, rel_path: str, *, must_exist: bool = True) -> Path:
    """The path of ``rel_path`` under ``root``, checked lexically (symlinks below the root are not resolved: they may
    lead to a mount elsewhere)."""
    base = root_folder(root)
    rel = (rel_path or "").strip().strip("/")
    if not rel:
        return base
    parts = PurePosixPath(rel).parts
    if ".." in parts or any(part in ("", ".") for part in parts):
        raise Invalid("leads_outside_sandbox", path=rel_path)
    path = base.joinpath(*parts)
    if any(str(path) == prefix or str(path).startswith(prefix + "/") for prefix in _DANGEROUS_PREFIXES):
        raise Invalid("leads_outside_sandbox", path=rel_path)
    if must_exist and not (path.exists() or path.is_symlink()):
        raise Missing("path_not_found", path=rel_path)
    return path


def relative(root: str, path: Path) -> str:
    return path.relative_to(root_folder(root)).as_posix()


def _check_name(name: str) -> str:
    name = name.strip()
    if not name or name in (".", "..") or "/" in name or "\0" in name:
        raise Invalid("invalid_entry_name", name=name)
    return name


def _entry(root: str, path: Path) -> Entry:
    import os

    is_symlink = path.is_symlink()
    try:
        stat = path.stat()   # follows links
        is_dir = path.is_dir()
        size = None if is_dir else stat.st_size
        mtime = int(stat.st_mtime)
    except OSError:          # a broken link
        is_dir, size, mtime = False, None, None
    return Entry(path.name, relative(root, path), is_dir, is_symlink, size, mtime, os.access(path, os.W_OK))


def list_folder(root: str, rel_path: str, *, show_hidden: bool = False) -> tuple[list[Entry], bool]:
    """The entries of a folder, folders first, by name; and whether new entries can be made in it."""
    import os

    folder = resolve(root, rel_path)
    if not folder.is_dir():
        raise Invalid("not_a_folder", path=rel_path)
    try:
        children = [child for child in folder.iterdir() if show_hidden or not child.name.startswith(".")]
    except OSError as error:
        raise Invalid("folder_unreadable", path=rel_path, reason=str(error)) from error
    entries = sorted((_entry(root, child) for child in children), key=lambda entry: (not entry.is_dir, entry.name.lower()))
    return entries, os.access(folder, os.W_OK)


def search(root: str, rel_path: str, text: str, *, deep: bool) -> list[Entry]:
    """Entries whose name contains ``text`` (any case), in the folder or, ``deep``, below it (not into symlinks)."""
    folder = resolve(root, rel_path)
    wanted = text.lower()
    found: list[Entry] = []
    pending = [folder]
    while pending:
        current = pending.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name.startswith("."):
                continue
            if wanted in child.name.lower():
                found.append(_entry(root, child))
            if deep and child.is_dir() and not child.is_symlink():
                pending.append(child)
    return sorted(found, key=lambda entry: (not entry.is_dir, entry.rel_path.lower()))


def _free(target: Path, name: str) -> Path:
    if target.exists() or target.is_symlink():
        raise Conflict("entry_exists", name=name)
    return target


def _done(work: str, error: OSError) -> NotDone:
    return NotDone("file_operation_failed", work=work, reason=error.strerror or str(error))


def create_folder(root: str, rel_path: str, name: str) -> Path:
    target = _free(resolve(root, rel_path) / _check_name(name), name)
    try:
        target.mkdir()
    except OSError as error:
        raise _done("mkdir", error) from error
    return target


def write_file(root: str, rel_path: str, name: str, data: bytes) -> Path:
    """A new file (an upload); a name in use is refused, nothing is overwritten."""
    target = _free(resolve(root, rel_path) / _check_name(name), name)
    try:
        target.write_bytes(data)
    except OSError as error:
        raise _done("write", error) from error
    return target


def rename(root: str, rel_path: str, name: str) -> Path:
    source = resolve(root, rel_path)
    if source == root_folder(root):
        raise Invalid("leads_outside_sandbox", path=rel_path)
    target = _free(source.parent / _check_name(name), name)
    try:
        source.rename(target)
    except OSError as error:
        raise _done("rename", error) from error
    return target


def delete(root: str, rel_path: str) -> None:
    """A file, a symlink (the link, never its target) or a folder with its content (not following links in it)."""
    target = resolve(root, rel_path)
    if target == root_folder(root):
        raise Invalid("leads_outside_sandbox", path=rel_path)
    try:
        if target.is_symlink() or not target.is_dir():
            target.unlink()
        else:
            shutil.rmtree(target)
    except OSError as error:
        raise _done("delete", error) from error


def transfer(root: str, sources: list[str], destination: str, *, keep: bool) -> None:
    """Copy (``keep``) or move the entries into a folder of the root; a name in use there is refused."""
    folder = resolve(root, destination)
    if not folder.is_dir():
        raise Invalid("not_a_folder", path=destination)
    for rel in sources:
        source = resolve(root, rel)
        target = _free(folder / source.name, source.name)
        if source.is_dir() and not source.is_symlink() and (folder == source or source in folder.parents):
            raise Invalid("folder_into_itself", path=rel)
        try:
            if not keep:
                shutil.move(str(source), str(target))
            elif source.is_symlink():
                target.symlink_to(source.readlink())
            elif source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target)
        except OSError as error:
            raise _done("copy" if keep else "move", error) from error


def create_symlink(root: str, rel_path: str, name: str, target_rel: str) -> Path:
    """A link in ``rel_path`` to the folder ``target_rel`` of the root. Where the folder really is decides: one reached
    through a link that leads out is refused, a new link out would make the whole filesystem reachable."""
    link = _free(resolve(root, rel_path) / _check_name(name), name)
    target = resolve(root, target_rel)
    if not target.is_dir():
        raise Invalid("not_a_folder", path=target_rel)
    try:
        target.resolve().relative_to(root_folder(root))
    except ValueError as error:
        raise Invalid("leads_outside_sandbox", path=target_rel) from error
    try:
        link.symlink_to(target)
    except OSError as error:
        raise _done("symlink", error) from error
    return link


def mime_type(path: Path) -> str | None:
    return None if path.is_dir() else mimetypes.guess_type(path.name)[0]
