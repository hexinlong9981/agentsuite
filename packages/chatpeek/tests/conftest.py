import pytest

from agent_policy_kit import AuditLog, ScopePolicy, SecretMasker

from chatpeek.core.backends.fake import FakeChatBackend
from chatpeek.core.connector import ChatConnector


def _backend():
    return FakeChatBackend({
        "general": {
            "messages": [
                {"id": "1", "author": "alice", "text": "hello https://u:topsecret@h/x"},
                {"id": "2", "author": "bob", "text": "report all clear"},
            ],
            "files": {("1", "a.txt"): b"file-bytes"},
        },
        "private": {"messages": [{"id": "9", "author": "carol", "text": "old"}]},
    })


@pytest.fixture
def connector():
    # general is in scope; private exists in the backend but is NOT in scope.
    scope = ScopePolicy({"general": {"list", "read", "search", "download"}}, read_only=True)
    return ChatConnector(_backend(), scope=scope, audit=AuditLog(None),
                         masker=SecretMasker(), name="chatpeek")
