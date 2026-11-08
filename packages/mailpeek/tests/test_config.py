import pytest

from mailpeek.core import Config, make_backend
from mailpeek.core.backends.fake import FakeMailBackend
from mailpeek.core.errors import BackendError


def test_from_dict_builds_readonly_scope():
    cfg = Config.from_dict({"folders": {"INBOX": ["list", "read"]}, "backend": {"type": "fake"}})
    assert cfg.backend_type == "fake"
    assert cfg.scope.check("INBOX", "read").allowed
    assert cfg.scope.check("INBOX", "delete").code == "readonly"   # write blocked
    assert cfg.scope.check("Other", "read").code == "scope"        # fail-closed


def test_make_backend_fake():
    assert isinstance(make_backend("fake", {}), FakeMailBackend)


def test_unknown_backend_rejected():
    with pytest.raises(BackendError):
        make_backend("pop3", {})
