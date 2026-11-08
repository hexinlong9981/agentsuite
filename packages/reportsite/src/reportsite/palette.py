"""Light/dark token palette and WCAG contrast verification.

The palette is the single source of the colour tokens that ``theme.py`` writes to
``site_assets/rs.css``. Values are hex/``rgb()`` colours or ``var(--other)``
references (resolved here; an undefined reference or a cycle is a failure).
``verify_palette`` checks every foreground/background pair a report relies on in
BOTH themes (text >= 4.5:1, graphical objects >= 3:1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TEXT_MIN = 4.5      # WCAG AA, normal text
GRAPHIC_MIN = 3.0   # WCAG AA, large text and graphical objects

LIGHT: dict[str, str] = {
    "--rs-bg": "#ffffff",
    "--rs-surface": "#f4f6f9",
    "--rs-text": "#1b1f24",
    "--rs-muted": "#4a5360",
    "--rs-border": "#c5ccd6",
    "--rs-accent": "#0b57d0",
    "--rs-accent-bg": "#e3edfc",
    "--rs-ok": "#146c2e",
    "--rs-warn": "#8a5a00",
    "--rs-bad": "#b3261e",
    # diagram tokens
    "--rs-d-box-fill": "var(--rs-surface)",
    "--rs-d-box-stroke": "#5a6676",
    "--rs-d-box-text": "var(--rs-text)",
    "--rs-d-line": "var(--rs-muted)",
    "--rs-d-label": "var(--rs-text)",
    "--rs-d-hot-fill": "var(--rs-accent-bg)",
    "--rs-d-hot-stroke": "var(--rs-accent)",
    "--rs-d-hot-text": "var(--rs-text)",
}

DARK: dict[str, str] = {
    "--rs-bg": "#12161c",
    "--rs-surface": "#1c222b",
    "--rs-text": "#e8ecf1",
    "--rs-muted": "#aab4c0",
    "--rs-border": "#3a4452",
    "--rs-accent": "#8ab4f8",
    "--rs-accent-bg": "#23324a",
    "--rs-ok": "#7bd88f",
    "--rs-warn": "#f2c56b",
    "--rs-bad": "#ff8a80",
    "--rs-d-box-stroke": "#8c98a8",
}

# (foreground token, background token, minimum ratio, description)
TEXT_PAIRS: list[tuple[str, str, float, str]] = [
    ("--rs-text", "--rs-bg", TEXT_MIN, "body text on page"),
    ("--rs-text", "--rs-surface", TEXT_MIN, "text on surface"),
    ("--rs-text", "--rs-accent-bg", TEXT_MIN, "text on accent background"),
    ("--rs-muted", "--rs-bg", TEXT_MIN, "muted text on page"),
    ("--rs-muted", "--rs-surface", TEXT_MIN, "muted text on surface"),
    ("--rs-accent", "--rs-bg", TEXT_MIN, "link/accent text on page"),
    ("--rs-ok", "--rs-bg", TEXT_MIN, "ok status text on page"),
    ("--rs-warn", "--rs-bg", TEXT_MIN, "warn status text on page"),
    ("--rs-bad", "--rs-bg", TEXT_MIN, "bad status text on page"),
]
DIAGRAM_PAIRS: list[tuple[str, str, float, str]] = [
    ("--rs-d-label", "--rs-bg", TEXT_MIN, "diagram label on page"),
    ("--rs-d-box-text", "--rs-d-box-fill", TEXT_MIN, "label on box"),
    ("--rs-d-hot-text", "--rs-d-hot-fill", TEXT_MIN, "label on highlighted box"),
    ("--rs-d-box-stroke", "--rs-bg", GRAPHIC_MIN, "box outline on page"),
    ("--rs-d-box-stroke", "--rs-d-box-fill", GRAPHIC_MIN, "box outline on box fill"),
    ("--rs-d-line", "--rs-bg", GRAPHIC_MIN, "connector on page"),
    ("--rs-d-hot-stroke", "--rs-bg", GRAPHIC_MIN, "highlight outline on page"),
    ("--rs-d-hot-stroke", "--rs-d-hot-fill", GRAPHIC_MIN, "highlight outline on highlight fill"),
]
PAIRS = TEXT_PAIRS + DIAGRAM_PAIRS

_VAR = re.compile(r"^var\(\s*(--[\w-]+)\s*\)$")


@dataclass(frozen=True)
class ContrastFailure:
    theme: str        # "light" | "dark"
    fg: str
    bg: str
    ratio: float      # 0.0 when a token could not be resolved
    required: float
    reason: str       # description, or why it could not be resolved

    def message(self) -> str:
        if self.ratio:
            return (f"{self.theme}: {self.fg} on {self.bg} is {self.ratio:.2f}:1, "
                    f"needs {self.required:g}:1 ({self.reason})")
        return f"{self.theme}: {self.fg} on {self.bg} cannot be checked: {self.reason}"


def css_declarations(tokens: dict[str, str]) -> str:
    """Token dict -> ``  --name: value;`` lines (for the stylesheet)."""
    return "\n".join(f"  {k}: {v};" for k, v in tokens.items())


def parse_color(value: str) -> tuple[int, int, int] | None:
    """Parse ``#rgb`` / ``#rrggbb`` / ``rgb(r,g,b)`` to an RGB triple, else None."""
    v = value.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    m = re.fullmatch(r"rgb\(\s*(\d{1,3})\s*[ ,]\s*(\d{1,3})\s*[ ,]\s*(\d{1,3})\s*\)", v)
    if m:
        rgb = tuple(int(x) for x in m.groups())
        if all(0 <= c <= 255 for c in rgb):
            return rgb  # type: ignore[return-value]
    return None


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    def lin(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio (1.0 .. 21.0) between two colours. ValueError if unparseable."""
    ca, cb = parse_color(a), parse_color(b)
    if ca is None or cb is None:
        raise ValueError(f"unparseable colour: {a if ca is None else b!r}")
    la, lb = relative_luminance(ca), relative_luminance(cb)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def resolve_token(name: str, tokens: dict[str, str]) -> str:
    """Follow ``var(--x)`` references to a concrete value. ValueError if undefined/cyclic."""
    seen: list[str] = []
    cur = name
    while True:
        if cur in seen:
            raise ValueError(f"circular reference: {' -> '.join(seen + [cur])}")
        seen.append(cur)
        if cur not in tokens:
            raise ValueError(f"undefined custom property {cur}")
        val = tokens[cur].strip()
        m = _VAR.match(val)
        if not m:
            return val
        cur = m.group(1)


def verify_palette(light: dict[str, str] | None = None, dark: dict[str, str] | None = None,
                   pairs: list[tuple[str, str, float, str]] | None = None) -> list[ContrastFailure]:
    """Check every pair in both themes; return failures (empty list = pass).

    The dark theme is the light tokens overridden by ``dark`` (the CSS cascade), so a
    token the dark block forgets to redefine is tested with its light value.
    """
    light = LIGHT if light is None else light
    dark = DARK if dark is None else dark
    pairs = PAIRS if pairs is None else pairs
    out: list[ContrastFailure] = []
    for theme, toks in (("light", dict(light)), ("dark", {**light, **dark})):
        for fg, bg, need, why in pairs:
            try:
                ratio = contrast_ratio(resolve_token(fg, toks), resolve_token(bg, toks))
            except ValueError as exc:
                out.append(ContrastFailure(theme, fg, bg, 0.0, need, str(exc)))
                continue
            if ratio + 1e-9 < need:
                out.append(ContrastFailure(theme, fg, bg, ratio, need, why))
    return out
