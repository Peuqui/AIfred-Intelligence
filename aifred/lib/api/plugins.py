"""Plugins for the Vue settings: the tool plugins and channels with their switches, a plugin's settings form and its
OAuth connection (lib/plugin_admin.py). A password never comes back: the form says whether one is set."""

import asyncio
from dataclasses import asdict

from fastapi import Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ..logging_utils import log_message
from .app import api_app
from .schemas import ChoiceOption


class ToolPluginRow(BaseModel):
    name: str
    label: str
    description: str = Field(description="'' while the plugin is switched off (it is not loaded)")
    enabled: bool
    has_settings: bool
    needs_connection: bool = Field(description="An OAuth plugin without a connection with the rights it needs")


class ChannelRow(BaseModel):
    name: str
    label: str
    description: str
    configured: bool
    monitor: bool = Field(description="The channel is on")
    listener: bool = Field(description="It listens in the background (a channel that always answers: with the switch)")
    auto_reply: bool
    always_reply: bool
    tier: int | None = Field(description="What a message of the channel may make AIfred do; null: no tier")


class PluginOverview(BaseModel):
    tools: list[ToolPluginRow]
    channels: list[ChannelRow]
    tiers: list[ChoiceOption]


class ToolSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class ChannelChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monitor: bool | None = None
    listener: bool | None = None
    auto_reply: bool | None = None
    tier: int | None = None


class FormOption(BaseModel):
    value: str
    label: str


class FormField(BaseModel):
    key: str
    label: str
    tooltip: str
    placeholder: str
    password: bool = Field(description="Never sent back: is_set says whether one is stored")
    group: str = Field(description="Fields of one group stand in one row")
    width: int
    options: list[FormOption]
    value: str
    is_set: bool


class PluginForm(BaseModel):
    plugin: str
    title: str
    fields: list[FormField]
    oauth_provider: str
    connected: bool
    rights_missing: bool = Field(description="Connected, but a service switched on since needs a new consent")


class FormValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str] = Field(description="By field key; an empty password keeps the stored one")


class OAuthLink(BaseModel):
    url: str = Field(description="The provider's sign-in page; '' without the provider's client credentials")


def _overview() -> PluginOverview:
    from ..plugin_admin import channels, tier_choices, tools
    from ..prompt_loader import get_language

    lang = get_language()
    return PluginOverview(
        tools=[ToolPluginRow(**asdict(row)) for row in tools(lang)],
        channels=[ChannelRow(**asdict(row)) for row in channels(lang)],
        tiers=[ChoiceOption(id=str(tier), label=label) for tier, label in tier_choices(lang)],
    )


def _form(name: str) -> PluginForm:
    from ..plugin_admin import form
    from ..prompt_loader import get_language

    described = form(name, get_language())
    fields = [FormField(**{**asdict(field), "options": [FormOption(value=value, label=label) for value, label in field.options]})
              for field in described.fields]
    return PluginForm(plugin=described.plugin, title=described.title, fields=fields, oauth_provider=described.oauth_provider,
                      connected=described.connected, rights_missing=described.rights_missing)


@api_app.get("/plugins", response_model=PluginOverview, tags=["Plugins"])
async def plugins_overview() -> PluginOverview:
    return await asyncio.to_thread(_overview)


@api_app.put("/plugins/tools/{name}", response_model=PluginOverview, tags=["Plugins"])
async def plugins_tool_switch(name: str, switch: ToolSwitch) -> PluginOverview:
    """Switch a tool plugin on or off (switching the vision off disarms the eye); 404 for no such plugin."""
    from ..plugin_admin import set_tool

    await set_tool(name, switch.enabled)
    log_message(f"🔌 Plugin {name} {'enabled' if switch.enabled else 'disabled'}")
    return await asyncio.to_thread(_overview)


@api_app.patch("/plugins/channels/{name}", response_model=PluginOverview, tags=["Plugins"])
async def plugins_channel_change(name: str, change: ChannelChange) -> PluginOverview:
    """A channel's switches or tier; its listener starts or stops as they say. 404 for no such channel, 409 for
    switching on one that is not set up, 422 for a tier that does not exist."""
    from ..plugin_admin import set_channel

    changes = change.model_dump(exclude_none=True)
    await set_channel(name, **changes)
    log_message(f"📨 Channel {name}: {changes}")
    return await asyncio.to_thread(_overview)


@api_app.get("/plugins/{name}/form", response_model=PluginForm, tags=["Plugins"])
async def plugins_form(name: str) -> PluginForm:
    """The plugin's settings form in the UI language; 404 for a plugin without one."""
    return await asyncio.to_thread(_form, name)


@api_app.put("/plugins/{name}/form", response_model=PluginForm, tags=["Plugins"])
async def plugins_form_save(name: str, saved: FormValues) -> PluginForm:
    """Store the form (secrets in .env, the rest in the plugin's settings.json); a channel is switched on with its
    listener. 422 for a field it does not have or an option it does not offer."""
    from ..plugin_admin import save_form

    await save_form(name, saved.values)
    log_message(f"🔧 Plugin {name}: settings saved ({sorted(saved.values)})")
    return await asyncio.to_thread(_form, name)


@api_app.get("/plugins/{name}/oauth-link", response_model=OAuthLink, tags=["Plugins"])
async def plugins_oauth_link(name: str, origin: str = Query(..., description="The page's own address (scheme://host[:port])")) -> OAuthLink:
    """The provider's sign-in page for the plugin; the provider sends the user back to ``origin``."""
    from ..plugin_admin import oauth_link

    return OAuthLink(url=await asyncio.to_thread(oauth_link, name, origin))


@api_app.delete("/plugins/{name}/oauth", status_code=204, tags=["Plugins"])
async def plugins_oauth_disconnect(name: str) -> Response:
    from ..plugin_admin import oauth_disconnect

    await oauth_disconnect(name)
    log_message(f"🔌 Plugin {name}: OAuth disconnected")
    return Response(status_code=204)
