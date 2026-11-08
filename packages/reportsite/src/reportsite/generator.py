"""Build a report site: entry page + one page per topic, shared css/js, left nav."""

from __future__ import annotations

import html
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_policy_kit import contained_join, safe_name

from .databind import (DATA_DIR, DATA_JS, DATA_JS_PATH, embed_block, referenced_files,
                       render_data_file)
from .errors import SpecError
from .theme import CSS, CSS_PATH, JS, JS_PATH

GENERATOR_MARK = '<meta name="generator" content="reportsite">'
ENTRY_ID = "index"
DIAGRAM_ID = "diagram"


@dataclass
class PageSpec:
    id: str
    title: str
    body_html: str = ""


@dataclass
class SiteSpec:
    title: str
    pages: list[PageSpec] = field(default_factory=list)
    conclusion_html: str = ""
    overview_html: str = ""
    lang: str = "en"
    # display data: {file name in site_data/: content}. JSON: any value; CSV: list of row dicts.
    data: dict[str, Any] = field(default_factory=dict)
    # optional "big picture" page placed right after index (markup from diagram.py)
    diagram_html: str = ""
    diagram_title: str = "Big picture"


@dataclass
class BuildSummary:
    out_dir: str
    files: list[str]
    removed: list[str]


def spec_from_dict(data: dict[str, Any]) -> SiteSpec:
    if not isinstance(data, dict):
        raise SpecError("spec must be an object")
    pages = []
    for p in data.get("pages", []) or []:
        if not isinstance(p, dict):
            raise SpecError("each page must be an object")
        pages.append(PageSpec(id=str(p.get("id", "")), title=str(p.get("title", "")),
                              body_html=str(p.get("body_html", ""))))
    return SiteSpec(title=str(data.get("title", "")), pages=pages,
                    conclusion_html=str(data.get("conclusion_html", "")),
                    overview_html=str(data.get("overview_html", "")),
                    lang=str(data.get("lang", "en")),
                    data=dict(data.get("data") or {}),
                    diagram_html=str(data.get("diagram_html", "")),
                    diagram_title=str(data.get("diagram_title", "Big picture")))


def _validate(spec: SiteSpec) -> dict[str, str]:
    """Return {page id -> file name}; raise SpecError on a bad spec."""
    if not spec.title.strip():
        raise SpecError("site title is required")
    if not spec.conclusion_html.strip():
        raise SpecError("conclusion_html is required (the entry page leads with it)")
    files: dict[str, str] = {}
    seen: set[str] = set()
    if spec.diagram_html.strip():
        if not spec.diagram_title.strip():
            raise SpecError("diagram_title must not be empty")
        files[DIAGRAM_ID] = f"{DIAGRAM_ID}.html"
        seen.add(DIAGRAM_ID)
    for p in spec.pages:
        if not p.title.strip():
            raise SpecError(f"page {p.id!r} needs a title")
        sid = safe_name(p.id, default="")
        if not sid or sid != p.id:
            raise SpecError(f"page id {p.id!r} must be a safe name (letters, digits, . _ -)")
        if sid.lower() == ENTRY_ID:
            raise SpecError("page id 'index' is reserved for the entry page")
        if sid.lower() in seen:  # includes the reserved 'diagram' id when a diagram page exists
            raise SpecError(f"duplicate page id {sid!r}")
        seen.add(sid.lower())
        files[sid] = f"{sid}.html"
    return files


def _all_pages(spec: SiteSpec) -> list[PageSpec]:
    """Pages in nav order; the diagram page (if any) sits right after index."""
    if not spec.diagram_html.strip():
        return list(spec.pages)
    return [PageSpec(id=DIAGRAM_ID, title=spec.diagram_title,
                     body_html=spec.diagram_html)] + list(spec.pages)


