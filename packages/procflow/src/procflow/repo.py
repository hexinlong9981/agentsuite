"""Load definitions, interfaces and config files from a *sop-root* directory.

Layout::

    <root>/**/*.yaml            procedure definitions (directories starting with ``_`` are skipped)
    <root>/_interfaces/IF-*.yaml  interface contracts
    <root>/_config/runtime.yaml   layer 2: suite-wide defaults / caps / env blocks
    <root>/_config/procedures/<ID>.yaml   layer 4: per-procedure config

A procedure's id is the ``sop.id`` inside the file (the file name is free).
"""

from __future__ import annotations

import hashlib
from functools import cached_property
from pathlib import Path

import yaml


def blob_sha(data: bytes) -> str:
    """git blob sha1 of ``data`` (what ``git hash-object`` prints), computed without git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 (identifier, not security)


class Repo:
    def __init__(self, root, interfaces_dir=None, config_dir=None):
        self.root = Path(root)
        self.interfaces_dir = Path(interfaces_dir) if interfaces_dir else self.root / "_interfaces"
        self.config_dir = Path(config_dir) if config_dir else self.root / "_config"
        self.load_errors: list[tuple[str, str]] = []
        self._defs: dict = {}

    @cached_property
    def _loaded(self):
        paths, defs = {}, {}
        files = sorted(list(self.root.rglob("*.yaml")) + list(self.root.rglob("*.yml")))
        for p in files:
            rel = p.relative_to(self.root).parts
            if any(part.startswith("_") for part in rel):
                continue
            try:
                d = yaml.safe_load(p.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as x:
                self.load_errors.append((str(p), f"cannot read as YAML: {x}"))
                continue
            sop = d.get("sop") if isinstance(d, dict) else None
            sid = sop.get("id") if isinstance(sop, dict) else None
            if not isinstance(sid, str):
                self.load_errors.append((str(p), "not a procedure definition (no sop.id)"))
                continue
            if sid in paths:
                self.load_errors.append((str(p), f"duplicate procedure id {sid} (also in {paths[sid].name})"))
                continue
            paths[sid], defs[sid] = p, d
        return paths, defs

    @property
    def sop_paths(self):
        return self._loaded[0]

    def ids(self):
        return sorted(self.sop_paths)

    def path(self, sop_id):
        return self.sop_paths.get(sop_id)

    def definition(self, sop_id):
        """The definition dict (KeyError if unknown)."""
        d = self._loaded[1].get(sop_id)
        if d is None:
            raise KeyError(sop_id)
        return d

    def text(self, sop_id):
        return self.sop_paths[sop_id].read_text(encoding="utf-8")

    def source_sha256(self, sop_id):
        return hashlib.sha256(self.text(sop_id).encode("utf-8")).hexdigest()

    @cached_property
    def interface_paths(self):
        if not self.interfaces_dir.is_dir():
            return {}
        return {p.stem: p for p in sorted(self.interfaces_dir.glob("IF-*.yaml"))}

    def interface(self, if_id):
        """The interface dict, or None if absent/unreadable."""
        p = self.interface_paths.get(if_id)
        if p is None:
            return None
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        return d if isinstance(d, dict) else None

    @property
    def runtime_path(self):
        return self.config_dir / "runtime.yaml"

    def sop_config_path(self, sop_id):
        return self.config_dir / "procedures" / f"{sop_id}.yaml"

    def sop_config_paths(self):
        d = self.config_dir / "procedures"
        return sorted(d.glob("*.yaml")) if d.is_dir() else []
