"""The file store connector — a thin ReadOnlyConnector over a file store backend."""

from __future__ import annotations

from agent_policy_kit import ReadOnlyConnector

from .backends import FileStoreBackend, make_backend
from .config import Config


class FileStoreConnector(ReadOnlyConnector):
    def __init__(self, backend: FileStoreBackend, **kwargs) -> None:
        super().__init__(**kwargs)
        self.backend = backend

    @classmethod
    def from_config(cls, config: Config, approval=None) -> "FileStoreConnector":
        return cls(
            make_backend(config.backend_type, config.backend_options),
            scope=config.scope, audit=config.audit, masker=config.masker, name="filepeek",
            **({"approval": approval} if approval is not None else {}),
        )

    def upload(self, folder: str, name: str, data: bytes):
        """Upload a file (kit operation "create"; needs approval and a non-read-only scope)."""
        return self.operate(folder, "create", name=name, data=data)

    def allowed_folders(self) -> list[str]:
        return sorted(self.scope.resources)

    def _dispatch(self, resource: str, operation: str, **params):
        if operation == "list":
            return self.backend.list_files(resource)
        if operation == "read":
            return self.backend.read_file(resource, params["file_id"])
        if operation == "search":
            return self.backend.search(resource, params.get("query", ""))
        if operation == "download":
            return self.backend.download(resource, params["file_id"])
        if operation == "create":
            return self.backend.upload(resource, params["name"], params["data"])
        raise ValueError(f"unsupported operation: {operation}")
