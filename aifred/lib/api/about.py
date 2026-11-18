"""The Auctor page's endpoint: who made AIfred, under which licence, and which version runs."""

import re
import subprocess
from datetime import datetime

from pydantic import BaseModel, Field

from .. import config
from .app import api_app

_NOTICE = re.compile(r"^>?\s*(Required Notice:.*\S)\s*$", re.MULTILINE)
_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_URL = re.compile(r"^<(https?://\S+)>\s*$", re.MULTILINE)


class About(BaseModel):
    author_notice: str = Field(description="The 'Required Notice:' line of LICENSE (names the author)")
    license_name: str = Field(description="First heading of LICENSE")
    license_url: str = Field(description="The licence's URL, from LICENSE")
    version_commit: str | None = Field(description="Short hash of the commit the server started on; null without git")
    version_date: datetime | None = Field(description="Commit date; null without git")
    repo_url: str


def _git_version() -> tuple[str, datetime] | None:
    """Commit hash and date of the checkout the server runs from, read once at start; None where git cannot say."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h %cI"], cwd=config.PROJECT_ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out[0], datetime.fromisoformat(out[1])


_VERSION = _git_version()


def _match(pattern: re.Pattern[str], text: str) -> str:
    found = pattern.search(text)
    if not found:
        raise ValueError(f"{config.LICENSE_FILE} lacks {pattern.pattern}")
    return found.group(1)


@api_app.get("/about", response_model=About, tags=["System"])
async def about() -> About:
    """Author notice, licence and running version; the notice and the licence come from the LICENSE file."""
    text = config.LICENSE_FILE.read_text(encoding="utf-8")
    return About(
        author_notice=_match(_NOTICE, text),
        license_name=_match(_TITLE, text),
        license_url=_match(_URL, text),
        version_commit=_VERSION[0] if _VERSION else None,
        version_date=_VERSION[1] if _VERSION else None,
        repo_url=config.REPO_URL,
    )
