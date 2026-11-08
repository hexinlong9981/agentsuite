"""MCP adapter — thin wrapper exposing the core as tools (fail-closed).

Requires the optional ``mcp`` dependency (``pip install gitborrow[mcp]``).
Tools return citation-bearing dicts and never return secret values; there is
deliberately no tool that reads credentials.

Wires both the lifecycle tools (fetch, release, purge, list, status) and
the high-level analysis tools (search, read, tree, definition, references,
outline, call_trace, impact_scan).
"""

from __future__ import annotations

import dataclasses

from . import core
from .core.analysis import AnalysisEngine


def _dump(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, list):
        return [_dump(x) for x in obj]
    return obj


def build_server(config: core.Config | None = None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "the 'mcp' package is required: pip install gitborrow[mcp]"
        ) from exc

    cfg = config or core.Config.load()
    app = FastMCP("gitborrow")
    engine = AnalysisEngine(config=cfg)

    # Lifecycle tools
    @app.tool()
    def fetch_repository(repo: str, ref: str | None = None, shallow: bool = False) -> dict:
        res = core.fetch(core.FetchRequest(repo=repo, ref=ref, full_history=not shallow), config=cfg)
        return _dump(res)

    @app.tool()
    def release_ephemeral(target: str) -> dict:
        return _dump(core.release(target, config=cfg))

    @app.tool()
    def purge_ephemeral(max_age_hours: float | None = None, purge_all: bool = False) -> dict:
        return _dump(core.purge(cfg, max_age_hours=max_age_hours, purge_all=purge_all))

    @app.tool()
    def list_ephemeral_checkouts() -> list:
        return _dump(core.list_checkouts(cfg))

    @app.tool()
    def get_status(repo: str) -> dict:
        return _dump(core.status(repo, cfg))

    # Analysis tools (read-only, citation-bearing)
    @app.tool()
    def search_source(
        repo: str,
        pattern: str,
        ref: str | None = None,
        regex: bool = False,
        globs: list[str] | None = None,
        lang: str | None = None,
        context: int = 2,
    ) -> list:
        """Search for pattern across files with citation and context lines."""
        hits = engine.search(repo, ref, pattern, regex=regex, globs=globs, lang=lang, context=context)
        return _dump(hits)

    @app.tool()
    def read_source_snippet(
        repo: str,
        path: str,
        ref: str | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> dict:
        """Read a bounded range of lines from a file with citation."""
        snippet = engine.read(repo, ref, path, start=start, end=end)
        return _dump(snippet)

    @app.tool()
    def list_source_tree(
        repo: str,
        ref: str | None = None,
        dir_path: str | None = None,
    ) -> list:
        """List files in the tree with size and detected language."""
        entries = engine.tree(repo, ref, dir_path=dir_path)
        return _dump(entries)

    @app.tool()
    def find_definition(
        repo: str,
        symbol: str,
        ref: str | None = None,
        lang: str | None = None,
        kind: str | None = None,
    ) -> list:
        """Find definitions of a symbol across the repository."""
        defs = engine.definition(repo, ref, symbol, lang=lang, kind=kind)
        return _dump(defs)

    @app.tool()
    def find_references(
        repo: str,
        symbol: str,
        ref: str | None = None,
        lang: str | None = None,
    ) -> list:
        """Find references/usages of a symbol across the repository."""
        refs = engine.references(repo, ref, symbol, lang=lang)
        return _dump(refs)

    @app.tool()
    def get_outline(
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> list:
        """Extract structural outline (classes, functions, methods) from a file."""
        items = engine.outline(repo, ref, path)
        return _dump(items)

    @app.tool()
    def trace_calls(
        repo: str,
        symbol: str,
        ref: str | None = None,
        direction: str = "callers",
        depth: int = 2,
    ) -> dict:
        """Trace a call chain either upwards (callers) or downwards (callees)."""
        graph = engine.call_trace(repo, ref, symbol, direction=direction, depth=depth)
        return _dump(graph)

    @app.tool()
    def impact_scan(
        repo: str,
        target: str,
        ref: str | None = None,
        kind: str = "symbol",
    ) -> dict:
        """Scan all code paths accessing a target symbol, table, or data state."""
        table = engine.impact_scan(repo, ref, target, kind=kind)
        return _dump(table)

    return app


def main() -> None:  # pragma: no cover
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
