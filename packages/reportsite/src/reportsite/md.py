"""Markdown -> report page HTML. Safe by construction: no raw HTML passes through.

The built-in converter (always available, offline) supports headings, paragraphs, ordered/
unordered (nested) lists, fenced code, block quotes, rules, simple pipe tables, and inline
code / bold / italic / links. All text is HTML-escaped first; link targets are limited to
http(s), mailto, relative paths and ``#`` anchors (anything else renders as plain text).
The optional ``markdown`` library is used only for ``engine="markdown"`` (or ``auto`` when
no table-as-data is requested and the library imports); its input is escaped first and its
output links are re-filtered, so the same safety holds.

Mapping: a Markdown heading level L becomes ``h{L - 1 + base_level}`` (clamped to h2..h6; the page title is the h1) with a stable
``id`` anchor. A pipe table becomes a static ``<table>`` or, with ``tables_as_data=True``, a
a ``<div data-table="...json">`` container whose rows are collected into ``data_out``
(merge it into ``SiteSpec.data``).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from agent_policy_kit import safe_name

from .errors import MarkdownError
from .generator import PageSpec, SiteSpec

MAX_BYTES = 2_000_000
CONCLUSION_TITLES = ("conclusion", "conclusions", "summary", "executive summary", "overview")

_FENCE = re.compile(r"^\s{0,3}(```|~~~)\s*([\w+-]*)\s*$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_TSEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_SAFE_URL = re.compile(r"^(https?://|mailto:|#|[^:]*$)", re.I)


@dataclass
class MdResult:
    body_html: str
    data: dict[str, Any] = field(default_factory=dict)


def slugify(text: str, default: str = "section") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or default


# ---------------------------------------------------------------- inline
def _safe_href(url: str) -> str | None:
    u = html.unescape(url).strip()
    if re.search(r"[\x00-\x20\\]", u) or not _SAFE_URL.match(u):
        return None
    return u


def inline(text: str) -> str:
    """Escape ``text`` and apply code / bold / italic / link."""
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(f"<code>{html.escape(m.group(1).strip())}</code>")
        return f"\x00{len(codes) - 1}\x00"

    t = re.sub(r"`([^`]+)`", stash, text)
    t = html.escape(t, quote=True)

    def link(m: re.Match) -> str:
        href = _safe_href(m.group(2))
        if href is None:
            return m.group(1)
        return f'<a href="{html.escape(href, quote=True)}">{m.group(1)}</a>'

    t = _LINK.sub(link, t)
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", t)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], t)


# ---------------------------------------------------------------- blocks
def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", s)]


def _list(lines: list[str], i: int) -> tuple[str, int]:
    """Parse a (nested) list starting at ``i``; return (html, next index)."""
    def block(i: int, indent: int) -> tuple[str, int]:
        m = _ITEM.match(lines[i])
        tag = "ol" if m.group(2)[0].isdigit() else "ul"
        out = [f"<{tag}>"]
        while i < len(lines):
            m = _ITEM.match(lines[i])
            if (not m or len(m.group(1).expandtabs(4)) != indent
                    or ("ol" if m.group(2)[0].isdigit() else "ul") != tag):
                break
            item = [inline(m.group(3))]
            i += 1
            while i < len(lines):
                n = _ITEM.match(lines[i])
                if n and len(n.group(1).expandtabs(4)) > indent:
                    sub, i = block(i, len(n.group(1).expandtabs(4)))
                    item.append(sub)
                elif (lines[i].strip() and not n and lines[i].startswith(" ")
                      and not _HEADING.match(lines[i])):
                    item.append(" " + inline(lines[i].strip()))
                    i += 1
                else:
                    break
            out.append("<li>" + "".join(item) + "</li>")
        out.append(f"</{tag}>")
        return "".join(out), i

    return block(i, len(_ITEM.match(lines[i]).group(1).expandtabs(4)))


def _builtin(md: str, base_level: int, tables_as_data: bool, data_prefix: str,
             data_out: dict[str, Any]) -> str:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    ids: set[str] = set()
    para: list[str] = []
    ntab = 0

    def flush() -> None:
        if para:
            out.append("<p>" + " ".join(inline(p.strip()) for p in para) + "</p>")
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        fm = _FENCE.match(line)
        if fm:
            flush()
            fence, lang = fm.group(1), fm.group(2)
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(code))}</code></pre>")
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        hm = _HEADING.match(line)
        if hm:
            flush()
            lvl = min(6, max(2, len(hm.group(1)) - 1 + base_level))
            text = hm.group(2)
            base = slugify(re.sub(r"[`*_]", "", text))
            hid, n = base, 2
            while hid in ids:
                hid, n = f"{base}-{n}", n + 1
            ids.add(hid)
            out.append(f'<h{lvl} id="{hid}">{inline(text)}</h{lvl}>')
            i += 1
            continue
        if _RULE.match(line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and _TSEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            head = _cells(line)
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(_cells(lines[i]))
                i += 1
            if tables_as_data:
                ntab += 1
                name = f"{data_prefix}-{ntab}.json"
                keys: list[str] = []
                for h in head:
                    k, n = h or "column", 2
                    while k in keys:
                        k, n = f"{h or 'column'} {n}", n + 1
                    keys.append(k)
                data_out[name] = [{k: (r[j] if j < len(r) else "") for j, k in enumerate(keys)}
                                  for r in rows]
                out.append(f'<div data-table="{name}"></div>')
            else:
                th = "".join(f'<th scope="col">{inline(h)}</th>' for h in head)
                tb = "".join("<tr>" + "".join(
                    f"<td>{inline(r[j] if j < len(r) else '')}</td>" for j in range(len(head)))
                    + "</tr>" for r in rows)
                out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>")
            continue
        if _ITEM.match(line):
            flush()
            h, i = _list(lines, i)
            out.append(h)
            continue
        if line.lstrip().startswith(">"):
            flush()
            q: list[str] = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                q.append(lines[i].lstrip()[1:].lstrip())
                i += 1
            out.append("<blockquote>" + _builtin("\n".join(q), base_level, False, "", {})
                       + "</blockquote>")
            continue
        para.append(line)
        i += 1
    flush()
    return "\n".join(out)


def _via_library(md: str, base_level: int) -> str:
    import markdown  # optional dependency

    esc = md.replace("&", "&amp;").replace("<", "&lt;")
    h = markdown.markdown(esc, extensions=["tables", "fenced_code"])

    def hd(m: re.Match) -> str:
        lvl = min(6, max(2, int(m.group(2)) - 1 + base_level))
        return f"<{m.group(1)}h{lvl}"

    h = re.sub(r"(<|</)h([1-6])", hd, h)

    def href(m: re.Match) -> str:
        safe = _safe_href(m.group(1))
        return f'href="{html.escape(safe, quote=True)}"' if safe is not None else 'href="#"'

    return re.sub(r'href="([^"]*)"', href, h)


def _have_library() -> bool:
    try:
        import markdown  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------- public API
def _check(md_text: str) -> str:
    if not isinstance(md_text, str):
        raise MarkdownError("markdown input must be a string")
    if len(md_text.encode("utf-8")) > MAX_BYTES:
        raise MarkdownError(f"markdown input exceeds {MAX_BYTES} bytes")
    return md_text


def convert(md_text: str, *, base_level: int = 2, tables_as_data: bool = False,
            data_prefix: str = "table", engine: str = "auto") -> MdResult:
    """Convert Markdown to page-body HTML (+ collected table data)."""
    md = _check(md_text)
    if engine not in ("auto", "builtin", "markdown"):
        raise MarkdownError(f"unknown engine {engine!r}")
    if not 0 <= base_level <= 6:
        raise MarkdownError("base_level must be 0..6")
    if safe_name(data_prefix, default="") != data_prefix or not data_prefix:
        raise MarkdownError("data_prefix must be a safe name")
    use_lib = engine == "markdown" or (engine == "auto" and not tables_as_data
                                       and _have_library())
    if engine == "markdown" and tables_as_data:
        raise MarkdownError("tables_as_data needs the built-in engine")
    if use_lib:
        try:
            return MdResult(_via_library(md, base_level))
        except ImportError as exc:
            raise MarkdownError("the optional 'markdown' library is not installed") from exc
    data: dict[str, Any] = {}
    return MdResult(_builtin(md, base_level, tables_as_data, data_prefix, data), data)


def md_to_body(md_text: str, *, base_level: int = 2, tables_as_data: bool = False,
               data_out: dict[str, Any] | None = None, data_prefix: str = "table",
               engine: str = "auto") -> str:
    """Single-page body HTML. Collected table data (if requested) is merged into ``data_out``."""
    r = convert(md_text, base_level=base_level, tables_as_data=tables_as_data,
                data_prefix=data_prefix, engine=engine)
    if data_out is not None:
        data_out.update(r.data)
    return r.body_html


def _split(md_text: str, level: int) -> tuple[str, list[tuple[str, str]]]:
    """Split at headings of exactly ``level`` hashes (outside code fences)."""
    pre: list[str] = []
    secs: list[tuple[str, list[str]]] = []
    fence = None
    for line in _check(md_text).replace("\r\n", "\n").split("\n"):
        fm = _FENCE.match(line)
        if fm:
            fence = None if fence == fm.group(1) else (fence or fm.group(1))
        hm = None if fence else _HEADING.match(line)
        if hm and len(hm.group(1)) == level:
            secs.append((hm.group(2), []))
        elif secs:
            secs[-1][1].append(line)
        else:
            pre.append(line)
    return "\n".join(pre), [(t, "\n".join(b)) for t, b in secs]


def _doc_split(md_text: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(doc title, preamble, sections): one leading ``#`` is the title and ``##`` split."""
    pre, secs = _split(md_text, 1)
    if len(secs) == 1 and not pre.strip():
        title, body = secs[0]
        pre2, secs2 = _split(body, 2)
        return title, pre2, secs2
    return "", pre, secs


