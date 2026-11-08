import pytest

from web_operator.core import AllowAll, FakeBackend, Operator
from web_operator.core.errors import (
    HostNotAllowed,
    InvalidOperation,
    MutationNotApproved,
    OperationNotAllowed,
)


def test_navigate_and_read(operator):
    operator.navigate("ex.example", "https://ex.example/login")
    assert operator.read("ex.example") == "Login page"


def test_interact_allowed(operator):
    assert operator.fill("ex.example", "#user", "alice")["ok"]


def test_submit_denied_by_default(operator):
    operator.navigate("ex.example", "https://ex.example/login")
    with pytest.raises(MutationNotApproved):
        operator.submit("ex.example", "#form")


def test_submit_with_approval(config, pages):
    op = Operator(backend=FakeBackend(pages), policy=config.policy, approval=AllowAll())
    op.navigate("ex.example", "https://ex.example/login")
    res = op.submit("ex.example", "#form")
    assert res["submitted"] and any(a[0] == "submit" for a in op.backend.actions)


def test_mutate_blocked_by_matrix_before_approval(config, pages):
    op = Operator(backend=FakeBackend(pages), policy=config.policy, approval=AllowAll())
    with pytest.raises(OperationNotAllowed):
        op.submit("a.readonly.example", "#form")
    assert all(a[0] != "submit" for a in op.backend.actions)   # never reached backend


def test_unlisted_host_refused(operator):
    with pytest.raises(HostNotAllowed):
        operator.read("nope.example")


def test_invalid_operation(operator):
    with pytest.raises(InvalidOperation):
        operator.operate("ex.example", "teleport")


def test_audit_records(tmp_path, config, pages):
    import json

    from web_operator.core import AuditLog, DenyAll
    path = tmp_path / "wo.log"
    op = Operator(backend=FakeBackend(pages), policy=config.policy,
                  approval=DenyAll(), audit=AuditLog(str(path)))
    op.navigate("ex.example", "https://ex.example/login")
    with pytest.raises(MutationNotApproved):
        op.submit("ex.example", "#form")
    actions = [json.loads(line)["action"] for line in path.read_text().splitlines()]
    assert "operate" in actions and "mutate_request" in actions
