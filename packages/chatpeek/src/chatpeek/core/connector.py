"""The chat connector — a thin ReadOnlyConnector over a chat backend."""

from __future__ import annotations

from agent_policy_kit import ReadOnlyConnector

from .backends import ChatBackend, make_backend
from .config import Config


class ChatConnector(ReadOnlyConnector):
    def __init__(self, backend: ChatBackend, **kwargs) -> None:
        super().__init__(**kwargs)
        self.backend = backend

    @classmethod
    def from_config(cls, config: Config, approval=None) -> "ChatConnector":
        return cls(
            make_backend(config.backend_type, config.backend_options),
            scope=config.scope, audit=config.audit, masker=config.masker, name="chatpeek",
            approval=approval,
        )

    def post(self, channel: str, text: str):
        return self.operate(channel, "send", text=text)

    def allowed_channels(self) -> list[str]:
        return sorted(self.scope.resources)

    def _dispatch(self, resource: str, operation: str, **params):
        if operation == "list":
            return self.backend.list_messages(resource)
        if operation == "read":
            return self.backend.read_message(resource, params["message_id"])
        if operation == "search":
            return self.backend.search(resource, params.get("query", ""))
        if operation == "download":
            return self.backend.download_file(resource, params["message_id"], params["name"])
        if operation == "send":
            return self.backend.post_message(resource, params["text"])
        raise ValueError(f"unsupported operation: {operation}")
