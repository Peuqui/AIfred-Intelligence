"""EPIM plugin settings — the single source for database path and write switch.

Both values live in the plugin's own ``settings.json`` (written by the
plugin settings UI through ``EpimPlugin._save_settings``). Nothing else in
AIfred reads them, so the plugin stays atomic.
"""

from dataclasses import dataclass
from pathlib import Path

from ....lib.plugin_base import load_plugin_settings, save_plugin_settings

DB_PATH_KEY = "EPIM_DB_PATH"
WRITE_ENABLED_KEY = "EPIM_WRITE_ENABLED"
WRITE_ENABLED_VALUES = ("true", "false")
WRITE_ENABLED_DEFAULT = "false"


@dataclass(frozen=True)
class EpimSettings:
    db_path: Path | None
    write_enabled: bool


def load_raw_settings() -> dict[str, str]:
    return load_plugin_settings(__file__)


def save_raw_settings(settings: dict[str, str]) -> None:
    save_plugin_settings(__file__, settings)


def load_epim_settings() -> EpimSettings:
    return parse_settings(load_raw_settings())


def parse_settings(raw: dict[str, str]) -> EpimSettings:
    """An unknown write-switch value raises instead of being read as either
    state — the switch guards the database."""
    path = raw.get(DB_PATH_KEY, "").strip()
    write = raw.get(WRITE_ENABLED_KEY, WRITE_ENABLED_DEFAULT)
    if write not in WRITE_ENABLED_VALUES:
        raise ValueError(
            f"{WRITE_ENABLED_KEY} must be one of {WRITE_ENABLED_VALUES}, got {write!r}"
        )
    return EpimSettings(db_path=Path(path) if path else None, write_enabled=write == "true")
