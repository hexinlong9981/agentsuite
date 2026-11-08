"""Bundle: combine built report sites into one portable, two-level bundle.

Layout::

    <out>/index.html            entry: level 1 = reports, level 2 = each report's pages
    <out>/bundle.json           manifest (title, reports, pages)
    <out>/reports/<slug>/...    each report site, copied verbatim

Every link is relative, so the folder opens from ``file://`` and can be moved or
zipped. ``check_bundle`` is the fail-closed gate: entry present, every referenced
report present and passing ``check_site`` / ``check_render``, all links relative and
resolving, no leaked secret.
"""

from __future__ import annotations

import html
import json
import os
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agent_policy_kit import contained_join, is_under, safe_name

from .check_site import ERROR, Finding, _MASKER, _Scan, check_site, passed
from .errors import CheckFailed, SpecError
from .render_check import check_render

BUNDLE_MARK = '<meta name="generator" content="reportsite-bundle">'
REPORTS_DIR = "reports"
MANIFEST = "bundle.json"
FRAME = "rs-frame"

_CSS = """:root{--bg:#fff;--fg:#1b1f24;--mut:#57606a;--line:#d0d7de;--acc:#0b57d0}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1318;--fg:#e6edf3;--mut:#9aa7b4;--line:#2f3a46;--acc:#7cb2ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
.b-wrap{display:flex;min-height:100vh}
.b-nav{flex:0 0 18rem;padding:1rem;border-right:1px solid var(--line);overflow:auto}
.b-nav h1{font-size:1.1rem;margin:0 0 .75rem}
.b-nav ul{list-style:none;margin:0;padding:0}.b-nav ul ul{padding-left:1rem;margin-bottom:.5rem}
.b-nav a{color:var(--acc)}.b-nav .b-report{font-weight:600;display:block;margin-top:.5rem}
.b-frame{flex:1;border:0;min-height:100vh;background:var(--bg)}
@media (max-width:700px){.b-wrap{flex-direction:column}.b-nav{flex-basis:auto;border-right:0}}
"""


