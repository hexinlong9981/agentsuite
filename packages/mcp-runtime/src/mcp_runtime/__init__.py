"""mcp-runtime — host agentsuite tools behind one MCP server.

You register tool handlers (e.g. a connector's `read`, selectgate's `run_select`)
in a `ToolRegistry`; a `Runtime` exposes only the tools listed in a fail-closed
manifest, audits every dispatch, and (with the optional `mcp` extra) serves them
as MCP tools. The runtime holds no credentials and registers no tool that returns
credentials — those stay inside each tool's own credential store.
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
