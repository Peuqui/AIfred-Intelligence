"""The plugins as the settings pages manage them: tool plugins on or off, channels (on, listening, answering on their
own, their security tier) and a plugin's settings form (secrets in .env, the rest in the plugin's settings.json) with
its OAuth connection. One place for the Reflex page and the API.

A password never leaves the server: the form only says whether one is set, and an empty password field keeps the
stored one. Field texts come from the plugin's own i18n.json, the page's frame from the central one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .plugin_base import (
    CredentialField,
    load_plugin_i18n,
    plugin_description,
    plugin_dir_of,
    plugin_display_name,
)
from .user_error import Conflict, Missing

CHANNEL_TOGGLES = "channel_toggles"
CHANNEL_TIERS = "channel_security_tiers"
NO_TIER_CHANNELS = ("browser",)     # the user in front of the screen: no tier to choose


def translated_key(key: str, plugin: object, lang: str) -> str | None:
    """A credential field's i18n key from the plugin's own i18n.json; None when the plugin does not know it (a
    label written as plain text)."""
    text = load_plugin_i18n(plugin_dir_of(plugin)).get(key, {}).get(lang, "")
    return text or None


def translated_options(field: CredentialField, plugin: object, lang: str) -> list[tuple[str, str]]:
    """A dropdown field's (value, label) pairs, the labels translated (plain-text labels stay as written)."""
    if not field.options:
        return []
    return [(value, translated_key(label, plugin, lang) or label) for value, label in field.options]


# ── The list ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolRow:
    name: str
    label: str
    description: str          # "" for a plugin switched off (it is not loaded)
    enabled: bool
    has_settings: bool        # it has a settings form
    needs_connection: bool    # an OAuth plugin without a connection carrying the rights it needs


@dataclass(frozen=True)
class ChannelRow:
    name: str
    label: str
    description: str
    configured: bool
    monitor: bool             # the channel is on
    listener: bool            # it listens in the background (a channel that always answers: with the switch)
    auto_reply: bool          # it answers on its own
    always_reply: bool
    tier: int | None          # what a message of the channel may make AIfred do; None: no tier (the browser)


def _oauth_current(plugin: Any) -> bool:
    """Connected with every right the plugin's settings need (a plugin without ``oauth_grant_current`` takes any)."""
    from .oauth import oauth_broker

    provider = getattr(plugin, "oauth_provider", None) or ""
    if not provider or not oauth_broker.is_connected(provider):
        return False
    check = getattr(plugin, "oauth_grant_current", None)
    return check is None or bool(check(oauth_broker.granted_scopes(provider)))


def tools(lang: str) -> list[ToolRow]:
    from .plugin_registry import get_tool_plugin, list_all_plugins

    rows = []
    for entry in list_all_plugins():
        if entry["type"] != "tool":
            continue
        plugin = get_tool_plugin(entry["name"])     # None while switched off
        oauth = bool(plugin is not None and getattr(plugin, "oauth_provider", None))
        rows.append(ToolRow(
            name=entry["name"], label=entry[f"display_{lang}"],
            description=plugin_description(plugin, lang) if plugin is not None else "",
            enabled=bool(entry["enabled"]), has_settings=bool(entry["has_credentials"]),
            needs_connection=oauth and not _oauth_current(plugin),
        ))
    return rows


def _channel_settings() -> tuple[dict[str, Any], dict[str, Any]]:
    from .settings import load_settings

    settings = load_settings() or {}
    return dict(settings.get(CHANNEL_TOGGLES) or {}), dict(settings.get(CHANNEL_TIERS) or {})


def channels(lang: str) -> list[ChannelRow]:
    from .plugin_registry import all_channels
    from .security import DEFAULT_TIER_BY_SOURCE, TIER_COMMUNICATE

    toggles, tiers = _channel_settings()
    rows = []
    for name, plugin in all_channels().items():
        switches = toggles.get(name) or {}
        rows.append(ChannelRow(
            name=name, label=plugin_display_name(plugin, lang), description=plugin_description(plugin, lang),
            configured=plugin.is_configured(), monitor=bool(switches.get("monitor")),
            listener=bool(switches.get("listener")), auto_reply=bool(switches.get("auto_reply")),
            always_reply=bool(plugin.always_reply),
            tier=None if name in NO_TIER_CHANNELS else int(tiers.get(name, DEFAULT_TIER_BY_SOURCE.get(name, TIER_COMMUNICATE))),
        ))
    return rows


