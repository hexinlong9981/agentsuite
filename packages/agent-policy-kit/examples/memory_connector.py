"""A tiny read-only connector over an in-memory dataset (demo + tests).

Shows how a provider becomes a thin instance: implement read operations in
``_dispatch``; scope, audit and masking are handled by the base class.
"""

from __future__ import annotations

from agent_policy_kit import ReadOnlyConnector


class MemoryConnector(ReadOnlyConnector):
    def __init__(self, data: dict[str, dict], **kwargs) -> None:
        super().__init__(**kwargs)
        self.data = data

    def _dispatch(self, resource: str, operation: str, **params):
        bucket = self.data.get(resource, {})
        if operation == "list":
            return sorted(bucket.keys())
        if operation == "read":
            return bucket.get(params.get("key"))
        if operation == "search":
            q = params.get("query", "")
            return [k for k in bucket if q in k]
        if operation == "create":        # a write op (approval-gated by the base)
            self.data.setdefault(resource, {})[params["key"]] = params["value"]
            return {"created": params["key"]}
        raise ValueError(f"unsupported operation: {operation}")
