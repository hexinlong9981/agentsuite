"""Error hierarchy for knowledgebase."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class KnowledgeBaseError(PolicyKitError):
    """Base class for all knowledgebase errors."""


class ValidationError(KnowledgeBaseError):
    """An item (front-matter or name) is malformed."""


class ItemNotFound(KnowledgeBaseError):
    """No such item."""


class ItemExists(KnowledgeBaseError):
    """An item with that name already exists (and overwrite was not requested)."""
