import json

import pytest

from agent_policy_kit import AuditLog, DenyAll

from term_operator.core.errors import (
    CommandNotAllowed,
    OperationNotAllowed,
    SendNotApproved,
    SessionNotAllowed,
)


def test_send_allowed(op):
    out = op.send("dev", ["echo", "hi"])
    assert "echo hi" in out
    assert op.backend.sent["dev"] == ["echo hi"]


def test_send_command_not_allowed(op):
    with pytest.raises(CommandNotAllowed):
        op.send("dev", ["cat", "/etc/passwd"])      # not in the send allowlist


def test_send_irreversible_blocked(op):
    with pytest.raises(CommandNotAllowed):
        op.send("dev", ["rm", "-rf", "/tmp/x"])     # cmdgate read_only blocks irreversible


def test_send_not_approved(make_op):
    o = make_op(approval=DenyAll())
    with pytest.raises(SendNotApproved):
        o.send("dev", ["echo", "hi"])
    assert o.backend.sent["dev"] == []              # nothing reached the backend


def test_send_denied_by_session_scope(op):
    with pytest.raises(OperationNotAllowed):
        op.send("ro", ["echo", "hi"])               # 'ro' has no 'send'


def test_unlisted_session(op):
    with pytest.raises(SessionNotAllowed):
        op.read("nope")


def test_read_masks_secrets(op):
    op.backend.buffers["dev"] += "login at https://u:topsecret@h/x\n"
    out = op.read("dev")
    assert "topsecret" not in out and "***MASKED***" in out


def test_audit_records(tmp_path, make_op):
    path = tmp_path / "t.log"
    o = make_op(audit=AuditLog(str(path)))
    o.send("dev", ["echo", "hi"])
    with pytest.raises(CommandNotAllowed):
        o.send("dev", ["cat", "x"])
    results = [json.loads(line)["result"] for line in path.read_text().splitlines()]
    assert "ok" in results and "command_denied" in results
