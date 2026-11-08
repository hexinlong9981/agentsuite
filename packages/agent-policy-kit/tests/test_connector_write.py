import pytest

from agent_policy_kit import AllowAll, DenyAll, ScopePolicy, SecretMasker
from agent_policy_kit.errors import OperationNotAllowed, OperationNotApproved

from memory_connector import MemoryConnector


def test_write_allowed_with_approval():
    data = {"notes": {}}
    c = MemoryConnector(data, scope=ScopePolicy({"notes": {"read", "create"}}, read_only=False),
                        approval=AllowAll(), masker=SecretMasker(), name="demo")
    c.create("notes", key="a", value="1")
    assert data["notes"]["a"] == "1"


def test_write_denied_without_approval():
    c = MemoryConnector({"notes": {}}, scope=ScopePolicy({"notes": {"create"}}, read_only=False),
                        approval=DenyAll(), name="demo")
    with pytest.raises(OperationNotApproved):
        c.create("notes", key="a", value="1")


def test_write_blocked_by_readonly_scope():
    # read_only scope refuses the write before approval is even consulted
    c = MemoryConnector({"notes": {}}, scope=ScopePolicy({"notes": {"create"}}, read_only=True),
                        approval=AllowAll(), name="demo")
    with pytest.raises(OperationNotAllowed):
        c.create("notes", key="a", value="1")


def test_default_approval_denies_writes():
    # no approval provided -> safe default (DenyAll)
    c = MemoryConnector({"notes": {}}, scope=ScopePolicy({"notes": {"create"}}, read_only=False),
                        name="demo")
    with pytest.raises(OperationNotApproved):
        c.create("notes", key="a", value="1")