def _unique_ids(titles: list[str], reserved: set[str]) -> list[str]:
    seen, ids = set(reserved), []
    for n, t in enumerate(titles, 1):
        base = safe_name(slugify(t, f"section-{n}"), default=f"section-{n}")
        pid, k = base, 2
        while pid.lower() in seen:
            pid, k = f"{base}-{k}", k + 1
        seen.add(pid.lower())
        ids.append(pid)
    return ids


def md_to_pages(md_text: str, *, tables_as_data: bool = False,
                data_out: dict[str, Any] | None = None, engine: str = "auto",
                intro_title: str = "Introduction") -> list[PageSpec]:
    """One page per top-level (``#``) heading; text before the first heading becomes an
    ``intro_title`` page. Within a page, ``##`` becomes ``h2``."""
    pre, secs = _split(md_text, 1)
    items = ([(intro_title, pre)] if pre.strip() else []) + secs
    if not items:
        raise MarkdownError("no top-level (#) headings or text to turn into pages")
    ids = _unique_ids([t for t, _ in items], {"index", "diagram"})
    pages = []
    for pid, (t, body) in zip(ids, items):
        pages.append(PageSpec(id=pid, title=re.sub(r"[`*_]", "", t).strip() or pid,
                              body_html=md_to_body(body, base_level=1, tables_as_data=tables_as_data,
                                                   data_out=data_out, engine=engine,
                                                   data_prefix=f"{pid}-table")))
    return pages


