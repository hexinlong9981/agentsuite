# skillkit

Conventions, a consistency **linter**, and a **scaffolder** for authoring agent
skills and sub-agent definitions — business-agnostic.

- **Schema**: a skill is a directory with `SKILL.md` (YAML front-matter: `name`,
  `description`, optional trigger/`allowed-tools`) + a body; a sub-agent is a
  markdown file with front-matter (`name`, `description`, `tools`, `skills`,
  `model`, optional `omitClaudeMd`) + a thin body.
- **Lint** (`lint_tree`): front-matter validity, name/dir match, referenced
  skills/tools exist (fail-closed against a registry), one-source-of-truth
  enumeration budget (warning), symlink integrity (link not a drifting copy), a
  thin-agent body budget, and an `omitClaudeMd`-must-reference-guidelines rule.
- **Scaffold**: create a new skill or sub-agent from templates, path-contained.
- **CLI**: `skillkit lint <root>`, `skillkit check <root>`, `skillkit scaffold
  skill|agent <name> --root ...`.

Built on `agent-policy-kit` (errors base, path containment, CLI exit codes). See
[`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).
