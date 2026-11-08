import pytest

from agent_policy_kit import AuditLog, ScopePolicy, SecretMasker

from issuepeek.core.backends.fake import FakeIssueBackend
from issuepeek.core.connector import IssueTrackerConnector


def _backend():
    return FakeIssueBackend({
        "DEMO": {
            "issues": [
                {"id": "1", "title": "login timeout", "reporter": "a@example.invalid",
                 "description": "see https://u:topsecret@h/x for details\n"},
                {"id": "2", "title": "report", "reporter": "b@example.invalid",
                 "description": "all clear"},
            ],
            "attachments": {("1", "a.txt"): b"file-bytes"},
        },
        "HIDDEN": {"issues": [{"id": "9", "title": "old", "description": ""}]},
    })


@pytest.fixture
def connector():
    # DEMO is in scope; HIDDEN exists in the backend but is NOT in scope.
    scope = ScopePolicy({"DEMO": {"list", "read", "search", "download"}}, read_only=True)
    return IssueTrackerConnector(_backend(), scope=scope, audit=AuditLog(None),
                                 masker=SecretMasker(), name="issuepeek")