def spec_from_markdown(md_text: str, *, title: str | None = None, preset: Any = None,
                       tables_as_data: bool = False, engine: str = "auto",
                       lang: str = "en") -> SiteSpec:
    """A SiteSpec from one Markdown document.

    A single leading ``#`` is the site title and ``##`` sections become pages (otherwise
    ``#`` sections do). A section titled Conclusion/Summary/Overview, or else the text before
    the first section, is the entry-page conclusion. With ``preset`` (name or Preset), sections
    whose title matches a preset page are ordered by it and the preset's diagram slot is kept.
    """
    from .presets import Preset, get_preset, placeholder_diagram

    doc_title, pre, secs = _doc_split(md_text)
    site_title = (title or doc_title or "").strip()
    if not site_title:
        raise MarkdownError("no title: start the document with '# Title' or pass title=")
    data: dict[str, Any] = {}
    bl = 0 if doc_title else 1  # '##' sections under a '#' title: their '###' becomes h2
    conclusion = ""
    rest = []
    for t, b in secs:
        if not conclusion and t.strip().lower() in CONCLUSION_TITLES:
            conclusion = md_to_body(b, base_level=bl, tables_as_data=tables_as_data,
                                    data_out=data, engine=engine, data_prefix="conclusion-table")
        else:
            rest.append((t, b))
    if not conclusion and pre.strip():
        conclusion = md_to_body(pre, base_level=bl, tables_as_data=tables_as_data,
                                data_out=data, engine=engine, data_prefix="intro-table")
    if not conclusion:
        raise MarkdownError("no conclusion: add a 'Conclusion'/'Summary' section "
                            "or text before the first section")
    p: Preset | None = None
    if preset is not None:
        p = preset if isinstance(preset, Preset) else get_preset(preset)
        order = {t.lower(): n for n, (pid, t) in enumerate(p.pages)}
        order.update({pid: n for n, (pid, t) in enumerate(p.pages)})
        key = lambda x: order.get(x[1][0].strip().lower(), len(order))  # noqa: E731
        rest = [s for _, s in sorted(enumerate(rest), key=lambda x: (key(x), x[0]))]
    ids = _unique_ids([t for t, _ in rest], {"index", "diagram"})
    pages = [PageSpec(id=pid, title=re.sub(r"[`*_]", "", t).strip() or pid,
                      body_html=md_to_body(b, base_level=bl, tables_as_data=tables_as_data,
                                           data_out=data, engine=engine,
                                           data_prefix=f"{pid}-table"))
             for pid, (t, b) in zip(ids, rest)]
    return SiteSpec(title=site_title, pages=pages, conclusion_html=conclusion, lang=lang,
                    data=data, diagram_html=placeholder_diagram() if p and p.diagram else "")
