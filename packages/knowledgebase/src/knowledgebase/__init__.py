"""knowledgebase — a store of small reusable facts an AI agent reads and grows.

One fact = one Markdown file with YAML front-matter (name / description / type /
links), an index, an inbox for proposed facts (grow-via-review), freshness
tracking, relevance recall and a consistency check. Built on agent-policy-kit
(audit, secret masking, path containment).
"""

from __future__ import annotations

from .errors import ItemExists, ItemNotFound, KnowledgeBaseError, ValidationError
from .freshness import Freshness, StaleEntry
from .inbox import Inbox
from .model import ITEM_TYPES, Item, parse, serialize
from .recall import Hit, recall
from .store import Issue, KnowledgeBase

__version__ = "0.0.1"

__all__ = [
    "Item", "ITEM_TYPES", "parse", "serialize", "KnowledgeBase", "Issue", "Inbox",
    "Freshness", "StaleEntry", "recall", "Hit",
    "KnowledgeBaseError", "ValidationError", "ItemNotFound", "ItemExists", "__version__",
]
