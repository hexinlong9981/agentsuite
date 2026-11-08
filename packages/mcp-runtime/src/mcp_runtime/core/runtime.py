"""Runtime — expose only manifest-enabled tools, audited and fail-closed."""

from __future__ import annotations

from .config import Config
from .errors import McpRuntimeError, ToolNotEnabled
from .registry import ToolRegistry


class Runtime:
    def __init__(self, registry: ToolRegistry, config: Config) -> None:
        self.registry = registry
        self.config = config
        self.audit = config.audit

    def enabled_tools(self) -> list[str]:
        """Registered tools that the manifest also enables (fail-closed)."""
        return [n for n in self.registry.names() if n in self.config.enabled]

    def call(self, name: str, **kwargs):
        if self.registry.get(name) is None:
            self.audit.record({"action": "call", "tool": name, "result": "unknown"})
            raise McpRuntimeError(f"unknown tool: {name!r}")
        if name not in self.config.enabled:
            self.audit.record({"action": "call", "tool": name,
                               "result": "denied", "reason": "not enabled"})
            raise ToolNotEnabled(f"tool {name!r} is not enabled in the manifest (fail-closed)")
        result = self.registry.get(name).handler(**kwargs)
        self.audit.record({"action": "call", "tool": name, "result": "ok"})
        return result

    def build_mcp(self, app=None):  # pragma: no cover - needs the optional 'mcp' package
        try:
            from mcp.server.fastmcp import FastMCP
        except ModuleNotFoundError as exc:
            raise McpRuntimeError("the 'mcp' package is required: pip install mcp-runtime[mcp]") from exc
        app = app or FastMCP("agentsuite")
        for name in self.enabled_tools():
            spec = self.registry.get(name)
            app.tool(name=name, description=spec.description)(spec.handler)
        return app
