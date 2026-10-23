"""EPIM plugin settings — the single source for the database path and the
access level of every module.

All values live in the plugin's own ``settings.json`` (written by the plugin
settings UI through ``EpimPlugin._save_settings``). Nothing else in AIfred
reads them, so the plugin stays atomic.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ....lib.plugin_base import load_plugin_settings, save_plugin_settings
from ....lib.plugin_modules import OFF, READ, ModuleAccess

DB_PATH_KEY = "EPIM_DB_PATH"
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


def load_raw_settings() -> dict[str, str]:
    return load_plugin_settings(__file__)


def save_raw_settings(settings: dict[str, str]) -> None:
    save_plugin_settings(__file__, settings)


def load_epim_settings() -> EpimSettings:
    return parse_settings(load_raw_settings())


def parse_settings(raw: dict[str, str]) -> EpimSettings:
    path = raw.get(DB_PATH_KEY, "").strip()
    return EpimSettings(
        db_path=Path(path) if path else None,
        modules=ModuleAccess.parse(raw, SETTINGS_PREFIX, MODULE_DEFAULTS),
    )
