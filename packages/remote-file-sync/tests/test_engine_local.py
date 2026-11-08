import pytest

from remote_file_sync import core
from remote_file_sync.core.errors import PolicyNotAllowed


def test_sync_routes_by_category(config, tmp_path):
    res = core.sync("p", config=config)
    paths = {f.path for f in res.fetched}
    assert "etc/app.conf" in paths
    assert "var/log/x.log" in paths
    assert "etc/secret.key" not in paths            # excluded by **/*.key

    by_path = {f.path: f.category for f in res.fetched}
    assert by_path["etc/app.conf"] == "config"
    assert by_path["var/log/x.log"] == "data"

    assert (tmp_path / "dst_config" / "etc" / "app.conf").is_file()
    assert (tmp_path / "dst_data" / "var" / "log" / "x.log").is_file()
    assert res.by_category == {"config": 1, "data": 1}


def test_dry_run_writes_nothing(config, tmp_path):
    res = core.sync("p", config=config, dry_run=True)
    assert res.dry_run and len(res.fetched) == 2
    assert not (tmp_path / "dst_config").exists()
    assert not (tmp_path / "dst_data").exists()


def test_source_is_read_only(config, src_tree):
    before = sorted(p.name for p in src_tree.rglob("*"))
    core.sync("p", config=config)
    after = sorted(p.name for p in src_tree.rglob("*"))
    assert before == after


def test_unknown_policy_rejected(config):
    with pytest.raises(PolicyNotAllowed):
        core.sync("nope", config=config)
