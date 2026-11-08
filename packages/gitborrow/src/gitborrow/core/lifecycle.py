"""Fetch → reference → release/purge. Ties the four pillars together."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import audit, containment
from . import filter as secret_filter
from . import guard
from .config import Config
from .credentials import CredentialStore, make_store
from .errors import (
    FilterError,
    GitborrowError,
    MirrorUnreachable,
    RefNotAllowed,
    RepositoryNotAllowed,
)
from .gitrunner import run_git
from .types import (
    Checkout,
    FetchRequest,
    FetchResult,
    PurgeResult,
    ReleaseResult,
    Status,
    utcnow_iso,
)


def _resolve_source(req: FetchRequest, config: Config) -> tuple[str, str]:
    repos = config.repositories
    if req.repo in repos.entries:
        return repos.entries[req.repo].url, req.repo
    if repos.allow_any:
        return req.repo, containment.derive_repo_dir_name(req.repo)
    raise RepositoryNotAllowed(
        f"repository {req.repo!r} not in allowlist (set repositories.allow_any=true "
        "or add a [repositories.<name>] entry)"
    )


def _check_allowed_ref(config: Config, repo_key: str, ref: str) -> None:
    entry = config.repositories.entries.get(repo_key)
    if entry and entry.allowed_refs and ref not in entry.allowed_refs:
        raise RefNotAllowed(f"ref {ref!r} is not allowed for {repo_key!r}")


def _classify_clone_error(err: str) -> GitborrowError:
    low = err.lower()
    if any(k in low for k in ("permission denied", "authentication failed",
                              "could not read from remote", "publickey")):
        return MirrorUnreachable("authentication problem reaching remote", cause="ssh_key")
    if any(k in low for k in ("whitelist", "not allowed", "403", "forbidden")):
        return MirrorUnreachable("access to remote not permitted", cause="ip_not_allowed")
    if any(k in low for k in ("timed out", "could not resolve host", "network is unreachable")):
        return MirrorUnreachable("could not reach remote", cause="timeout")
    return GitborrowError(f"git clone failed: {err.strip()[:200]}")


def fetch(req: FetchRequest, *, config: Config,
          store: CredentialStore | None = None) -> FetchResult:
    store = store or make_store(config)
    source, repo_label = _resolve_source(req, config)
    guard.validate_url(source)

    ref = req.ref
    if ref is not None:
        guard.validate_ref(ref)
        _check_allowed_ref(config, req.repo, ref)

    root = containment.resolve_ephemeral_root(config)
    root.mkdir(parents=True, exist_ok=True)
    target = containment.validate_target(root / repo_label, root, config)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    clone = ["clone"]
    if not req.full_history:
        clone += ["--depth", "1"]
    if ref is not None:
        clone += ["--branch", ref]
    clone += [source, str(target)]

    rc, _out, err = run_git(clone, config=config, store=store, network=True, timeout=300)
    if rc != 0:
        shutil.rmtree(target, ignore_errors=True)
        audit.log(config, {"action": "fetch", "repo": repo_label, "ref": ref or "",
                           "result": "error", "reason": err.strip()[:200]})
        raise _classify_clone_error(err)

    warnings: list[str] = []
    if config.push_guard.enabled:
        run_git(["remote", "remove", "origin"], config=config, cwd=target)
        guard_state = "enabled"
    else:
        guard_state = "disabled"
        warnings.append("push guard disabled: origin kept; writes back to the remote "
                        "are NOT prevented")

    _, sha_out, _ = run_git(["rev-parse", "HEAD"], config=config, cwd=target)
    sha = sha_out.strip()
    _, cnt_out, _ = run_git(["rev-list", "--count", "HEAD"], config=config, cwd=target)
    commits = int(cnt_out.strip() or "0")
    ref_label = ref or sha[:12]

    try:
        mask = secret_filter.scan_and_filter(target, config)
    except FilterError:
        shutil.rmtree(target, ignore_errors=True)
        audit.log(config, {"action": "fetch", "repo": repo_label, "ref": ref_label,
                           "result": "filter_fail"})
        raise

    cid, prov_path = containment.write_provenance(
        target, repo=repo_label, ref=ref_label, sha=sha, commits=commits)
    checkout = Checkout(
        id=cid, repo=repo_label, ref=ref_label, path=str(target),
        commit_sha=sha, commits_available=commits,
        created_at=utcnow_iso(), provenance_path=str(prov_path),
    )
    audit.log(config, {"action": "fetch", "repo": repo_label, "ref": ref_label,
                       "result": "ok", "checkout_id": cid})
    return FetchResult(checkout=checkout, citation_base=f"{repo_label}@{sha}",
                       mask=mask, push_guard=guard_state, warnings=warnings)


def _checkout_from_prov(path: Path, prov: dict) -> Checkout:
    return Checkout(
        id=prov.get("id", path.name), repo=prov.get("repo", path.name),
        ref=prov.get("ref", ""), path=str(path),
        commit_sha=prov.get("sha", ""), commits_available=int(prov.get("commits", 0)),
        created_at=prov.get("created_at", ""),
        provenance_path=str(path / containment.PROVENANCE_FILENAME),
    )


def list_checkouts(config: Config) -> list[Checkout]:
    root = containment.resolve_ephemeral_root(config)
    return [_checkout_from_prov(d, p) for d, p in containment.iter_checkouts(root)]


def status(repo: str, config: Config) -> Status:
    return Status(repo=repo, checkouts=[c for c in list_checkouts(config) if c.repo == repo])


def release(target: str, *, config: Config) -> ReleaseResult:
    root = containment.resolve_ephemeral_root(config)
    released: list[str] = []
    for d, prov in list(containment.iter_checkouts(root)):
        if target in (prov.get("id"), prov.get("repo")) or d.name == target:
            shutil.rmtree(d, ignore_errors=True)
            released.append(prov.get("id", d.name))
    audit.log(config, {"action": "release", "target": target,
                       "result": "ok", "released": released})
    return ReleaseResult(released=released)


def purge(config: Config, *, max_age_hours: float | None = None,
          purge_all: bool = False) -> PurgeResult:
    root = containment.resolve_ephemeral_root(config)
    ttl = config.ephemeral.ttl_hours if max_age_hours is None else max_age_hours
    purged, kept = containment.purge(root, ttl_hours=ttl, purge_all=purge_all)
    audit.log(config, {"action": "purge", "result": "ok",
                       "purged": purged, "kept_foreign": kept})
    return PurgeResult(purged=purged, kept=kept)
