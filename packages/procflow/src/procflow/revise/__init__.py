"""Autonomous revision package with GitOps safety net and regression verification."""
from .core import RevisionOutcome, apply_revision_with_safety_net
from .gitops import GitOpsError, create_branch, get_dirty_paths, get_repo_toplevel, rollback_commit
from .judge import JudgementResult, judge_revision

__all__ = [
    "RevisionOutcome",
    "apply_revision_with_safety_net",
    "GitOpsError",
    "create_branch",
    "get_dirty_paths",
    "get_repo_toplevel",
    "rollback_commit",
    "JudgementResult",
    "judge_revision",
]
