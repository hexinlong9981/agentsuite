"""Pillar 1 — containment & ephemerality (fail-closed).

- Fetch targets live only under the ephemeral root.
- Protected roots can never be a target, and the ephemeral root may not resolve
  under a protected root (so one config line cannot break "leave no trace").
- purge only touches directories this tool created (identified by a provenance
  file), never user-placed directories.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_policy_kit.paths import is_under as _is_under

from .config import Config
from .errors import ContainmentError

PROVENANCE_FILENAME = ".gitborrow-ephemeral.json"
REPO_DIR_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _resolve(p: str | os.PathLike) -> Path:
    return Path(p).expanduser().resolve()


def protected_roots(config: Config) -> list[Path]:
    return [_resolve(p) for p in config.containment.protected_roots]


def resolve_ephemeral_root(config: Config) -> Path:
    """Resolve the ephemeral root (env override wins) and enforce fail-closed."""
    raw = os.environ.get("GITBORROW_EPHEMERAL_DIR") or config.ephemeral.dir
    root = _resolve(raw)
    for guarded in protected_roots(config):
        if _is_under(root, guarded) or root == guarded:
            raise ContainmentError(
                f"ephemeral root {root} resolves under protected root {guarded}; refusing (fail-closed)"
            )
    return root


def derive_repo_dir_name(repo: str) -> str:
    name = repo.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    if not REPO_DIR_NAME_RE.match(name):
        raise ContainmentError(f"invalid repository directory name: {name!r}")
    return name


def validate_target(target: Path, root: Path, config: Config) -> Path:
    target = _resolve(target)
    if not (_is_under(target, root)):
        raise ContainmentError(f"target {target} is not under ephemeral root {root}")
    for guarded in protected_roots(config):
        if _is_under(target, guarded) or target == guarded:
            raise ContainmentError(f"target {target} is under protected root {guarded}")
    if not REPO_DIR_NAME_RE.match(target.name):
        raise ContainmentError(f"invalid target directory name: {target.name!r}")
    return target


def write_provenance(checkout_dir: Path, *, repo: str, ref: str,
                     sha: str = "", commits: int = 0) -> tuple[str, Path]:
    cid = uuid.uuid4().hex[:12]
    info = {
        "id": cid,
        "repo": repo,
        "ref": ref,
        "sha": sha,
        "commits": commits,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pid": os.getpid(),
        "tool": "gitborrow",
    }
    path = checkout_dir / PROVENANCE_FILENAME
    path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return cid, path


def read_provenance(checkout_dir: Path) -> dict | None:
    path = Path(checkout_dir) / PROVENANCE_FILENAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def iter_checkouts(root: Path):
    """Yield (dir, provenance) for every tool-created checkout under root."""
    if not root.is_dir():
        return
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        prov = read_provenance(child)
        if prov is not None:
            yield child, prov


def _age_hours(prov: dict, checkout_dir: Path) -> float:
    created = prov.get("created_at")
    if created:
        try:
            dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        except ValueError:
            pass
    mtime = checkout_dir.stat().st_mtime
    return (datetime.now(timezone.utc).timestamp() - mtime) / 3600.0


def purge(root: Path, *, ttl_hours: float, purge_all: bool = False) -> tuple[list[str], list[str]]:
    """Remove tool-created checkouts. Returns (purged_ids, kept_foreign_names)."""
    purged: list[str] = []
    kept_foreign: list[str] = []
    if not root.is_dir():
        return purged, kept_foreign
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        prov = read_provenance(child)
        if prov is None:
            kept_foreign.append(child.name)   # never touch user-placed dirs
            continue
        if purge_all or _age_hours(prov, child) >= ttl_hours:
            shutil.rmtree(child, ignore_errors=True)
            purged.append(prov.get("id", child.name))
    return purged, kept_foreign
