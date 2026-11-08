"""Presets / skeletons: a named, ordered chapter structure for a report site.

A preset is only a list of pages (id + title) plus an optional "big picture" diagram slot.
``skeleton_from_preset`` returns an ordinary :class:`SiteSpec` with placeholder bodies, so the
author fills content instead of hand-writing navigation/theming boilerplate; it builds with
``build_site`` unchanged. Preset names and chapter titles are deliberately generic.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from .diagram import Diagram, diagram_page_body
from .errors import PresetError
from .generator import PageSpec, SiteSpec


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    pages: tuple[tuple[str, str], ...]  # (page id, title) in nav order
    diagram: bool = False  # declares a "big picture" diagram slot (diagram.html after index)
    conclusion_title: str = "Conclusion"


PRESETS: dict[str, Preset] = {p.name: p for p in (
    Preset("report", "General report: background, findings, recommendation, next steps.",
           (("background", "Background"), ("findings", "Findings"),
            ("recommendation", "Recommendation"), ("next-steps", "Next steps"))),
    Preset("investigation", "Investigation: question, evidence, analysis, impact, recommendation.",
           (("scope", "Question and scope"), ("evidence", "Evidence"), ("analysis", "Analysis"),
            ("impact", "Impact"), ("recommendation", "Recommendation"), ("appendix", "Appendix")),
           diagram=True),
    Preset("proposal", "Proposal: problem, goals, options, approach, plan, risks.",
           (("problem", "Problem"), ("goals", "Goals"), ("options", "Options"),
            ("approach", "Recommended approach"), ("plan", "Plan"), ("risks", "Risks")),
           diagram=True),
)}


def list_presets() -> list[Preset]:
    return list(PRESETS.values())


def get_preset(name: str) -> Preset:
    try:
        return PRESETS[name]
    except (KeyError, TypeError):
        raise PresetError(f"unknown preset {name!r}; available: {', '.join(PRESETS)}") from None


def placeholder_body(title: str) -> str:
    return (f'<p class="rs-placeholder">Fill in this section: '
            f"{html.escape(title)}.</p>")


def placeholder_diagram() -> str:
    """Markup for the diagram slot: a one-box placeholder to replace with the real picture."""
    d = Diagram(320, 90, "Big picture placeholder", "Replace with the overview diagram")
    d.add_box(10, 20, 300, 50, "Replace with the big picture", hot=True)
    return diagram_page_body(
        d, "<p>One picture of the whole situation, drawn before the details.</p>")


def skeleton_from_preset(preset: str | Preset, title: str, *, with_diagram: bool | None = None,
                         lang: str = "en") -> SiteSpec:
    """A SiteSpec with placeholder page bodies for ``preset``.

    ``with_diagram``: None follows the preset's diagram slot; True/False forces it.
    """
    p = preset if isinstance(preset, Preset) else get_preset(preset)
    if not isinstance(title, str) or not title.strip():
        raise PresetError("a site title is required")
    want = p.diagram if with_diagram is None else bool(with_diagram)
    return SiteSpec(
        title=title.strip(), lang=lang,
        conclusion_html=placeholder_body(p.conclusion_title),
        pages=[PageSpec(id=pid, title=t, body_html=placeholder_body(t)) for pid, t in p.pages],
        diagram_html=placeholder_diagram() if want else "")
