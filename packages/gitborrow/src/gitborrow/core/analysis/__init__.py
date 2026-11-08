"""Source-analysis layer (read-only) for gitborrow.

Provides high-level, citation-bearing code analysis operations across 3 tiers:
  Tier 1 (Primitives):     search, read, tree, history, compare
  Tier 2 (Language-Aware): definition, references, outline
  Tier 3 (Composite):      call_trace, impact_scan

All operations pin evidence to immutable citations (repo@sha:path:line).
"""

from __future__ import annotations

from gitborrow.core.analysis.composite import call_trace, impact_scan
from gitborrow.core.analysis.engine import AnalysisEngine
from gitborrow.core.analysis.language import definition, outline, references
from gitborrow.core.analysis.primitives import (
    compare,
    detect_language,
    history,
    read,
    search,
    tree,
)
from gitborrow.core.analysis.types import (
    CommitInfo,
    Def,
    FileDiff,
    Graph,
    GraphEdge,
    GraphNode,
    Hit,
    OutlineItem,
    PathRow,
    PathTable,
    ReadSnippet,
    TreeEntry,
    make_citation,
)

__all__ = [
    # Engine
    "AnalysisEngine",
    # Tier 1 primitives
    "search",
    "read",
    "tree",
    "history",
    "compare",
    "detect_language",
    # Tier 2 language-aware
    "definition",
    "references",
    "outline",
    # Tier 3 composite
    "call_trace",
    "impact_scan",
    # Types
    "make_citation",
    "Hit",
    "ReadSnippet",
    "TreeEntry",
    "Def",
    "Ref",
    "OutlineItem",
    "Graph",
    "GraphNode",
    "GraphEdge",
    "PathRow",
    "PathTable",
    "CommitInfo",
    "FileDiff",
]
