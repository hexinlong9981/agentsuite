"""CLI adapter.

Exit codes: 0 ok · 3 scope/operation denied · 4 backend · 1 other.
"""

from __future__ import annotations

import argparse
import json
import sys

from agent_policy_kit import OperationNotAllowed, PolicyKitError, ScopeDenied, exit_code_for

from . import core
from .core.errors import BackendError

_EXIT = {ScopeDenied: 3, OperationNotAllowed: 3, BackendError: 4}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chatpeek", description="Read-only chat connector.")
    p.add_argument("--config")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("channels", help="list allowed channels")

    lm = sub.add_parser("list", help="list messages in a channel")
    lm.add_argument("--channel", required=True)

    r = sub.add_parser("read", help="read a message")
    r.add_argument("--channel", required=True)
    r.add_argument("--id", required=True)

    s = sub.add_parser("search", help="search messages in a channel")
    s.add_argument("--channel", required=True)
    s.add_argument("--query", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        conn = core.ChatConnector.from_config(core.Config.load(args.config))
        if args.cmd == "channels":
            out = conn.allowed_channels()
        elif args.cmd == "list":
            out = conn.list(args.channel)
        elif args.cmd == "read":
            out = conn.read(args.channel, message_id=args.id)
        elif args.cmd == "search":
            out = conn.search(args.channel, query=args.query)
        else:  # pragma: no cover
            return 1
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0
    except PolicyKitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exit_code_for(exc, _EXIT, default=1)


if __name__ == "__main__":
    raise SystemExit(main())
