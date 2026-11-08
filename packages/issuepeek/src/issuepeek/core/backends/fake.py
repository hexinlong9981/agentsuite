"""In-memory fake issue backend for demos and offline tests.

``projects`` shape::

    {
      "DEMO": {
        "issues": [{"id": "1", "title": "...", "reporter": "...", "description": "..."}],
        "attachments": {("1", "a.txt"): b"bytes"},
      }
    }
"""

from __future__ import annotations

from ..errors import BackendError
from . import IssueBackend


class FakeIssueBackend(IssueBackend):
    def __init__(self, projects: dict | None = None) -> None:
        self.projects = projects or {}

    def list_projects(self) -> list[str]:
        return sorted(self.projects)

    def list_issues(self, project: str) -> list[dict]:
        return [{"id": i["id"], "title": i.get("title", ""), "reporter": i.get("reporter", "")}
                for i in self.projects.get(project, {}).get("issues", [])]

    def read_issue(self, project: str, issue_id: str) -> dict:
        for i in self.projects.get(project, {}).get("issues", []):
            if i["id"] == issue_id:
                return dict(i)
        raise BackendError(f"issue not found: {issue_id}")

    def search(self, project: str, query: str) -> list[dict]:
        out = []
        for i in self.projects.get(project, {}).get("issues", []):
            hay = f"{i.get('title', '')} {i.get('description', '')} {i.get('reporter', '')}"
            if query in hay:
                out.append({"id": i["id"], "title": i.get("title", "")})
        return out

    def download_attachment(self, project: str, issue_id: str, name: str) -> bytes:
        att = self.projects.get(project, {}).get("attachments", {})
        if (issue_id, name) in att:
            return att[(issue_id, name)]
        raise BackendError(f"attachment not found: {issue_id}/{name}")

    def create_issue(self, project: str, fields: dict) -> dict:
        issues = self.projects.setdefault(project, {}).setdefault("issues", [])
        new_id = str(len(issues) + 1)
        issues.append({"id": new_id, **fields})
        return {"created": new_id, "project": project}
