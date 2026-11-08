"""Local filesystem transport (read-only)."""

from __future__ import annotations

import os
from pathlib import Path

from ..errors import TransportError
from . import Transport


class LocalTransport(Transport):
    def __init__(self, root: str) -> None:
        self.root = Path(root).expanduser().resolve()

    def list(self) -> list[str]:
        out: list[str] = []
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                full = Path(dirpath) / name
                out.append(str(full.relative_to(self.root)))
        return sorted(out)

    def read(self, relpath: str) -> bytes:
        full = (self.root / relpath).resolve()
        try:
            full.relative_to(self.root)
        except ValueError:
            raise TransportError(f"path {relpath!r} escapes source root") from None
        return full.read_bytes()
