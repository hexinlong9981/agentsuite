import pytest

from agent_policy_kit import AuditLog, ScopePolicy, SecretMasker

from filepeek.core.backends.fake import FakeFileStoreBackend
from filepeek.core.connector import FileStoreConnector


def _backend():
    return FakeFileStoreBackend({
        "docs": {
            "files": [
                {"id": "1", "name": "notes.txt",
                 "description": "see https://u:topsecret@h/x\nAPI_TOKEN=abc123xyzdef\n"},
                {"id": "2", "name": "report.txt", "description": "all clear"},
            ],
            "contents": {"1": b"file-bytes"},
        },
        "archive": {"files": [{"id": "9", "name": "old", "description": ""}]},
    })


@pytest.fixture
def connector():
    # docs is in scope; archive exists in the backend but is NOT in scope.
    scope = ScopePolicy({"docs": {"list", "read", "search", "download"}}, read_only=True)
    return FileStoreConnector(_backend(), scope=scope, audit=AuditLog(None),
                              masker=SecretMasker(), name="filepeek")
