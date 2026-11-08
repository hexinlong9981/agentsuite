import json

import pytest

from agent_policy_kit import AuditLog, OperationNotAllowed, ScopeDenied, ScopePolicy, SecretMasker

from filepeek.core.backends.fake import FakeFileStoreBackend
from filepeek.core.connector import FileStoreConnector


def test_list(connector):
    assert {f["id"] for f in connector.list("docs")} == {"1", "2"}


def test_read_masks_secrets(connector):
    f = connector.read("docs", file_id="1")
    assert "topsecret" not in f["description"]
    assert "abc123xyzdef" not in f["description"]
    assert "***MASKED***" in f["description"]


def test_search(connector):
    assert [f["id"] for f in connector.search("docs", query="report")] == ["2"]


def test_download(connector):
    assert connector.download("docs", file_id="1") == b"file-bytes"


def test_unlisted_folder_denied(connector):
    with pytest.raises(ScopeDenied):
        connector.list("archive")        # exists in backend, not in scope -> fail-closed


def test_write_operation_denied(connector):
    with pytest.raises(OperationNotAllowed):
        connector.operate("docs", "delete", file_id="1")


def test_audit_records(tmp_path):
    path = tmp_path / "a.log"
    scope = ScopePolicy({"docs": {"list", "read"}}, read_only=True)
    c = FileStoreConnector(
        FakeFileStoreBackend({"docs": {"files": [{"id": "1", "name": "x", "description": "y"}]}}),
        scope=scope, audit=AuditLog(str(path)), masker=SecretMasker(), name="filepeek")
    c.list("docs")
    with pytest.raises(ScopeDenied):
        c.list("secret")
    results = [json.loads(line)["result"] for line in path.read_text().splitlines()]
    assert "allow" in results and "deny" in results
