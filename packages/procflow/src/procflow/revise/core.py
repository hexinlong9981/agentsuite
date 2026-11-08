"""Autonomous revision orchestrator with safety-net and automated rollback.

Applies a proposed revision to a user procedure repository, judges it against the
full safety-net (static checks + characterization replay), and commits ONLY on
success — otherwise it restores the working tree. It never pushes, never touches
the toolkit's own repository, never leaves the repo on a different
branch, and (when a run history is available) enforces a revision-frequency cap
and records every attempt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from ..repo import Repo
from ..replay import replay_all
from . import gitops
from .judge import JudgementResult, judge_revision


@dataclass
class RevisionOutcome:
    success: bool
    status: str  # APPLIED, ROLLED_BACK, ABORTED_DIRTY, ABORTED_FREQUENCY, ABORTED_SELF, ERROR
    sop_id: str
    target_file: str
    branch_name: str | None = None
    commit_sha: str | None = None
    backup_tag: str | None = None
    judgement: JudgementResult | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "sop_id": self.sop_id,
            "target_file": self.target_file,
            "branch_name": self.branch_name,
            "commit_sha": self.commit_sha,
            "backup_tag": self.backup_tag,
            "judgement": self.judgement.to_dict() if self.judgement else None,
            "message": self.message,
        }


def _is_toolkit_repo(repo_root: Path) -> bool:
    """True if repo_root is the toolkit's own repository (never revise ourselves)."""
    try:
        toolkit = gitops.get_repo_toplevel(Path(__file__))
        return Path(repo_root).resolve() == Path(toolkit).resolve()
    except Exception:
        return False


def _history_path(runs_root: Path | str, sop_id: str) -> Path:
    return Path(runs_root) / "_revisions" / f"{sop_id}.jsonl"


def _recent_applied(hist: Path, days: int) -> int:
    if not hist.is_file():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = 0
    for line in hist.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("status") != "APPLIED":
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get("ts", "")).replace("Z", "+00:00"))
        except Exception:
            continue
        if ts >= cutoff:
            n += 1
    return n


