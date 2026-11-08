"""Pillar 4 — credential isolation (built on agent_policy_kit.credentials).

Shares the store abstraction and handle with the rest of the suite; keeps only
the git-specific SSH-identity store here.
"""

from __future__ import annotations

import os

from agent_policy_kit.credentials import (
    CredentialHandle,
    CredentialStore,
    NullCredentialStore,
)

from .config import Config
from .errors import CredentialError


class EnvCredentialStore(CredentialStore):
    """SSH identity supplied as a *path* via ``GITBORROW_SSH_KEY``.

    The key material itself is never read by gitborrow; git uses it directly.
    """

    def resolve(self, target: str) -> CredentialHandle:
        key = os.environ.get("GITBORROW_SSH_KEY")
        if not key:
            return CredentialHandle(env={})
        if not os.path.isfile(key):
            # Note: message never contains the path's contents.
            raise CredentialError("configured SSH key is not a readable file")
        cmd = f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        return CredentialHandle(env={"GIT_SSH_COMMAND": cmd})


def make_store(config: Config) -> CredentialStore:
    if config.credentials.source == "env":
        return EnvCredentialStore()
    # 'keyring' / 'file' land later; fall back to Null (documented) for now.
    return NullCredentialStore()
