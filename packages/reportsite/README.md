# reportsite

Build accessible, self-contained, data-separated **HTML report sites** for AI
agents, and verify them with a fail-closed completion gate.

- **Site**: an entry page (conclusion first) + one page per topic, a shared
  left-hand nav, light/dark token themes; overwrite in place.
- **Data separation**: display rows live in `site_data/` (JSON/CSV) and are pulled
  into the page through typed containers by a generated binding script; the page
  source carries structure, not copy-pasted tables.
- **Diagrams**: theme-aware helper classes (boxes/connectors/text/highlight) with
  explicit fills (never an invisible default) and verified contrast.
- **Completion gate**: `check_site` (well-formed structure, every nav entry has a
  panel, **no leaked secret**, accessibility/contrast, data-binding integrity,
  resolvable links, hygiene) and `check_render` (diagram pages). "Done" = zero
  problems.
- **Bundle**: combine several report sites into one portable, relatively-linked
  bundle with two-level navigation.

Built on `agent-policy-kit` (SecretMasker, path containment, errors, CLI exit
codes). See [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md). 


## Data containers

Put display data in `SiteSpec.data` (`{"metrics.json": [{"name": "a", "count": 1}], "rows.csv": [...]}`);
`build_site` writes it to `site_data/`, emits the generated `site_assets/rs-data.js` and loads it
from every page. Authors mark where data goes (full contract: `reportsite/databind.py`):

```html
<div data-table="metrics.json"></div>                       <!-- list of objects / CSV -> table -->
<table data-table="rows.csv" data-columns="name,count"></table>
<span data-value="summary.json#totals.open"></span>         <!-- file#dotted.field -->
```

`check_site` adds `data.*` rules: `missing_file`, `missing_field`, `bad_ref`, `table_shape`,
`invalid_json`, `invalid_csv`, `unreadable`, `script_missing`, `script_unlinked`, `script_modified`
(the script carries a marker and content hash; hand edits fail the gate). The build also
embeds each data file a page uses inline (`<script type="application/json" data-embed="file">`,
data only, never executed); the script prefers the embed and falls back to `fetch`, so sites open
from `file://`. `check_site` adds `data.embed_invalid` / `embed_drift` / `embed_type` /
`embed_unknown` (errors) and `embed_missing` (warning); `site_data/` stays the source of truth.

## Bundles

```
reportsite bundle out/ siteA/ siteB/ --title "All reports"   # or build_bundle([...], "out", "All reports")
reportsite check-bundle out/                                 # or check_bundle("out")
```

