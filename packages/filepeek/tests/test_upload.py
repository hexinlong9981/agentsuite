import pytest

import agent_policy_kit as kit
from agent_policy_kit import AllowAll, AuditLog, DenyAll, ScopePolicy, SecretMasker

from filepeek.core import Config
from filepeek.core.backends.fake import FakeFileStoreBackend
from filepeek.core.connector import FileStoreConnector


def _conn(read_only, approval):
    backend = FakeFileStoreBackend({"docs": {"files": []}})
    scope = ScopePolicy({"docs": {"read", "create"}}, read_only=read_only)
    conn = FileStoreConnector(backend, scope=scope, audit=AuditLog(None),
                              masker=SecretMasker(), name="filepeek", approval=approval)
    return conn, backend


def test_upload_allowed():
    conn, backend = _conn(False, AllowAll())
    assert conn.upload("docs", "a.txt", b"x") == {"uploaded": "a.txt"}
    assert backend.uploads == [("docs", "a.txt", b"x")]


def test_upload_unapproved():
    conn, backend = _conn(False, DenyAll())
    with pytest.raises(kit.OperationNotApproved):
        conn.upload("docs", "a.txt", b"x")
    assert backend.uploads == []


def test_upload_read_only_scope():
    conn, backend = _conn(True, AllowAll())
    with pytest.raises(kit.OperationNotAllowed):
        conn.upload("docs", "a.txt", b"x")
    assert backend.uploads == []


def test_config_read_only_false():
    cfg = Config.from_dict({"read_only": False, "folders": {"docs": ["read", "create"]}})
    assert cfg.scope.check("docs", "create").allowed
    assert Config.from_dict({"folders": {"docs": ["create"]}}).scope.check("docs", "create").allowed is False
