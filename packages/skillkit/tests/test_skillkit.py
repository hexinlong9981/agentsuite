import os

import pytest

from skillkit import (LintOptions, ParseError, ScaffoldError, lint_tree, parse_agent, parse_skill,
                      scaffold_agent, scaffold_skill)
from skillkit.cli import main

SKILL = "---\nname: {n}\ndescription: does a thing\n{extra}---\n\nBody\n"
AGENT = "---\nname: {n}\ndescription: delegate here\n{extra}---\n\n{body}\n"


def mk_skill(root, name, extra="", body=None):
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    text = SKILL.format(n=name, extra=extra)
    if body:
        text += body
    (d / "SKILL.md").write_text(text)
    return d


def mk_agent(root, name, extra="", body="Thin body."):
    d = root / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(AGENT.format(n=name, extra=extra, body=body))


def rules(root, **kw):
    return {f.rule for f in lint_tree(root, LintOptions(**kw))}


def test_parse_valid():
    s = parse_skill(SKILL.format(n="my-skill", extra="allowed-tools: Read, Grep\ntrigger: when asked\n"))
    assert s.name == "my-skill" and s.allowed_tools == ["Read", "Grep"] and s.trigger == "when asked"
    a = parse_agent(AGENT.format(n="my-agent", extra="tools: [Read]\nskills: [x]\nmodel: m\nomitClaudeMd: true\n", body="b"))
    assert a.tools == ["Read"] and a.skills == ["x"] and a.omit_claude_md and a.model == "m"


@pytest.mark.parametrize("text", ["no frontmatter", "---\nname: a\n", "---\n- list\n---\nx",
                                  "---\nname: Bad_Name\ndescription: d\n---\n", "---\nname: a\n---\n"])
def test_parse_invalid(text):
    with pytest.raises(ParseError):
        parse_skill(text)


def test_clean_tree(tmp_path):
    mk_skill(tmp_path, "alpha")
    mk_agent(tmp_path, "bot", "skills: [alpha]\ntools: [Read]\n")
    assert lint_tree(tmp_path, LintOptions(tools_registry={"Read"})) == []


def test_missing_key_and_bad_yaml(tmp_path):
    d = tmp_path / "skills" / "alpha"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: alpha\n---\nx")
    assert "frontmatter" in rules(tmp_path)
    (d / "SKILL.md").write_text("---\nname: [unclosed\n---\nx")
    assert "frontmatter" in rules(tmp_path)


def test_name_mismatch(tmp_path):
    d = mk_skill(tmp_path, "alpha")
    (d / "SKILL.md").write_text(SKILL.format(n="beta", extra=""))
    assert "name-mismatch" in rules(tmp_path)
    mk_agent(tmp_path, "bot")
    (tmp_path / "agents" / "bot.md").write_text(AGENT.format(n="other", extra="", body="x"))
    assert "name-mismatch" in rules(tmp_path)


def test_referenced_missing(tmp_path):
    mk_skill(tmp_path, "alpha")
    mk_agent(tmp_path, "bot", "skills: [ghost]\ntools: [Nope, mcp__x__*]\n")
    r = rules(tmp_path, tools_registry={"Read", "mcp__y__a"})
    assert {"ref-missing-skill", "ref-missing-tool"} <= r
    # wildcard that matches is fine; explicit registry overrides discovery
    mk_agent(tmp_path, "bot", "skills: [alpha]\ntools: [mcp__y__*]\n")
    assert rules(tmp_path, tools_registry={"mcp__y__a"}) == set()
    mk_agent(tmp_path, "bot", "skills: [alpha]\n")
    assert "ref-missing-skill" in rules(tmp_path, skills_registry={"other"})


def test_enum_budget(tmp_path):
    items = "\n".join(f"- `item{i}`" for i in range(6))
    mk_skill(tmp_path, "alpha", body=items)
    assert "enum-budget" in rules(tmp_path, enum_budget=5)
    assert "enum-budget" not in rules(tmp_path, enum_budget=6)
    fenced = "```\n" + items + "\n```\n"
    mk_skill(tmp_path, "alpha", body=fenced)
    assert "enum-budget" not in rules(tmp_path, enum_budget=1)


def test_symlinks(tmp_path):
    canon = tmp_path / "canon"
    mk_skill(canon, "alpha")
    target = tmp_path / "dist"
    (target / "skills").mkdir(parents=True)
    link = target / "skills" / "alpha"
    link.symlink_to(canon / "skills" / "alpha")
    cd = canon / "skills"
    assert rules(target, canonical_dir=cd) == set()
    link.unlink()
    link.symlink_to(tmp_path / "missing")
    assert "symlink-broken" in rules(target)
    link.unlink()
    mk_skill(target, "alpha")  # plain copy
    assert "symlink-copy" in rules(target, canonical_dir=cd)
    assert "symlink-copy" not in rules(target)


def test_agent_fat_and_omit(tmp_path):
    mk_agent(tmp_path, "bot", body="\n".join(f"line {i}" for i in range(80)))
    assert "agent-fat" in rules(tmp_path)
    assert "agent-fat" not in rules(tmp_path, agent_max_body_lines=100)
    mk_agent(tmp_path, "bot", "omitClaudeMd: true\n", body="Do things.")
    assert "agent-omit-guidelines" in rules(tmp_path)
    mk_agent(tmp_path, "bot", "omitClaudeMd: true\n", body="First, read the shared guidelines file.")
    assert "agent-omit-guidelines" not in rules(tmp_path)


def test_scaffold_lints_clean(tmp_path):
    s = scaffold_skill(tmp_path, "new-skill", "Summarize: things")
    a = scaffold_agent(tmp_path, "new-agent")
    assert s.exists() and a.exists()
    assert parse_skill(s.read_text()).description == "Summarize: things"
    assert lint_tree(tmp_path, LintOptions(tools_registry={"Read", "Grep"})) == []
    with pytest.raises(ScaffoldError):
        scaffold_skill(tmp_path, "new-skill")
    with pytest.raises(ScaffoldError):
        scaffold_skill(tmp_path, "../escape")
    with pytest.raises(ScaffoldError):
        scaffold_agent(tmp_path, "Bad Name")


def test_cli(tmp_path, capsys):
    assert main(["scaffold", "skill", "demo", "--root", str(tmp_path)]) == 0
    assert main(["lint", str(tmp_path)]) == 0
    reg = tmp_path / "tools.txt"
    reg.write_text("# tools\nRead\n")
    assert main(["scaffold", "agent", "bot", "--root", str(tmp_path)]) == 0
    assert main(["check", str(tmp_path), "--tools-registry", str(reg)]) == 1  # Grep unknown
    assert main(["scaffold", "skill", "demo", "--root", str(tmp_path)]) == 2  # exists
    assert "FAIL" in capsys.readouterr().out
