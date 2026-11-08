"""reportsite — accessible, data-separated HTML report sites for AI agents.

Build a report site (entry page + one page per topic, stable navigation, light/
dark themes), bind display data from data files, draw theme-aware diagrams, and
verify everything with a fail-closed completion gate (`check_site` / `check_render`)
that also refuses to emit a site containing a secret. Built on the
agent-policy-kit core. See docs/ARCHITECTURE.md for the model.

Features: core generator (`build_site`) and the completion checker (`check_site`);
data separation (`SiteSpec.data`, `data-table`/`data-value` containers, binding script);
theme-aware diagrams (`Diagram`), contrast-verified palette, `check_render`;
presets/skeletons (`skeleton_from_preset`) and Markdown-to-page conversion (`md_to_body`, `md_to_pages`);
portability (inline data embeds, opens from file://) and bundling (`build_bundle`, `check_bundle`).
"""

from __future__ import annotations

from .bundle import build_bundle, check_bundle
from .check_site import ERROR, WARNING, Finding, check_site, format_findings, passed
from .databind import DATA_JS_PATH, DATA_DIR
from .diagram import Diagram, diagram_page_body
from .palette import (DARK, LIGHT, ContrastFailure, contrast_ratio, resolve_token,
                      verify_palette)
from .render_check import check_render
from .errors import CheckFailed, MarkdownError, PresetError, ReportSiteError, SpecError
from .generator import BuildSummary, PageSpec, SiteSpec, build_site, spec_from_dict
from .md import md_to_body, md_to_pages, spec_from_markdown
from .presets import PRESETS, Preset, get_preset, list_presets, skeleton_from_preset

__version__ = "0.0.1"

__all__ = [
    "build_site", "check_site", "build_bundle", "check_bundle", "format_findings", "passed", "spec_from_dict",
    "check_render", "Diagram", "diagram_page_body", "contrast_ratio", "verify_palette",
    "resolve_token", "ContrastFailure", "LIGHT", "DARK", "SiteSpec", "PageSpec", "BuildSummary", "Finding", "ERROR", "WARNING",
    "md_to_body", "md_to_pages", "spec_from_markdown", "skeleton_from_preset", "PRESETS", "Preset",
    "get_preset", "list_presets", "MarkdownError", "PresetError", "DATA_JS_PATH", "DATA_DIR", "ReportSiteError", "SpecError", "CheckFailed", "__version__",
]
