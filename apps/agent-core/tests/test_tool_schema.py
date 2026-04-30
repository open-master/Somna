from __future__ import annotations

from app.tools.client import ToolManifest
from app.tools.schema import (
    clear_cache_for_tests,
    manifest_to_openai,
    manifests_to_openai_tools,
    openai_tool_choice,
)


def _mk(name: str, schema=None) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} tool for testing",
        input_schema=schema or {"type": "object", "properties": {"q": {"type": "string"}}},
    )


def setup_function(_):
    clear_cache_for_tests()


def test_manifest_to_openai_produces_valid_shape():
    m = _mk("shell")
    out = manifest_to_openai(m)
    assert out["type"] == "function"
    assert out["function"]["name"] == "shell"
    assert out["function"]["parameters"]["type"] == "object"
    assert "properties" in out["function"]["parameters"]


def test_manifest_sanitizes_illegal_names():
    m = _mk("weird tool!!")
    out = manifest_to_openai(m)
    assert out["function"]["name"] == "weird_tool__"


def test_manifest_patches_non_object_schema():
    m = _mk("x", schema={"type": "string"})
    out = manifest_to_openai(m)
    assert out["function"]["parameters"] == {"type": "object", "properties": {}}


def test_tool_choice_variants():
    assert openai_tool_choice() == "auto"
    tc = openai_tool_choice("shell")
    assert tc == {"type": "function", "function": {"name": "shell"}}


def test_manifests_to_openai_tools_list():
    tools = manifests_to_openai_tools([_mk("shell"), _mk("search")])
    names = [t["function"]["name"] for t in tools]
    assert names == ["shell", "search"]
