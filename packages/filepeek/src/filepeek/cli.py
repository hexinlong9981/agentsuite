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
    p = argparse.ArgumentParser(prog="filepeek", description="Read-only file store connector.")
    p.add_argument("--config")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("folders", help="list allowed folders")

    lm = sub.add_parser("list", help="list files in a folder")
    lm.add_argument("--folder", required=True)

    r = sub.add_parser("read", help="read a file")
    r.add_argument("--folder", required=True)
    r.add_argument("--id", required=True)

    s = sub.add_parser("search", help="search files in a folder")
    s.add_argument("--folder", required=True)
    s.add_argument("--query", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        conn = core.FileStoreConnector.from_config(core.Config.load(args.config))
        if args.cmd == "folders":
            out = conn.allowed_folders()
        elif args.cmd == "list":
            out = conn.list(args.folder)
        elif args.cmd == "read":
            out = conn.read(args.folder, file_id=args.id)
        elif args.cmd == "search":
            out = conn.search(args.folder, query=args.query)
        else:  # pragma: no cover
            return 1
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0
    except PolicyKitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exit_code_for(exc, _EXIT, default=1)


if __name__ == "__main__":
    raise SystemExit(main())
