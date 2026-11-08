import json

import pytest

from agent_policy_kit import AuditLog, OperationNotAllowed, ScopeDenied, ScopePolicy, SecretMasker

from chatpeek.core.backends.fake import FakeChatBackend
from chatpeek.core.connector import ChatConnector


def test_list(connector):
    assert {m["id"] for m in connector.list("general")} == {"1", "2"}


def test_read_masks_secrets(connector):
    m = connector.read("general", message_id="1")
    assert "topsecret" not in m["text"]
    assert "***MASKED***" in m["text"]


def test_search(connector):
    assert [m["id"] for m in connector.search("general", query="report")] == ["2"]


def test_download(connector):
    assert connector.download("general", message_id="1", name="a.txt") == b"file-bytes"


def test_unlisted_channel_denied(connector):
    with pytest.raises(ScopeDenied):
        connector.list("private")        # exists in backend, not in scope -> fail-closed


def test_write_operation_denied(connector):
    with pytest.raises(OperationNotAllowed):
        connector.operate("general", "delete", message_id="1")


def test_allowed_channels(connector):
    assert connector.allowed_channels() == ["general"]


def test_audit_records(tmp_path):
    path = tmp_path / "a.log"
    scope = ScopePolicy({"general": {"list", "read"}}, read_only=True)
    c = ChatConnector(
        FakeChatBackend({"general": {"messages": [{"id": "1", "author": "x", "text": "y"}]}}),
        scope=scope, audit=AuditLog(str(path)), masker=SecretMasker(), name="chatpeek")
    c.list("general")
    with pytest.raises(ScopeDenied):
        c.list("secret")
    results = [json.loads(line)["result"] for line in path.read_text().splitlines()]
    assert "allow" in results and "deny" in results


def _write_conn(approval, read_only=False, ops=("read", "send")):
    import agent_policy_kit as k
    backend = FakeChatBackend({"general": {"messages": []}})
    conn = ChatConnector(backend, scope=ScopePolicy({"general": set(ops)}, read_only=read_only),
                         audit=AuditLog(None), masker=SecretMasker(), name="chatpeek",
                         approval=approval)
    return conn, backend


def test_post_allowed_with_approval():
    import agent_policy_kit as k
    conn, backend = _write_conn(k.AllowAll())
    assert conn.post("general", "hi") == {"posted": "general"}
    assert backend.posts["general"] == ["hi"]


def test_post_denied_without_approval():
    import agent_policy_kit as k
    conn, backend = _write_conn(k.DenyAll())
    with pytest.raises(k.OperationNotApproved):
        conn.post("general", "hi")
    assert backend.posts == {}


def test_post_blocked_by_read_only_scope():
    import agent_policy_kit as k
    conn, backend = _write_conn(k.AllowAll(), read_only=True, ops=("send",))
    with pytest.raises(k.OperationNotAllowed):
        conn.post("general", "hi")
    assert backend.posts == {}
