"""Safe local GitOps operations for autonomous procedure revisions.

Guarantees that git mutations only occur within the explicitly targeted user procedure
repository (sop_root), never on the toolkit itself, and never performs remote pushes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


class GitOpsError(RuntimeError):
    pass


def run_git(repo_root: Path | str, *args: str, check: bool = True) -> str:
    """Execute git command strictly inside repo_root without shell expansion."""
    root = Path(repo_root).resolve()
    cmd = ["git", "-C", str(root), *args]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        raise GitOpsError(f"git {' '.join(args)} failed (code {res.returncode}): {err}")
    return res.stdout.strip()


def get_repo_toplevel(path: Path | str) -> Path:
    p = Path(path).resolve()
    dir_to_check = p if p.is_dir() else p.parent
    top = run_git(dir_to_check, "rev-parse", "--show-toplevel")
    return Path(top).resolve()


def get_current_head(repo_root: Path | str) -> str:
    return run_git(repo_root, "rev-parse", "HEAD")


def get_dirty_paths(repo_root: Path | str, relative_paths: Sequence[str] | None = None) -> list[str]:
    """Check for uncommitted changes in specific paths."""
    args = ["status", "--porcelain", "--untracked-files=all"]
    if relative_paths:
        args.extend(["--", *relative_paths])
    out = run_git(repo_root, *args, check=False)
    dirty = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            # Drop status prefix like " M path" or "?? path"
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                dirty.append(parts[1])
    return dirty


def create_branch(repo_root: Path | str, branch_name: str) -> None:
    run_git(repo_root, "checkout", "-b", branch_name)


def checkout_path(repo_root: Path | str, relative_path: str) -> None:
    """Discard uncommitted working-tree changes to a specific file."""
    run_git(repo_root, "checkout", "--", relative_path, check=False)


def current_branch(repo_root: Path | str) -> str:
    """Return the current branch name (or 'HEAD' when detached)."""
    return run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD", check=False)


def create_tag(repo_root: Path | str, tag: str, ref: str = "HEAD") -> None:
    """Create/replace a lightweight local tag (best-effort, never pushed)."""
    run_git(repo_root, "tag", "-f", tag, ref, check=False)


def commit_paths(repo_root: Path | str, relative_paths: Sequence[str], message: str) -> str:
    """Commit ONLY the given paths and return the new commit SHA.

    The pathspec is passed to ``git commit`` itself so that any *other* changes
    already staged in the user's repository are never swept into this commit.
    """
    run_git(repo_root, "add", "--", *relative_paths)
    run_git(repo_root, "commit", "-m", message, "--", *relative_paths)
    return get_current_head(repo_root)


def rollback_commit(repo_root: Path | str, commit_sha: str) -> None:
    """Safely revert a specific commit creating a new rollback commit."""
    run_git(repo_root, "revert", "--no-edit", commit_sha)
