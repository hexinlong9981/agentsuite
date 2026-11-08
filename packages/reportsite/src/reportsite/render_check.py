"""``check_render``: fail-closed checks for diagrams and the theme tokens behind them.

Rules (all errors unless noted): ``diagram.unset_fill``, ``diagram.black_fill``,
``diagram.undefined_token``, ``diagram.stretched``, ``diagram.hardcoded_color`` (warning),
``diagram.no_theme`` and ``a11y.contrast``. Sites without diagrams and without a
stylesheet pass untouched.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from .check_site import ERROR, WARNING, Finding
from .palette import DIAGRAM_PAIRS, PAIRS, verify_palette
from .theme import CSS_PATH

SHAPES = {"rect", "circle", "ellipse", "path", "polygon", "polyline", "text", "line"}
_VAR_USE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,[^)]*)?\)")
_DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;}]+)")
_COMMENT = re.compile(r"(?s)/\*.*?\*/")
_STYLE_BLOCK = re.compile(r"(?is)<style\b[^>]*>(.*?)</style\s*>")
_BLACK = {"#000", "#000000", "black", "rgb(0,0,0)", "rgb(0, 0, 0)"}
_OK_LITERAL = {"none", "transparent", "currentcolor", "inherit"}
_STRETCH_CSS = re.compile(r"svg[^{}]*\{[^}]*\bwidth\s*:\s*(?:100%|100vw)")


def _decls(style: str) -> dict[str, str]:
    d = {}
    for part in style.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            d[k.strip().lower()] = v.strip()
    return d


class _SvgScan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.has_svg = False
        self.issues: list[tuple[str, str]] = []  # (rule, message)
        self.warnings: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        line = self.getpos()[0]
        if tag == "svg":
            self.depth += 1
            self.has_svg = True
            w = a.get("width", "").strip()
            st = _decls(a.get("style", ""))
            sw = st.get("width", "").strip()
            if w.endswith("%") or sw.endswith("%") or sw.endswith("vw") or w.endswith("vw"):
                self.issues.append(("diagram.stretched",
                                    f"<svg> at line {line} is sized relative to its container "
                                    f"({w or sw}); draw at natural size"))
            if a.get("preserveaspectratio", "").strip().lower() == "none":
                self.issues.append(("diagram.stretched",
                                    f'<svg> at line {line} uses preserveAspectRatio="none" (distorts)'))
            if "viewbox" not in a and not (w and a.get("height")):
                self.issues.append(("diagram.stretched",
                                    f"<svg> at line {line} has neither viewBox nor width/height"))
            return
        if not self.depth or tag not in SHAPES:
            return
        st = _decls(a.get("style", ""))
        if tag == "line":
            if not (a.get("stroke") or st.get("stroke")):
                self.issues.append(("diagram.unset_fill", f"<line> at line {line} has no explicit stroke"))
            return
        fill = (st.get("fill") or a.get("fill") or "").strip()
        if not fill:
            self.issues.append(("diagram.unset_fill",
                                f"<{tag}> at line {line} has no explicit fill (defaults to black)"))
            return
        low = fill.lower().replace(" ", "")
        if low in _BLACK:
            self.issues.append(("diagram.black_fill", f"<{tag}> at line {line} is filled black"))
        elif not low.startswith("var(") and low not in _OK_LITERAL:
            self.warnings.append(("diagram.hardcoded_color",
                                  f"<{tag}> at line {line} uses fixed colour {fill!r}, not a theme token"))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag == "svg":
            self.depth -= 1

    def handle_endtag(self, tag):
        if tag == "svg" and self.depth:
            self.depth -= 1


def _blocks(css: str) -> tuple[dict[str, str], dict[str, str]]:
    css = _COMMENT.sub("", css)
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    m = re.search(r":root\s*\{([^}]*)\}", css)
    if m:
        light = {k: v.strip() for k, v in _DECL.findall(m.group(1))}
    m = re.search(r':root\[data-theme="dark"\]\s*\{([^}]*)\}', css)
    if m:
        dark = {k: v.strip() for k, v in _DECL.findall(m.group(1))}
    return light, dark


def check_render(site_dir: str) -> list[Finding]:
    """Verify diagrams and tokens in a built site. Fail-closed; returns Findings."""
    root = Path(site_dir)
    if not root.is_dir():
        return [Finding(str(site_dir), "site.missing", "site directory does not exist")]
    out: list[Finding] = []
    pages = sorted(p for p in root.rglob("*") if p.suffix.lower() in {".html", ".htm"} and p.is_file())
    css_file = root / CSS_PATH
    css_text = ""
    if css_file.is_file():
        try:
            css_text = css_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out.append(Finding(CSS_PATH, "diagram.no_theme", f"stylesheet unreadable: {exc}"))

    defined = {k for k, _ in _DECL.findall(_COMMENT.sub("", css_text))}
    any_svg = False
    uses: list[tuple[str, str, str]] = []  # (rel, token, where)
    for p in pages:
        rel = p.relative_to(root).as_posix()
        try:
            raw = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # check_site reports unreadable files
        sc = _SvgScan()
        try:
            sc.feed(raw)
            sc.close()
        except Exception as exc:  # noqa: BLE001 - fail-closed
            out.append(Finding(rel, "diagram.malformed", f"HTML could not be parsed: {exc}"))
            continue
        any_svg = any_svg or sc.has_svg
        for rule, msg in sc.issues:
            out.append(Finding(rel, rule, msg))
        for rule, msg in sc.warnings:
            out.append(Finding(rel, rule, msg, WARNING))
        for blk in _STYLE_BLOCK.findall(raw):
            defined.update(k for k, _ in _DECL.findall(_COMMENT.sub("", blk)))
            if _STRETCH_CSS.search(blk):
                out.append(Finding(rel, "diagram.stretched", "a <style> rule forces svg width to 100%"))
        if sc.has_svg or "var(" in raw:
            for tok in sorted({m.group(1) for m in _VAR_USE.finditer(raw)}):
                uses.append((rel, tok, "page"))
    if css_text:
        if _STRETCH_CSS.search(_COMMENT.sub("", css_text)):
            out.append(Finding(CSS_PATH, "diagram.stretched", "stylesheet forces svg width to 100%"))
        for tok in sorted({m.group(1) for m in _VAR_USE.finditer(_COMMENT.sub("", css_text))}):
            uses.append((CSS_PATH, tok, "stylesheet"))
    for rel, tok, _ in uses:
        if tok not in defined:
            out.append(Finding(rel, "diagram.undefined_token",
                               f"custom property {tok} is used but never defined"))

    if css_text:
        light, dark = _blocks(css_text)
        pairs = PAIRS if any_svg or any(t.startswith("--rs-d-") for t in light) else \
            [p for p in PAIRS if p not in DIAGRAM_PAIRS]
        if not light:
            out.append(Finding(CSS_PATH, "a11y.contrast", "no :root token block found in the stylesheet"))
        else:
            for f in verify_palette(light, dark, pairs):
                out.append(Finding(CSS_PATH, "a11y.contrast", f.message()))
    elif any_svg:
        out.append(Finding(CSS_PATH, "diagram.no_theme",
                           "a page has a diagram but the theme stylesheet is missing"))
    return out
