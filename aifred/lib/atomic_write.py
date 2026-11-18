"""The one way to write a file so that readers and crashes never see half of it."""
import os
import uuid
from pathlib import Path


def write_text_atomic(path: Path, text: str, *, mode: int = 0o666) -> None:
    """Replace ``path`` with ``text`` in one step: it holds the old or the new content, never a part.

    The text goes into a temporary file beside ``path`` (same directory, so ``os.replace`` stays on one
    filesystem), is fsynced, and is swapped in. The temporary name is unique per call: two writers of the
    same file (tasks, threads) never share or delete each other's temporary file; the last swap wins.
    ``mode`` is the permission of the new file, passed to ``os.open`` so it never exists with wider rights
    (the umask applies as for ``open``; use 0o600 for secrets). The caller creates the directory and
    serializes the content (JSON, YAML); errors, including OSError, propagate after the temporary file is removed.
    """
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