def tier_choices(lang: str) -> list[tuple[int, str]]:
    from .i18n import t
    from .security import TIER_I18N_KEYS

    return [(tier, f"T{tier} — {t(label_key, lang=lang)}") for tier, label_key, _description in TIER_I18N_KEYS]


# ── Switches ─────────────────────────────────────────────────────────────


async def set_tool(name: str, enabled: bool) -> None:
    """Switch a tool plugin on or off (its ``.disabled`` marker). ``Missing`` for no such plugin. An OAuth plugin
    without its connection offers no tools until connected (its form; the list says ``needs_connection``).
    Switching the vision off disarms the eye first: nothing of it may keep watching."""
    from .plugin_registry import (
        disable_plugin,
        enable_plugin,
        is_plugin_enabled,
        list_all_plugins,
    )

    if name not in {entry["name"] for entry in list_all_plugins() if entry["type"] == "tool"}:
        raise Missing("no_plugin", plugin=name)
    if enabled == is_plugin_enabled(name):
        return
    if not enabled:
        if name == "vision":
            from .vigilantia import set_armed

            await set_armed(False)
        disable_plugin(name)
        return
    enable_plugin(name)


def _lang() -> str:
    from .prompt_loader import get_language

    return get_language()


async def _start_listener(name: str, plugin: Any) -> None:
    from .message_hub import message_hub

    if not message_hub.is_running(name):
        message_hub.register(name, plugin.listener_loop)
        await message_hub.start_all()


async def set_channel(
    name: str, *, monitor: bool | None = None, listener: bool | None = None, auto_reply: bool | None = None,
    tier: int | None = None,
) -> None:
    """Change a channel's switches or tier and start or stop its listener as they say. ``Missing`` for no such
    channel, ``Conflict`` for switching on a channel that is not configured (its form first), ``Invalid`` for a
    tier that does not exist or a channel without tiers."""
    from .message_hub import message_hub
    from .plugin_registry import get_channel
    from .security import TIER_I18N_KEYS
    from .settings import update_settings
    from .user_error import Invalid

    plugin = get_channel(name)
    if plugin is None:
        raise Missing("no_plugin", plugin=name)
    if monitor and not plugin.is_configured():
        raise Conflict("channel_not_configured", plugin=plugin_display_name(plugin, _lang()))
    if tier is not None and (name in NO_TIER_CHANNELS or tier not in {level for level, _, _ in TIER_I18N_KEYS}):
        raise Invalid("unknown_tier", value=tier)
    toggles, tiers = _channel_settings()
    switches = dict(toggles.get(name) or {})
    switches.update({key: value for key, value in (("monitor", monitor), ("listener", listener),
                                                    ("auto_reply", auto_reply)) if value is not None})
    toggles[name] = switches
    changes: dict[str, Any] = {CHANNEL_TOGGLES: toggles}
    if tier is not None:
        changes[CHANNEL_TIERS] = {**tiers, name: tier}
    if not update_settings(changes):
        from .user_error import NotDone

        raise NotDone("settings_not_saved")
    # A channel that always answers listens with its switch; the others have a switch of their own.
    listening = switches.get("monitor") if plugin.always_reply else switches.get("listener")
    if (monitor is not None and plugin.always_reply) or (listener is not None and not plugin.always_reply):
        if listening:
            await _start_listener(name, plugin)
        else:
            message_hub.unregister(name)


# ── The settings form ────────────────────────────────────────────────────


@dataclass(frozen=True)
class FormField:
    key: str                          # its environment variable (also its key in the plugin's settings.json)
    label: str
    tooltip: str
    placeholder: str
    password: bool                    # never sent: ``is_set`` says whether one is stored
    group: str                        # fields of one group stand in one row
    width: int
    options: list[tuple[str, str]]    # (value, label) of a dropdown
    value: str
    is_set: bool


@dataclass(frozen=True)
class Form:
    plugin: str
    title: str
    fields: list[FormField]
    oauth_provider: str
    connected: bool                   # connected with the rights the settings need
    rights_missing: bool              # connected, but a service switched on since needs a new consent


def _plugin(name: str) -> tuple[Any, list[CredentialField], bool]:
    """The plugin, its fields and whether it is a channel; ``Missing`` for no plugin with a settings form."""
    from .plugin_registry import get_channel, get_tool_plugin

    channel = get_channel(name)
    plugin: Any = channel if channel is not None else get_tool_plugin(name)
    fields = list(getattr(plugin, "credential_fields", None) or []) if plugin is not None else []
    if not fields:
        raise Missing("no_plugin_form", plugin=name)
    return plugin, fields, channel is not None


