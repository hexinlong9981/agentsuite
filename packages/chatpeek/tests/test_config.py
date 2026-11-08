import pytest

from chatpeek.core import Config, make_backend
from chatpeek.core.backends.fake import FakeChatBackend
from chatpeek.core.errors import BackendError


def test_from_dict_builds_readonly_scope():
    cfg = Config.from_dict({"channels": {"general": ["list", "read"]}, "backend": {"type": "fake"}})
    assert cfg.backend_type == "fake"
    assert cfg.scope.check("general", "read").allowed
    assert cfg.scope.check("general", "delete").code == "readonly"   # write blocked
    assert cfg.scope.check("other", "read").code == "scope"          # fail-closed


def test_make_backend_fake():
    assert isinstance(make_backend("fake", {}), FakeChatBackend)


def test_unknown_backend_rejected():
    with pytest.raises(BackendError):
        make_backend("irc", {})


def test_from_dict_read_only_false_allows_send():
    cfg = Config.from_dict({"read_only": False, "channels": {"general": ["read", "send"]}})
    assert cfg.scope.check("general", "send").allowed
