"""Fail-closed completion gate for a generated report site.

``check_site(dir)`` returns structured findings. "Pass" means zero findings with
severity ``error``. An unreadable / missing / empty site is itself an error.
Secret values are never echoed in findings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agent_policy_kit import SecretMasker, is_under

from .databind import (DATA_DIR, DATA_FILE_RE, DATA_JS_PATH, lookup_field, parse_data_file,
                       script_is_pristine)

ERROR = "error"
WARNING = "warning"

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
SVG_SHAPES = {"rect", "circle", "ellipse", "path", "polygon", "polyline", "text"}
TEXT_SUFFIXES = {".html", ".htm", ".css", ".js", ".json", ".csv", ".txt", ".md", ".svg"}

_EXTRA_SECRET_PATTERNS = [
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    r"\bsk-[A-Za-z0-9_\-]{20,}\b",
    r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b",
    r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b",
    r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
    r"[\"']?\s*[:=]\s*[\"']?[^\s\"'<>&;,]{6,}",
    r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}",
]
_MASKER = SecretMasker(extra_patterns=_EXTRA_SECRET_PATTERNS)

_TZ_DT = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?P<tz>\s*(?:Z|[+-]\d{2}:?\d{2}|UTC|GMT|[A-Z]{3,4})\b|Z\b|[+-]\d{2}:?\d{2}\b)?")
_TODO = re.compile(r"\bTODO\b")
_SCRIPT_STYLE = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")
_TAG = re.compile(r"(?s)<[^>]*>")


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    message: str
    severity: str = ERROR


def passed(findings: list[Finding]) -> bool:
    return not any(f.severity == ERROR for f in findings)


# ---------------------------------------------------------------- html scanning
class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []
        self.ids: set[str] = set()
        self.nav_hrefs: list[str] = []
        self.hrefs: list[str] = []
        self.panel_targets: list[str] = []
        self.panels: list[str] = []
        self.data_tables: list[tuple[str, str, int]] = []  # (file, columns attr, line)
        self.data_values: list[tuple[str, int]] = []
        self.scripts: list[str] = []
        self.embeds: list[tuple[str, str, str, int]] = []  # (file, script type, payload, line)
        self._embed: list | None = None
        self.style_issues: list[str] = []
        self.svg_issues: list[str] = []
        self._in_nav = 0
        self._in_defs = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        line = self.getpos()[0]
        if "id" in a:
            self.ids.add(a["id"])
        if "name" in a and tag == "a":
            self.ids.add(a["name"])
        if tag == "nav":
            self._in_nav += 1
        if tag == "defs":
            self._in_defs += 1
        if tag == "a" and "href" in a:
            self.hrefs.append(a["href"])
            if self._in_nav:
                self.nav_hrefs.append(a["href"])
        for k in ("src",):
            if k in a and tag in {"script", "link", "img"}:
                self.hrefs.append(a[k])
        if tag == "link" and "href" in a:
            self.hrefs.append(a["href"])
        if "data-table" in a:
            self.data_tables.append((a["data-table"], a.get("data-columns", ""), line))
        if "data-value" in a:
            self.data_values.append((a["data-value"], line))
        if tag == "script" and "src" in a:
            self.scripts.append(a["src"])
        if tag == "script" and "data-embed" in a:
            self._embed = [a["data-embed"], a.get("type", ""), [], line]
        if "data-panel-target" in a:
            self.panel_targets.append(a["data-panel-target"])
        if "data-panel" in a:
            self.panels.append(a["data-panel"])
        style = a.get("style", "")
        if style:
            self._check_style(tag, style, line)
        if tag in SVG_SHAPES and not self._in_defs and not (
                "class" in a or "fill" in a or re.search(r"fill\s*:", style)):
            self.svg_issues.append(f"<{tag}> at line {line} has no fill/class (renders black)")
        if tag not in VOID:
            self.stack.append((tag, line))

    def handle_data(self, data):
        if self._embed is not None:
            self._embed[2].append(data)

    def handle_startendtag(self, tag, attrs):
        # self-closing: process as a start without pushing
        pushed = len(self.stack)
        self.handle_starttag(tag, attrs)
        del self.stack[pushed:]
        if tag == "nav":
            self._in_nav -= 1
        if tag == "defs":
            self._in_defs -= 1

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag == "script" and self._embed is not None:
            n, ty, parts, ln = self._embed
            self.embeds.append((n, ty, "".join(parts), ln))
            self._embed = None
        if not self.stack:
            self.errors.append(f"stray </{tag}> at line {self.getpos()[0]}")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
        elif any(t == tag for t, _ in self.stack):
            while self.stack and self.stack[-1][0] != tag:
                t, ln = self.stack.pop()
                self.errors.append(f"<{t}> opened at line {ln} is never closed")
            self.stack.pop()
        else:
            self.errors.append(f"stray </{tag}> at line {self.getpos()[0]}")
        if tag == "nav":
            self._in_nav = max(0, self._in_nav - 1)
        if tag == "defs":
            self._in_defs = max(0, self._in_defs - 1)

    def _check_style(self, tag: str, style: str, line: int) -> None:
        decl = {}
        for part in style.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                decl[k.strip().lower()] = v.strip().lower()
        bg = decl.get("background-color") or decl.get("background")
        if bg and "color" not in decl:
            lum = _luminance(bg)
            if lum is not None and lum < 0.2:
                self.style_issues.append(
                    f"<{tag}> at line {line} has a dark inline background and no text colour")

    def close_report(self) -> list[str]:
        out = list(self.errors)
        for t, ln in self.stack:
            out.append(f"<{t}> opened at line {ln} is never closed")
        return out


_NAMED_DARK = {"black": 0.0, "navy": 0.02, "darkblue": 0.02, "midnightblue": 0.02,
               "darkslategray": 0.07, "darkgreen": 0.1, "maroon": 0.05, "dimgray": 0.14}


def _luminance(value: str) -> float | None:
    """Relative luminance of the first colour in a CSS value, or None if unknown."""
    v = value.strip()
    if v in _NAMED_DARK:
        return _NAMED_DARK[v]
    m = re.search(r"#([0-9a-f]{3}|[0-9a-f]{6})\b", v)
    rgb = None
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    else:
        m = re.search(r"rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)", v)
        if m:
            rgb = tuple(int(x) for x in m.groups())
    if rgb is None:
        return None

    def lin(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# --------------------------------------------------------------------- the gate
def _visible_text(raw: str) -> str:
    return _TAG.sub(" ", _SCRIPT_STYLE.sub(" ", raw))


def check_site(site_dir: str) -> list[Finding]:
    """Run all rules over ``site_dir``. Fail-closed."""
    root = Path(site_dir)
    out: list[Finding] = []
    if not root.is_dir():
        return [Finding(str(site_dir), "site.missing", "site directory does not exist")]
    index = root / "index.html"
    if not index.is_file():
        out.append(Finding("index.html", "site.no_entry", "entry page index.html is missing"))

    files = sorted(p for p in root.rglob("*") if p.is_file())
    pages = [p for p in files if p.suffix.lower() in {".html", ".htm"}]
    page_ids: dict[str, set[str]] = {}
    scans: dict[Path, _Scan] = {}

    for p in files:
        rel = p.relative_to(root).as_posix()
        if p.suffix.lower() in TEXT_SUFFIXES:
            try:
                raw = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                out.append(Finding(rel, "file.unreadable", f"cannot read as UTF-8 text: {exc}"))
                continue
            if _MASKER.mask_text(raw) != raw:  # leak -> never echo the value
                out.append(Finding(rel, "secret.leak",
                                   "a secret-like value (token/key/password/credential) is present"))
        else:
            continue
        if p in pages:
            sc = _Scan()
            try:
                sc.feed(raw)
                sc.close()
            except Exception as exc:  # parser failure is fail-closed
                out.append(Finding(rel, "html.malformed", f"HTML could not be parsed: {exc}"))
                continue
            scans[p] = sc
            page_ids[rel] = sc.ids
            for msg in sc.close_report():
                out.append(Finding(rel, "html.unpaired", msg))
            for msg in sc.style_issues:
                out.append(Finding(rel, "a11y.dark_on_dark", msg))
            for msg in sc.svg_issues:
                out.append(Finding(rel, "a11y.unset_fill", msg))
            text = _visible_text(raw)
            if _TODO.search(text):
                out.append(Finding(rel, "hygiene.todo", "a TODO marker is left in the page"))
            for m in _TZ_DT.finditer(text + " " + " ".join(re.findall(r'datetime="([^"]*)"', raw))):
                if not m.group("tz"):
                    out.append(Finding(rel, "hygiene.datetime_no_tz",
                                       f"datetime {m.group(0)!r} has no timezone"))
            # in-page panels
            for t in sc.panel_targets:
                if t not in sc.panels:
                    out.append(Finding(rel, "nav.no_panel", f"tab {t!r} has no matching panel"))
            for pn in sc.panels:
                if pn not in sc.panel_targets:
                    out.append(Finding(rel, "nav.orphan_panel", f"panel {pn!r} has no tab"))

    # ---- site-level nav <-> panel (page) consistency, from the entry page's nav
    if index in scans:
        nav_files: set[str] = set()
        for href in scans[index].nav_hrefs:
            parts = urlsplit(href)
            if parts.scheme or parts.netloc:
                continue
            target = unquote(parts.path) or "index.html"
            nav_files.add(target)
            if not (root / target).is_file() or not is_under(root / target, root):
                out.append(Finding("index.html", "nav.no_panel",
                                   f"nav entry {href!r} leads to no page"))
        for p in pages:
            rel = p.relative_to(root).as_posix()
            if "/" not in rel and rel not in nav_files:
                out.append(Finding(rel, "nav.orphan_panel",
                                   "page has no entry in the navigation"))
        if scans[index].nav_hrefs == []:
            out.append(Finding("index.html", "nav.empty", "entry page has no navigation"))
        # conclusion first
        main = re.search(r"(?is)<main\b.*?>(.*?)</main>", index.read_text(encoding="utf-8"))
        if main and 'id="conclusion"' not in main.group(1):
            out.append(Finding("index.html", "structure.no_conclusion",
                               "entry page has no #conclusion section"))

    out.extend(_check_data(root, scans, files))

    # ---- internal links / anchors
    for p, sc in scans.items():
        rel = p.relative_to(root).as_posix()
        for href in sc.hrefs:
            parts = urlsplit(href)
            if parts.scheme in {"http", "https", "mailto", "tel", "data"} or parts.netloc:
                continue
            if parts.scheme:
                continue
            target = p if not parts.path else (p.parent / unquote(parts.path))
            if parts.path and (not is_under(target, root) or not target.exists()):
                out.append(Finding(rel, "link.broken", f"link {href!r} does not resolve"))
                continue
            if parts.fragment and target.suffix.lower() in {".html", ".htm"}:
                trel = target.resolve().relative_to(root.resolve()).as_posix()
                ids = page_ids.get(trel)
                if ids is not None and unquote(parts.fragment) not in ids and parts.fragment != "top":
                    out.append(Finding(rel, "link.broken_anchor",
                                       f"anchor {href!r} has no matching id"))
    return out


def _check_data(root: Path, scans: dict[Path, _Scan], files: list[Path]) -> list[Finding]:
    """Data-binding integrity (``data.*`` rules). Fail-closed."""
    out: list[Finding] = []
    parsed: dict[str, object] = {}  # file name -> parsed data, or _BAD

    def get(name: str) -> object:
        if name in parsed:
            return parsed[name]
        path = root / DATA_DIR / name
        rel = f"{DATA_DIR}/{name}"
        parsed[name] = None
        if not path.is_file():
            return None
        try:
            parsed[name] = parse_data_file(name, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            out.append(Finding(rel, "data.unreadable", f"cannot read data file: {exc}"))
            parsed[name] = _BAD
        except ValueError as exc:
            rule = "data.invalid_json" if name.lower().endswith(".json") else "data.invalid_csv"
            out.append(Finding(rel, rule, f"malformed data file: {exc}"))
            parsed[name] = _BAD
        return parsed[name]

    def ref_ok(rel: str, name: str, attr: str) -> bool:
        if not DATA_FILE_RE.match(name):
            out.append(Finding(rel, "data.bad_ref",
                               f"{attr}={name!r} must name a .json/.csv file in {DATA_DIR}/"))
            return False
        if not (root / DATA_DIR / name).is_file():
            out.append(Finding(rel, "data.missing_file",
                               f"{attr} references {DATA_DIR}/{name}, which does not exist"))
            return False
        return True

    using: list[str] = []
    for p, sc in scans.items():
        rel = p.relative_to(root).as_posix()
        for name, ty, payload, line in sc.embeds:
            _check_embed(out, rel, name, ty, payload, line, get, root)
        if not (sc.data_tables or sc.data_values):
            continue
        using.append(rel)
        embedded = {e[0] for e in sc.embeds}
        for name in dict.fromkeys([t[0] for t in sc.data_tables] + [v[0].partition("#")[0]
                                                                   for v in sc.data_values]):
            if DATA_FILE_RE.match(name) and name not in embedded:
                out.append(Finding(rel, "data.embed_missing",
                                   f"{name} is not embedded inline; the page needs HTTP, "
                                   "not file://, to show it", WARNING))
        if not any(s.split("?")[0].split("#")[0] == DATA_JS_PATH for s in sc.scripts):
            out.append(Finding(rel, "data.script_unlinked",
                               f"page uses data containers but does not load {DATA_JS_PATH}"))
        for name, cols, line in sc.data_tables:
            if not ref_ok(rel, name, f"data-table (line {line})"):
                continue
            data = get(name)
            if data is _BAD:
                continue
            if not (isinstance(data, list) and all(isinstance(r, dict) for r in data)):
                out.append(Finding(rel, "data.table_shape",
                                   f"{name} must be a list of row objects to fill a table (line {line})"))
                continue
            for c in [c.strip() for c in cols.split(",") if c.strip()]:
                if not any(c in r for r in data):
                    out.append(Finding(rel, "data.missing_field",
                                       f"data-columns names {c!r}, absent from {name} (line {line})"))
        for ref, line in sc.data_values:
            name, _, field_ = ref.partition("#")
            if not field_:
                out.append(Finding(rel, "data.bad_ref",
                                   f"data-value={ref!r} must be '<file>#<field>' (line {line})"))
                continue
            if not ref_ok(rel, name, f"data-value (line {line})"):
                continue
            data = get(name)
            if data is _BAD:
                continue
            if not lookup_field(data, field_):
                out.append(Finding(rel, "data.missing_field",
                                   f"{name} has no field {field_!r} (line {line})"))

    # every file in site_data/ must be valid, referenced or not
    for p in files:
        rel = p.relative_to(root).as_posix()
        if rel.startswith(DATA_DIR + "/") and p.suffix.lower() in {".json", ".csv"}:
            if p.parent == root / DATA_DIR:
                get(p.name)

    script = root / DATA_JS_PATH
    if script.is_file():
        try:
            text = script.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out.append(Finding(DATA_JS_PATH, "data.script_modified", f"cannot read script: {exc}"))
        else:
            if not script_is_pristine(text):
                out.append(Finding(DATA_JS_PATH, "data.script_modified",
                                   "binding script lost its generated marker or was modified; "
                                   "regenerate it, never hand-edit"))
    elif using:
        out.append(Finding(DATA_JS_PATH, "data.script_missing",
                           "pages use data containers but the generated binding script is missing"))
    return out


_BAD = object()


def _check_embed(out: list[Finding], rel: str, name: str, ty: str, payload: str, line: int,
                 get, root: Path) -> None:
    """An inline embed must be non-executed JSON equal to its ``site_data/`` file."""
    where = f"embed {name!r} (line {line})"
    if ty.strip().lower() != "application/json":
        out.append(Finding(rel, "data.embed_type",
                           f"{where} must use type=\"application/json\" (data, never executed)"))
        return
    if not DATA_FILE_RE.match(name) or not (root / DATA_DIR / name).is_file():
        out.append(Finding(rel, "data.embed_unknown",
                           f"{where} has no matching {DATA_DIR}/ file"))
        return
    try:
        value = json.loads(payload)
    except ValueError as exc:
        out.append(Finding(rel, "data.embed_invalid", f"{where} is not valid JSON: {exc}"))
        return
    data = get(name)
    if data is _BAD:
        return
    try:
        if name.lower().endswith(".csv"):
            same = isinstance(value, str) and parse_data_file(name, value) == data
        else:
            same = value == data
    except ValueError:
        same = False
    if not same:
        out.append(Finding(rel, "data.embed_drift",
                           f"{where} differs from {DATA_DIR}/{name}; rebuild the site"))


def format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "ALL PASS (0 findings)"
    lines = [f"{f.severity.upper():7} {f.path}: [{f.rule}] {f.message}" for f in findings]
    n = sum(1 for f in findings if f.severity == ERROR)
    lines.append(f"{'FAIL' if n else 'PASS'}: {n} error(s), {len(findings) - n} warning(s)")
    return "\n".join(lines)