class _Nav(HTMLParser):
    """Collect level-1/level-2 entries from a built site's index.html nav."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pages: list[tuple[str, str]] = []
        self.title = ""
        self._cur: list | None = None
        self._h2 = False
        self._in_nav = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "nav":
            self._in_nav = True
        elif tag == "h2" and self._in_nav and not self.title:
            self._h2 = True
        elif tag == "a" and "data-nav" in a and a.get("href"):
            self._cur = [a["href"], []]

    def handle_data(self, data):
        if self._cur is not None:
            self._cur[1].append(data)
        elif self._h2:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self.pages.append((self._cur[0], "".join(self._cur[1]).strip()))
            self._cur = None
        elif tag == "h2":
            self._h2 = False
        elif tag == "nav":
            self._in_nav = False


def _describe(site: Path) -> tuple[str, list[tuple[str, str]]]:
    nav = _Nav()
    nav.feed((site / "index.html").read_text(encoding="utf-8"))
    nav.close()
    pages = [(h, t) for h, t in nav.pages if not urlsplit(h).scheme and not h.startswith("/")]
    if not pages:
        pages = [("index.html", "Overview")]
    return (nav.title.strip() or site.name), pages


def _copy_tree(src: Path, out_dir: str, rel_root: str) -> None:
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for fn in filenames:
            f = Path(dirpath) / fn
            if f.is_symlink() or not f.is_file():
                continue
            rel = f.relative_to(src).as_posix()
            dest = contained_join(out_dir, f"{rel_root}/{rel}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)


def _entry_html(title: str, reports: list[dict]) -> str:
    items = []
    for r in reports:
        pages = "".join(
            f'<li><a href="{html.escape(r["dir"] + "/" + h)}" target="{FRAME}">'
            f"{html.escape(t)}</a></li>" for h, t in r["pages"])
        items.append(f'<li><a class="b-report" data-bundle-report="{html.escape(r["slug"])}" '
                     f'href="{html.escape(r["dir"] + "/index.html")}" target="{FRAME}">'
                     f'{html.escape(r["title"])}</a><ul>{pages}</ul></li>')
    first = html.escape(reports[0]["dir"] + "/index.html")
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{BUNDLE_MARK}\n<title>{html.escape(title)}</title>\n<style>{_CSS}</style>\n"
        '</head>\n<body>\n<div class="b-wrap">\n'
        f'<nav class="b-nav" aria-label="Reports"><h1>{html.escape(title)}</h1>'
        f'<ul>{"".join(items)}</ul></nav>\n'
        f'<iframe class="b-frame" name="{FRAME}" title="Selected report" src="{first}"></iframe>\n'
        "</div>\n</body>\n</html>\n")


def build_bundle(sites: list[str], out_dir: str, title: str) -> dict:
    """Combine built report sites (each must pass ``check_site``) into one bundle."""
    if not title or not title.strip():
        raise SpecError("bundle title is required")
    if not sites:
        raise SpecError("at least one report site is required")
    described = []
    used: set[str] = set()
    for s in sites:
        src = Path(s)
        if not (src / "index.html").is_file():
            raise SpecError(f"{s!r} is not a built report site (no index.html)")
        findings = check_site(str(src))
        if not passed(findings):
            raise CheckFailed(f"report {s!r} fails check_site; fix it before bundling")
        base = slug = safe_name(src.resolve().name, default="report")
        n = 2
        while slug.lower() in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug.lower())
        rtitle, pages = _describe(src)
        described.append({"slug": slug, "dir": f"{REPORTS_DIR}/{slug}", "title": rtitle,
                          "pages": pages, "src": src})
    out = Path(out_dir)
    entry = out / "index.html"
    if out.exists() and any(out.iterdir()):
        marked = entry.is_file() and BUNDLE_MARK in entry.read_text(encoding="utf-8", errors="replace")
        if not marked:
            raise SpecError(f"{out_dir!r} is not empty and not a reportsite bundle")
    out.mkdir(parents=True, exist_ok=True)
    reports_root = contained_join(out_dir, REPORTS_DIR)
    if reports_root.exists():
        shutil.rmtree(reports_root)
    for r in described:
        _copy_tree(r["src"], out_dir, r["dir"])
    manifest = {"title": title, "reports": [
        {k: r[k] for k in ("slug", "dir", "title")} | {"pages": [{"href": h, "title": t}
                                                                for h, t in r["pages"]]}
        for r in described]}
    entry.write_text(_entry_html(title, described), encoding="utf-8")
    contained_join(out_dir, MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _bad_target(href: str) -> str:
    """Reason a link breaks portability, or ''. (http(s)/mailto/tel/data are allowed.)"""
    parts = urlsplit(href)
    if parts.scheme in {"http", "https", "mailto", "tel", "data"}:
        return ""
    if parts.scheme == "file":
        return "file:// link"
    if parts.scheme:
        return f"unsupported scheme {parts.scheme!r}"
    if parts.netloc or href.startswith("//"):
        return "protocol-relative link"
    if href.startswith("/") or href.startswith("\\"):
        return "absolute path"
    return ""


def check_bundle(bundle_dir: str) -> list[Finding]:
    """Fail-closed gate for a bundle directory."""
    root = Path(bundle_dir)
    if not root.is_dir():
        return [Finding(str(bundle_dir), "bundle.missing", "bundle directory does not exist")]
    out: list[Finding] = []
    entry = root / "index.html"
    if not entry.is_file():
        return [Finding("index.html", "bundle.no_entry", "bundle entry index.html is missing")]
    try:
        raw = entry.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Finding("index.html", "bundle.unreadable", f"cannot read entry: {exc}")]
    if BUNDLE_MARK not in raw:
        out.append(Finding("index.html", "bundle.not_bundle", "entry is not a reportsite bundle"))
    for rel in ("index.html", MANIFEST):
        p = root / rel
        if p.is_file():
            try:
                txt = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if _MASKER.mask_text(txt) != txt:
                out.append(Finding(rel, "secret.leak", "a secret-like value is present"))

    sc = _Scan()
    try:
        sc.feed(raw)
        sc.close()
    except Exception as exc:  # noqa: BLE001 - fail-closed
        return out + [Finding("index.html", "html.malformed", f"cannot parse entry: {exc}")]
    refs = list(sc.hrefs) + _frame_srcs(raw)
    slugs: list[str] = []
    for href in refs:
        why = _bad_target(href)
        if why:
            out.append(Finding("index.html", "bundle.link_not_portable",
                               f"link {href!r} is a {why}; use a relative link"))
            continue
        parts = urlsplit(href)
        if parts.scheme or not parts.path:
            continue
        target = root / unquote(parts.path)
        if not is_under(target, root) or not target.exists():
            out.append(Finding("index.html", "bundle.link_broken", f"link {href!r} does not resolve"))
            continue
        segs = Path(unquote(parts.path)).parts
        if len(segs) >= 2 and segs[0] == REPORTS_DIR and segs[1] not in slugs:
            slugs.append(segs[1])
    if not slugs:
        out.append(Finding("index.html", "bundle.no_reports", "entry links to no report"))

    for slug in slugs:
        rdir = root / REPORTS_DIR / slug
        prefix = f"{REPORTS_DIR}/{slug}/"
        if not (rdir / "index.html").is_file():
            out.append(Finding(prefix, "bundle.report_missing",
                               f"report {slug!r} is referenced but has no index.html"))
            continue
        out.extend(Finding(prefix + f.path, f.rule, f.message, f.severity)
                   for f in check_site(str(rdir)) + check_render(str(rdir)))
        for page in sorted(rdir.rglob("*.htm*")):
            prel = page.relative_to(rdir).as_posix()
            psc = _Scan()
            try:
                psc.feed(page.read_text(encoding="utf-8"))
                psc.close()
            except Exception:  # noqa: BLE001 - check_site already reports
                continue
            for href in psc.hrefs:
                why = _bad_target(href)
                if why:
                    out.append(Finding(prefix + prel, "bundle.link_not_portable",
                                       f"link {href!r} is a {why}; breaks file://"))
    on_disk = (root / REPORTS_DIR)
    if on_disk.is_dir():
        for d in sorted(p for p in on_disk.iterdir() if p.is_dir()):
            if d.name not in slugs:
                out.append(Finding(f"{REPORTS_DIR}/{d.name}/", "bundle.unlisted_report",
                                   "report directory is not linked from the entry", "warning"))
    return out


def _frame_srcs(raw: str) -> list[str]:
    import re
    return re.findall(r"""<iframe\b[^>]*\bsrc\s*=\s*["']([^"']*)["']""", raw, flags=re.I)


__all__ = ["build_bundle", "check_bundle", "ERROR"]
