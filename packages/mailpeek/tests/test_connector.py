import json

import pytest

from agent_policy_kit import AuditLog, OperationNotAllowed, ScopeDenied, ScopePolicy, SecretMasker

from mailpeek.core.backends.fake import FakeMailBackend
from mailpeek.core.connector import MailboxConnector


def test_list(connector):
    assert {m["id"] for m in connector.list("INBOX")} == {"1", "2"}


def test_read_masks_secrets(connector):
    m = connector.read("INBOX", message_id="1")
    assert "topsecret" not in m["body"]
    assert "abc123xyzdef" not in m["body"]
    assert "***MASKED***" in m["body"]


def test_search(connector):
    assert [m["id"] for m in connector.search("INBOX", query="report")] == ["2"]


def test_download(connector):
    assert connector.download("INBOX", message_id="1", name="a.txt") == b"file-bytes"


def test_unlisted_folder_denied(connector):
    with pytest.raises(ScopeDenied):
        connector.list("Archive")        # exists in backend, not in scope -> fail-closed


def test_write_operation_denied(connector):
    with pytest.raises(OperationNotAllowed):
        connector.operate("INBOX", "delete", message_id="1")


def test_audit_records(tmp_path):
    path = tmp_path / "a.log"
    scope = ScopePolicy({"INBOX": {"list", "read"}}, read_only=True)
    c = MailboxConnector(
        FakeMailBackend({"INBOX": {"messages": [{"id": "1", "subject": "x", "body": "y"}]}}),
        scope=scope, audit=AuditLog(str(path)), masker=SecretMasker(), name="mailpeek")
    c.list("INBOX")
    with pytest.raises(ScopeDenied):
        c.list("Secret")
    results = [json.loads(line)["result"] for line in path.read_text().splitlines()]
    assert "allow" in results and "deny" in results
