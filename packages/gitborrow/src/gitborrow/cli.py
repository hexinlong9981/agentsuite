"""CLI adapter — thin wrapper over gitborrow.core and analysis layer.

Exit codes: 0 ok · 2 guard/containment rejection · 3 allowlist · 4 unreachable
· 5 filter fail · 1 other error.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from . import core
from .core.analysis import AnalysisEngine
from .core.errors import (
    ContainmentError,
    FilterError,
    GitborrowError,
    GuardViolation,
    MirrorUnreachable,
    RefNotAllowed,
    RepositoryNotAllowed,
)

_EXIT = {
    GuardViolation: 2,
    ContainmentError: 2,
    RepositoryNotAllowed: 3,
    RefNotAllowed: 3,
    MirrorUnreachable: 4,
    FilterError: 5,
}


def _emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, default=_asdict, indent=2))
    else:
        if isinstance(obj, list):
            for it in obj:
                print(it)
        else:
            print(obj)


def _asdict(o):
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    return str(o)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gitborrow", description="Borrow source into disposable checkouts & analyze.")
    p.add_argument("--config", help="path to gitborrow.toml")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    # Lifecycle commands
    f = sub.add_parser("fetch", help="fetch a repository into an ephemeral checkout")
    f.add_argument("repo", help="allowlist key, local path, or URL")
    f.add_argument("--ref", help="branch/tag/sha")
    f.add_argument("--mode", default="auto", choices=["auto", "mirror", "direct"])
    f.add_argument("--shallow", action="store_true", help="shallow (depth 1) fetch")

    r = sub.add_parser("release", help="discard a checkout by id or repo")
    r.add_argument("target")

    pu = sub.add_parser("purge", help="discard old checkouts (tool-created only)")
    pu.add_argument("--all", action="store_true")
    pu.add_argument("--max-age-hours", type=float, default=None)

    sub.add_parser("list", help="list current checkouts")

    st = sub.add_parser("status", help="show checkouts for a repo")
    st.add_argument("repo")

    gc = sub.add_parser("guard-check", help="validate a git argv against the push guard")
    gc.add_argument("gitargv", nargs=argparse.REMAINDER)

    # Analysis commands
    sc = sub.add_parser("search", help="search for pattern across files")
    sc.add_argument("repo")
    sc.add_argument("pattern")
    sc.add_argument("--ref")
    sc.add_argument("--regex", action="store_true")
    sc.add_argument("--glob", action="append", dest="globs")
    sc.add_argument("--lang")
    sc.add_argument("--context", type=int, default=2)

    rd = sub.add_parser("read", help="read a bounded slice of lines from a file")
    rd.add_argument("repo")
    rd.add_argument("path")
    rd.add_argument("--ref")
    rd.add_argument("--start", type=int, default=None)
    rd.add_argument("--end", type=int, default=None)

    tr = sub.add_parser("tree", help="list file tree with language detection")
    tr.add_argument("repo")
    tr.add_argument("--ref")
    tr.add_argument("--dir", dest="dir_path")

    df = sub.add_parser("definition", help="find definition of a symbol")
    df.add_argument("repo")
    df.add_argument("symbol")
    df.add_argument("--ref")
    df.add_argument("--lang")
    df.add_argument("--kind")

    rf = sub.add_parser("references", help="find references/usages of a symbol")
    rf.add_argument("repo")
    rf.add_argument("symbol")
    rf.add_argument("--ref")
    rf.add_argument("--lang")

    ot = sub.add_parser("outline", help="extract structural outline of a file")
    ot.add_argument("repo")
    ot.add_argument("path")
    ot.add_argument("--ref")

    ct = sub.add_parser("call-trace", help="trace call chain of a symbol")
    ct.add_argument("repo")
    ct.add_argument("symbol")
    ct.add_argument("--ref")
    ct.add_argument("--direction", default="callers", choices=["callers", "callees"])
    ct.add_argument("--depth", type=int, default=2)

    imp = sub.add_parser("impact-scan", help="scan all code paths accessing a target")
    imp.add_argument("repo")
    imp.add_argument("target")
    imp.add_argument("--ref")
    imp.add_argument("--kind", default="symbol", choices=["symbol", "table", "data"])

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = core.Config.load(args.config)

        if args.cmd == "fetch":
            res = core.fetch(
                core.FetchRequest(repo=args.repo, ref=args.ref, mode=args.mode,
                                  full_history=not args.shallow),
                config=config)
            if args.json:
                _emit(res, True)
            else:
                c = res.checkout
                print(f"checkout {c.id}  {res.citation_base}")
                print(f"  path:    {c.path}")
                print(f"  commits: {c.commits_available}")
                print(f"  guard:   {res.push_guard}")
                print(f"  filter:  enabled={res.mask.enabled} action={res.mask.action} "
                      f"hits={res.mask.hits} masked={res.mask.files_masked}")
                for w in res.warnings:
                    print(f"  WARNING: {w}")

        elif args.cmd == "release":
            _emit(core.release(args.target, config=config), args.json)

        elif args.cmd == "purge":
            _emit(core.purge(config, max_age_hours=args.max_age_hours, purge_all=args.all), args.json)

        elif args.cmd == "list":
            items = core.list_checkouts(config)
            if args.json:
                _emit(items, True)
            else:
                for c in items:
                    print(f"{c.id}  {c.repo}@{c.commit_sha[:12]}  {c.ref}  {c.path}")
                if not items:
                    print("(no checkouts)")

        elif args.cmd == "status":
            _emit(core.status(args.repo, config), args.json)

        elif args.cmd == "guard-check":
            core_argv = [a for a in args.gitargv if a != "--"]
            from .core import guard
            guard.validate_git_command(core_argv, enforce=config.push_guard.enabled)
            print("OK: allowed")

        # Analysis commands
        elif args.cmd in (
            "search", "read", "tree", "definition", "references",
            "outline", "call-trace", "impact-scan"
        ):
            engine = AnalysisEngine(config=config)
            try:
                if args.cmd == "search":
                    res = engine.search(
                        args.repo, args.ref, args.pattern,
                        regex=args.regex, globs=args.globs, lang=args.lang, context=args.context
                    )
                elif args.cmd == "read":
                    res = engine.read(args.repo, args.ref, args.path, start=args.start, end=args.end)
                elif args.cmd == "tree":
                    res = engine.tree(args.repo, args.ref, dir_path=args.dir_path)
                elif args.cmd == "definition":
                    res = engine.definition(args.repo, args.ref, args.symbol, lang=args.lang, kind=args.kind)
                elif args.cmd == "references":
                    res = engine.references(args.repo, args.ref, args.symbol, lang=args.lang)
                elif args.cmd == "outline":
                    res = engine.outline(args.repo, args.ref, args.path)
                elif args.cmd == "call-trace":
                    res = engine.call_trace(args.repo, args.ref, args.symbol, direction=args.direction, depth=args.depth)
                elif args.cmd == "impact-scan":
                    res = engine.impact_scan(args.repo, args.ref, args.target, kind=args.kind)
                _emit(res, args.json)
            finally:
                engine.close()

        return 0
    except GitborrowError as e:
        print(f"error: {e}", file=sys.stderr)
        for cls, code in _EXIT.items():
            if isinstance(e, cls):
                return code
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
