"""Theme-aware inline-SVG diagram builder.

Primitives (each carries an EXPLICIT fill/stroke, as CSS custom-property tokens, so
nothing falls back to SVG's default black and the diagram follows light/dark):

=============  ==========================  =====================================
primitive      class                       tokens
=============  ==========================  =====================================
box            ``rs-bx`` (+ ``rs-hot``)    ``--rs-d-box-fill/-stroke/-text``
connector      ``rs-ln``                   ``--rs-d-line``
label          ``rs-t`` (+ ``rs-tb``)      ``--rs-d-label`` / box text token
highlight      ``rs-hot-frame`` / ``rs-hot``  ``--rs-d-hot-fill/-stroke/-text``
=============  ==========================  =====================================

A diagram renders at its natural pixel size (``width``/``height`` + ``viewBox``); it is
never stretched to the container, so its text matches the body text size.
"""

from __future__ import annotations

import html
import math
import re

from .errors import SpecError

_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def _num(v: float, what: str, positive: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise SpecError(f"{what} must be a finite number")
    if positive and v <= 0:
        raise SpecError(f"{what} must be > 0")
    return float(v)


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


class Diagram:
    """Compose a diagram; ``render()`` returns the markup (a ``<figure class="rs-diagram">``)."""

    def __init__(self, width: int, height: int, title: str, description: str = "",
                 diagram_id: str = "d1") -> None:
        self.width = _num(width, "width", True)
        self.height = _num(height, "height", True)
        if not title.strip():
            raise SpecError("a diagram needs a title (its accessible name)")
        if not _ID.fullmatch(diagram_id):
            raise SpecError("diagram_id must be a safe id (letters, digits, _ -)")
        self.title, self.description, self.diagram_id = title, description, diagram_id
        self._boxes: dict[str, tuple[float, float, float, float]] = {}
        self._parts: list[str] = []
        self._n = 0
        self._has_arrow = False

    # ------------------------------------------------------------ primitives
    def add_box(self, x: float, y: float, w: float, h: float, text: str = "",
                box_id: str | None = None, hot: bool = False) -> str:
        """Add a labelled box (``\\n`` splits lines). ``hot`` emphasises it. Returns its id."""
        x, y = _num(x, "x"), _num(y, "y")
        w, h = _num(w, "w", True), _num(h, "h", True)
        self._n += 1
        bid = box_id or f"b{self._n}"
        if not _ID.fullmatch(bid):
            raise SpecError(f"box id {bid!r} must be a safe id")
        if bid in self._boxes:
            raise SpecError(f"duplicate box id {bid!r}")
        self._boxes[bid] = (x, y, w, h)
        if hot:
            cls = "rs-bx rs-hot"
            st = ("fill:var(--rs-d-hot-fill);stroke:var(--rs-d-hot-stroke);stroke-width:3")
            tfill = "var(--rs-d-hot-text)"
        else:
            cls = "rs-bx"
            st = ("fill:var(--rs-d-box-fill);stroke:var(--rs-d-box-stroke);stroke-width:1.5")
            tfill = "var(--rs-d-box-text)"
        self._parts.append(
            f'<rect id="{self.diagram_id}-{bid}" class="{cls}" x="{_f(x)}" y="{_f(y)}" '
            f'width="{_f(w)}" height="{_f(h)}" rx="6" style="{st}"/>')
        if text:
            self._text(x + w / 2, y + h / 2, text, "middle", tfill, "rs-t", centered=True)
        return bid

    def add_label(self, x: float, y: float, text: str, bold: bool = False,
                  anchor: str = "start") -> None:
        """Free text on the page background (uses the page-label token)."""
        if anchor not in {"start", "middle", "end"}:
            raise SpecError("anchor must be start, middle or end")
        self._text(_num(x, "x"), _num(y, "y"), text, anchor, "var(--rs-d-label)",
                   "rs-t rs-lbl" + (" rs-tb" if bold else ""), bold=bold)

    def add_connector(self, src: str | tuple[float, float], dst: str | tuple[float, float],
                      label: str = "", arrow: bool = True) -> None:
        """Line from a box id (or an (x, y) point) to another; optional arrowhead + label."""
        (x1, y1), (x2, y2) = self._endpoints(src, dst)
        marker = ""
        if arrow:
            self._has_arrow = True
            marker = f' marker-end="url(#{self.diagram_id}-arrow)"'
        self._parts.append(
            f'<path class="rs-ln" d="M{_f(x1)} {_f(y1)} L{_f(x2)} {_f(y2)}" '
            f'style="fill:none;stroke:var(--rs-d-line);stroke-width:2"{marker}/>')
        if label:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            lw = 7.5 * len(label) + 8
            self._parts.append(
                f'<rect class="rs-lblbg" x="{_f(mx - lw / 2)}" y="{_f(my - 11)}" width="{_f(lw)}" '
                f'height="22" style="fill:var(--rs-bg);stroke:none"/>')
            self._text(mx, my, label, "middle", "var(--rs-d-label)", "rs-t rs-lbl", centered=True)

    def add_highlight(self, target: str | tuple[float, float, float, float],
                      pad: float = 6) -> None:
        """Dashed emphasis frame around a box id or an (x, y, w, h) rectangle."""
        x, y, w, h = self._boxes[self._known(target)] if isinstance(target, str) else (
            tuple(_num(v, "highlight rect") for v in target))
        p = _num(pad, "pad")
        self._parts.append(
            f'<rect class="rs-hot-frame" x="{_f(x - p)}" y="{_f(y - p)}" width="{_f(w + 2 * p)}" '
            f'height="{_f(h + 2 * p)}" rx="8" style="fill:none;stroke:var(--rs-d-hot-stroke);'
            f'stroke-width:3;stroke-dasharray:6 4"/>')

    # -------------------------------------------------------------- internals
    def _known(self, bid: str) -> str:
        if bid not in self._boxes:
            raise SpecError(f"unknown box id {bid!r}")
        return bid

    def _endpoints(self, src, dst):
        def centre(t):
            if isinstance(t, str):
                x, y, w, h = self._boxes[self._known(t)]
                return (x + w / 2, y + h / 2), (x, y, w, h)
            return (_num(t[0], "x"), _num(t[1], "y")), None
        (cs, rs), (cd, rd) = centre(src), centre(dst)

        def edge(c, r, toward):
            if r is None:
                return c
            x, y, w, h = r
            dx, dy = toward[0] - c[0], toward[1] - c[1]
            if dx == 0 and dy == 0:
                return c
            # leave through the side the direction points at most
            if abs(dx) * h > abs(dy) * w:
                return (x + w if dx > 0 else x, c[1])
            return (c[0], y + h if dy > 0 else y)
        return edge(cs, rs, cd), edge(cd, rd, cs)

    def _text(self, x: float, y: float, text: str, anchor: str, fill: str, cls: str,
              centered: bool = False, bold: bool = False) -> None:
        lines = text.split("\n")
        lh = 18
        first = y - (len(lines) - 1) * lh / 2 + (5 if centered else 0)
        spans = "".join(
            f'<tspan x="{_f(x)}" dy="{0 if i == 0 else lh}">{html.escape(ln)}</tspan>'
            for i, ln in enumerate(lines))
        self._parts.append(
            f'<text class="{cls}" x="{_f(x)}" y="{_f(first)}" text-anchor="{anchor}" '
            f'style="fill:{fill}"' + (' font-weight="700"' if bold else "") + f">{spans}</text>")

    # ----------------------------------------------------------------- output
    def render(self) -> str:
        defs = ""
        if self._has_arrow:
            defs = (f'<defs><marker id="{self.diagram_id}-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
                    'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
                    '<path class="rs-arrowhead" d="M0 0 L10 5 L0 10 z" '
                    'style="fill:var(--rs-d-line);stroke:none"/></marker></defs>')
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" role="img" '
                f'aria-labelledby="{self.diagram_id}-t" width="{_f(self.width)}" '
                f'height="{_f(self.height)}" viewBox="0 0 {_f(self.width)} {_f(self.height)}">'
                f'<title id="{self.diagram_id}-t">{html.escape(self.title)}</title>')
        if self.description:
            head += f"<desc>{html.escape(self.description)}</desc>"
        return (f'<figure class="rs-diagram">{head}{defs}' + "".join(self._parts)
                + f"</svg><figcaption>{html.escape(self.title)}</figcaption></figure>")


def diagram_page_body(diagram: Diagram | str, intro_html: str = "") -> str:
    """Body HTML for the dedicated "big picture" diagram page (intro, then the figure)."""
    markup = diagram.render() if isinstance(diagram, Diagram) else str(diagram)
    return (intro_html + "\n" if intro_html else "") + markup
