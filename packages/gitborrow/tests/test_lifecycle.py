"""End-to-end lifecycle on a local source repo (offline)."""

import subprocess
from pathlib import Path

import pytest

from gitborrow import core
from gitborrow.core import Config
from gitborrow.core.errors import RepositoryNotAllowed


def _fetch(src_repo, config):
    return core.fetch(core.FetchRequest(repo=str(src_repo)), config=config)


def test_fetch_creates_contained_checkout(src_repo, config):
    res = _fetch(src_repo, config)
    c = res.checkout
    assert Path(c.path).is_dir()
    assert c.commit_sha and res.citation_base == f"{c.repo}@{c.commit_sha}"
    assert c.commits_available >= 1
    assert Path(c.provenance_path).exists()
    # push guard enabled -> origin disconnected
    assert res.push_guard == "enabled"
    out = subprocess.run(["git", "remote"], cwd=c.path, capture_output=True, text=True)
    assert out.stdout.strip() == ""


def test_secret_filter_masks_planted_secrets(src_repo, config):
    res = _fetch(src_repo, config)
    assert res.mask.enabled and res.mask.hits >= 2
    ini = (Path(res.checkout.path) / "config.ini").read_text()
    assert "supersecret" not in ini
    assert "***MASKED***" in ini
    pem = (Path(res.checkout.path) / "id_key.pem").read_text()
    assert "PRIVATE KEY" not in pem or "***MASKED***" in pem


def test_list_release_roundtrip(src_repo, config):
    res = _fetch(src_repo, config)
    listed = core.list_checkouts(config)
    assert any(c.id == res.checkout.id for c in listed)
    rel = core.release(res.checkout.id, config=config)
    assert res.checkout.id in rel.released
    assert not Path(res.checkout.path).exists()


def test_purge_all(src_repo, config):
    _fetch(src_repo, config)
    pr = core.purge(config, purge_all=True)
    assert len(pr.purged) >= 1
    assert core.list_checkouts(config) == []


def test_push_guard_disabled_keeps_origin(src_repo, tmp_path):
    cfg = Config.from_dict({
        "ephemeral": {"dir": str(tmp_path / "eph2")},
        "repositories": {"allow_any": True},
        "push_guard": {"enabled": False},
        "secret_filter": {"enabled": False},
    })
    res = core.fetch(core.FetchRequest(repo=str(src_repo)), config=cfg)
    assert res.push_guard == "disabled" and res.warnings
    out = subprocess.run(["git", "remote"], cwd=res.checkout.path, capture_output=True, text=True)
    assert "origin" in out.stdout


def test_repository_not_allowed_fail_closed(src_repo, tmp_path):
    cfg = Config.from_dict({
        "ephemeral": {"dir": str(tmp_path / "eph3")},
        "repositories": {"allow_any": False},   # empty allowlist => nothing allowed
    })
    with pytest.raises(RepositoryNotAllowed):
        core.fetch(core.FetchRequest(repo=str(src_repo)), config=cfg)
