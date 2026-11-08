import pytest

from web_operator.core.errors import HostNotAllowed, OperationNotAllowed


def test_host_in_scope(config):
    assert config.policy.check("ex.example", "mutate").allowed


def test_unlisted_host_fail_closed(config):
    d = config.policy.check("evil.example", "read")
    assert not d.allowed and d.code == "scope"       # kit ScopePolicy code


def test_category_not_allowed(config):
    d = config.policy.check("a.readonly.example", "mutate")
    assert not d.allowed and d.code == "operation"   # kit ScopePolicy code


def test_glob_host(config):
    assert config.policy.check("a.readonly.example", "read").allowed


def test_require_raises(config):
    with pytest.raises(HostNotAllowed):
        config.policy.require("evil.example", "read")
    with pytest.raises(OperationNotAllowed):
        config.policy.require("a.readonly.example", "mutate")
