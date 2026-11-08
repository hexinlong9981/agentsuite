"""Item model: one fact = one Markdown file with YAML front-matter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from .errors import ValidationError

ITEM_TYPES = ("user", "feedback", "project", "reference")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 300


def validate_name(name: object) -> str:
    if not isinstance(name, str) or not name:
        raise ValidationError("name must be a non-empty string")
    if len(name) > MAX_NAME_LEN or not SLUG_RE.match(name):
        raise ValidationError(f"name {name!r} is not a kebab-case slug (a-z, 0-9, '-'; max {MAX_NAME_LEN})")
    return name


@dataclass
class Item:
    name: str
    description: str
    type: str
    links: list[str] = field(default_factory=list)
    body: str = ""

    def validate(self) -> "Item":
        validate_name(self.name)
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValidationError("description is required")
        if "\n" in self.description or "\r" in self.description:
            raise ValidationError("description must be a single line")
        if len(self.description) > MAX_DESCRIPTION_LEN:
            raise ValidationError(f"description exceeds {MAX_DESCRIPTION_LEN} chars")
        if self.type not in ITEM_TYPES:
            raise ValidationError(f"type {self.type!r} not in {ITEM_TYPES}")
        if not isinstance(self.links, list):
            raise ValidationError("links must be a list")
        for ln in self.links:
            validate_name(ln)
        if self.name in self.links:
            raise ValidationError("an item must not link to itself")
        if not isinstance(self.body, str):
            raise ValidationError("body must be text")
        return self

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "type": self.type,
                "links": list(self.links), "body": self.body}


def serialize(item: Item) -> str:
    item.validate()
    fm = yaml.safe_dump(
        {"name": item.name, "description": item.description, "type": item.type, "links": list(item.links)},
        sort_keys=False, allow_unicode=True, default_flow_style=False, width=10_000,
    )
    return f"---\n{fm}---\n\n{item.body.strip()}\n"


def parse(text: str) -> Item:
    """Parse a file's text into a validated Item. Raises ValidationError."""
    if not text.startswith("---"):
        raise ValidationError("missing front-matter (file must start with '---')")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise ValidationError("front-matter is not closed with '---'")
    head = parts[0][3:]
    rest = parts[1]
    if rest.startswith("\n"):
        rest = rest[1:]
    try:
        data = yaml.safe_load(head)
    except yaml.YAMLError as exc:
        raise ValidationError(f"front-matter is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise ValidationError("front-matter must be a mapping")
    for key in ("name", "description", "type"):
        if key not in data:
            raise ValidationError(f"front-matter missing required field {key!r}")
    links = data.get("links") or []
    return Item(name=data["name"], description=data["description"], type=data["type"],
                links=links, body=rest.strip()).validate()
