import pytest

from cmdgate.core import Config
from cmdgate.core.errors import TargetNotAllowed


def test_policy_referencing_unknown_target_rejected():
    with pytest.raises(ValueError):
        Config.from_dict({"policies": {"p": {"targets": ["ghost"]}}})


def test_get_target_fail_closed():
    cfg = Config.from_dict({})
    with pytest.raises(TargetNotAllowed):
        cfg.get_target("anything")


def test_roundtrip():
    cfg = Config.from_dict({
        "targets": {"local": {"kind": "local"}},
        "policies": {"ro": {"targets": ["local"], "allow": [{"program": "echo"}]}},
    })
    assert "local" in cfg.targets
    assert cfg.policies["ro"].read_only is True
    assert cfg.policies["ro"].allow[0].program == "echo"
