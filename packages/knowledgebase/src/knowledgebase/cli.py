"""`kb` command line."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from agent_policy_kit import AuditLog, exit_code_for

from .errors import ItemExists, ItemNotFound, KnowledgeBaseError, ValidationError
from .freshness import Freshness
from .inbox import Inbox
from .model import ITEM_TYPES, Item
from .recall import recall
from .store import KnowledgeBase

EXIT_CODES = {ItemNotFound: 4, ItemExists: 5, ValidationError: 3, KnowledgeBaseError: 1}


def _item_from_args(a) -> Item:
    body = a.body or ""
    if a.body_file:
        body = sys.stdin.read() if a.body_file == "-" else open(a.body_file, encoding="utf-8").read()
    links = [x for x in (a.links or "").split(",") if x]
    return Item(a.name, a.description, a.type, links, body)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kb", description="Small reusable facts an agent reads and grows.")
    p.add_argument("--root", required=True, help="knowledge base root directory")
    p.add_argument("--audit", help="audit log file (JSON lines)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    g = sub.add_parser("get"); g.add_argument("name")
    for cmd in ("add", "propose"):
        s = sub.add_parser(cmd)
        s.add_argument("--name", required=True)
        s.add_argument("--description", required=True)
        s.add_argument("--type", required=True, choices=ITEM_TYPES)
        s.add_argument("--links", help="comma-separated item names")
        s.add_argument("--body")
        s.add_argument("--body-file", help="file path, or - for stdin")
    pr = sub.add_parser("promote"); pr.add_argument("name"); pr.add_argument("--overwrite", action="store_true")
    r = sub.add_parser("recall"); r.add_argument("query"); r.add_argument("--limit", type=int, default=5)
    f = sub.add_parser("freshness")
    f.add_argument("--used-days", type=int, default=90)
    f.add_argument("--reviewed-days", type=int, default=180)
    f.add_argument("--mark-used", metavar="NAME")
    f.add_argument("--mark-reviewed", metavar="NAME")
    sub.add_parser("check")
    return p


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    kb = KnowledgeBase(a.root, audit=AuditLog(a.audit))
    try:
        if a.cmd == "list":
            _out([{"name": i.name, "type": i.type, "description": i.description} for i in kb.list()])
        elif a.cmd == "get":
            _out(kb.get(a.name).to_dict())
        elif a.cmd == "add":
            kb.add(_item_from_args(a)); _out({"added": a.name})
        elif a.cmd == "propose":
            Inbox(kb).propose(_item_from_args(a)); _out({"proposed": a.name})
        elif a.cmd == "promote":
            Inbox(kb).promote(a.name, overwrite=a.overwrite); _out({"promoted": a.name})
        elif a.cmd == "recall":
            _out([asdict(h) for h in recall(kb, a.query, a.limit)])
        elif a.cmd == "freshness":
            fr = Freshness(kb)
            if a.mark_used:
                _out({"last_used": fr.mark_used(a.mark_used)})
            elif a.mark_reviewed:
                _out({"last_reviewed": fr.mark_reviewed(a.mark_reviewed)})
            else:
                _out([asdict(e) for e in fr.staleness_report(used_days=a.used_days, reviewed_days=a.reviewed_days)])
        elif a.cmd == "check":
            issues = kb.check_consistency()
            _out([asdict(i) for i in issues])
            return 1 if any(i.severity == "error" for i in issues) else 0
        return 0
    except KnowledgeBaseError as exc:
        print(f"kb: error: {exc}", file=sys.stderr)
        return exit_code_for(exc, EXIT_CODES)


if __name__ == "__main__":
    sys.exit(main())
