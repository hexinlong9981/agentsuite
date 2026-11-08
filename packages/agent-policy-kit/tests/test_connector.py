import json

import pytest

from agent_policy_kit import AuditLog, ScopePolicy, SecretMasker
from agent_policy_kit.errors import OperationNotAllowed, ScopeDenied
from memory_connector import MemoryConnector


def _conn(audit_path=None):
    data = {
        "docs": {
            "readme": "intro",
            "config": "db = postgres://user:supersecret@host/db\nAPI_TOKEN = abc123xyz",
        }
    }
    scope = ScopePolicy({"docs": {"list", "read", "search"}})
    return MemoryConnector(
        data,
        scope=scope,
        audit=AuditLog(audit_path),
        masker=SecretMasker(),
        name="demo",
    )


def test_list_and_read():
    c = _conn()
    assert c.list("docs") == ["config", "readme"]
    assert c.read("docs", key="readme") == "intro"


def test_read_masks_secrets():
    c = _conn()
    out = c.read("docs", key="config")
    assert "supersecret" not in out and "abc123xyz" not in out


def test_write_operation_refused():
    c = _conn()
    with pytest.raises(OperationNotAllowed):
        c.operate("docs", "delete", key="readme")


def test_unlisted_resource_refused():
    c = _conn()
    with pytest.raises(ScopeDenied):
        c.read("secrets", key="x")


def test_audit_records(tmp_path):
    path = tmp_path / "a.log"
    c = _conn(str(path))
    c.read("docs", key="readme")
    with pytest.raises(ScopeDenied):
        c.read("secrets", key="x")
    results = [json.loads(line)["result"] for line in path.read_text().splitlines()]
    assert "allow" in results and "deny" in results
