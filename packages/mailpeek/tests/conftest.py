import pytest

from agent_policy_kit import AuditLog, ScopePolicy, SecretMasker

from mailpeek.core.backends.fake import FakeMailBackend
from mailpeek.core.connector import MailboxConnector


def _backend():
    return FakeMailBackend({
        "INBOX": {
            "messages": [
                {"id": "1", "subject": "hello", "from": "a@example.invalid",
                 "body": "visit https://u:topsecret@h/x\nAPI_TOKEN=abc123xyzdef\n"},
                {"id": "2", "subject": "report", "from": "b@example.invalid", "body": "all clear"},
            ],
            "attachments": {("1", "a.txt"): b"file-bytes"},
        },
        "Archive": {"messages": [{"id": "9", "subject": "old", "body": ""}]},
    })


@pytest.fixture
def connector():
    # INBOX is in scope; Archive exists in the backend but is NOT in scope.
    scope = ScopePolicy({"INBOX": {"list", "read", "search", "download"}}, read_only=True)
    return MailboxConnector(_backend(), scope=scope, audit=AuditLog(None),
                            masker=SecretMasker(), name="mailpeek")