`out/index.html` is a two-level launcher (reports, then each report's pages) with the first report
shown in a frame; each site is copied verbatim to `out/reports/<slug>/`; `bundle.json` is the
manifest. All links are relative, so the folder opens from `file://`. `check_bundle` is fail-closed:
entry present, every linked report present and passing `check_site` + `check_render`, links relative
and resolving (no `file:`, `/abs`, `//host`), no secret.

## Diagrams, tokens and contrast

`reportsite.Diagram` composes an inline-SVG diagram from four primitives. Every shape sets an
explicit fill/stroke from a theme token (never SVG's black default), so it is legible in light
and dark; it renders at natural pixel size (`width`/`height` + `viewBox`, never stretched).

```python
from reportsite import Diagram, SiteSpec, build_site
d = Diagram(420, 120, "Request flow")
a = d.add_box(10, 30, 120, 50, "Client")
b = d.add_box(290, 30, 120, 50, "Service", hot=True)   # emphasised
d.add_connector(a, b, label="call")                      # arrow, edge-aware
d.add_highlight(b)                                       # dashed emphasis frame
d.add_label(10, 15, "Overview", bold=True)
build_site(SiteSpec(title="R", conclusion_html="<p>x</p>", diagram_html=d.render()), "out")
```

`diagram_html` (JSON spec: `diagram_html`, `diagram_title`) adds the "big picture" page `diagram.html`
right after index (page id `diagram` becomes reserved). Or put `d.render()` in any page body.

| Primitive | Class | Tokens |
|---|---|---|
| box | `rs-bx` (`rs-hot` emphasised) | `--rs-d-box-fill/-stroke/-text`, `--rs-d-hot-*` |
| connector | `rs-ln` | `--rs-d-line` |
| label | `rs-t` (`rs-tb` bold, `rs-lbl` on page) | `--rs-d-label` |
| highlight frame | `rs-hot-frame` | `--rs-d-hot-stroke` |

Tokens live in `palette.py` (`LIGHT`, `DARK`; values are colours or `var(--x)` references) and are
written to `site_assets/rs.css`. `contrast_ratio(a, b)` is the WCAG ratio; `verify_palette()` checks
every text/surface/box/line pair in both themes (text >= 4.5:1, graphical >= 3:1; dark = light
overridden by dark, as in the cascade) and reports unresolvable tokens. Box *fill* vs page is not
held to 3:1 (tinted fills); the box *outline* is.

`check_render(dir)` (also run by `reportsite check`; alone: `reportsite render-check <dir>`) returns
`Finding`s: `diagram.unset_fill`, `diagram.black_fill`, `diagram.undefined_token`,
`diagram.stretched`, `diagram.no_theme`, `a11y.contrast` (errors) and `diagram.hardcoded_color`
(warning: a fixed colour instead of a token).

## Presets and Markdown

**Presets** give an author the whole nav/theme skeleton so only content is written.
`skeleton_from_preset(name, title)` returns a `SiteSpec` with placeholder page bodies that
builds with `build_site` as-is; `reportsite scaffold --preset <name> <title> <out_dir>` writes it.

| Preset | Pages (after the index/conclusion) | Diagram slot |
|---|---|---|
| `report` | background, findings, recommendation, next-steps | no |
| `investigation` | scope, evidence, analysis, impact, recommendation, appendix | yes |
| `proposal` | problem, goals, options, approach, plan, risks | yes |

**Diagram-page slot.** A preset with `diagram=True` (or `with_diagram=True`) fills `diagram_html`
with a one-box placeholder, so `diagram.html` ("Big picture") sits right after index. Replace
`spec.diagram_html` with `Diagram(...).render()`; `with_diagram=False` removes the slot.

**Markdown to page.** `md_to_body(text)` returns page-body HTML; `md_to_pages(text)` makes one
page per top-level `#` heading; `spec_from_markdown(text, preset=...)` makes a whole `SiteSpec`;
`reportsite from-markdown <doc.md> <out_dir> [--preset NAME] [--title T] [--tables-as-data]`
builds the site. A single leading `#` is the site title and its `##` sections become pages;
a "Conclusion"/"Summary"/"Overview" section (or text before the first section) is the entry-page
conclusion; with a preset, sections matching its page titles follow its order.

| Markdown | HTML |
|---|---|
| `#`..`######` | `h2`..`h6` (page title is `h1`; offset via `base_level`), each with an `id` anchor |
| paragraphs, `-`/`*`/`1.` lists (nested), `>` quotes, `---` | `p`, `ul`/`ol`, `blockquote`, `hr` |
| fenced code | `<pre><code class="language-x">` (escaped) |
| pipe table | static `<table>`, or with `tables_as_data` a `<div data-table="x.json">` container + `site_data/` rows |
| `` `code` ``, `**b**`, `*i*`, `[t](url)` | `code`, `strong`, `em`, `a` |

Safety: all text is HTML-escaped (raw HTML in the source is shown as text, never injected); link
targets must be http(s), `mailto:`, relative or `#` anchors, otherwise only the link text is kept.
The built-in converter needs no dependencies; the optional `markdown` extra is used by `engine="auto"`
when installed (its input is escaped and its links re-filtered); `engine="builtin"` forces the built-in.
