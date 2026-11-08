"""Path containment helpers (shared core).

- ``contained_join`` joins a relative path under a root and refuses any result
  that would escape the root (traversal).
- ``safe_name`` reduces an arbitrary string to a safe single path segment.
"""

from __future__ import annotations

import re
from pathlib import Path

from .errors import PolicyKitError

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class PathEscape(PolicyKitError):
    """A path would escape its root directory."""


def safe_name(name: str, *, default: str = "file") -> str:
    return _SAFE.sub("_", name).strip("_") or default


def contained_join(root: str, relpath: str) -> Path:
    root_p = Path(root).expanduser().resolve()
    dest = (root_p / relpath).resolve()
    try:
        dest.relative_to(root_p)
    except ValueError:
        raise PathEscape(f"path {relpath!r} escapes root {root_p}") from None
    return dest


def is_under(child, parent) -> bool:
    """True if ``child`` resolves to ``parent`` or a path inside it."""
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False
