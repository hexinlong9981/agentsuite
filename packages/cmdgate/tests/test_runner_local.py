import pytest

from cmdgate import core
from cmdgate.core.errors import CommandTimeout, PolicyDenied, TargetNotAllowed


def test_run_allowed_echo(config):
    res = core.run("local", ["echo", "hello"], config=config)
    assert res.returncode == 0
    assert "hello" in res.stdout
    assert res.allowed_by == "readonly:echo"


def test_run_denied_raises(config):
    with pytest.raises(PolicyDenied):
        core.run("local", ["cat", "/etc/hostname"], config=config)


def test_run_unknown_target(config):
    with pytest.raises(TargetNotAllowed):
        core.run("remote", ["echo", "x"], config=config)


def test_run_timeout(config_factory):
    cfg = config_factory(timeout=0.3)
    with pytest.raises(CommandTimeout):
        core.run("local", ["sleep", "5"], config=cfg)
