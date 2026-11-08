"""Filesystem store: one file per fact, plus a one-line-per-item index."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent_policy_kit import AuditLog, SecretMasker, contained_join, is_under

from .errors import ItemExists, ItemNotFound, ValidationError
from .model import Item, parse, serialize, validate_name

ITEMS_DIR = "items"
INBOX_DIR = "_inbox"
INDEX_FILE = "INDEX.md"
INDEX_HEADER = "# Index\n\n"
_INDEX_LINE = re.compile(r"^- \[([^\]]+)\]\(([^)]+)\) — (.*)$")


@dataclass(frozen=True)
class Issue:
    code: str       # index-missing-entry | index-stale-entry | index-mismatch | bad-front-matter | name-mismatch | dangling-link
    name: str
    message: str
    severity: str = "error"   # "error" | "warning"


class KnowledgeBase:
    def __init__(self, root: str, *, audit: AuditLog | None = None, masker: SecretMasker | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.audit = audit or AuditLog(None)
        self.masker = masker or SecretMasker()

    # -- paths -----------------------------------------------------------
    @property
    def items_dir(self) -> Path:
        return contained_join(str(self.root), ITEMS_DIR)

    @property
    def inbox_dir(self) -> Path:
        return contained_join(str(self.root), INBOX_DIR)

    @property
    def index_path(self) -> Path:
        return contained_join(str(self.root), INDEX_FILE)

    def _path(self, directory: Path, name: str) -> Path:
        validate_name(name)
        p = contained_join(str(directory), f"{name}.md")
        if not is_under(p, directory):  # defence in depth
            raise ValidationError("path escapes store")
        return p

    def item_path(self, name: str) -> Path:
        return self._path(self.items_dir, name)

    def _mask(self, item: Item) -> Item:
        return Item(item.name, self.masker.mask_text(item.description), item.type,
                    list(item.links), self.masker.mask_text(item.body))

    # -- read ------------------------------------------------------------
    def names(self) -> list[str]:
        d = self.items_dir
        return sorted(p.stem for p in d.glob("*.md")) if d.is_dir() else []

    def get(self, name: str) -> Item:
        p = self.item_path(name)
        if not p.is_file():
            raise ItemNotFound(name)
        return parse(p.read_text(encoding="utf-8"))

    def exists(self, name: str) -> bool:
        return self.item_path(name).is_file()

    def list(self) -> list[Item]:
        """All well-formed items (malformed files are reported by check_consistency)."""
        out = []
        for n in self.names():
            try:
                out.append(self.get(n))
            except ValidationError:
                continue
        return out

    # -- mutate ----------------------------------------------------------
    def _write(self, directory: Path, item: Item) -> Path:
        item = self._mask(item).validate()
        p = self._path(directory, item.name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(serialize(item), encoding="utf-8")
        return p

    def add(self, item: Item, *, overwrite: bool = False) -> Path:
        item.validate()
        if self.exists(item.name) and not overwrite:
            self.audit.deny("exists", op="add", name=item.name)
            raise ItemExists(item.name)
        p = self._write(self.items_dir, item)
        self.build_index()
        self.audit.allow(op="add", name=item.name)
        return p

    def update(self, name: str, /, **fields) -> Item:
        cur = self.get(name)
        allowed = {"description", "type", "links", "body"}
        bad = set(fields) - allowed
        if bad:
            raise ValidationError(f"cannot update fields: {sorted(bad)}")
        new = Item(cur.name, fields.get("description", cur.description), fields.get("type", cur.type),
                   list(fields.get("links", cur.links)), fields.get("body", cur.body))
        self._write(self.items_dir, new)
        self.build_index()
        self.audit.allow(op="update", name=name, fields=sorted(fields))
        return self.get(name)

    def remove(self, name: str) -> None:
        p = self.item_path(name)
        if not p.is_file():
            raise ItemNotFound(name)
        p.unlink()
        self.build_index()
        self.audit.allow(op="remove", name=name)

    # -- index -----------------------------------------------------------
    def build_index(self) -> str:
        lines = [f"- [{i.name}]({ITEMS_DIR}/{i.name}.md) — {i.description}" for i in self.list()]
        text = INDEX_HEADER + "\n".join(lines) + ("\n" if lines else "")
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(text, encoding="utf-8")
        return text

    def read_index(self) -> dict[str, str]:
        p = self.index_path
        if not p.is_file():
            return {}
        out = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            m = _INDEX_LINE.match(line)
            if m:
                out[m.group(1)] = m.group(3)
        return out

    # -- consistency -----------------------------------------------------
    def check_consistency(self) -> list[Issue]:
        issues: list[Issue] = []
        good: dict[str, Item] = {}
        for n in self.names():
            try:
                it = self.get(n)
            except ValidationError as exc:
                issues.append(Issue("bad-front-matter", n, str(exc)))
                continue
            if it.name != n:
                issues.append(Issue("name-mismatch", n, f"front-matter name {it.name!r} != file name"))
                continue
            good[n] = it
        index = self.read_index()
        for n, it in good.items():
            if n not in index:
                issues.append(Issue("index-missing-entry", n, "item is not listed in the index"))
            elif index[n] != it.description:
                issues.append(Issue("index-mismatch", n, "index description differs from the item"))
        for n in index:
            if n not in good and n not in self.names():
                issues.append(Issue("index-stale-entry", n, "index lists an item with no file"))
        for n, it in good.items():
            for ln in it.links:
                if ln not in good:
                    issues.append(Issue("dangling-link", n, f"links to unknown item {ln!r}", "warning"))
        return issues
