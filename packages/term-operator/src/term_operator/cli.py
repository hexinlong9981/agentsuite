"""CLI adapter. ``send`` prompts for approval on stdin.

Exit codes: 0 ok · 3 session/operation · 4 command/backend · 5 not approved · 1 other.
"""

from __future__ import annotations

import argparse
import json
import sys

from agent_policy_kit import Callback, PolicyKitError, exit_code_for

from . import core
from .core.errors import (
    BackendError,
    CommandNotAllowed,
    OperationNotAllowed,
    SendNotApproved,
    SessionNotAllowed,
)

_EXIT = {
    SessionNotAllowed: 3,
    OperationNotAllowed: 3,
    CommandNotAllowed: 4,
    BackendError: 4,
    SendNotApproved: 5,
}


def _prompt(session: str, operation: str, command: str) -> bool:
    try:
        answer = input(f"[term-operator] send to {session!r}: {command}\napprove? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="term-operator", description="Policy-gated terminal driver.")
    p.add_argument("--config")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sessions", help="list allowed sessions")

    r = sub.add_parser("read", help="read a session's screen/output")
    r.add_argument("--session", required=True)

    s = sub.add_parser("send", help="send a command (prompts for approval)")
    s.add_argument("--session", required=True)
    s.add_argument("argv", nargs=argparse.REMAINDER, help="-- program args...")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = core.Config.load(args.config)
        op = core.TermOperator.from_config(config, approval=Callback(_prompt))
        if args.cmd == "sessions":
            print(json.dumps(sorted(config.scope.sessions)))
            return 0
        if args.cmd == "read":
            op.open(args.session)
            print(op.read(args.session))
            return 0
        if args.cmd == "send":
            cmd_argv = [a for a in args.argv if a != "--"]
            op.open(args.session)
            print(op.send(args.session, cmd_argv))
            return 0
        return 1
    except PolicyKitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exit_code_for(exc, _EXIT, default=1)


if __name__ == "__main__":
    raise SystemExit(main())
