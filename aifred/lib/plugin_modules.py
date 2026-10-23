"""Per-module access levels for plugins whose data falls into modules — EPIM
(calendar, contacts, notes, …) and Nextcloud (calendars, contacts, tasks).

Each module is ``off``, ``read`` or ``write`` in the plugin's own settings.
The labels of the three levels live in every plugin's ``i18n.json`` under
:data:`LEVEL_LABEL_KEYS` (plugins ship their texts themselves).
"""

from dataclasses import dataclass

from .plugin_base import CredentialField

OFF, READ, WRITE = "off", "read", "write"
ACCESS_LEVELS = (OFF, READ, WRITE)
LEVEL_LABEL_KEYS = {level: f"module_access_{level}" for level in ACCESS_LEVELS}


def module_setting_key(prefix: str, module: str) -> str:
    """``("EPIM", "contacts")`` → ``EPIM_MODULE_CONTACTS``."""
    return f"{prefix}_MODULE_{module.upper()}"


@dataclass(frozen=True)
class ModuleAccess:
    levels: dict[str, str]

    def can_read(self, module: str) -> bool:
        return self.levels[module] != OFF

    def can_write(self, module: str) -> bool:
        return self.levels[module] == WRITE

    @property
    def any_write(self) -> bool:
        return WRITE in self.levels.values()

    @classmethod
    def parse(cls, raw: dict[str, str], prefix: str, defaults: dict[str, str]) -> "ModuleAccess":
        """An unknown level raises instead of being read as any level — the
        setting guards the plugin's data."""
        levels: dict[str, str] = {}
        for module, default in defaults.items():
            key = module_setting_key(prefix, module)
            level = raw.get(key, default)
            if level not in ACCESS_LEVELS:
                raise ValueError(f"{key} must be one of {ACCESS_LEVELS}, got {level!r}")
            levels[module] = level
        return cls(levels)


def module_credential_fields(prefix: str, defaults: dict[str, str], label_key: str) -> list[CredentialField]:
    """One dropdown per module for the plugin settings UI. ``label_key`` is a
    format string with ``{module}`` naming the plugin's i18n key."""
    options = [(level, LEVEL_LABEL_KEYS[level]) for level in ACCESS_LEVELS]
    return [
        CredentialField(
            env_key=module_setting_key(prefix, module),
            label_key=label_key.format(module=module),
            default=default,
            options=options,
        )
        for module, default in defaults.items()
    ]
