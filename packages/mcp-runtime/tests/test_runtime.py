import json

import pytest

from mcp_runtime.core import Config, Runtime, ToolRegistry, ToolSpec
from mcp_runtime.core.errors import McpRuntimeError, ToolNotEnabled


def _registry():
    r = ToolRegistry()
    r.register(ToolSpec("echo", lambda text: {"echo": text}, "echo text"))
    r.register(ToolSpec("add", lambda a, b: {"sum": a + b}, "add two numbers"))
    return r


def test_enabled_tools_fail_closed():
    rt = Runtime(_registry(), Config.from_dict({"tools": {"enabled": ["echo"]}}))
    assert rt.enabled_tools() == ["echo"]          # 'add' registered but not enabled


def test_call_enabled():
    rt = Runtime(_registry(), Config.from_dict({"tools": {"enabled": ["echo"]}}))
    assert rt.call("echo", text="hi") == {"echo": "hi"}


def test_call_not_enabled():
    rt = Runtime(_registry(), Config.from_dict({"tools": {"enabled": ["echo"]}}))
    with pytest.raises(ToolNotEnabled):
        rt.call("add", a=1, b=2)


def test_call_unknown():
    rt = Runtime(_registry(), Config.from_dict({"tools": {"enabled": ["ghost"]}}))
    with pytest.raises(McpRuntimeError):
        rt.call("ghost")


def test_audit(tmp_path):
    path = tmp_path / "a.log"
    rt = Runtime(_registry(), Config.from_dict({
        "tools": {"enabled": ["echo"]}, "audit": {"path": str(path)}}))
    rt.call("echo", text="x")
    with pytest.raises(ToolNotEnabled):
        rt.call("add", a=1, b=2)
    results = [json.loads(line)["result"] for line in path.read_text().splitlines()]
    assert "ok" in results and "denied" in results
