"""load_tool_parameters: Parameter-Schemas der Tools kommen aus den Plugin-Prompts."""

import json

import pytest

from aifred.lib.plugin_base import load_tool_parameters


def _plugin_file(tmp_path):
    """Plugins pass their own ``__file__`` — the loader resolves its directory."""
    plugin_file = tmp_path / "__init__.py"
    plugin_file.touch()
    return plugin_file


def _write(tmp_path, name: str, content: str) -> None:
    tools = tmp_path / "prompts" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / f"{name}.params.json").write_text(content, encoding="utf-8")


def test_schema_is_read_from_the_plugin_prompts(tmp_path):
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}
    _write(tmp_path, "my_tool", json.dumps(schema))
    assert load_tool_parameters(_plugin_file(tmp_path), "my_tool") == schema


@pytest.mark.parametrize("content", [None, "{not json", '["a list"]', '{"type": "string"}'])
def test_missing_or_broken_schema_fails_loud(tmp_path, content):
    if content is not None:
        _write(tmp_path, "my_tool", content)
    with pytest.raises(RuntimeError, match="my_tool.params.json"):
        load_tool_parameters(_plugin_file(tmp_path), "my_tool")