def _stored_settings(plugin: Any, is_channel: bool) -> dict[str, str]:
    if is_channel:
        return dict(plugin.load_settings())
    loader = getattr(plugin, "_load_settings", None)
    return dict(loader()) if callable(loader) else {}


def form(name: str, lang: str) -> Form:
    from .i18n import t
    from .oauth import oauth_broker

    plugin, fields, is_channel = _plugin(name)
    stored = _stored_settings(plugin, is_channel)
    described = []
    for field in fields:
        value = os.environ.get(field.env_key, field.default) if field.is_secret else stored.get(field.env_key, os.environ.get(field.env_key, field.default))
        described.append(FormField(
            key=field.env_key, label=translated_key(field.label_key, plugin, lang) or field.label_key,
            tooltip=translated_key(f"{field.label_key}_tooltip", plugin, lang) or "", placeholder=field.placeholder,
            password=field.is_password, group=field.group, width=field.width_ratio,
            options=translated_options(field, plugin, lang), value="" if field.is_password else value,
            is_set=bool(os.environ.get(field.env_key)),
        ))
    provider = getattr(plugin, "oauth_provider", None) or ""
    connected = bool(provider) and _oauth_current(plugin)
    return Form(
        plugin=name, title=f"{plugin_display_name(plugin, lang)} — {t('cred_title_suffix', lang=lang)}",
        fields=described, oauth_provider=provider, connected=connected,
        rights_missing=bool(provider) and oauth_broker.is_connected(provider) and not connected,
    )


async def save_form(name: str, values: dict[str, str]) -> None:
    """Store the form: secrets in .env (and the environment), the rest in the plugin's settings.json; an empty
    password keeps the stored one. A channel takes the values and is switched on with its listener. ``Missing``
    for no such form, ``Invalid`` for a field it does not have or an option it does not offer."""
    from dotenv import set_key

    from .config import PROJECT_ROOT
    from .user_error import Invalid

    plugin, fields, is_channel = _plugin(name)
    by_key = {field.env_key: field for field in fields}
    unknown = set(values) - set(by_key)
    if unknown:
        raise Invalid("unknown_fields", fields=", ".join(sorted(unknown)))
    for key, value in values.items():
        offered = [option for option, _label in by_key[key].options or []]
        if offered and value not in offered:
            raise Invalid("unknown_option", field=key, value=value)
    env_path = str(PROJECT_ROOT / ".env")
    stored = _stored_settings(plugin, is_channel)
    applied: dict[str, str] = {}
    for field in fields:
        value = values.get(field.env_key, "")
        if field.is_password and not value:
            applied[field.env_key] = os.environ.get(field.env_key, "")
            continue
        applied[field.env_key] = value
        if field.is_secret:
            set_key(env_path, field.env_key, value)
        else:
            stored[field.env_key] = value
        os.environ[field.env_key] = value
    if stored:
        if is_channel:
            plugin.save_settings(stored)
        else:
            saver = getattr(plugin, "_save_settings", None)
            if callable(saver):
                saver(stored)
    if is_channel:
        plugin.apply_credentials(applied)
        enabled_key = f"{name.upper()}_ENABLED"
        set_key(env_path, enabled_key, "true")
        os.environ[enabled_key] = "true"
        await set_channel(name, monitor=True)
        await _start_listener(name, plugin)


# ── OAuth ────────────────────────────────────────────────────────────────


def oauth_link(name: str, origin: str) -> str:
    """The provider's sign-in page for the plugin, back to ``origin`` (the page's own address); "" without the
    provider's client credentials. ``Missing`` for a plugin without OAuth."""
    from .credential_broker import broker
    from .oauth import oauth_broker

    plugin, _fields, _is_channel = _plugin(name)
    provider = getattr(plugin, "oauth_provider", None) or ""
    if not provider:
        raise Missing("no_plugin_oauth", plugin=name)
    if not broker.get(provider, "client_id"):
        return ""
    scopes = getattr(plugin, "aggregated_scopes", None)
    redirect = f"{origin.rstrip('/')}/api/oauth/{provider}/callback"
    return str(oauth_broker.get_auth_url(provider, list(scopes()) if callable(scopes) else [], redirect))


async def oauth_disconnect(name: str) -> None:
    from .oauth import oauth_broker

    plugin, _fields, _is_channel = _plugin(name)
    provider = getattr(plugin, "oauth_provider", None) or ""
    if not provider:
        raise Missing("no_plugin_oauth", plugin=name)
    await oauth_broker.disconnect(provider)
