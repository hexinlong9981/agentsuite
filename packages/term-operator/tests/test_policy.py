import pytest

from term_operator.core.errors import OperationNotAllowed, SessionNotAllowed
from term_operator.core.policy import SessionPolicy


def _policy():
    return SessionPolicy({"dev": {"open", "read", "send"}, "ro": {"open", "read"}})


def test_send_allowed_when_listed():
    assert _policy().check("dev", "send").allowed


def test_send_not_allowed_on_readonly_session():
    d = _policy().check("ro", "send")
    assert not d.allowed and d.code == "operation"


def test_unlisted_session_fail_closed():
    d = _policy().check("nope", "read")
    assert not d.allowed and d.code == "scope"


def test_require_raises():
    p = _policy()
    with pytest.raises(SessionNotAllowed):
        p.require("nope", "read")
    with pytest.raises(OperationNotAllowed):
        p.require("ro", "send")
