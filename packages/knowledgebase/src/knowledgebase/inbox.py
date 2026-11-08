"""Inbox: where proposed/learned facts wait for review before reaching the shelf."""

from __future__ import annotations

from .errors import ItemExists, ItemNotFound, ValidationError
from .model import Item, parse
from .store import KnowledgeBase


class Inbox:
    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb

    def _path(self, name: str):
        return self.kb._path(self.kb.inbox_dir, name)

    def propose(self, item: Item, *, overwrite: bool = False):
        """Deposit an item in the inbox (never on the shelf)."""
        item.validate()
        if self._path(item.name).is_file() and not overwrite:
            self.kb.audit.deny("exists", op="propose", name=item.name)
            raise ItemExists(item.name)
        p = self.kb._write(self.kb.inbox_dir, item)
        self.kb.audit.allow(op="propose", name=item.name)
        return p

    def names(self) -> list[str]:
        d = self.kb.inbox_dir
        return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []

    def get(self, name: str) -> Item:
        p = self._path(name)
        if not p.is_file():
            raise ItemNotFound(name)
        return parse(p.read_text(encoding="utf-8"))

    def list_inbox(self) -> list[Item]:
        out = []
        for n in self.names():
            try:
                out.append(self.get(n))
            except ValidationError:
                continue
        return out

    def promote(self, name: str, *, overwrite: bool = False) -> Item:
        """Move a proposal onto the shelf and refresh the index."""
        item = self.get(name)
        if self.kb.exists(name) and not overwrite:
            self.kb.audit.deny("exists", op="promote", name=name)
            raise ItemExists(name)
        self.kb._write(self.kb.items_dir, item)
        self._path(name).unlink()
        self.kb.build_index()
        self.kb.audit.allow(op="promote", name=name)
        return item

    def reject(self, name: str) -> None:
        p = self._path(name)
        if not p.is_file():
            raise ItemNotFound(name)
        p.unlink()
        self.kb.audit.allow(op="reject", name=name)
