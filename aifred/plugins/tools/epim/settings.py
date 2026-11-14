"""EPIM plugin settings — the single source for the database path and the
access level of every module and whether the deletion sync to Nextcloud runs (``scripts/epim_deletions_to_nextcloud.py``
reads it, with the plugin on or off).

All values live in the plugin's own ``settings.json`` (written by the plugin
settings UI through ``EpimPlugin._save_settings``). Nothing else in AIfred
reads them, so the plugin stays atomic.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ....lib.plugin_base import load_plugin_settings, save_plugin_settings
from ....lib.plugin_modules import OFF, READ, ModuleAccess

DB_PATH_KEY = "EPIM_DB_PATH"
DELETION_SYNC_KEY = "EPIM_DELETION_SYNC"     # "on" | "off": the hourly carrying of deletions over to Nextcloud
DELETION_SYNC_VALUES = ("on", "off")
SETTINGS_PREFIX = "EPIM"

# EPIM's modules as this plugin offers them, with their default access.
# Password entries hold credentials and start switched off.
MODULE_DEFAULTS: dict[str, str] = {
    "calendar": READ,
    "contacts": READ,
    "notes": READ,
    "todos": READ,
    "passwords": OFF,
}


@dataclass(frozen=True)
class EpimSettings:
    db_path: Path | None
    modules: ModuleAccess = field(default_factory=lambda: ModuleAccess(dict(MODULE_DEFAULTS)))
    deletion_sync: bool = True


def load_raw_settings() -> dict[str, str]:
    return load_plugin_settings(__file__)


def save_raw_settings(settings: dict[str, str]) -> None:
    save_plugin_settings(__file__, settings)


def load_epim_settings() -> EpimSettings:
    return parse_settings(load_raw_settings())


def parse_settings(raw: dict[str, str]) -> EpimSettings:
    path = raw.get(DB_PATH_KEY, "").strip()
    deletion_sync = raw.get(DELETION_SYNC_KEY, "on")
    if deletion_sync not in DELETION_SYNC_VALUES:
        raise ValueError(f"{DELETION_SYNC_KEY} must be one of {DELETION_SYNC_VALUES}, got {deletion_sync!r}")
    return EpimSettings(
        db_path=Path(path) if path else None,
        modules=ModuleAccess.parse(raw, SETTINGS_PREFIX, MODULE_DEFAULTS),
        deletion_sync=deletion_sync == "on",
    )
