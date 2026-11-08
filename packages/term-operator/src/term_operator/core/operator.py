"""The operator: policy (session x op) + command allowlist + approval + audit."""

from __future__ import annotations

from agent_policy_kit import AuditLog, DenyAll
from cmdgate.core import evaluate as evaluate_command

from .backends import make_backend
from .config import Config
from .errors import CommandNotAllowed, SendNotApproved


class TermOperator:
    def __init__(self, *, backend, scope, command_policy, approval=None,
                 audit=None, masker=None, name: str = "term-operator") -> None:
        self.backend = backend
        self.scope = scope
        self.command_policy = command_policy
        self.approval = approval or DenyAll()
        self.audit = audit or AuditLog(None)
        self.masker = masker
        self.name = name

    @classmethod
    def from_config(cls, config: Config, approval=None) -> "TermOperator":
        return cls(backend=make_backend(config.backend_type, config.backend_options),
                   scope=config.scope, command_policy=config.command_policy,
                   approval=approval, audit=config.audit, masker=config.masker)

    def _mask(self, text):
        return self.masker.mask_text(text) if (self.masker and isinstance(text, str)) else text

    def _require(self, session: str, operation: str):
        try:
            self.scope.require(session, operation)
        except Exception as exc:
            self.audit.record({"action": operation, "session": session,
                               "result": "denied", "reason": type(exc).__name__})
            raise

    def open(self, session: str):
        self._require(session, "open")
        self.audit.record({"action": "open", "session": session, "result": "ok"})
        return self.backend.open(session)

    def read(self, session: str):
        self._require(session, "read")
        out = self.backend.read(session)
        self.audit.record({"action": "read", "session": session, "result": "ok"})
        return self._mask(out)

    def close(self, session: str):
        self._require(session, "close")
        self.audit.record({"action": "close", "session": session, "result": "ok"})
        return self.backend.close(session)

    def send(self, session: str, argv: list[str]):
        self._require(session, "send")

        decision = evaluate_command(argv, self.command_policy)
        if not decision.allowed:
            self.audit.record({"action": "send", "session": session,
                               "result": "command_denied", "reason": decision.reason})
            raise CommandNotAllowed(decision.reason)

        command = " ".join(argv)
        if not self.approval.request(session, "send", command):
            self.audit.record({"action": "send", "session": session,
                               "result": "not_approved", "command": self._mask(command)})
            raise SendNotApproved(f"send not approved: {command}")

        output = self.backend.send(session, command)
        self.audit.record({"action": "send", "session": session,
                           "result": "ok", "command": self._mask(command)})
        return self._mask(output)