def _record_history(hist: Path | None, rec: dict[str, Any]) -> None:
    if hist is None:
        return
    try:
        hist.parent.mkdir(parents=True, exist_ok=True)
        with hist.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def apply_revision_with_safety_net(
    sop_file_path: Path | str,
    revised_def: dict[str, Any],
    commit_message: str | None = None,
    create_feature_branch: bool = False,
    runs_root: Path | str | None = None,
    repo: Repo | None = None,
    max_per_7d: int = 50,
    max_per_30d: int = 200,
) -> RevisionOutcome:
    """Safely apply a revised procedure definition with regression checks and rollback.

    By default the change is applied on the current branch (not an isolated branch),
    guarded by a backup tag so a successful revision can be rolled back later; the
    repository is always left on its original branch. Characterization and the
    frequency cap are enforced only when ``runs_root`` is provided.
    """
    target_p = Path(sop_file_path).resolve()
    if not target_p.exists():
        return RevisionOutcome(False, "ERROR", "unknown", str(target_p),
                               message=f"Target file does not exist: {target_p}")

    try:
        repo_root = gitops.get_repo_toplevel(target_p)
    except Exception as e:
        return RevisionOutcome(False, "ERROR", "unknown", str(target_p),
                               message=f"Failed to locate git repository for {target_p}: {e}")

    # #7 — never revise the toolkit's own repository.
    if _is_toolkit_repo(repo_root):
        return RevisionOutcome(False, "ABORTED_SELF", revised_def.get("id", "unknown"),
                               str(target_p),
                               message="Refusing to autonomously revise the agentsuite toolkit repository itself.")

    rel_path = str(target_p.relative_to(repo_root))

    # Abort if the target already has uncommitted changes (never clobber work).
    if gitops.get_dirty_paths(repo_root, [rel_path]):
        return RevisionOutcome(False, "ABORTED_DIRTY", revised_def.get("id", "unknown"), rel_path,
                               message=f"Target file has uncommitted changes: {rel_path}. Aborting revision.")

    try:
        orig_def = yaml.safe_load(target_p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return RevisionOutcome(False, "ERROR", "unknown", rel_path,
                               message=f"Failed to read original definition: {e}")

    sop_id = orig_def.get("id") or revised_def.get("id") or target_p.stem

    # Build a Repo for cross-procedure checks/lookup (best-effort).
    chk_repo = repo
    if chk_repo is None:
        try:
            chk_repo = Repo(repo_root)
        except Exception:
            chk_repo = None

    def _lookup(pid: str) -> Any:
        if chk_repo is None:
            return None
        try:
            return chk_repo.definition(pid)
        except Exception:
            return None

    interface_lookup = getattr(chk_repo, "interface", None) if chk_repo is not None else None

    # Frequency cap + history (only when a run history location is given).
    hist = _history_path(runs_root, sop_id) if runs_root is not None else None
    if hist is not None:
        if _recent_applied(hist, 7) >= max_per_7d or _recent_applied(hist, 30) >= max_per_30d:
            return RevisionOutcome(False, "ABORTED_FREQUENCY", sop_id, rel_path,
                                   message="Revision frequency cap reached; try later or raise the cap.")

    # Characterization baseline (captured BEFORE applying, using the current on-disk def).
    baseline_accepted: set[str] | None = None
    if runs_root is not None:
        try:
            rows = replay_all(runs_root, repo=chk_repo, sop=sop_id, current=False)
            baseline_accepted = {r["run_id"] for r in rows if r.get("verdict") == "accepted"}
        except Exception:
            baseline_accepted = set()

    # Backup point so a committed revision can be rolled back later.
    orig_branch = gitops.current_branch(repo_root)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_tag = None
    try:
        pre_head = gitops.get_current_head(repo_root)
        backup_tag = f"procflow-revise-backup/{sop_id}/{ts}"
        gitops.create_tag(repo_root, backup_tag, pre_head)
    except Exception:
        backup_tag = None

    branch_name = None
    if create_feature_branch:
        branch_name = f"revise/{sop_id}_{ts}"
        try:
            gitops.create_branch(repo_root, branch_name)
        except Exception as e:
            return RevisionOutcome(False, "ERROR", sop_id, rel_path, backup_tag=backup_tag,
                                   message=f"Failed to create revision branch {branch_name}: {e}")

    def _restore_branch() -> None:
        if branch_name and orig_branch and orig_branch != "HEAD":
            gitops.run_git(repo_root, "checkout", orig_branch, check=False)

    # Apply the revised content.
    try:
        target_p.write_text(yaml.dump(revised_def, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except Exception as e:
        gitops.checkout_path(repo_root, rel_path)
        _restore_branch()
        return RevisionOutcome(False, "ERROR", sop_id, rel_path, branch_name=branch_name, backup_tag=backup_tag,
                               message=f"Failed to write revised file: {e}")

    # Judge against the full safety-net.
    judgement = judge_revision(
        orig_def, revised_def, sop_path=target_p,
        lookup=_lookup, interface_lookup=interface_lookup,
        repo=chk_repo, runs_root=runs_root, sop_id=sop_id,
        baseline_accepted=baseline_accepted,
    )

    if not judgement.passed:
        gitops.checkout_path(repo_root, rel_path)
        _restore_branch()
        _record_history(hist, {"ts": datetime.now(timezone.utc).isoformat(), "sop": sop_id,
                               "status": "ROLLED_BACK", "verdict": judgement.verdict})
        return RevisionOutcome(False, "ROLLED_BACK", sop_id, rel_path, branch_name=branch_name,
                               backup_tag=backup_tag, judgement=judgement,
                               message=f"Revision rejected by safety-net ({judgement.verdict}). Working tree restored.")

    msg = commit_message or f"refactor({sop_id}): autonomous revision verified and applied"
    try:
        commit_sha = gitops.commit_paths(repo_root, [rel_path], msg)
    except Exception as e:
        gitops.checkout_path(repo_root, rel_path)
        _restore_branch()
        return RevisionOutcome(False, "ERROR", sop_id, rel_path, branch_name=branch_name, backup_tag=backup_tag,
                               judgement=judgement, message=f"Verification passed but git commit failed: {e}")

    try:
        gitops.create_tag(repo_root, f"procflow-revise/{sop_id}/{ts}", commit_sha)
    except Exception:
        pass
    _restore_branch()
    _record_history(hist, {"ts": datetime.now(timezone.utc).isoformat(), "sop": sop_id,
                           "status": "APPLIED", "commit": commit_sha})

    return RevisionOutcome(True, "APPLIED", sop_id, rel_path, branch_name=branch_name,
                           commit_sha=commit_sha, backup_tag=backup_tag, judgement=judgement,
                           message="Revision passed the full safety-net and was committed (not pushed).")
