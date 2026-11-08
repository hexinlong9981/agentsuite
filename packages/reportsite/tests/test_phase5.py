import json
import re

import pytest

from reportsite import (build_bundle, build_site, check_bundle, check_site, passed)
from reportsite.cli import main
from reportsite.databind import DATA_JS_PATH, script_is_pristine
from reportsite.errors import CheckFailed, SpecError

DATA = {"m.json": [{"name": "a</script>", "count": 1}], "r.csv": [{"x": "1", "y": "q"}]}
BODY = '<div data-table="m.json"></div><table data-table="r.csv"></table>'


def mk(tmp_path, name, title, data=True):
    spec = {"title": title, "conclusion_html": "<p>ok</p>",
            "pages": [{"id": "p1", "title": "Page one", "body_html": BODY if data else "<p>x</p>"}]}
    if data:
        spec["data"] = DATA
    d = tmp_path / name
    build_site(spec, str(d))
    return d


def rules(f):
    return {x.rule for x in f}


def test_embed_present_and_matches(tmp_path):
    d = mk(tmp_path, "s", "S")
    html = (d / "p1.html").read_text()
    m = re.search(r'<script type="application/json" data-embed="m.json">(.*?)</script>', html, re.S)
    assert m and "</script" not in m.group(1)
    assert json.loads(m.group(1)) == json.loads((d / "site_data/m.json").read_text())
    assert 'data-embed="r.csv"' in html
    js = (d / DATA_JS_PATH).read_text()
    assert "data-embed" in js and "fetch(" in js and script_is_pristine(js)
    assert js.index("embedded(name)") < js.index("fetch(")
    assert check_site(str(d)) == []


def test_embed_drift_and_invalid(tmp_path):
    d = mk(tmp_path, "s", "S")
    (d / "site_data/m.json").write_text('[{"name": "other", "count": 2}]')
    assert "data.embed_drift" in rules(check_site(str(d)))
    d2 = mk(tmp_path, "t", "T")
    p = d2 / "p1.html"
    p.write_text(p.read_text().replace('data-embed="m.json">[', 'data-embed="m.json">{[', 1))
    assert "data.embed_invalid" in rules(check_site(str(d2)))
    d3 = mk(tmp_path, "u", "U")
    p = d3 / "p1.html"
    p.write_text(p.read_text().replace('type="application/json"', 'type="text/javascript"', 1))
    assert "data.embed_type" in rules(check_site(str(d3)))


@pytest.fixture
def bundle(tmp_path):
    sites = [mk(tmp_path, "a", "Alpha"), mk(tmp_path, "b", "Beta", data=False),
             mk(tmp_path, "c", "Gamma")]
    out = tmp_path / "bundle"
    build_bundle([str(s) for s in sites], str(out), "All")
    return out


def test_bundle_builds_and_passes(bundle):
    idx = (bundle / "index.html").read_text()
    for slug in "abc":
        assert (bundle / "reports" / slug / "index.html").is_file()
        assert passed(check_site(str(bundle / "reports" / slug)))
    assert "Alpha" in idx and "Page one" in idx
    for href in re.findall(r'(?:href|src)="([^"]*)"', idx):
        assert not href.startswith(("/", "file:", "http"))
    assert check_bundle(str(bundle)) == []


def test_bundle_rejects_bad_input(tmp_path):
    with pytest.raises(SpecError):
        build_bundle([], str(tmp_path / "o"), "t")
    d = mk(tmp_path, "a", "A")
    (d / "p1.html").write_text("<p>unclosed <div>")
    with pytest.raises(CheckFailed):
        build_bundle([str(d)], str(tmp_path / "o"), "t")


def test_check_bundle_failures(bundle):
    import shutil
    # absolute / file:// link
    idx = bundle / "index.html"
    orig = idx.read_text()
    idx.write_text(orig.replace('href="reports/a/index.html"', 'href="file:///x/index.html"', 1))
    assert "bundle.link_not_portable" in rules(check_bundle(str(bundle)))
    idx.write_text(orig.replace('href="reports/a/index.html"', 'href="/reports/a/index.html"', 1))
    assert "bundle.link_not_portable" in rules(check_bundle(str(bundle)))
    idx.write_text(orig)
    # report fails own check
    (bundle / "reports/b/p1.html").write_text("<p>oops <div>")
    f = check_bundle(str(bundle))
    assert any(x.path.startswith("reports/b/") for x in f) and not passed(f)
    # planted secret
    (bundle / "reports/b/p1.html").write_text("ok")
    (bundle / "reports/c/notes.txt").write_text("pass" + "word: " + "hunter" + "22xyz")
    assert "secret.leak" in rules(check_bundle(str(bundle)))
    (bundle / "reports/c/notes.txt").unlink()
    # missing report
    shutil.rmtree(bundle / "reports/a")
    r = rules(check_bundle(str(bundle)))
    assert "bundle.link_broken" in r or "bundle.report_missing" in r
    assert not passed(check_bundle(str(bundle)))


def test_check_bundle_missing_entry(tmp_path):
    assert not passed(check_bundle(str(tmp_path)))
    assert not passed(check_bundle(str(tmp_path / "nope")))


def test_cli(tmp_path, capsys):
    a, b = mk(tmp_path, "a", "A"), mk(tmp_path, "b", "B")
    out = tmp_path / "o"
    assert main(["bundle", str(out), str(a), str(b), "--title", "T"]) == 0
    assert main(["check-bundle", str(out)]) == 0
    (out / "index.html").unlink()
    assert main(["check-bundle", str(out)]) == 1
