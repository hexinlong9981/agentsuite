"""The mailbox connector — a thin ReadOnlyConnector over a mail backend."""

from __future__ import annotations

from agent_policy_kit import ReadOnlyConnector

from .backends import MailBackend, make_backend
from .config import Config


class MailboxConnector(ReadOnlyConnector):
    def __init__(self, backend: MailBackend, **kwargs) -> None:
        super().__init__(**kwargs)
        self.backend = backend

    @classmethod
    def from_config(cls, config: Config) -> "MailboxConnector":
        return cls(
            make_backend(config.backend_type, config.backend_options),
            scope=config.scope, audit=config.audit, masker=config.masker, name="mailpeek",
        )

    def allowed_folders(self) -> list[str]:
        return sorted(self.scope.resources)

    def _dispatch(self, resource: str, operation: str, **params):
        if operation == "list":
            return self.backend.list_messages(resource)
        if operation == "read":
            return self.backend.read_message(resource, params["message_id"])
        if operation == "search":
            return self.backend.search(resource, params.get("query", ""))
        if operation == "download":
            return self.backend.download_attachment(resource, params["message_id"], params["name"])
        raise ValueError(f"unsupported operation: {operation}")
