"""Include/exclude matching and path classification."""

from __future__ import annotations

import fnmatch

from .config import Policy


def _norm(pattern: str) -> str:
    # fnmatch '*' already spans '/', so '**' is treated like '*' here.
    return pattern.replace("**", "*")


def matches(relpath: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relpath, _norm(p)) for p in patterns)


def classify(relpath: str, policy: Policy) -> str:
    for rule in policy.classify:
        if fnmatch.fnmatch(relpath, _norm(rule.pattern)):
            return rule.category
    return policy.default_category
