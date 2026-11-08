import pytest

from term_operator.core import Config, make_backend
from term_operator.core.backends.fake import FakeTerminal
from term_operator.core.errors import BackendError


def test_from_dict_builds_scope_and_command_policy():
    cfg = Config.from_dict({
        "sessions": {"dev": ["read", "send"]},
        "commands": {"read_only": True, "allow": [{"program": "ls"}]},
        "backend": {"type": "fake"},
    })
    assert cfg.scope.check("dev", "send").allowed
    assert cfg.scope.check("dev", "read").allowed
    assert cfg.scope.check("other", "read").code == "scope"
    assert cfg.command_policy.allow[0].program == "ls"
    assert cfg.command_policy.read_only is True
    assert cfg.backend_type == "fake"


def test_make_backend_fake():
    assert isinstance(make_backend("fake", {}), FakeTerminal)


def test_unknown_backend_rejected():
    with pytest.raises(BackendError):
        make_backend("telnet", {})
