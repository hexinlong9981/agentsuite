"""CLI adapter (inspection). Serving requires registering tools in code + [mcp].

Exit codes: 0 ok · 1 other.
"""

from __future__ import annotations

import argparse
import json

from . import core


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcp-runtime", description="agentsuite MCP runtime (inspection).")
    p.add_argument("--config")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("manifest", help="show the enabled-tool manifest")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = core.Config.load(args.config)
    enabled = sorted(config.enabled)
    if args.json:
        print(json.dumps(enabled))
    else:
        print("\n".join(enabled) or "(no tools enabled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
