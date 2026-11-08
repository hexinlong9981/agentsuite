"""The issue-tracker connector — a thin ReadOnlyConnector over an issue backend."""

from __future__ import annotations

from agent_policy_kit import ReadOnlyConnector

from .backends import IssueBackend, make_backend
from .config import Config


class IssueTrackerConnector(ReadOnlyConnector):
    def __init__(self, backend: IssueBackend, **kwargs) -> None:
        super().__init__(**kwargs)
        self.backend = backend

    @classmethod
    def from_config(cls, config: Config, approval=None) -> "IssueTrackerConnector":
        return cls(
            make_backend(config.backend_type, config.backend_options),
            scope=config.scope, audit=config.audit, masker=config.masker, approval=approval, name="issuepeek",
        )

    def allowed_projects(self) -> list[str]:
        return sorted(self.scope.resources)

    def create_issue(self, project: str, **fields):
        return self.operate(project, "create", fields=fields)

    def _dispatch(self, resource: str, operation: str, **params):
        if operation == "list":
            return self.backend.list_issues(resource)
        if operation == "read":
            return self.backend.read_issue(resource, params["issue_id"])
        if operation == "search":
            return self.backend.search(resource, params.get("query", ""))
        if operation == "download":
            return self.backend.download_attachment(resource, params["issue_id"], params["name"])
        if operation == "create":
            return self.backend.create_issue(resource, params.get("fields") or {})
        raise ValueError(f"unsupported operation: {operation}")
