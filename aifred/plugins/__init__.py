"""Unified AIfred Plugin System.

All plugins live here:
- plugins/channels/  → Message channel plugins (email, discord, ...)
- plugins/tools/     → LLM tool plugins (research, EPIM, sandbox, ...)

A plugin switched off in the Plugin Manager stays where it is and gets a
machine-local marker (``.disabled`` in its folder, gitignored).

Usage:
    from aifred.lib.plugin_registry import all_channels, discover_tools, get_channel
    from aifred.lib.plugin_base import BaseChannel, ToolPlugin, PluginContext
"""
