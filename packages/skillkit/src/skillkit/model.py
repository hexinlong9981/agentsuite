"""Definition schema: SkillDef / AgentDef and their front-matter parsers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ParseError

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LEN = 64
_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.DOTALL)


def is_valid_name(name: object) -> bool:
    return isinstance(name, str) and len(name) <= MAX_NAME_LEN and bool(SLUG_RE.match(name))


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---`` YAML front-matter from the body. Raises ParseError."""
    m = _FM_RE.match(text.lstrip("﻿"))
    if not m:
        raise ParseError("missing or unterminated YAML front-matter (--- ... ---)")
    try:
        data = yaml.safe_load(m.group(1)) if m.group(1).strip() else {}
    except yaml.YAMLError as exc:
        raise ParseError(f"front-matter is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise ParseError("front-matter must be a YAML mapping")
    return data, m.group(2)


def _as_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v.strip() for v in value if v.strip()]
    raise ParseError(f"{key!r} must be a string or a list of strings")


def _require(data: dict[str, Any], keys: tuple[str, ...]) -> None:
    missing = [k for k in keys if not isinstance(data.get(k), str) or not data[k].strip()]
    if missing:
        raise ParseError(f"missing required front-matter key(s): {', '.join(missing)}")


@dataclass
class SkillDef:
    name: str
    description: str
    body: str = ""
    trigger: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)  # other skills this one relies on
    extra: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None


@dataclass
class AgentDef:
    name: str
    description: str
    body: str = ""
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    model: str | None = None
    omit_claude_md: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None


def parse_skill(text: str, path: Path | None = None) -> SkillDef:
    data, body = split_frontmatter(text)
    _require(data, ("name", "description"))
    if not is_valid_name(data["name"]):
        raise ParseError(f"invalid skill name {data['name']!r} (lowercase slug, <= {MAX_NAME_LEN} chars)")
    known = {"name", "description", "trigger", "when", "allowed-tools", "skills"}
    trig = data.get("trigger", data.get("when"))
    return SkillDef(
        name=data["name"], description=data["description"].strip(), body=body,
        trigger=str(trig).strip() if trig is not None else None,
        allowed_tools=_as_list(data.get("allowed-tools"), "allowed-tools"),
        skills=_as_list(data.get("skills"), "skills"),
        extra={k: v for k, v in data.items() if k not in known}, path=path,
    )


def parse_agent(text: str, path: Path | None = None) -> AgentDef:
    data, body = split_frontmatter(text)
    _require(data, ("name", "description"))
    if not is_valid_name(data["name"]):
        raise ParseError(f"invalid agent name {data['name']!r} (lowercase slug, <= {MAX_NAME_LEN} chars)")
    omit = data.get("omitClaudeMd", False)
    if not isinstance(omit, bool):
        raise ParseError("'omitClaudeMd' must be a boolean")
    model = data.get("model")
    known = {"name", "description", "tools", "skills", "model", "omitClaudeMd"}
    return AgentDef(
        name=data["name"], description=data["description"].strip(), body=body,
        tools=_as_list(data.get("tools"), "tools"), skills=_as_list(data.get("skills"), "skills"),
        model=str(model) if model is not None else None, omit_claude_md=omit,
        extra={k: v for k, v in data.items() if k not in known}, path=path,
    )


def load_skill(path: str | Path) -> SkillDef:
    p = Path(path)
    return parse_skill(p.read_text(encoding="utf-8"), p)


def load_agent(path: str | Path) -> AgentDef:
    p = Path(path)
    return parse_agent(p.read_text(encoding="utf-8"), p)
