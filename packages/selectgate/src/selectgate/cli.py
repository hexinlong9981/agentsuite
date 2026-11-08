"""CLI adapter — thin wrapper over selectgate.core.

Exit codes: 0 ok · 2 statement rejected · 3 target/driver · 4 plan gate · 5 timeout · 1 other.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import core
from .core.errors import (
    DriverError,
    PlanGateExceeded,
    QueryTimeout,
    SelectGateError,
    StatementRejected,
    TargetNotAllowed,
    WriteNotApproved,
    WriteNotEnabled,
)

_EXIT = {
    StatementRejected: 2,
    TargetNotAllowed: 3,
    DriverError: 3,
    WriteNotEnabled: 3,
    PlanGateExceeded: 4,
    QueryTimeout: 5,
    WriteNotApproved: 5,
}


def _prompt(target: str, statement: str, sql: str) -> bool:
    try:
        answer = input(f"[selectgate] write ({statement}) on {target!r}:\n  {sql}\napprove? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="selectgate", description="Read-only SQL gateway.")
    p.add_argument("--config", help="path to selectgate.toml")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("targets", help="list configured targets")

    e = sub.add_parser("explain", help="show the plan estimate for a query")
    e.add_argument("--target", required=True)
    e.add_argument("--sql", required=True)

    q = sub.add_parser("query", help="run a read-only query")
    q.add_argument("--target", required=True)
    q.add_argument("--sql", required=True)
    q.add_argument("--max-rows", type=int, default=None)

    w = sub.add_parser("write", help="run an opt-in write (prompts for approval)")
    w.add_argument("--target", required=True)
    w.add_argument("--sql", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = core.Config.load(args.config)
        if args.cmd == "targets":
            names = sorted(config.targets)
            print(json.dumps(names) if args.json else ("\n".join(names) or "(no targets)"))
        elif args.cmd == "explain":
            est = core.explain_query(args.target, args.sql, config=config)
            if args.json:
                print(json.dumps(est.__dict__, ensure_ascii=False, indent=2))
            else:
                print(f"estimated scanned rows: {est.max_scanned_rows}")
                print(f"full scans: {est.full_scans}")
                for line in est.plan:
                    print(f"  {line}")
        elif args.cmd == "query":
            res, est = core.run_select(args.target, args.sql, config=config, max_rows=args.max_rows)
            if args.json:
                print(json.dumps({"columns": res.columns, "rows": res.rows,
                                  "rowcount": res.rowcount, "truncated": res.truncated,
                                  "scanned_est": est.max_scanned_rows}, ensure_ascii=False, default=str))
            else:
                print("\t".join(res.columns))
                for row in res.rows:
                    print("\t".join("" if v is None else str(v) for v in row))
                note = f"({res.rowcount} rows"
                if res.truncated:
                    note += ", truncated"
                print(note + f", ~{est.max_scanned_rows} scanned)")
        elif args.cmd == "write":
            res = core.run_write(args.target, args.sql, config=config, approval=core.Callback(_prompt))
            if args.json:
                print(json.dumps({"statement": res.statement, "rowcount": res.rowcount}))
            else:
                print(f"{res.statement}: {res.rowcount} row(s) affected")
        return 0
    except SelectGateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for cls, code in _EXIT.items():
            if isinstance(exc, cls):
                return code
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
