import pytest

from reportsite import (MarkdownError, PresetError, PRESETS, build_site, check_render, check_site,
                        md_to_body, md_to_pages, passed, skeleton_from_preset, spec_from_markdown)
from reportsite.cli import main
from reportsite.md import convert

DOC = """# Sample Study

Short lead paragraph with **bold**, *italic*, `code` and a [link](https://example.org/x).

## Summary

The conclusion text.

## Findings

- first
- second
  - nested item
1. one

| name | count |
|------|-------|
| a | 1 |
| b | 2 |

```python
x = "<b>&</b>"
```

## Background

Context here.
"""


def gate(d):
    fs = check_site(str(d)) + check_render(str(d))
    assert passed(fs), [f for f in fs if f.severity == "error"]


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_skeleton_builds_and_passes(tmp_path, name):
    spec = skeleton_from_preset(name, "Neutral Topic")
    assert [p.id for p in spec.pages] == [i for i, _ in PRESETS[name].pages]
    out = tmp_path / "s"
    build_site(spec, str(out))
    gate(out)
    index = (out / "index.html").read_text()
    assert index.index("Conclusion") < index.index("Pages")
    for p in spec.pages:
        assert f'data-nav="{p.id}"' in index and (out / f"{p.id}.html").exists()
    assert (out / "diagram.html").exists() == PRESETS[name].diagram


def test_preset_pages_and_errors():
    assert [p.id for p in skeleton_from_preset("report", "T").pages] == [
        "background", "findings", "recommendation", "next-steps"]
    assert skeleton_from_preset("report", "T", with_diagram=True).diagram_html
    assert not skeleton_from_preset("proposal", "T", with_diagram=False).diagram_html
    with pytest.raises(PresetError):
        skeleton_from_preset("nope", "T")
    with pytest.raises(PresetError):
        skeleton_from_preset("report", "  ")


def test_body_basics():
    h = md_to_body(DOC, engine="builtin")
    assert '<h2 id="sample-study">Sample Study</h2>' in h
    assert "<strong>bold</strong>" in h and "<em>italic</em>" in h and "<code>code</code>" in h
    assert '<a href="https://example.org/x">link</a>' in h
    assert "<ul><li>first</li><li>second<ul><li>nested item</li></ul></li></ul>" in h
    assert "<ol><li>one</li></ol>" in h
    assert '<th scope="col">name</th>' in h and "<td>2</td>" in h
    assert '<pre><code class="language-python">' in h
    assert "&lt;b&gt;&amp;&lt;/b&gt;" in h


def test_escapes_and_no_unsafe_html():
    evil = ("<script>alert(1)</script>\n\n[x](javascript:alert(1)) [y](data:text/html,a) "
            "![i](x) <img src=x onerror=alert(1)>\n\n# <b>t</b>")
    h = md_to_body(evil, engine="builtin")
    assert "<script" not in h and "<img" not in h and "<b>" not in h
    assert "href=\"javascript" not in h and "href=\"data" not in h
    assert "&lt;script&gt;" in h


def test_pages_split_by_top_level(tmp_path):
    pages = md_to_pages("lead\n\n# One\n\n## Sub\ntext\n\n# Two\n\n```\n# not a heading\n```\n",
                        engine="builtin")
    assert [p.title for p in pages] == ["Introduction", "One", "Two"]
    assert [p.id for p in pages] == ["introduction", "one", "two"]
    assert "<h2" in pages[1].body_html and "# not a heading" in pages[2].body_html
    with pytest.raises(MarkdownError):
        md_to_pages("")


def test_table_as_data_validates(tmp_path):
    data = {}
    body = md_to_body("| a | a |\n|---|---|\n| 1 | 2 |\n", tables_as_data=True, data_out=data,
                      engine="builtin")
    assert 'data-table="table-1.json"' in body
    assert data["table-1.json"] == [{"a": "1", "a 2": "2"}]
    out = tmp_path / "s"
    build_site({"title": "T", "conclusion_html": "<p>c</p>", "data": data,
                "pages": [{"id": "p", "title": "P", "body_html": body}]}, str(out))
    gate(out)


def test_builtin_when_library_absent(monkeypatch):
    import reportsite.md as m
    monkeypatch.setattr(m, "_have_library", lambda: False)
    assert "<strong>x</strong>" in md_to_body("**x**")  # auto falls back to built-in
    assert convert("a", engine="builtin").body_html == "<p>a</p>"
    with pytest.raises(MarkdownError):
        convert("a", engine="markdown", tables_as_data=True)


def test_spec_from_markdown_and_preset(tmp_path):
    spec = spec_from_markdown(DOC, preset="report", tables_as_data=True)
    assert spec.title == "Sample Study"
    assert "conclusion text" in spec.conclusion_html
    assert [p.id for p in spec.pages] == ["background", "findings"]  # preset order
    assert "table-1.json" in "".join(spec.data) or spec.data
    out = tmp_path / "s"
    build_site(spec, str(out))
    gate(out)
    with pytest.raises(MarkdownError):
        spec_from_markdown("## A\n\ntext")  # no title
    with pytest.raises(MarkdownError):
        spec_from_markdown("# T\n\n## Only\n\ntext")  # no conclusion section or lead text
    with pytest.raises(MarkdownError):
        spec_from_markdown("x" * 3_000_000, title="T")


def test_cli_scaffold_and_from_markdown(tmp_path, capsys):
    doc = tmp_path / "d.md"
    doc.write_text(DOC, encoding="utf-8")
    out = tmp_path / "md"
    assert main(["from-markdown", str(doc), str(out), "--preset", "proposal",
                 "--tables-as-data"]) == 0
    gate(out)
    assert (out / "diagram.html").exists() and (out / "site_data" / "findings-table-1.json").exists()
    out2 = tmp_path / "sc"
    assert main(["scaffold", "--preset", "investigation", "Neutral", str(out2)]) == 0
    gate(out2)
    assert main(["from-markdown", str(tmp_path / "missing.md"), str(tmp_path / "x")]) == 2
