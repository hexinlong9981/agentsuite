import pytest

from agent_policy_kit import (AllowAll, AuditLog, DenyAll, OperationNotAllowed,
                              OperationNotApproved, ScopePolicy, SecretMasker)

from issuepeek.core import Config
from issuepeek.core.backends.fake import FakeIssueBackend
from issuepeek.core.connector import IssueTrackerConnector


def _conn(read_only, approval):
    backend = FakeIssueBackend({"PRJ": {"issues": []}})
    scope = ScopePolicy({"PRJ": {"read", "create"}}, read_only=read_only)
    return IssueTrackerConnector(backend, scope=scope, audit=AuditLog(None),
                                 masker=SecretMasker(), approval=approval, name="issuepeek"), backend


def test_create_allowed_and_recorded():
    conn, backend = _conn(False, AllowAll())
    res = conn.create_issue("PRJ", summary="x")
    assert res["created"] == "1"
    assert backend.projects["PRJ"]["issues"][0]["summary"] == "x"


def test_create_unapproved():
    conn, _ = _conn(False, DenyAll())
    with pytest.raises(OperationNotApproved):
        conn.create_issue("PRJ", summary="x")


def test_create_readonly_scope():
    conn, _ = _conn(True, AllowAll())
    with pytest.raises(OperationNotAllowed):
        conn.create_issue("PRJ", summary="x")


def test_config_read_only_false():
    cfg = Config.from_dict({"projects": {"PRJ": ["read", "create"]}, "read_only": False})
    assert cfg.scope.check("PRJ", "create").allowed
