import pytest

from agent_policy_kit import AllowAll, AuditLog, SecretMasker
from cmdgate.core import Policy as CommandPolicy
from cmdgate.core import Rule as CommandRule

from term_operator.core.backends.fake import FakeTerminal
from term_operator.core.operator import TermOperator
from term_operator.core.policy import SessionPolicy


def build(approval=None, audit=None):
    scope = SessionPolicy({
        "dev": {"open", "read", "close", "send"},
        "ro": {"open", "read"},                      # read-only session (no send)
    })
    command_policy = CommandPolicy(
        name="send", targets=[], read_only=True,
        allow=[CommandRule("echo"), CommandRule("ls"),
               CommandRule("git", r"^(status|log)\b")],
    )
    op = TermOperator(backend=FakeTerminal(), scope=scope, command_policy=command_policy,
                      approval=approval or AllowAll(), audit=audit or AuditLog(None),
                      masker=SecretMasker())
    op.open("dev")
    op.open("ro")
    return op


@pytest.fixture
def op():
    return build()


@pytest.fixture
def make_op():
    return build