def _nav(spec: SiteSpec, files: dict[str, str], current: str) -> str:
    items = [(ENTRY_ID, "index.html", "Overview")] + [
        (p.id, files[p.id], p.title) for p in _all_pages(spec)]
    lis = []
    for pid, href, title in items:
        cur = ' aria-current="page"' if pid == current else ""
        lis.append(f'<li><a class="rs-tab" data-nav="{html.escape(pid)}" '
                   f'href="{html.escape(href)}"{cur}>{html.escape(title)}</a></li>')
    return ('<nav class="rs-nav" aria-label="Report pages"><h2>'
            f'{html.escape(spec.title)}</h2><ul>' + "".join(lis) + "</ul>"
            '<div class="rs-toolbar"><button type="button" data-rs-theme-toggle>'
            "Toggle theme</button></div></nav>")


def _page(spec: SiteSpec, files: dict[str, str], current: str, title: str, main: str,
          rendered: dict[str, str] | None = None) -> str:
    embeds = "".join(embed_block(n, rendered[n])
                     for n in referenced_files(main) if rendered and n in rendered)
    return (
        "<!doctype html>\n"
        f'<html lang="{html.escape(spec.lang)}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{GENERATOR_MARK}\n"
        f"<title>{html.escape(title)} - {html.escape(spec.title)}</title>\n"
        f'<link rel="stylesheet" href="{CSS_PATH}">\n</head>\n<body>\n'
        '<div class="rs-layout">\n'
        f"{_nav(spec, files, current)}\n"
        f'<main class="rs-main" id="main">\n<h1>{html.escape(title)}</h1>\n{main}\n</main>\n'
        "</div>\n"
        f'<script src="{JS_PATH}"></script>\n'
        + embeds
        + (f'<script src="{DATA_JS_PATH}"></script>\n' if spec.data else "")
        + "</body>\n</html>\n"
    )


def _write(out_dir: str, rel: str, text: str) -> str:
    dest = contained_join(out_dir, rel)  # raises PathEscape if rel escapes out_dir
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
    return rel


def build_site(spec: SiteSpec | dict[str, Any], out_dir: str) -> BuildSummary:
    """Write the site under ``out_dir``, overwriting in place."""
    if isinstance(spec, dict):
        spec = spec_from_dict(spec)
    files = _validate(spec)
    if not isinstance(spec.data, dict):
        raise SpecError("data must be an object {file name: content}")
    rendered = {n: render_data_file(n, c) for n, c in spec.data.items()}  # validate first
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    cards = "".join(
        f'<li><a href="{html.escape(files[p.id])}">{html.escape(p.title)}</a></li>'
        for p in _all_pages(spec))
    index_main = (
        f'<section class="rs-conclusion" id="conclusion"><h2>Conclusion</h2>\n'
        f"{spec.conclusion_html}\n</section>\n"
        + (f'<section id="overview">{spec.overview_html}</section>\n' if spec.overview_html else "")
        + (f'<h2>Pages</h2>\n<ul class="rs-cards">{cards}</ul>' if cards else ""))
    written.append(_write(out_dir, "index.html",
                          _page(spec, files, ENTRY_ID, spec.title, index_main, rendered)))
    for p in _all_pages(spec):
        written.append(_write(out_dir, files[p.id],
                              _page(spec, files, p.id, p.title, p.body_html, rendered)))
    written.append(_write(out_dir, CSS_PATH, CSS))
    written.append(_write(out_dir, JS_PATH, JS))
    if spec.data:
        for n, text in rendered.items():
            written.append(_write(out_dir, f"{DATA_DIR}/{n}", text))
        written.append(_write(out_dir, DATA_JS_PATH, DATA_JS))

    # Overwrite in place: drop pages this generator wrote earlier that are no longer in the spec.
    removed: list[str] = []
    keep = set(written)
    for f in sorted(Path(out_dir).glob("*.html")):
        if f.name in keep:
            continue
        try:
            if GENERATOR_MARK in f.read_text(encoding="utf-8", errors="replace"):
                f.unlink()
                removed.append(f.name)
        except OSError:
            pass
    return BuildSummary(out_dir=str(Path(out_dir)), files=written, removed=removed)
