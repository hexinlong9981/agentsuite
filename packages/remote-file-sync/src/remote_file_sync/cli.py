"""CLI adapter.

Exit codes: 0 ok · 3 policy/source/destination · 4 limit · 5 transport · 1 other.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import core
from .core.errors import (
    DestinationError,
    LimitExceeded,
    PolicyNotAllowed,
    RemoteFileSyncError,
    SourceNotAllowed,
    TransportError,
)

_EXIT = {
    PolicyNotAllowed: 3,
    SourceNotAllowed: 3,
    DestinationError: 3,
    LimitExceeded: 4,
    TransportError: 5,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rfsync", description="Pull files into local destinations by policy.")
    p.add_argument("--config", help="path to rfsync.toml")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("policies", help="list configured policies")
    sub.add_parser("sources", help="list configured sources")

    s = sub.add_parser("sync", help="pull files for a policy")
    s.add_argument("--policy", required=True)
    s.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = core.Config.load(args.config)
        if args.cmd == "policies":
            print(json.dumps(sorted(config.policies)) if args.json
                  else ("\n".join(sorted(config.policies)) or "(none)"))
        elif args.cmd == "sources":
            print(json.dumps(sorted(config.sources)) if args.json
                  else ("\n".join(sorted(config.sources)) or "(none)"))
        elif args.cmd == "sync":
            res = core.sync(args.policy, config=config, dry_run=args.dry_run)
            if args.json:
                print(json.dumps({
                    "policy": res.policy, "source": res.source,
                    "files": len(res.fetched), "skipped": res.skipped,
                    "total_bytes": res.total_bytes, "by_category": res.by_category,
                    "dry_run": res.dry_run,
                }, ensure_ascii=False))
            else:
                verb = "would fetch" if res.dry_run else "fetched"
                print(f"{verb} {len(res.fetched)} file(s), {res.total_bytes} bytes "
                      f"(skipped {res.skipped}) by category: {res.by_category}")
                for f in res.fetched:
                    print(f"  [{f.category}] {f.path} -> {f.dest}")
        return 0
    except RemoteFileSyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for cls, code in _EXIT.items():
            if isinstance(exc, cls):
                return code
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
