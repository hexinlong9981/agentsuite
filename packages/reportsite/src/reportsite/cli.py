"""``reportsite`` command line: build a site from a spec, check a site."""

from __future__ import annotations

import argparse
import json
import sys

from agent_policy_kit import exit_code_for

from .bundle import build_bundle, check_bundle
from .check_site import check_site, format_findings, passed
from .errors import EXIT_CODES, CheckFailed, SpecError
from .generator import build_site
from .md import MAX_BYTES, spec_from_markdown
from .presets import PRESETS, skeleton_from_preset
from .render_check import check_render


def _build(args: argparse.Namespace) -> int:
    try:
        spec = json.loads(open(args.spec, encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        raise SpecError(f"cannot read spec: {exc}") from exc
    summary = build_site(spec, args.out_dir)
    for f in summary.files:
        print(f"wrote {f}")
    for f in summary.removed:
        print(f"removed stale {f}")
    return 0


def _scaffold(args: argparse.Namespace) -> int:
    summary = build_site(skeleton_from_preset(args.preset, args.title), args.out_dir)
    for f in summary.files:
        print(f"wrote {f}")
    return 0


def _from_markdown(args: argparse.Namespace) -> int:
    try:
        with open(args.doc, encoding="utf-8") as fh:
            text = fh.read(MAX_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise SpecError(f"cannot read markdown: {exc}") from exc
    spec = spec_from_markdown(text, title=args.title, preset=args.preset,
                              tables_as_data=args.tables_as_data)
    summary = build_site(spec, args.out_dir)
    for f in summary.files:
        print(f"wrote {f}")
    return 0


def _check(args: argparse.Namespace) -> int:
    findings = check_site(args.dir) + check_render(args.dir)
    print(format_findings(findings))
    if not passed(findings):
        raise CheckFailed("completion gate failed")
    return 0


def _render_check(args: argparse.Namespace) -> int:
    findings = check_render(args.dir)
    print(format_findings(findings))
    if not passed(findings):
        raise CheckFailed("render check failed")
    return 0


def _bundle(args: argparse.Namespace) -> int:
    manifest = build_bundle(args.sites, args.out_dir, args.title)
    for r in manifest["reports"]:
        print(f"bundled {r['dir']} ({r['title']})")
    print(f"wrote {args.out_dir}/index.html")
    return 0


def _check_bundle(args: argparse.Namespace) -> int:
    findings = check_bundle(args.dir)
    print(format_findings(findings))
    if not passed(findings):
        raise CheckFailed("bundle gate failed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reportsite")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a site from a JSON spec")
    b.add_argument("spec")
    b.add_argument("out_dir")
    b.set_defaults(fn=_build)
    s = sub.add_parser("scaffold", help="emit a placeholder site from a preset")
    s.add_argument("--preset", default="report", choices=sorted(PRESETS))
    s.add_argument("title")
    s.add_argument("out_dir")
    s.set_defaults(fn=_scaffold)
    m = sub.add_parser("from-markdown", help="build a site from a Markdown document")
    m.add_argument("doc")
    m.add_argument("out_dir")
    m.add_argument("--preset", choices=sorted(PRESETS), default=None)
    m.add_argument("--title", default=None)
    m.add_argument("--tables-as-data", action="store_true",
                   help="emit Markdown tables as data-table containers (site_data/*.json)")
    m.set_defaults(fn=_from_markdown)
    c = sub.add_parser("check", help="run the completion gate on a site directory")
    c.add_argument("dir")
    c.set_defaults(fn=_check)
    r = sub.add_parser("render-check", help="check diagrams and theme tokens/contrast only")
    r.add_argument("dir")
    r.set_defaults(fn=_render_check)
    bd = sub.add_parser("bundle", help="combine built report sites into one portable bundle")
    bd.add_argument("out_dir")
    bd.add_argument("sites", nargs="+")
    bd.add_argument("--title", default="Report bundle")
    bd.set_defaults(fn=_bundle)
    cb = sub.add_parser("check-bundle", help="run the bundle gate on a bundle directory")
    cb.add_argument("dir")
    cb.set_defaults(fn=_check_bundle)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:  # noqa: BLE001 - mapped to an exit code below
        if not isinstance(exc, CheckFailed):
            print(f"error: {exc}", file=sys.stderr)
        return exit_code_for(exc, EXIT_CODES, default=1)


if __name__ == "__main__":
    sys.exit(main())
