import pytest

from agent_policy_kit import ScopePolicy
from agent_policy_kit.errors import OperationNotAllowed, ScopeDenied


def test_unlisted_resource_denied_fail_closed():
    pol = ScopePolicy({"docs": {"read"}})
    d = pol.check("secrets", "read")
    assert not d.allowed and d.code == "scope"


def test_listed_read_allowed():
    pol = ScopePolicy({"docs": {"list", "read"}})
    assert pol.check("docs", "read").allowed


def test_write_denied_under_read_only():
    pol = ScopePolicy({"docs": {"read", "delete"}})   # even if listed...
    d = pol.check("docs", "delete")
    assert not d.allowed and d.code == "readonly"


def test_write_allowed_when_not_read_only():
    pol = ScopePolicy({"docs": {"read", "delete"}}, read_only=False)
    assert pol.check("docs", "delete").allowed


def test_glob_match():
    pol = ScopePolicy({"files/*": {"read"}})
    assert pol.check("files/a.txt", "read").allowed
    assert not pol.check("other/a.txt", "read").allowed


def test_allow_unlisted_read_only():
    pol = ScopePolicy({}, allow_unlisted=True)
    assert pol.check("anything", "read").allowed
    assert not pol.check("anything", "delete").allowed   # still read-only


def test_require_raises():
    pol = ScopePolicy({"docs": {"read"}})
    with pytest.raises(ScopeDenied):
        pol.require("secrets", "read")
    with pytest.raises(OperationNotAllowed):
        pol.require("docs", "list")
