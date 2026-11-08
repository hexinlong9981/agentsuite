import re

import pytest

from reportsite import (Diagram, SpecError, build_site, check_render, check_site, contrast_ratio,
                        passed, resolve_token, verify_palette)
from reportsite.cli import main
from reportsite.palette import DARK, LIGHT


def make_diagram():
    d = Diagram(420, 160, "Flow", "A to B")
    a = d.add_box(10, 40, 120, 50, "Client\nside")
    b = d.add_box(290, 40, 120, 50, "Service", hot=True)
    d.add_connector(a, b, label="call")
    d.add_highlight(b)
    d.add_label(10, 20, "Heading", bold=True)
    return d


def build(tmp_path, **kw):
    out = tmp_path / "site"
    build_site({"title": "S", "conclusion_html": "<p>ok</p>",
                "pages": [{"id": "p", "title": "P", "body_html": kw.get("body", "<p>x</p>")}],
                **{k: v for k, v in kw.items() if k != "body"}}, str(out))
    return out


def rules(fs):
    return {f.rule for f in fs}


def test_markup_explicit_fills_and_tokens():
    m = make_diagram().render()
    for tag in re.findall(r"<(?:rect|text|path)\b[^>]*>", m):
        assert re.search(r"(fill|stroke):", tag), tag
        assert "#000" not in tag and "black" not in tag
    assert "var(--rs-d-box-fill)" in m and "var(--rs-d-hot-stroke)" in m
    assert "var(--rs-d-line)" in m and "var(--rs-d-label)" in m
    assert 'width="420"' in m and "100%" not in m


def test_bad_inputs_rejected():
    with pytest.raises(SpecError):
        Diagram(0, 10, "t")
    d = Diagram(100, 100, "t")
    with pytest.raises(SpecError):
        d.add_connector("nope", (1, 1))
    d.add_box(0, 0, 10, 10, box_id="a")
    with pytest.raises(SpecError):
        d.add_box(0, 0, 10, 10, box_id="a")
    with pytest.raises(SpecError):
        d.add_box(float("nan"), 0, 10, 10)


def test_contrast_known_values():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#fff", "#fff") == pytest.approx(1.0)
    assert contrast_ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.02)
    assert contrast_ratio("rgb(0,0,0)", "#fff") == pytest.approx(21.0)
    with pytest.raises(ValueError):
        contrast_ratio("red", "#fff")


def test_resolve_token():
    assert resolve_token("--rs-d-box-fill", LIGHT) == LIGHT["--rs-surface"]
    with pytest.raises(ValueError):
        resolve_token("--a", {"--a": "var(--b)"})
    with pytest.raises(ValueError):
        resolve_token("--a", {"--a": "var(--b)", "--b": "var(--a)"})


def test_shipped_palette_passes_and_low_contrast_fails():
    assert verify_palette() == []
    bad = dict(LIGHT, **{"--rs-muted": "#bbbbbb"})
    fails = verify_palette(bad, DARK)
    assert any(f.theme == "light" and f.fg == "--rs-muted" for f in fails)
    bad_dark = dict(DARK, **{"--rs-d-box-stroke": "#222a33"})
    assert any(f.theme == "dark" for f in verify_palette(LIGHT, bad_dark))
    undefined = dict(LIGHT, **{"--rs-d-line": "var(--rs-missing)"})
    assert any("undefined" in f.reason for f in verify_palette(undefined, DARK))


def test_good_diagram_site_passes(tmp_path):
    out = build(tmp_path, diagram_html=make_diagram().render())
    assert (out / "diagram.html").is_file()
    nav = (out / "index.html").read_text()
    assert nav.index("diagram.html") < nav.index("p.html")
    assert check_render(str(out)) == []
    assert passed(check_site(str(out)))
    assert main(["check", str(out)]) == 0
    assert main(["render-check", str(out)]) == 0


def test_site_without_diagrams_unchanged(tmp_path):
    out = build(tmp_path)
    assert not (out / "diagram.html").exists()
    assert check_render(str(out)) == []


def test_diagram_id_reserved(tmp_path):
    with pytest.raises(SpecError):
        build_site({"title": "S", "conclusion_html": "<p>x</p>", "diagram_html": "<svg></svg>",
                    "pages": [{"id": "diagram", "title": "D"}]}, str(tmp_path / "o"))


def test_unset_fill_flagged(tmp_path):
    body = '<svg width="10" height="10" viewBox="0 0 10 10"><rect class="x" width="5" height="5"/></svg>'
    fs = check_render(str(build(tmp_path, body=body)))
    assert "diagram.unset_fill" in rules(fs) and not passed(fs)
    assert main(["check", str(tmp_path / "site")]) == 1


def test_black_fill_flagged_and_hardcoded_warns(tmp_path):
    body = ('<svg width="10" height="10" viewBox="0 0 10 10"><rect class="x" fill="#000"/>'
            '<rect class="y" fill="#abcdef"/></svg>')
    fs = check_render(str(build(tmp_path, body=body)))
    assert "diagram.black_fill" in rules(fs)
    assert any(f.rule == "diagram.hardcoded_color" and f.severity == "warning" for f in fs)


def test_undefined_token_flagged(tmp_path):
    body = ('<svg width="10" height="10" viewBox="0 0 10 10">'
            '<rect class="x" style="fill:var(--rs-nope)" width="5" height="5"/></svg>')
    fs = check_render(str(build(tmp_path, body=body)))
    assert any(f.rule == "diagram.undefined_token" and "--rs-nope" in f.message for f in fs)


def test_low_contrast_token_flagged(tmp_path):
    out = build(tmp_path, diagram_html=make_diagram().render())
    css = out / "site_assets" / "rs.css"
    css.write_text(css.read_text().replace("--rs-muted: #4a5360;", "--rs-muted: #bbbbbb;", 1))
    fs = check_render(str(out))
    assert any(f.rule == "a11y.contrast" and "--rs-muted" in f.message for f in fs)
    assert not passed(fs)


def test_stretched_flagged(tmp_path):
    for svg in ('<svg width="100%" viewBox="0 0 10 10"><rect style="fill:var(--rs-bg)"/></svg>',
                '<svg width="10" height="10" viewBox="0 0 10 10" preserveAspectRatio="none">'
                '<rect style="fill:var(--rs-bg)"/></svg>'):
        out = tmp_path / "s"
        build_site({"title": "S", "conclusion_html": "<p>x</p>",
                    "pages": [{"id": "p", "title": "P", "body_html": svg}]}, str(out))
        assert "diagram.stretched" in rules(check_render(str(out)))


def test_missing_site(tmp_path):
    assert rules(check_render(str(tmp_path / "nope"))) == {"site.missing"}
