"""``skillkit`` command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_policy_kit.cli import exit_code_for

from .errors import SkillkitError
from .lint import LintOptions, has_errors, lint_tree, load_registry
from .scaffold import scaffold_agent, scaffold_skill


def _opts(a: argparse.Namespace) -> LintOptions:
    return LintOptions(
        skills_registry=load_registry(a.skills_registry) if a.skills_registry else None,
        tools_registry=load_registry(a.tools_registry) if a.tools_registry else None,
        enum_budget=a.enum_budget, agent_max_body_lines=a.agent_max_lines,
        canonical_dir=Path(a.canonical) if a.canonical else None)


def _lint(a: argparse.Namespace, out) -> int:
    findings = lint_tree(a.root, _opts(a))
    for f in findings:
        print(f, file=out)
    errs = sum(1 for f in findings if f.severity == "error")
    print(f"{len(findings)} finding(s), {errs} error(s)", file=out)
    return 1 if has_errors(findings) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skillkit", description="Author and lint agent skills and sub-agent definitions.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_lint_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("root")
        sp.add_argument("--skills-registry")
        sp.add_argument("--tools-registry")
        sp.add_argument("--canonical")
        sp.add_argument("--enum-budget", type=int, default=15)
        sp.add_argument("--agent-max-lines", type=int, default=60)

    add_lint_args(sub.add_parser("lint", help="lint a tree and print findings (exit 1 on errors)"))
    add_lint_args(sub.add_parser("check", help="lint with a one-line verdict; exit code for CI"))
    sc = sub.add_parser("scaffold", help="create a new skill or agent")
    sc.add_argument("kind", choices=["skill", "agent"])
    sc.add_argument("name")
    sc.add_argument("--root", default=".")
    sc.add_argument("--description")
    sc.add_argument("--force", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if a.cmd == "lint":
            return _lint(a, sys.stdout)
        if a.cmd == "check":
            rc = _lint(a, sys.stderr)
            print("OK" if rc == 0 else "FAIL")
            return rc
        fn = scaffold_skill if a.kind == "skill" else scaffold_agent
        kw = {"description": a.description} if a.description else {}
        print(fn(a.root, a.name, overwrite=a.force, **kw))
        return 0
    except (SkillkitError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exit_code_for(exc, {SkillkitError: 2, OSError: 2, ValueError: 2})


if __name__ == "__main__":
    raise SystemExit(main())
