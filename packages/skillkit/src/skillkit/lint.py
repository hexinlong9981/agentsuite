"""Consistency linter for skill directories and sub-agent definitions.

Layout understood (all parameterized):
    <root>/skills/<name>/SKILL.md   (or <root>/<name>/SKILL.md if no skills/ dir)
    <root>/agents/<name>.md
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from agent_policy_kit.paths import is_under

from .errors import ParseError
from .model import AgentDef, SkillDef, is_valid_name, parse_agent, parse_skill

ERROR, WARNING = "error", "warning"
SKILL_FILE = "SKILL.md"

_CODE_BULLET = re.compile(r"^\s*(?:[-*]|\d+\.)\s+`[^`\n]+`")
_CODE_ROW = re.compile(r"^\s*\|\s*`[^`\n]+`\s*\|")


@dataclass(frozen=True)
class Finding:
    target: str
    rule: str
    message: str
    severity: str = ERROR

    def __str__(self) -> str:
        return f"{self.severity.upper()} [{self.rule}] {self.target}: {self.message}"


@dataclass
class LintOptions:
    skills_registry: set[str] | None = None   # None: skills found under root (+ canonical)
    tools_registry: set[str] | None = None    # None: tool-existence check disabled
    enum_budget: int = 15                     # max code-span list items in a body
    agent_max_body_lines: int = 60
    canonical_dir: Path | None = None         # canonical skills dir for symlink checks
    guidelines_markers: tuple[str, ...] = ("guidelines", "AGENTS.md")
    guidelines_within_lines: int = 10         # marker must appear in the first N body lines


def load_registry(path: str | Path) -> set[str]:
    """Read a registry file: a JSON list, or one name per line (# comments)."""
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ParseError(f"registry {path} must be a JSON list")
        return {str(x) for x in data}
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")}


def count_enumerated_items(body: str) -> int:
    """Count list/table items led by a code span, ignoring fenced blocks."""
    n, fenced = 0, False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced and (_CODE_BULLET.match(line) or _CODE_ROW.match(line)):
            n += 1
    return n


def _tool_known(tool: str, registry: set[str]) -> bool:
    if tool in registry:
        return True
    return "*" in tool and any(fnmatch.fnmatchcase(r, tool) for r in registry)


def _check_refs(target: str, skills: list[str], tools: list[str], opts: LintOptions,
                skills_reg: set[str]) -> list[Finding]:
    out = [Finding(target, "ref-missing-skill", f"references unknown skill {s!r}")
           for s in skills if s not in skills_reg]
    if opts.tools_registry is not None:
        out += [Finding(target, "ref-missing-tool", f"references unknown tool {t!r}")
                for t in tools if not _tool_known(t, opts.tools_registry)]
    return out


def _skills_dir(root: Path) -> Path:
    return root / "skills" if (root / "skills").is_dir() else root


def discover_skill_names(root: Path) -> set[str]:
    d = _skills_dir(root)
    if not d.is_dir():
        return set()
    return {p.name for p in d.iterdir() if (p / SKILL_FILE).exists()}


def lint_skill(entry: Path, opts: LintOptions, skills_reg: set[str]) -> list[Finding]:
    """Lint one skill entry (a directory, or a symlink to one)."""
    target = str(entry)
    out: list[Finding] = []
    skill_file = entry / SKILL_FILE
    if entry.is_symlink() or skill_file.is_symlink():
        if not skill_file.exists():
            return [Finding(target, "symlink-broken", "symlink does not resolve to an existing SKILL.md")]
        if opts.canonical_dir is not None and not is_under(skill_file, opts.canonical_dir):
            out.append(Finding(target, "symlink-outside-canonical",
                               f"link resolves outside canonical dir {opts.canonical_dir}"))
    elif opts.canonical_dir is not None and (Path(opts.canonical_dir) / entry.name / SKILL_FILE).exists():
        out.append(Finding(target, "symlink-copy",
                           "plain copy of a canonical skill; replace with a symlink to avoid drift"))
    if not skill_file.exists():
        return out + [Finding(target, "skill-file-missing", f"{SKILL_FILE} not found")]
    try:
        sd: SkillDef = parse_skill(skill_file.read_text(encoding="utf-8"), skill_file)
    except ParseError as exc:
        return out + [Finding(target, "frontmatter", str(exc))]
    if sd.name != entry.name:
        out.append(Finding(target, "name-mismatch", f"name {sd.name!r} != directory {entry.name!r}"))
    out += _check_refs(target, sd.skills, sd.allowed_tools, opts, skills_reg)
    n = count_enumerated_items(sd.body)
    if n > opts.enum_budget:
        out.append(Finding(target, "enum-budget",
                           f"{n} enumerated items exceed budget {opts.enum_budget}; "
                           "authoritative lists belong in config, not prose", WARNING))
    return out


def lint_agent(path: Path, opts: LintOptions, skills_reg: set[str]) -> list[Finding]:
    target = str(path)
    if path.is_symlink() and not path.exists():
        return [Finding(target, "symlink-broken", "symlink does not resolve to an existing file")]
    try:
        ad: AgentDef = parse_agent(path.read_text(encoding="utf-8"), path)
    except ParseError as exc:
        return [Finding(target, "frontmatter", str(exc))]
    out: list[Finding] = []
    if ad.name != path.stem:
        out.append(Finding(target, "name-mismatch", f"name {ad.name!r} != file name {path.stem!r}"))
    out += _check_refs(target, ad.skills, ad.tools, opts, skills_reg)
    lines = ad.body.splitlines()
    if len(lines) > opts.agent_max_body_lines:
        out.append(Finding(target, "agent-fat",
                           f"body has {len(lines)} lines (> {opts.agent_max_body_lines}); "
                           "keep agent definitions thin, put procedure in skills"))
    if ad.omit_claude_md:
        head = "\n".join([ln for ln in lines if ln.strip()][: opts.guidelines_within_lines])
        if not any(m in head for m in opts.guidelines_markers):
            out.append(Finding(target, "agent-omit-guidelines",
                               "omitClaudeMd is set but the body does not reference reading a shared "
                               f"guidelines file first (markers: {', '.join(opts.guidelines_markers)})"))
    return out


def lint_tree(root: str | Path, opts: LintOptions | None = None) -> list[Finding]:
    """Lint every skill and agent under ``root``. Missing root is a finding."""
    opts = opts or LintOptions()
    root_p = Path(root)
    if not root_p.is_dir():
        return [Finding(str(root_p), "root-missing", "root directory does not exist")]
    skills_reg = set(opts.skills_registry) if opts.skills_registry is not None else (
        discover_skill_names(root_p)
        | (discover_skill_names(Path(opts.canonical_dir)) if opts.canonical_dir else set()))
    out: list[Finding] = []
    sdir = _skills_dir(root_p)
    for entry in sorted(sdir.iterdir()):
        if entry.name.startswith(".") or entry.name == "agents" and sdir == root_p:
            continue
        if entry.is_dir() or (entry.is_symlink() and not entry.exists()):
            out += lint_skill(entry, opts, skills_reg)
    adir = root_p / "agents"
    if adir.is_dir():
        for p in sorted(adir.iterdir()):
            if p.suffix == ".md" and not p.name.startswith("."):
                out += lint_agent(p, opts, skills_reg)
    return out


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity == ERROR for f in findings)
