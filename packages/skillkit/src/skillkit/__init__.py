"""skillkit — conventions, linter, and scaffolder for authoring agent skills.

A business-agnostic toolkit for writing and maintaining agent skills and
sub-agent definitions: a definition schema, a consistency linter (front-matter
validity, referenced-skill/tool existence, one-source-of-truth, enumeration
budgets, symlink integrity), a scaffolder, and templates. Built on the
agent-policy-kit core.
"""

from __future__ import annotations

from .errors import ParseError, ScaffoldError, SkillkitError
from .lint import ERROR, WARNING, Finding, LintOptions, has_errors, lint_tree, load_registry
from .model import AgentDef, SkillDef, load_agent, load_skill, parse_agent, parse_skill
from .scaffold import scaffold_agent, scaffold_skill

__version__ = "0.0.1"

__all__ = [
    "SkillDef", "AgentDef", "parse_skill", "parse_agent", "load_skill", "load_agent",
    "Finding", "LintOptions", "lint_tree", "load_registry", "has_errors", "ERROR", "WARNING",
    "scaffold_skill", "scaffold_agent",
    "SkillkitError", "ParseError", "ScaffoldError",
]
