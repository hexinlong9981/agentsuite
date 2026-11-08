import pytest

from filepeek.core import Config, make_backend
from filepeek.core.backends.fake import FakeFileStoreBackend
from filepeek.core.errors import BackendError


def test_from_dict_builds_readonly_scope():
    cfg = Config.from_dict({"folders": {"docs": ["list", "read"]}, "backend": {"type": "fake"}})
    assert cfg.backend_type == "fake"
    assert cfg.scope.check("docs", "read").allowed
    assert cfg.scope.check("docs", "delete").code == "readonly"   # write blocked
    assert cfg.scope.check("other", "read").code == "scope"       # fail-closed


def test_make_backend_fake():
    assert isinstance(make_backend("fake", {}), FakeFileStoreBackend)


def test_unknown_backend_rejected():
    with pytest.raises(BackendError):
        make_backend("s3", {})
