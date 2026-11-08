"""Pillar 1 regression tests."""

import pytest

from gitborrow.core import containment
from gitborrow.core.config import Config
from gitborrow.core.errors import ContainmentError


def test_ephemeral_under_protected_rejected(tmp_path):
    cfg = Config.from_dict({
        "ephemeral": {"dir": str(tmp_path / "p" / "sub")},
        "containment": {"protected_roots": [str(tmp_path / "p")]},
    })
    with pytest.raises(ContainmentError):
        containment.resolve_ephemeral_root(cfg)


def test_valid_ephemeral_root_ok(tmp_path):
    cfg = Config.from_dict({
        "ephemeral": {"dir": str(tmp_path / "eph")},
        "containment": {"protected_roots": [str(tmp_path / "workspace")]},
    })
    root = containment.resolve_ephemeral_root(cfg)
    assert str(root).endswith("eph")


def test_validate_target_outside_root_rejected(tmp_path):
    cfg = Config.from_dict({"ephemeral": {"dir": str(tmp_path / "eph")}})
    root = containment.resolve_ephemeral_root(cfg)
    with pytest.raises(ContainmentError):
        containment.validate_target(tmp_path / "elsewhere" / "x", root, cfg)
    ok = containment.validate_target(root / "myrepo", root, cfg)
    assert ok.name == "myrepo"


def test_repo_dir_name():
    assert containment.derive_repo_dir_name("https://h/a/b/foo.git") == "foo"
    assert containment.derive_repo_dir_name("/local/path/bar") == "bar"
    with pytest.raises(ContainmentError):
        containment.derive_repo_dir_name("bad name")


def test_provenance_roundtrip(tmp_path):
    d = tmp_path / "co"
    d.mkdir()
    cid, path = containment.write_provenance(d, repo="foo", ref="main", sha="abc", commits=3)
    prov = containment.read_provenance(d)
    assert prov["id"] == cid and prov["repo"] == "foo" and prov["sha"] == "abc"
    assert path.exists()


def test_purge_only_touches_tool_dirs(tmp_path):
    root = tmp_path / "eph"
    root.mkdir()
    managed = root / "managed"
    managed.mkdir()
    containment.write_provenance(managed, repo="r", ref="main")
    foreign = root / "user_data"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("keep")

    purged, kept = containment.purge(root, ttl_hours=0, purge_all=False)

    assert not managed.exists()        # tool-created, aged out
    assert foreign.exists()            # never touched
    assert "user_data" in kept
    assert len(purged) == 1
