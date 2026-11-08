"""CLI adapter. Exit codes: 0 ok · 3 run not found · 1 other."""

from __future__ import annotations

import argparse
import os
import sys

from . import core
from .core.errors import AgentflowError, RunNotFound

_EXIT = {RunNotFound: 3}


def _root(args) -> str:
    return args.root or os.environ.get("AGENTFLOW_ROOT", "agentflow-runs")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentflow", description="Record/inspect agent runs.")
    p.add_argument("--root", help="runs directory (default: $AGENTFLOW_ROOT or ./agentflow-runs)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list runs")

    s = sub.add_parser("show", help="render a run")
    s.add_argument("--run", required=True)
    s.add_argument("--html", action="store_true")
    s.add_argument("--out", help="write the rendering to this file instead of stdout")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        store = core.RunStore(_root(args))
        if args.cmd == "list":
            runs = store.list_runs()
            for m in runs:
                print(f"{m.run_id}\t{m.status}\t{m.flow}")
            if not runs:
                print("(no runs)")
        elif args.cmd == "show":
            meta, events = store.load(args.run)
            rendered = core.render_html(meta, events) if args.html else core.render_text(meta, events)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(rendered)
                print(f"wrote {args.out}")
            else:
                print(rendered)
        return 0
    except AgentflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for cls, code in _EXIT.items():
            if isinstance(exc, cls):
                return code
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
