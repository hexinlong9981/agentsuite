"""playbook — generic operating principles for AI agents.

A business-agnostic, reusable set of principles for how an AI agent should work
(delegation, commit hygiene, gating risky/external/irreversible actions,
deliverable style, progress reporting, conversation-cost discipline), exposed as
structured data plus a rendered guide and a light self-check. Built on the
agent-policy-kit core.
"""

from __future__ import annotations

__version__ = "0.0.1"

from .check import AuditResult, CheckResult, audit_text, check
from .doc import render
from .errors import InvalidPrinciple, PlaybookError, PrincipleNotFound
from .principles import PRINCIPLES, TAGS, Principle, get, ids

__all__ = [
    "__version__", "Principle", "PRINCIPLES", "TAGS", "get", "ids", "render",
    "check", "audit_text", "CheckResult", "AuditResult",
    "PlaybookError", "PrincipleNotFound", "InvalidPrinciple",
]
