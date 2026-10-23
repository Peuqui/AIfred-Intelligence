"""EPIM plugin settings — the single source for the database path and the
access level of every module.

All values live in the plugin's own ``settings.json`` (written by the plugin
settings UI through ``EpimPlugin._save_settings``). Nothing else in AIfred
reads them, so the plugin stays atomic.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ....lib.plugin_base import load_plugin_settings, save_plugin_settings

DB_PATH_KEY = "EPIM_DB_PATH"

OFF, READ, WRITE = "off", "read", "write"
ACCESS_LEVELS = (OFF, READ, WRITE)

# EPIM's modules as this plugin offers them, with their default access.
# Password entries hold credentials and start switched off.
MODULE_DEFAULTS: dict[str, str] = {
    "calendar": READ,
    "contacts": READ,
    "notes": READ,
    "todos": READ,
    "passwords": OFF,
}


def module_key(module: str) -> str:
    return f"EPIM_MODULE_{module.upper()}"


@dataclass(frozen=True)
class EpimSettings:
    db_path: Path | None
    access: dict[str, str] = field(default_factory=lambda: dict(MODULE_DEFAULTS))

    def can_read(self, module: str) -> bool:
        return self.access[module] in (READ, WRITE)

    def can_write(self, module: str) -> bool:
        return self.access[module] == WRITE

    @property
    def any_write(self) -> bool:
        return any(level == WRITE for level in self.access.values())


def load_raw_settings() -> dict[str, str]:
    return load_plugin_settings(__file__)


def save_raw_settings(settings: dict[str, str]) -> None:
    save_plugin_settings(__file__, settings)


def load_epim_settings() -> EpimSettings:
    return parse_settings(load_raw_settings())


def parse_settings(raw: dict[str, str]) -> EpimSettings:
    """An unknown access level raises instead of being read as any level —
    the setting guards the database."""
    path = raw.get(DB_PATH_KEY, "").strip()
    access: dict[str, str] = {}
    for module, default in MODULE_DEFAULTS.items():
        level = raw.get(module_key(module), default)
        if level not in ACCESS_LEVELS:
            raise ValueError(f"{module_key(module)} must be one of {ACCESS_LEVELS}, got {level!r}")
        access[module] = level
    return EpimSettings(db_path=Path(path) if path else None, access=access)
