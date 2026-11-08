"""CLI adapter.

Exit codes: 0 ok · 2 policy/validation denied · 3 target · 5 timeout · 1 other.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import core
from .core.errors import (
    CmdGateError,
    CommandTimeout,
    PolicyDenied,
    TargetNotAllowed,
    ValidationError,
)

_EXIT = {
    PolicyDenied: 2,
    ValidationError: 2,
    TargetNotAllowed: 3,
    CommandTimeout: 5,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cmdgate", description="Run allowlisted commands on allowlisted targets.")
    p.add_argument("--config", help="path to cmdgate.toml")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("targets", help="list configured targets")
    sub.add_parser("policies", help="list configured policies")

    la = sub.add_parser("allowed", help="list allowed commands for a target")
    la.add_argument("--target", required=True)

    r = sub.add_parser("run", help="run a command (after '--')")
    r.add_argument("--target", required=True)
    r.add_argument("--policy", default=None)
    r.add_argument("argv", nargs=argparse.REMAINDER, help="-- program args...")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = core.Config.load(args.config)
        if args.cmd == "targets":
            names = sorted(config.targets)
            print(json.dumps(names) if args.json else ("\n".join(names) or "(none)"))
        elif args.cmd == "policies":
            names = sorted(config.policies)
            print(json.dumps(names) if args.json else ("\n".join(names) or "(none)"))
        elif args.cmd == "allowed":
            rows = core.list_allowed(config, args.target)
            if args.json:
                print(json.dumps(rows))
            else:
                for pol, prog, rx in rows:
                    print(f"{pol}\t{prog}\t{rx}")
                if not rows:
                    print("(nothing allowed)")
        elif args.cmd == "run":
            cmd_argv = [a for a in args.argv if a != "--"]
            if not cmd_argv:
                raise ValidationError("no command given (use: run --target T -- program args)")
            res = core.run(args.target, cmd_argv, config=config, policy_name=args.policy)
            if args.json:
                print(json.dumps(res.__dict__, ensure_ascii=False))
            else:
                sys.stdout.write(res.stdout)
                if res.stderr:
                    sys.stderr.write(res.stderr)
                print(f"[cmdgate] rc={res.returncode} via {res.allowed_by} "
                      f"({res.duration_ms}ms{', truncated' if res.truncated else ''})",
                      file=sys.stderr)
            return res.returncode if res.returncode == 0 else 0  # command's own rc is informational
        return 0
    except CmdGateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for cls, code in _EXIT.items():
            if isinstance(exc, cls):
                return code
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
