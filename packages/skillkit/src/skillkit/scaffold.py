"""Scaffolder: create a skill directory or an agent file from templates."""

from __future__ import annotations

from pathlib import Path

from agent_policy_kit.paths import PathEscape, contained_join

from .errors import ScaffoldError
from .model import is_valid_name

_CANDIDATES = (Path(__file__).resolve().parents[2] / "templates", Path(__file__).resolve().parent / "templates")


def _template(filename: str) -> str:
    for d in _CANDIDATES:
        if (d / filename).is_file():
            return (d / filename).read_text(encoding="utf-8")
    raise ScaffoldError(f"template {filename} not found")


def _render(tmpl: str, name: str, description: str) -> str:
    return tmpl.replace("{{name}}", name).replace("{{description}}", description)


def _check(name: str, description: str) -> str:
    if not is_valid_name(name):
        raise ScaffoldError(f"invalid name {name!r} (lowercase slug like 'my-skill')")
    desc = " ".join(description.split())
    if not desc or any(c in desc for c in "\n\r"):
        raise ScaffoldError("description must be a non-empty single line")
    return desc if ": " not in desc and not desc.startswith(("-", "[", "{", "#", "'", '"')) else '"' + desc.replace('"', "'") + '"'


def _write(root: str | Path, rel: str, content: str, overwrite: bool) -> Path:
    try:
        dest = contained_join(str(root), rel)
    except PathEscape as exc:
        raise ScaffoldError(str(exc)) from None
    if dest.exists() and not overwrite:
        raise ScaffoldError(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


def scaffold_skill(root: str | Path, name: str, description: str = "TODO: describe what this skill does and when to use it",
                   *, overwrite: bool = False) -> Path:
    """Create ``<root>/skills/<name>/SKILL.md``; returns the file path."""
    desc = _check(name, description)
    return _write(root, f"skills/{name}/SKILL.md", _render(_template("SKILL.md.tmpl"), name, desc), overwrite)


def scaffold_agent(root: str | Path, name: str, description: str = "TODO: describe when to delegate to this agent",
                   *, overwrite: bool = False) -> Path:
    """Create ``<root>/agents/<name>.md``; returns the file path."""
    desc = _check(name, description)
    return _write(root, f"agents/{name}.md", _render(_template("agent.md.tmpl"), name, desc), overwrite)
