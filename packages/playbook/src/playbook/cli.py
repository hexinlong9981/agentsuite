"""``playbook`` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_policy_kit import exit_code_for

from .check import audit_text, check as run_check
from . import doc
from .errors import PlaybookError, PrincipleNotFound
from .principles import PRINCIPLES, get

EXIT_CODES = {PrincipleNotFound: 3, PlaybookError: 2}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="playbook", description="Agent operating principles.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list principle ids and titles")
    s = sub.add_parser("show", help="show one principle")
    s.add_argument("id")
    r = sub.add_parser("render", help="render the Markdown guide")
    r.add_argument("-o", "--output")
    sub.add_parser("check", help="validate the principle set")
    a = sub.add_parser("audit", help="report which principles a document references")
    a.add_argument("file")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "list":
            for p in PRINCIPLES:
                print(f"{p.id}\t{p.title}")
        elif args.cmd == "show":
            print(json.dumps(get(args.id).to_dict(), indent=2))
        elif args.cmd == "render":
            text = doc.render()
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
        elif args.cmd == "check":
            res = run_check()
            for prob in res.problems:
                print(prob)
            print("ok" if res.ok else f"{len(res.problems)} problem(s)")
            return 0 if res.ok else 1
        elif args.cmd == "audit":
            try:
                text = Path(args.file).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            res = audit_text(text)
            print(f"referenced: {', '.join(res.referenced) or '-'}")
            print(f"missing: {', '.join(res.missing) or '-'}")
            return 0 if res.ok else 1
    except PlaybookError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exit_code_for(exc, EXIT_CODES)
    return 0
