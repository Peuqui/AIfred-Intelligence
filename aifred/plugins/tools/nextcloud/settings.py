"""Nextcloud plugin settings — server address, user and the access level of
every module. The app password is a secret: it lives in ``.env`` and is read
through the credential broker, never from this file."""

from dataclasses import dataclass, field

from ....lib.plugin_base import load_plugin_settings, save_plugin_settings
from ....lib.plugin_modules import READ, ModuleAccess

URL_KEY = "NEXTCLOUD_URL"
USER_KEY = "NEXTCLOUD_USER"
APP_PASSWORD_KEY = "NEXTCLOUD_APP_PASSWORD"
CREDENTIAL_SERVICE = "nextcloud"
AGENT_FOLDER_KEY = "NEXTCLOUD_AGENT_FOLDER"
AGENT_FOLDER_DEFAULT = "AIfred-Intelligence"
NOTES_FOLDER_KEY = "NEXTCLOUD_NOTES_FOLDER"
NOTES_FOLDER_DEFAULT = "Notes"
SETTINGS_PREFIX = "NEXTCLOUD"
REQUEST_TIMEOUT_SEC = 20.0

# calendar = events (VEVENT), tasks = todos (VTODO), contacts = address books, notes = the Notes app,
# files = the WebDAV files.
MODULE_DEFAULTS: dict[str, str] = {
    "calendar": READ,
    "tasks": READ,
    "contacts": READ,
    "notes": READ,
    "files": READ,
}


@dataclass(frozen=True)
class NextcloudSettings:
    url: str
    user: str
    modules: ModuleAccess = field(default_factory=lambda: ModuleAccess(dict(MODULE_DEFAULTS)))
    agent_folder: str = AGENT_FOLDER_DEFAULT
    notes_folder: str = NOTES_FOLDER_DEFAULT


def load_raw_settings() -> dict[str, str]:
    return load_plugin_settings(__file__)


def save_raw_settings(settings: dict[str, str]) -> None:
    save_plugin_settings(__file__, settings)


def parse_settings(raw: dict[str, str]) -> NextcloudSettings:
    return NextcloudSettings(
        url=raw.get(URL_KEY, "").strip().rstrip("/"),
        user=raw.get(USER_KEY, "").strip(),
        modules=ModuleAccess.parse(raw, SETTINGS_PREFIX, MODULE_DEFAULTS),
        agent_folder=raw.get(AGENT_FOLDER_KEY, "").strip().strip("/") or AGENT_FOLDER_DEFAULT,
        notes_folder=raw.get(NOTES_FOLDER_KEY, "").strip().strip("/") or NOTES_FOLDER_DEFAULT,
    )


def load_nextcloud_settings() -> NextcloudSettings:
    return parse_settings(load_raw_settings())
