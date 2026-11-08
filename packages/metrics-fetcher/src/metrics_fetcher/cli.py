"""CLI adapter.

Exit codes: 0 ok · 2 invalid range · 3 source · 4 output · 1 other.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from . import core
from .core.errors import (
    InvalidRange,
    MetricsFetcherError,
    OutputError,
    SourceError,
    SourceNotAllowed,
)

_EXIT = {
    InvalidRange: 2,
    SourceNotAllowed: 3,
    SourceError: 3,
    OutputError: 4,
}


def _parse_time(s: str) -> int:
    if s.lstrip("-").isdigit():
        return int(s)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mfetch", description="Fetch metrics to CSV.")
    p.add_argument("--config")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sources", help="list configured sources")

    lm = sub.add_parser("list-metrics", help="list metrics for a source")
    lm.add_argument("--source", required=True)

    f = sub.add_parser("fetch", help="fetch a metric to CSV")
    f.add_argument("--source", required=True)
    f.add_argument("--metric", required=True)
    f.add_argument("--namespace", default="")
    f.add_argument("--start", required=True, help="epoch seconds or ISO 8601")
    f.add_argument("--end", required=True, help="epoch seconds or ISO 8601")
    f.add_argument("--max-points", type=int, default=None)
    f.add_argument("--no-write", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = core.Config.load(args.config)
        if args.cmd == "sources":
            print(json.dumps(sorted(config.sources)) if args.json
                  else ("\n".join(sorted(config.sources)) or "(none)"))
        elif args.cmd == "list-metrics":
            refs = core.list_metrics(args.source, config=config)
            if args.json:
                print(json.dumps([{"name": r.name, "namespace": r.namespace} for r in refs]))
            else:
                for r in refs:
                    print(f"{r.namespace + '/' if r.namespace else ''}{r.name}")
                if not refs:
                    print("(none)")
        elif args.cmd == "fetch":
            res = core.fetch(args.source, args.metric, _parse_time(args.start), _parse_time(args.end),
                             config=config, namespace=args.namespace, max_points=args.max_points,
                             write=not args.no_write)
            if args.json:
                print(json.dumps({"csv_path": res.csv_path, "points": res.points,
                                  "period": res.plan.period, "warnings": res.plan.warnings},
                                 ensure_ascii=False))
            else:
                print(f"{res.points} points @ {res.plan.period}s -> {res.csv_path}")
                for w in res.plan.warnings:
                    print(f"  WARNING: {w}")
        return 0
    except MetricsFetcherError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for cls, code in _EXIT.items():
            if isinstance(exc, cls):
                return code
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
