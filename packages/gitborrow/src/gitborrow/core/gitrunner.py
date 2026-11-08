"""Run git through the push guard, inject credentials into the subprocess only,
and scrub known secret shapes from captured output before it is used/logged."""

from __future__ import annotations

import os
import re
import subprocess

from . import guard
from .config import Config
from .credentials import CredentialStore, NullCredentialStore
from .errors import FetchTimeout

_SCRUB = [
    re.compile(r"(?i)([a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s/@]+(@)"),
]


def scrub(text: str) -> str:
    for pat in _SCRUB:
        text = pat.sub(r"\1***\2", text)
    return text


def run_git(argv: list[str], *, config: Config, cwd=None,
            store: CredentialStore | None = None, timeout: int = 120,
            network: bool = False) -> tuple[int, str, str]:
    """Run ``git <argv>``. Raises GuardViolation before exec if disallowed."""
    guard.validate_git_command(argv, enforce=config.push_guard.enabled)

    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if network:
        store = store or NullCredentialStore()
        url = next((a for a in argv if "://" in a or a.startswith("git@")), "")
        env.update(store.resolve(url).env)

    try:
        proc = subprocess.run(
            ["git", *argv],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise FetchTimeout(f"git {argv[0]} exceeded {timeout}s") from None
    return proc.returncode, scrub(proc.stdout), scrub(proc.stderr)
