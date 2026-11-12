"""A scheduled job's notes (its "Akte") and the folder it keeps its data in.

Every job has a folder in the workspace, ``SCHEDULER_JOBS_FOLDER/<name>_job-<id>``, with its notes file of the same name
inside. The model reads the notes at the start of a run and writes them at the end (its memory for the job: what it
reported, what it used, numbers and names it needs again) and keeps the data it holds on disk beside them. The folder
carries the job's name and its id: easy to find in the file tree, and the id finds it again when the job is renamed
(``sync``). Deleting the job deletes the folder (``remove``).
"""

from __future__ import annotations

import re

from . import file_manager
from .config import SCHEDULER_JOBS_FOLDER

_ID_SUFFIX = re.compile(r"_job-(\d+)$")


def _slug(name: str) -> str:
    """The job's name as part of a file name: letters and digits, the rest a hyphen (the underscore stays the
    separator before ``job-<id>``)."""
    return re.sub(r"[\W_]+", "-", name.lower()).strip("-") or "unnamed"


def _checked(result: file_manager.FileOpResult) -> None:
    if not result.success:
        raise result.problem or RuntimeError(result.detail)


def _existing(job_id: int) -> str | None:
    """The name of the job's folder as it lies in the workspace, whatever the job was called when it was made."""
    root = file_manager.DOCUMENTS_DIR / SCHEDULER_JOBS_FOLDER
    if not root.is_dir():
        return None
    for child in root.iterdir():
        found = _ID_SUFFIX.search(child.name)
        if child.is_dir() and found and int(found.group(1)) == job_id:
            return child.name
    return None


def sync(job_id: int, name: str) -> tuple[str, str]:
    """The job's folder and its notes file, both relative to the workspace: the folder made, or renamed after the
    job's present name (and its notes file with it)."""
    wanted = f"{_slug(name)}_job-{job_id}"
    existing = _existing(job_id)
    if existing is None:
        _checked(file_manager.create_folder("", SCHEDULER_JOBS_FOLDER))
        _checked(file_manager.create_folder(SCHEDULER_JOBS_FOLDER, wanted))
    elif existing != wanted:
        _checked(file_manager.rename(SCHEDULER_JOBS_FOLDER, existing, wanted))
        folder = f"{SCHEDULER_JOBS_FOLDER}/{wanted}"
        if (file_manager.DOCUMENTS_DIR / folder / f"{existing}.md").is_file():
            _checked(file_manager.rename(folder, f"{existing}.md", f"{wanted}.md"))
    folder = f"{SCHEDULER_JOBS_FOLDER}/{wanted}"
    return folder, f"{folder}/{wanted}.md"


def has_notes(notes_file: str) -> bool:
    return (file_manager.DOCUMENTS_DIR / notes_file).is_file()


async def remove(job_id: int) -> None:
    """Delete the job's folder with everything in it (and its entries in the search index)."""
    existing = _existing(job_id)
    if existing is not None:
        _checked(await file_manager.delete_folder(SCHEDULER_JOBS_FOLDER, existing, recursive=True))
