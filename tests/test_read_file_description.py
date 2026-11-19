"""read_file's description names the size from which to read in line ranges; the number lives in config only."""

import json
import re

from aifred.lib import config
from aifred.lib.plugin_base import PluginContext
from aifred.plugins.tools.workspace import plugin


def _description(monkeypatch=None, threshold_kb: int | None = None) -> str:
    if monkeypatch is not None:
        monkeypatch.setattr("aifred.plugins.tools.workspace.READ_FILE_CHUNK_THRESHOLD_KB", threshold_kb)
    ctx = PluginContext(agent_id="aifred", lang="en", session_id="0123456789abcdef0123456789abcdef")
    return next(tool for tool in plugin.get_tools(ctx) if tool.name == "read_file").description


def test_the_threshold_comes_from_the_config_constant():
    assert config.READ_FILE_CHUNK_THRESHOLD_KB == 30
    assert f"(>{config.READ_FILE_CHUNK_THRESHOLD_KB} KB)" in _description()


def test_the_description_follows_the_constant(monkeypatch):
    assert "(>7 KB)" in _description(monkeypatch, 7)


def test_no_placeholder_is_left_in_the_description():
    assert not re.search(r"\{[A-Z_]+\}", _description())


def test_render_html_documents_force_and_measuring():
    from aifred.plugins.tools.sandbox import get_sandbox_tools

    render = next(tool for tool in get_sandbox_tools(session_id="0123456789abcdef0123456789abcdef") if tool.name == "render_html")
    assert '"force": true' in render.description and "window.sim" in render.description
    assert "force" in json.dumps(render.parameters["properties"]["actions"])
