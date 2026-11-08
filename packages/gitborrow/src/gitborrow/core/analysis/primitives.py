"""Primitives layer for gitborrow source analysis (read-only).

Provides high-level, citation-bearing operations:
  - search: grep-like pattern search with surrounding context and citation
  - read: bounded slice of file content (never dumps entire files unboundedly)
  - tree: file tree traversal with size and language detection
  - history: commit log queries for repo or specific path
  - compare: file-level diff between revisions
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import subprocess
from pathlib import Path

from gitborrow.core.analysis.types import (
    CommitInfo,
    FileDiff,
    Hit,
    ReadSnippet,
    TreeEntry,
    make_citation,
)
from agent_policy_kit.paths import is_under

logger = logging.getLogger(__name__)

# Known extension to language mapping
LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".sql": "sql",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz",
    ".jar", ".war", ".class", ".exe", ".dll", ".so", ".dylib", ".pyc", ".woff",
    ".woff2", ".ttf", ".eot",
}


def _reject_option_like(ref: str, label: str) -> None:
    """Reject a git ref that could be parsed as a command-line option."""
    if not isinstance(ref, str) or not ref or ref.startswith("-"):
        raise ValueError(f"invalid {label}: {ref!r}")


def detect_language(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    return LANG_MAP.get(ext, "text")


def is_binary_file(path: str | Path) -> bool:
    ext = Path(path).suffix.lower()
    return ext in BINARY_EXTENSIONS


def _matches_globs(rel_path: str, globs: list[str] | None) -> bool:
    if not globs:
        return True
    return any(fnmatch.fnmatch(rel_path, g) or fnmatch.fnmatch(Path(rel_path).name, g) for g in globs)


def search(
    repo: str,
    commit_sha: str,
    root_path: str | Path,
    pattern: str,
    *,
    regex: bool = False,
    globs: list[str] | None = None,
    lang: list[str] | str | None = None,
    context: int = 2,
    max_results: int = 200,
) -> list[Hit]:
    """Search for pattern in files under root_path."""
    root = Path(root_path).resolve()
    target_langs = {lang.lower()} if isinstance(lang, str) else ({l.lower() for l in lang} if lang else None)

    hits: list[Hit] = []
    if regex:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}") from e
    else:
        rx = None

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip .git
        if ".git" in dirnames:
            dirnames.remove(".git")

        for fname in filenames:
            fpath = Path(dirpath) / fname
            if is_binary_file(fpath):
                continue

            rel_str = str(fpath.relative_to(root)).replace("\\", "/")
            if not _matches_globs(rel_str, globs):
                continue

            file_lang = detect_language(fpath)
            if target_langs and file_lang not in target_langs:
                continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = content.splitlines()
            for line_idx, line in enumerate(lines):
                col = -1
                if rx:
                    m = rx.search(line)
                    if m:
                        col = m.start() + 1
                else:
                    pos = line.find(pattern)
                    if pos >= 0:
                        col = pos + 1

                if col >= 0:
                    line_no = line_idx + 1
                    before_start = max(0, line_idx - context)
                    before = lines[before_start:line_idx]
                    after_end = min(len(lines), line_idx + 1 + context)
                    after = lines[line_idx + 1:after_end]

                    hit = Hit(
                        citation=make_citation(repo, commit_sha, rel_str, line_no),
                        path=rel_str,
                        line=line_no,
                        column=col,
                        text=line,
                        before=before,
                        after=after,
                    )
                    hits.append(hit)
                    if len(hits) >= max_results:
                        return hits

    return hits


def read(
    repo: str,
    commit_sha: str,
    root_path: str | Path,
    path: str,
    *,
    start: int | None = None,
    end: int | None = None,
    max_lines: int = 500,
) -> ReadSnippet:
    """Read a bounded range of lines from a file."""
    root = Path(root_path).resolve()
    target_path = (root / path).resolve()
    if not is_under(target_path, root):
        raise ValueError(f"Path escapes repository root: {path}")

    if not target_path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    rel_str = str(target_path.relative_to(root)).replace("\\", "/")

    try:
        content = target_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise IOError(f"Could not read {path}: {e}") from e

    lines = content.splitlines()
    total_lines = len(lines)

    s = max(1, start if start is not None else 1)
    if end is not None:
        e = min(total_lines, max(s, end))
        if e - s + 1 > max_lines:
            e = s + max_lines - 1
    else:
        e = min(total_lines, s + max_lines - 1)

    slice_lines = lines[s - 1:e]
    selected_text = "\n".join(slice_lines)

    return ReadSnippet(
        citation=make_citation(repo, commit_sha, rel_str, s),
        path=rel_str,
        start=s,
        end=e,
        text=selected_text,
    )


def tree(
    repo: str,
    commit_sha: str,
    root_path: str | Path,
    *,
    globs: list[str] | None = None,
    dir_path: str | None = None,
    max_entries: int = 1000,
) -> list[TreeEntry]:
    """List files in the tree with size and detected language."""
    root = Path(root_path).resolve()
    start_dir = (root / dir_path).resolve() if dir_path else root

    if not is_under(start_dir, root):
        raise ValueError(f"Directory escapes repository root: {dir_path}")

    entries: list[TreeEntry] = []
    for dirpath, dirnames, filenames in os.walk(start_dir):
        if ".git" in dirnames:
            dirnames.remove(".git")

        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            rel_str = str(fpath.relative_to(root)).replace("\\", "/")

            if not _matches_globs(rel_str, globs):
                continue

            try:
                sz = fpath.stat().st_size
            except OSError:
                sz = 0

            entries.append(
                TreeEntry(
                    path=rel_str,
                    size=sz,
                    lang=detect_language(fpath),
                )
            )
            if len(entries) >= max_entries:
                return entries

    return entries


def history(
    repo: str,
    commit_sha: str,
    root_path: str | Path,
    *,
    path: str | None = None,
    max_commits: int = 50,
) -> list[CommitInfo]:
    """Retrieve commit history for repo or a specific path."""
    root = Path(root_path).resolve()
    cmd = [
        "git",
        "log",
        f"-n{max_commits}",
        "--pretty=format:%H%x1f%an%x1f%ad%x1f%s%x1f%b%x1e",
        "--date=iso-strict",
    ]
    if path:
        target = (root / path).resolve()
        if not is_under(target, root):
            raise ValueError(f"Path escapes repository root: {path}")
        rel_str = str(target.relative_to(root)).replace("\\", "/")
        cmd.extend(["--", rel_str])

    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("history query failed in %s (path=%r): %s", root, path, e)
        return []

    commits: list[CommitInfo] = []
    raw_records = proc.stdout.split("\x1e")
    for rec in raw_records:
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split("\x1f")
        sha = parts[0] if len(parts) > 0 else ""
        author = parts[1] if len(parts) > 1 else ""
        date_str = parts[2] if len(parts) > 2 else ""
        summary = parts[3] if len(parts) > 3 else ""
        body = parts[4].strip() if len(parts) > 4 else ""

        rel_p = path or ""
        commits.append(
            CommitInfo(
                sha=sha,
                citation=make_citation(repo, sha, rel_p) if rel_p else f"{repo}@{sha}",
                author=author,
                date=date_str,
                summary=summary,
                message=body,
            )
        )
    return commits


def compare(
    repo: str,
    base_ref: str,
    target_ref: str,
    root_path: str | Path,
    *,
    path: str | None = None,
) -> list[FileDiff]:
    """Compare two revisions and return file-level diffs."""
    # The refs are interpolated into a single token (``base..target``) placed
    # *before* ``--``. A ref starting with ``-`` (e.g. ``--output=/path``) would
    # make that token look like a git option, allowing arbitrary file writes and
    # defeating the read-only guarantee. argv is a list (no shell), so a leading
    # dash is the only option-injection vector — reject it while still allowing
    # revision expressions like ``HEAD~1`` / ``HEAD^``.
    _reject_option_like(base_ref, "base_ref")
    _reject_option_like(target_ref, "target_ref")
    root = Path(root_path).resolve()
    cmd = ["git", "diff", "--numstat", f"{base_ref}..{target_ref}"]
    if path:
        cmd.extend(["--", path])

    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("compare failed in %s (%s..%s): %s", root, base_ref, target_ref, e)
        return []

    diffs: list[FileDiff] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 3:
            continue
        added_str, deleted_str, file_p = parts
        added = int(added_str) if added_str.isdigit() else 0
        deleted = int(deleted_str) if deleted_str.isdigit() else 0

        # Determine status
        status = "modified"
        if added > 0 and deleted == 0:
            status = "added"
        elif added == 0 and deleted > 0:
            status = "deleted"

        diffs.append(
            FileDiff(
                path=file_p,
                status=status,
                additions=added,
                deletions=deleted,
            )
        )
    return diffs
