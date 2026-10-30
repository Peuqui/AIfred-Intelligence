"""What the Nextcloud plugin refuses, for the tool layer and the document source."""


class NextcloudDisabled(PermissionError):
    """The module is switched off or not writable in the plugin settings."""


class NextcloudRefused(ValueError):
    """The request cannot be carried out as asked."""
