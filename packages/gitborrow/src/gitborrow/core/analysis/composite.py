"""Composite analysis layer for gitborrow (read-only).

Provides high-level compound queries:
  - call_trace: traces caller or callee chains up to a given depth.
  - impact_scan: scans all code paths accessing a target symbol, table, or data state.
"""

from __future__ import annotations

import re
from pathlib import Path

from gitborrow.core.analysis.language import definition, references
from gitborrow.core.analysis.primitives import search
from gitborrow.core.analysis.types import (
    Graph,
    GraphEdge,
    GraphNode,
    PathRow,
    PathTable,
    make_citation,
)


def call_trace(
    repo: str,
    commit_sha: str,
    root_path: str | Path,
    symbol: str,
    *,
    direction: str = "callers",     # callers | callees
    depth: int = 2,
    max_nodes: int = 50,
) -> Graph:
    """Trace a call chain either upwards (callers) or downwards (callees)."""
    root = Path(root_path).resolve()
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    truncated = False

    # Root symbol node
    root_defs = definition(repo, commit_sha, root, symbol)
    root_cit = root_defs[0].citation if root_defs else f"{repo}@{commit_sha}:{symbol}"
    nodes[symbol] = GraphNode(id=symbol, symbol=symbol, citation=root_cit)

    visited: set[str] = set()

    def trace_callers(current_sym: str, current_depth: int) -> None:
        nonlocal truncated
        if current_depth > depth or current_sym in visited:
            return
        visited.add(current_sym)

        refs = references(repo, commit_sha, root, current_sym)
        for r in refs:
            if r.kind != "call" or not r.enclosing:
                continue

            caller = r.enclosing
            if caller not in nodes:
                if len(nodes) >= max_nodes:
                    truncated = True
                    return
                nodes[caller] = GraphNode(id=caller, symbol=caller, citation=r.citation)

            edges.append(
                GraphEdge(from_node=caller, to_node=current_sym, citation=r.citation)
            )

            if current_depth < depth:
                trace_callers(caller, current_depth + 1)

    if direction == "callers":
        trace_callers(symbol, 1)

    return Graph(
        nodes=list(nodes.values()),
        edges=edges,
        truncated=truncated,
    )


def impact_scan(
    repo: str,
    commit_sha: str,
    root_path: str | Path,
    target: str,
    *,
    kind: str = "symbol",          # symbol | table | data
    patterns: list[str] | None = None,
    max_rows: int = 300,
) -> PathTable:
    """Scan all code paths accessing a target symbol, table, or data state."""
    root = Path(root_path).resolve()
    rows: list[PathRow] = []
    coverage_limits: list[str] = []

    if kind == "symbol":
        # Search references
        refs = references(repo, commit_sha, root, target, max_results=max_rows)
        for r in refs:
            entry_name = f"{r.path}:{r.enclosing}" if r.enclosing else r.path
            rows.append(
                PathRow(
                    entry=entry_name,
                    access_method=r.kind,
                    citation=r.citation,
                    caveat=r.text,
                )
            )

    elif kind in ("table", "data"):
        # Match table name or data key occurrences
        search_patterns = [target]
        if patterns:
            search_patterns.extend(patterns)

        # Also search SQL access patterns (SELECT/UPDATE/INSERT/DELETE)
        sql_verbs = ["SELECT", "FROM", "JOIN", "UPDATE", "INSERT INTO", "DELETE FROM"]
        combined_rx = rf"\b({'|'.join(sql_verbs)})\b.*?\b{re.escape(target)}\b|\b{re.escape(target)}\b"

        hits = search(
            repo,
            commit_sha,
            root,
            pattern=combined_rx,
            regex=True,
            max_results=max_rows,
        )

        for h in hits:
            line_upper = h.text.upper()
            method = "read"
            if any(v in line_upper for v in ("UPDATE", "INSERT", "DELETE", "ALTER", "DROP")):
                method = "write"
            elif any(v in line_upper for v in ("SELECT", "FROM", "JOIN")):
                method = "read"
            elif "=" in h.text or ":=" in h.text:
                method = "assignment"

            rows.append(
                PathRow(
                    entry=f"{h.path}:{h.line}",
                    access_method=method,
                    citation=h.citation,
                    caveat=h.text.strip(),
                )
            )

    if len(rows) >= max_rows:
        coverage_limits.append(f"Result truncated at {max_rows} rows limit")

    return PathTable(
        rows=rows,
        coverage_limits=coverage_limits,
    )
