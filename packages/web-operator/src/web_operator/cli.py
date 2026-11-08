"""CLI adapter (inspection only — running operations needs a live backend).

Exit codes: 0 ok · 2 invalid operation · 3 host/category denied · 1 other.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import core
from .core.errors import (
    HostNotAllowed,
    InvalidOperation,
    OperationNotAllowed,
    WebOperatorError,
)
from .core.policy import OP_TO_CATEGORY

_EXIT = {
    InvalidOperation: 2,
    HostNotAllowed: 3,
    OperationNotAllowed: 3,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="weboperator", description="Policy-gated browser automation (inspection CLI).")
    p.add_argument("--config")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("hosts", help="list configured hosts and allowed categories")

    c = sub.add_parser("check", help="check whether an operation is allowed for a host")
    c.add_argument("--host", required=True)
    c.add_argument("--operation", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = core.Config.load(args.config)
        if args.cmd == "hosts":
            rows = {h: sorted(ops) for h, ops in config.policy.hosts.items()}
            if args.json:
                print(json.dumps(rows))
            else:
                for h, ops in rows.items():
                    print(f"{h}\t{','.join(ops)}")
                if not rows:
                    print("(no hosts)")
        elif args.cmd == "check":
            if args.operation not in OP_TO_CATEGORY:
                raise InvalidOperation(f"unknown operation {args.operation!r}")
            category = OP_TO_CATEGORY[args.operation]
            config.policy.require(args.host, category)
            needs = " (requires approval at run time)" if category == "mutate" else ""
            print(f"OK: {args.operation} -> category {category} allowed for {args.host}{needs}")
        return 0
    except WebOperatorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for cls, code in _EXIT.items():
            if isinstance(exc, cls):
                return code
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
