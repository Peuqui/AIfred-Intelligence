"""Unified AIfred Plugin System.

All plugins live here:
- plugins/channels/  → Message channel plugins (email, discord, ...)
- plugins/tools/     → LLM tool plugins (research, EPIM, sandbox, ...)
- plugins/disabled/  → Disabled plugins (moved here by Plugin Manager)

Usage:
    from aifred.lib.plugin_registry import all_channels, discover_tools, get_channel
    from aifred.lib.plugin_base import BaseChannel, ToolPlugin, PluginContext
"""
