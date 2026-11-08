"""AnalysisEngine: high-level entry point for code analysis.

Hides fetch, checkout lifecycle, secret filtering, and release behind
intent-level methods that return structured results with citations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gitborrow.core.config import Config
from gitborrow.core.lifecycle import fetch, release
from gitborrow.core.types import FetchRequest

from gitborrow.core.analysis.composite import call_trace, impact_scan
from gitborrow.core.analysis.language import definition, outline, references
from gitborrow.core.analysis.primitives import compare, history, read, search, tree
from gitborrow.core.analysis.types import (
    CommitInfo,
    Def,
    FileDiff,
    Graph,
    Hit,
    OutlineItem,
    PathTable,
    ReadSnippet,
    Ref,
    TreeEntry,
)


class AnalysisEngine:
    """Manages ephemeral checkouts and provides high-level analysis operations."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self._checkouts: dict[str, tuple[str, str, Path]] = {}  # key -> (repo, sha, path)

    def _ensure_checkout(self, repo: str, ref: str | None = None) -> tuple[str, str, Path]:
        key = f"{repo}:{ref or 'HEAD'}"
        if key in self._checkouts:
            return self._checkouts[key]

        req = FetchRequest(repo=repo, ref=ref, mode="auto")
        res = fetch(req, config=self.config)
        info = (res.checkout.repo, res.checkout.commit_sha, Path(res.checkout.path))
        self._checkouts[key] = info
        return info

    def search(
        self,
        repo: str,
        ref: str | None,
        pattern: str,
        *,
        regex: bool = False,
        globs: list[str] | None = None,
        lang: list[str] | str | None = None,
        context: int = 2,
        max_results: int = 200,
    ) -> list[Hit]:
        """Search for pattern across files."""
        repo_name, sha, root = self._ensure_checkout(repo, ref)
        return search(
            repo_name,
            sha,
            root,
            pattern,
            regex=regex,
            globs=globs,
            lang=lang,
            context=context,
            max_results=max_results,
        )

    def read(
        self,
        repo: str,
        ref: str | None,
        path: str,
        *,
        start: int | None = None,
        end: int | None = None,
        max_lines: int = 500,
    ) -> ReadSnippet:
        """Read a bounded range of lines."""
        repo_name, sha, root = self._ensure_checkout(repo, ref)
        return read(repo_name, sha, root, path, start=start, end=end, max_lines=max_lines)

    def tree(
        self,
        repo: str,
        ref: str | None = None,
        *,
        globs: list[str] | None = None,
        dir_path: str | None = None,
        max_entries: int = 1000,
    ) -> list[TreeEntry]:
        """List files with language detection."""
        repo_name, sha, root = self._ensure_checkout(repo, ref)
        return tree(repo_name, sha, root, globs=globs, dir_path=dir_path, max_entries=max_entries)

    def history(
        self,
        repo: str,
        ref: str | None = None,
        *,
        path: str | None = None,
        max_commits: int = 50,
    ) -> list[CommitInfo]:
        """Query commit history."""
        repo_name, sha, root = self._ensure_checkout(repo, ref)
        return history(repo_name, sha, root, path=path, max_commits=max_commits)

    def compare(
        self,
        repo: str,
        base_ref: str,
        target_ref: str,
        *,
        path: str | None = None,
    ) -> list[FileDiff]:
        """Compare two refs."""
        repo_name, _, root = self._ensure_checkout(repo, target_ref)
        return compare(repo_name, base_ref, target_ref, root, path=path)

    def definition(
        self,
        repo: str,
        ref: str | None,
        symbol: str,
        *,
        lang: str | None = None,
        kind: str | None = None,
        globs: list[str] | None = None,
    ) -> list[Def]:
        """Find definitions of a symbol."""
        repo_name, sha, root = self._ensure_checkout(repo, ref)
        return definition(repo_name, sha, root, symbol, lang=lang, kind=kind, globs=globs)

    def references(
        self,
        repo: str,
        ref: str | None,
        symbol: str,
        *,
        lang: str | None = None,
        scope: list[str] | None = None,
        max_results: int = 500,
    ) -> list[Ref]:
        """Find usages/references to a symbol."""
        repo_name, sha, root = self._ensure_checkout(repo, ref)
        return references(repo_name, sha, root, symbol, lang=lang, scope=scope, max_results=max_results)

    def outline(
        self,
        repo: str,
        ref: str | None,
        path: str,
        *,
        lang: str | None = None,
    ) -> list[OutlineItem]:
        """Extract structural outline."""
        repo_name, sha, root = self._ensure_checkout(repo, ref)
        return outline(repo_name, sha, root, path, lang=lang)

    def call_trace(
        self,
        repo: str,
        ref: str | None,
        symbol: str,
        *,
        direction: str = "callers",
        depth: int = 2,
    ) -> Graph:
        """Trace call chain."""
        repo_name, sha, root = self._ensure_checkout(repo, ref)
        return call_trace(repo_name, sha, root, symbol, direction=direction, depth=depth)

    def impact_scan(
        self,
        repo: str,
        ref: str | None,
        target: str,
        *,
        kind: str = "symbol",
        patterns: list[str] | None = None,
        max_rows: int = 300,
    ) -> PathTable:
        """Scan codebase for access paths."""
        repo_name, sha, root = self._ensure_checkout(repo, ref)
        return impact_scan(repo_name, sha, root, target, kind=kind, patterns=patterns, max_rows=max_rows)

    def close(self) -> None:
        """Release all managed checkouts."""
        for repo, _, _ in self._checkouts.values():
            try:
                release(repo, config=self.config)
            except Exception:
                pass
        self._checkouts.clear()

    def __enter__(self) -> AnalysisEngine:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
