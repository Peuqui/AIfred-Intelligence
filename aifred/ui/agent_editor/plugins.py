"""Agent-Editor: Plugins-Tab — Tool-/Channel-Plugins mit Tier-Badges."""
# mypy: disable-error-code="index, operator, call-arg, func-returns-value, arg-type"
# Reflex UI code: Var indexing, rx.icon module callable, event handler binding
# are all runtime-correct but not statically typeable.

from __future__ import annotations

import reflex as rx

from ...state import AIState
from ..helpers import t
from .header import _editor_header
from .tool_pills import _tier_help_content


def _plugin_description_popover(description: str) -> rx.Component:
    """Lightbulb icon + popover showing the plugin description.

    Empty descriptions render an empty box (so column alignment stays
    consistent across the whole list).
    """
    if not description:
        return rx.box(width="14px", flex_shrink="0")
    return rx.popover.root(
        rx.popover.trigger(
            rx.icon("lightbulb", size=14, color="#FFD700", cursor="pointer"),
        ),
        rx.popover.content(
            rx.text(description, font_size="11px", color="white", padding="8px", max_width="320px"),
            side="right",
            style={"background": "#2a2a3e", "border": "1px solid #555", "border-radius": "8px"},
        ),
    )


def _plugins_view() -> rx.Component:
    """Plugins tab: channel and tool plugin management."""
    from ...lib.plugin_registry import all_channels
    from ...lib.security import TIER_I18N_KEYS
    from ...lib.i18n import TranslationManager as _TM
    # Build tier dropdown options per language (select needs static lists)
    _tier_opts = {
        lang: [f"T{tier} — {_TM.get_text(lk, lang)}" for tier, lk, _dk in TIER_I18N_KEYS]
        for lang in ("de", "en")
    }
    tier_options = rx.cond(AIState.ui_language == "de", _tier_opts["de"], _tier_opts["en"])

    # ── Build tool plugin rows at build time (static, like channels) ──
    # list_all_plugins() (not discover_tools) so DISABLED plugins still show up and
    # can be re-enabled. get_tool_plugin() is None for a disabled plugin — then
    # we render a minimal row (name + toggle), without description/settings.
    from ...lib.plugin_registry import list_all_plugins, get_tool_plugin
    tool_rows: list[rx.Component] = []
    for _pmeta in list_all_plugins():
        if _pmeta["type"] != "tool":
            continue
        name = _pmeta["name"]
        plugin = get_tool_plugin(name)  # None if disabled
        enabled_var = AIState.tool_plugin_toggles[name].to(str) == "1"
        has_creds = bool(getattr(plugin, "credential_fields", None)) if plugin else False
        description = (getattr(plugin, "description", "") or "") if plugin else ""
        display_name = plugin.display_name if plugin else _pmeta.get("display", name)

        row_children: list[rx.Component] = [
            # Name column — fixed width keeps lightbulb column aligned across rows
            rx.hstack(
                rx.icon("puzzle", size=14, color=rx.cond(enabled_var, "#4CAF50", "#666")),
                rx.text(display_name, font_size="14px", color=rx.cond(enabled_var, "white", "#999")),
                spacing="2", align="center", min_width="220px",
            ),
            _plugin_description_popover(description),
            rx.spacer(),
        ]

        if has_creds:
            row_children.append(
                rx.icon_button(
                    rx.icon("settings", size=14),
                    on_click=AIState.open_channel_credentials(name),
                    size="1",
                    variant="ghost",
                    color_scheme="gray",
                    cursor="pointer",
                ),
            )
        else:
            # Plugins can declare a custom settings event (e.g. audio_player
            # → "open_audio_settings"). The Plugin-Tab gear icon then
            # dispatches that state event instead of opening the generic
            # credentials modal.
            settings_event_name = getattr(plugin, "settings_event_name", None)
            if settings_event_name:
                handler = getattr(AIState, settings_event_name, None)
                if callable(handler):
                    row_children.append(
                        rx.icon_button(
                            rx.icon("settings", size=14),
                            on_click=handler,
                            size="1",
                            variant="ghost",
                            color_scheme="gray",
                            cursor="pointer",
                        ),
                    )

        row_children.extend([
            rx.switch(
                checked=enabled_var,
                on_change=lambda _val, n=name: AIState.toggle_tool_plugin(n),
                size="1",
            ),
            rx.text(
                rx.cond(enabled_var, "ON", "OFF"),
                font_size="11px",
                color=rx.cond(enabled_var, "#4CAF50", "#999"),
                min_width="24px",
            ),
        ])

        tool_rows.append(
            rx.hstack(*row_children, spacing="2", align="center", width="100%"),
        )

    # ── Build channel rows at build time (static) ──
    channel_rows: list[rx.Component] = []
    for name, channel_plugin in all_channels().items():
        enabled_var = AIState.channel_toggles[name]["monitor"].to(bool)

        # Build tier dropdown for middle column (if not browser)
        _tier_col: list[rx.Component] = []
        if name != "browser":
            _tier_match_de = rx.match(
                AIState.channel_security_tiers[name],
                *[(tier, f"T{tier} — {_TM.get_text(lk, 'de')}") for tier, lk, _dk in TIER_I18N_KEYS],
                f"T1 — {_TM.get_text('tier_1_label', 'de')}",
            )
            _tier_match_en = rx.match(
                AIState.channel_security_tiers[name],
                *[(tier, f"T{tier} — {_TM.get_text(lk, 'en')}") for tier, lk, _dk in TIER_I18N_KEYS],
                f"T1 — {_TM.get_text('tier_1_label', 'en')}",
            )
            _tier_match = rx.cond(AIState.ui_language == "de", _tier_match_de, _tier_match_en)
            _tier_col = [
                rx.cond(
                    enabled_var,
                    rx.select(
                        tier_options,
                        value=_tier_match,
                        on_change=lambda val, ch=name: AIState.set_channel_security_tier([ch, val]),
                        size="1",
                        width="180px",
                    ),
                ),
            ]

        ch_description = getattr(channel_plugin, "description", "") or ""
        header = rx.hstack(
            # Col 1: Icon + Name (fixed width for alignment)
            rx.hstack(
                rx.icon(channel_plugin.icon, size=14, color=rx.cond(enabled_var, "#4CAF50", "#666")),
                rx.text(channel_plugin.display_name, font_size="14px", color=rx.cond(enabled_var, "white", "#999")),
                spacing="2", align="center", min_width="220px",
            ),
            # Col 1b: lightbulb description popover (aligned across rows)
            _plugin_description_popover(ch_description),
            # Col 2: Tier dropdown (fixed position)
            rx.box(*_tier_col, width="190px", flex_shrink="0") if _tier_col else rx.box(width="190px", flex_shrink="0"),
            rx.spacer(),
            # Col 3: Gear + Switch + ON/OFF
            rx.icon_button(
                rx.icon("settings", size=14),
                on_click=AIState.open_channel_credentials(name),
                size="1",
                variant="ghost",
                color_scheme="gray",
                cursor="pointer",
            ),
            rx.switch(
                checked=enabled_var,
                on_change=lambda val, ch=name: AIState.toggle_channel_monitor([ch, val]),
                size="1",
            ),
            rx.text(
                rx.cond(enabled_var, "ON", "OFF"),
                font_size="11px",
                color=rx.cond(enabled_var, "#4CAF50", "#999"),
                min_width="24px",
            ),
            spacing="2",
            align="center",
            width="100%",
        )

        children: list[rx.Component] = [header]

        if not channel_plugin.always_reply:
            monitor_var = AIState.channel_toggles[name]["listener"].to(bool)
            auto_reply_var = AIState.channel_toggles[name]["auto_reply"].to(bool)

            children.append(
                rx.cond(
                    enabled_var,
                    rx.hstack(
                        rx.box(width="14px"),
                        rx.text("Monitor", font_size="11px", color="#999"),
                        rx.spacer(),
                        rx.switch(
                            checked=monitor_var,
                            on_change=lambda val, ch=name: AIState.toggle_channel_listener([ch, val]),
                            size="1",
                        ),
                        rx.text(rx.cond(monitor_var, "ON", "OFF"), font_size="11px", color=rx.cond(monitor_var, "#4CAF50", "#999"), min_width="24px"),
                        spacing="2", align="center", width="100%",
                    ),
                )
            )
            children.append(
                rx.cond(
                    enabled_var & monitor_var,
                    rx.hstack(
                        rx.box(width="14px"),
                        rx.text(t("auto_reply"), font_size="11px", color="#999"),
                        rx.spacer(),
                        rx.switch(
                            checked=auto_reply_var,
                            on_change=lambda val, ch=name: AIState.toggle_channel_auto_reply([ch, val]),
                            size="1",
                        ),
                        rx.text(rx.cond(auto_reply_var, "ON", "OFF"), font_size="11px", color=rx.cond(auto_reply_var, "#4CAF50", "#999"), min_width="24px"),
                        spacing="2", align="center", width="100%",
                    ),
                )
            )

        # Allowlist row (separate from tier)
        ch_plugin = all_channels().get(name)
        if ch_plugin and ch_plugin.has_allowlist:
            children.append(
                rx.cond(
                    enabled_var,
                    rx.hstack(
                        rx.box(width="14px"),
                        rx.icon("shield", size=12, color="#666"),
                        rx.text(
                            AIState.channel_allowlists[name],
                            font_size="10px", color="#888",
                            overflow="hidden", text_overflow="ellipsis",
                            white_space="nowrap",
                        ),
                        spacing="1", align="center", width="100%",
                    ),
                )
            )

        channel_rows.append(rx.vstack(*children, spacing="1", width="100%"))

    return rx.vstack(
        _editor_header(),
        rx.box(
            rx.vstack(
                # Channels
                rx.hstack(
                    rx.text(t("plugin_channels"), font_size="14px", font_weight="bold", color="#999", min_width="220px"),
                    rx.hstack(
                        rx.text(t("security_tiers_title"), font_size="11px", color="#666"),
                        rx.popover.root(
                            rx.popover.trigger(
                                rx.icon("lightbulb", size=14, color="#FFD700", cursor="pointer"),
                            ),
                            rx.popover.content(
                                _tier_help_content(),
                                side="right",
                                style={"background": "#2a2a3e", "border": "1px solid #555", "border-radius": "8px"},
                            ),
                        ),
                        spacing="1", align="center", width="190px",
                    ),
                    rx.spacer(),
                    align="center",
                    width="100%",
                ),
                rx.vstack(*channel_rows, spacing="2", width="100%"),
                rx.divider(),
                # Tool Plugins
                rx.text(t("plugin_tools"), font_size="14px", font_weight="bold", color="#999"),
                rx.vstack(
                    *tool_rows,
                    spacing="2",
                    width="100%",
                ),
                spacing="3",
                width="100%",
            ),
            flex="1",
            overflow_y="auto",
            width="100%",
        ),
        spacing="3",
        width="100%",
        flex="1",
        min_height="0",
    )
