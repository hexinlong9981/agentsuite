import json

import pytest

from agent_policy_kit import AuditLog, OperationNotAllowed, ScopeDenied, ScopePolicy, SecretMasker

from issuepeek.core.backends.fake import FakeIssueBackend
from issuepeek.core.connector import IssueTrackerConnector


def test_list(connector):
    assert {i["id"] for i in connector.list("DEMO")} == {"1", "2"}


def test_read_masks_secrets(connector):
    i = connector.read("DEMO", issue_id="1")
    assert "topsecret" not in i["description"]
    assert "***MASKED***" in i["description"]


def test_search(connector):
    assert [i["id"] for i in connector.search("DEMO", query="report")] == ["2"]


def test_download(connector):
    assert connector.download("DEMO", issue_id="1", name="a.txt") == b"file-bytes"


def test_unlisted_project_denied(connector):
    with pytest.raises(ScopeDenied):
        connector.list("HIDDEN")        # exists in backend, not in scope -> fail-closed


def test_write_operation_denied(connector):
    with pytest.raises(OperationNotAllowed):
        connector.operate("DEMO", "delete", issue_id="1")


def test_audit_records(tmp_path):
    path = tmp_path / "a.log"
    scope = ScopePolicy({"DEMO": {"list", "read"}}, read_only=True)
    c = IssueTrackerConnector(
        FakeIssueBackend({"DEMO": {"issues": [{"id": "1", "title": "x", "description": "y"}]}}),
        scope=scope, audit=AuditLog(str(path)), masker=SecretMasker(), name="issuepeek")
    c.list("DEMO")
    with pytest.raises(ScopeDenied):
        c.list("Secret")
    results = [json.loads(line)["result"] for line in path.read_text().splitlines()]
    assert "allow" in results and "deny" in results
