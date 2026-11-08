"""CLI helpers shared across the suite's command-line adapters."""

from __future__ import annotations


def exit_code_for(exc: BaseException, mapping: dict[type, int], default: int = 1) -> int:
    """Map an exception to a process exit code via an {exc_type: code} mapping.

    The first matching entry (by isinstance) wins; ``default`` if none match.
    """
    for cls, code in mapping.items():
        if isinstance(exc, cls):
            return code
    return default
