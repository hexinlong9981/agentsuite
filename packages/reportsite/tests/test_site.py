import json

import pytest

from reportsite import SpecError, build_site, check_site, passed
from reportsite.cli import main

SPEC = {
    "title": "Sample report",
    "conclusion_html": "<p>Everything is fine.</p>",
    "pages": [
        {"id": "overview", "title": "Overview", "body_html": "<p>Scope.</p>"},
        {"id": "findings", "title": "Findings",
         "body_html": '<p id="f1">One finding at 2026-01-02T03:04:05Z.</p>'},
        {"id": "next-steps", "title": "Next steps", "body_html": "<ul><li>Do it</li></ul>"},
    ],
}


@pytest.fixture
def site(tmp_path):
    out = tmp_path / "site"
    build_site(SPEC, str(out))
    return out


def rules(findings):
    return {f.rule for f in findings}


def test_build_files_and_conclusion_first(site):
    for f in ["index.html", "overview.html", "findings.html", "next-steps.html",
              "site_assets/rs.css", "site_assets/rs.js"]:
        assert (site / f).is_file()
    idx = (site / "index.html").read_text()
    assert idx.index('id="conclusion"') < idx.index('id="overview"') if 'id="overview"' in idx else True
    assert idx.index("Everything is fine") < idx.index("Pages")


def test_nav_matches_pages_and_themes(site):
    idx = (site / "index.html").read_text()
    for name in ["overview", "findings", "next-steps"]:
        assert f'href="{name}.html"' in idx
    css = (site / "site_assets/rs.css").read_text()
    assert "prefers-color-scheme: dark" in css and '[data-theme="dark"]' in css
    assert "body {" in css and "background-color: var(--rs-bg)" in css
    assert check_site(str(site)) == []


def test_overwrite_in_place_removes_stale_page(site):
    spec = dict(SPEC, pages=SPEC["pages"][:2])
    summary = build_site(spec, str(site))
    assert "next-steps.html" in summary.removed
    assert not (site / "next-steps.html").exists()
    assert passed(check_site(str(site)))


def test_bad_specs():
    with pytest.raises(SpecError):
        build_site({"title": "t", "conclusion_html": "x",
                    "pages": [{"id": "index", "title": "x"}]}, "/nonexistent-unused")
    with pytest.raises(SpecError):
        build_site({"title": "t", "conclusion_html": "x",
                    "pages": [{"id": "../evil", "title": "x"}]}, "/nonexistent-unused")


def test_missing_site_fails_closed(tmp_path):
    assert not passed(check_site(str(tmp_path / "nope")))
    assert "site.no_entry" in rules(check_site(str(tmp_path)))


def _append(site, name, text):
    p = site / name
    p.write_text(p.read_text().replace("</main>", text + "\n</main>"))


def test_nav_entry_without_panel(site):
    (site / "findings.html").unlink()
    assert "nav.no_panel" in rules(check_site(str(site)))


def test_page_without_nav_entry(site):
    (site / "extra.html").write_text((site / "findings.html").read_text())
    assert "nav.orphan_panel" in rules(check_site(str(site)))


def test_in_page_tab_without_panel(site):
    _append(site, "findings.html", '<button data-panel-target="x">x</button>')
    assert "nav.no_panel" in rules(check_site(str(site)))


def test_planted_secret(site):
    fake = "gh" + "p_" + "A1b2C3d4E5" * 4
    _append(site, "findings.html", f"<p>{fake}</p>")
    fs = check_site(str(site))
    assert "secret.leak" in rules(fs)
    assert all(fake not in f.message for f in fs)
    assert not passed(fs)


def test_planted_password_assignment(site):
    key = "pass" + "word"
    _append(site, "overview.html", f"<p>{key}: hunter2hunter2</p>")
    assert "secret.leak" in rules(check_site(str(site)))


def test_todo(site):
    _append(site, "overview.html", "<p>TO" + "DO finish</p>")
    assert "hygiene.todo" in rules(check_site(str(site)))


def test_datetime_without_tz(site):
    _append(site, "overview.html", "<p>at 2026-01-02 03:04:05 sharp</p>")
    assert "hygiene.datetime_no_tz" in rules(check_site(str(site)))


def test_broken_links(site):
    _append(site, "overview.html", '<a href="missing.html">a</a><a href="findings.html#nope">b</a>')
    r = rules(check_site(str(site)))
    assert {"link.broken", "link.broken_anchor"} <= r


def test_good_anchor_ok(site):
    _append(site, "overview.html", '<a href="findings.html#f1">ok</a><a href="#top">t</a>')
    assert check_site(str(site)) == []


def test_dark_on_dark(site):
    _append(site, "overview.html", '<div style="background:#101010">x</div>')
    assert "a11y.dark_on_dark" in rules(check_site(str(site)))
    _append(site, "findings.html", '<div style="background:#101010;color:#fff">x</div>')
    assert not any(f.path == "findings.html" and f.rule == "a11y.dark_on_dark"
                   for f in check_site(str(site)))


def test_unset_svg_fill(site):
    _append(site, "overview.html", '<svg><rect width="5" height="5"></rect></svg>')
    assert "a11y.unset_fill" in rules(check_site(str(site)))


def test_unpaired_tag(site):
    _append(site, "overview.html", "<div><p>oops</div>")
    assert "html.unpaired" in rules(check_site(str(site)))


def test_cli(tmp_path, capsys):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(SPEC))
    out = tmp_path / "o"
    assert main(["build", str(spec), str(out)]) == 0
    assert main(["check", str(out)]) == 0
    _append(out, "overview.html", "<p>TO" + "DO</p>")
    assert main(["check", str(out)]) == 1
    assert main(["build", str(tmp_path / "missing.json"), str(out)]) == 2
