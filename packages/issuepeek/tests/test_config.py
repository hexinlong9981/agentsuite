import pytest

from issuepeek.core import Config, make_backend
from issuepeek.core.backends.fake import FakeIssueBackend
from issuepeek.core.errors import BackendError


def test_from_dict_builds_readonly_scope():
    cfg = Config.from_dict({"projects": {"DEMO": ["list", "read"]}, "backend": {"type": "fake"}})
    assert cfg.backend_type == "fake"
    assert cfg.scope.check("DEMO", "read").allowed
    assert cfg.scope.check("DEMO", "delete").code == "readonly"   # write blocked
    assert cfg.scope.check("Other", "read").code == "scope"       # fail-closed


def test_make_backend_fake():
    assert isinstance(make_backend("fake", {}), FakeIssueBackend)


def test_unknown_backend_rejected():
    with pytest.raises(BackendError):
        make_backend("nosuch", {})
