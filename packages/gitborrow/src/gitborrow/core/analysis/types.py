"""Data types for the gitborrow source-analysis layer (read-only).

Every result type carries a structured citation (``repo@sha:path:line``)
pinning the evidence to a specific commit SHA so it remains verifiable
long after the ephemeral checkout has been released.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def make_citation(repo: str, sha: str, path: str, line: int | None = None) -> str:
    """Format: ``repo@sha:path`` or ``repo@sha:path:line``."""
    base = f"{repo}@{sha}:{path}"
    return f"{base}:{line}" if line is not None else base


@dataclass(frozen=True)
class Hit:
    """A search hit with surrounding context lines."""
    citation: str
    path: str
    line: int
    column: int
    text: str
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReadSnippet:
    """A bounded slice of a file's content."""
    citation: str
    path: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class TreeEntry:
    """A file entry in a repository tree."""
    path: str
    size: int
    lang: str


@dataclass(frozen=True)
class Def:
    """A definition (function, class, method, variable, etc.)."""
    citation: str
    path: str
    line: int
    kind: str                   # function | class | method | type | interface | variable
    signature: str
    container: str = ""         # enclosing class/module name if known


@dataclass(frozen=True)
class Ref:
    """A reference/usage of a symbol."""
    citation: str
    path: str
    line: int
    kind: str                   # call | import | read | write | mention
    enclosing: str = ""         # enclosing function/method name if known
    text: str = ""              # matching line text


@dataclass(frozen=True)
class OutlineItem:
    """A structural outline item (class, function, method)."""
    citation: str
    kind: str                   # class | function | method | interface
    name: str
    line: int
    signature: str = ""


@dataclass(frozen=True)
class GraphNode:
    id: str
    symbol: str
    citation: str


@dataclass(frozen=True)
class GraphEdge:
    from_node: str
    to_node: str
    citation: str


@dataclass(frozen=True)
class Graph:
    """Call-trace graph."""
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class PathRow:
    """A row in an impact-scan path table."""
    entry: str
    access_method: str
    citation: str
    caveat: str = ""


@dataclass(frozen=True)
class PathTable:
    """Result of an impact scan across a codebase."""
    rows: list[PathRow] = field(default_factory=list)
    coverage_limits: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommitInfo:
    """A commit in the repository history."""
    sha: str
    citation: str
    author: str
    date: str
    summary: str
    message: str = ""


@dataclass(frozen=True)
class FileDiff:
    """Diff summary for a file between two revisions."""
    path: str
    status: str                 # added | modified | deleted | renamed
    additions: int = 0
    deletions: int = 0
    patch: str = ""
