import json

from agent_policy_kit import AuditLog


def test_records_allow_and_deny(tmp_path):
    path = tmp_path / "audit.log"
    log = AuditLog(str(path))
    log.allow(connector="demo", resource="docs", operation="read")
    log.deny("not in scope", connector="demo", resource="secrets", operation="read")
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    a, d = json.loads(lines[0]), json.loads(lines[1])
    assert a["result"] == "allow" and "ts" in a
    assert d["result"] == "deny" and d["reason"] == "not in scope"


def test_null_audit_is_noop():
    AuditLog(None).allow(x=1)   # must not raise
