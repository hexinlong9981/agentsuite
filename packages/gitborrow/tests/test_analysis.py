import subprocess
import pytest
from pathlib import Path

from gitborrow.core.analysis import (
    AnalysisEngine,
    call_trace,
    compare,
    definition,
    detect_language,
    history,
    impact_scan,
    outline,
    read,
    references,
    search,
    tree,
)
from gitborrow.core.analysis.types import make_citation


def _git(args, cwd):
    import os
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def sample_code_repo(tmp_path):
    repo = tmp_path / "coderepo"
    repo.mkdir()

    # Python module
    (repo / "math_ops.py").write_text(
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n\n"
        "def compute_total(items):\n"
        "    calc = Calculator()\n"
        "    return calc.add(items[0], items[1])\n"
    )

    # JavaScript module
    (repo / "index.js").write_text(
        "function formatCurrency(amount) {\n"
        "    return '$' + amount;\n"
        "}\n\n"
        "const total = formatCurrency(100);\n"
    )

    # SQL queries
    (repo / "queries.sql").write_text(
        "SELECT id, amount FROM orders WHERE status = 'COMPLETED';\n"
        "UPDATE orders SET status = 'REFUNDED' WHERE id = 42;\n"
    )

    _git(["init", "-q"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "feat: initial commit"], repo)

    # Second commit
    (repo / "math_ops.py").write_text(
        "class Calculator:\n"
        "    def add(self, a, b):\n"
        "        return a + b\n\n"
        "    def multiply(self, a, b):\n"
        "        return a * b\n\n"
        "def compute_total(items):\n"
        "    calc = Calculator()\n"
        "    return calc.add(items[0], items[1])\n"
    )
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "feat: add multiply"], repo)

    return repo


def test_make_citation():
    c1 = make_citation("myrepo", "abc1234", "src/main.py", 42)
    assert c1 == "myrepo@abc1234:src/main.py:42"
    c2 = make_citation("myrepo", "abc1234", "src/main.py")
    assert c2 == "myrepo@abc1234:src/main.py"


def test_detect_language():
    assert detect_language("foo.py") == "python"
    assert detect_language("foo.ts") == "typescript"
    assert detect_language("foo.js") == "javascript"
    assert detect_language("foo.sql") == "sql"
    assert detect_language("unknown.xyz") == "text"


def test_primitives_search(sample_code_repo):
    hits = search("myrepo", "headsha", sample_code_repo, "Calculator")
    assert len(hits) >= 1
    hit = hits[0]
    assert hit.path == "math_ops.py"
    assert hit.line == 1
    assert "Calculator" in hit.text
    assert "myrepo@headsha:math_ops.py:1" == hit.citation
    assert len(hit.after) > 0


def test_primitives_search_regex(sample_code_repo):
    hits = search("myrepo", "headsha", sample_code_repo, r"def\s+\w+\(", regex=True)
    assert len(hits) == 3
    names = [h.text.strip() for h in hits]
    assert any("def add" in n for n in names)
    assert any("def multiply" in n for n in names)
    assert any("def compute_total" in n for n in names)


def test_primitives_read(sample_code_repo):
    snippet = read("myrepo", "headsha", sample_code_repo, "math_ops.py", start=1, end=3)
    assert snippet.path == "math_ops.py"
    assert snippet.start == 1
    assert snippet.end == 3
    assert "class Calculator:" in snippet.text
    assert "return a + b" in snippet.text


def test_primitives_tree(sample_code_repo):
    entries = tree("myrepo", "headsha", sample_code_repo)
    paths = {e.path: e.lang for e in entries}
    assert paths.get("math_ops.py") == "python"
    assert paths.get("index.js") == "javascript"
    assert paths.get("queries.sql") == "sql"


def test_primitives_history(sample_code_repo):
    commits = history("myrepo", "headsha", sample_code_repo)
    assert len(commits) == 2
    summaries = [c.summary for c in commits]
    assert "feat: add multiply" in summaries[0]
    assert "feat: initial commit" in summaries[1]


def test_primitives_compare(sample_code_repo):
    diffs = compare("myrepo", "HEAD~1", "HEAD", sample_code_repo)
    assert len(diffs) == 1
    assert diffs[0].path == "math_ops.py"
    assert diffs[0].additions > 0


def test_primitives_compare_rejects_option_like_ref(sample_code_repo):
    # A ref that would be read by git as an option (arbitrary file write via
    # --output=) must be refused before the git command is built.
    with pytest.raises(ValueError):
        compare("myrepo", "--output=/tmp/pwned", "HEAD", sample_code_repo)
    with pytest.raises(ValueError):
        compare("myrepo", "HEAD", "-O/tmp/pwned", sample_code_repo)
    with pytest.raises(ValueError):
        compare("myrepo", "", "HEAD", sample_code_repo)


def test_language_definition(sample_code_repo):
    defs = definition("myrepo", "headsha", sample_code_repo, "Calculator")
    assert len(defs) == 1
    assert defs[0].kind == "class"
    assert defs[0].path == "math_ops.py"

    method_defs = definition("myrepo", "headsha", sample_code_repo, "add")
    assert len(method_defs) == 1
    assert method_defs[0].kind == "method"
    assert method_defs[0].container == "Calculator"

    js_defs = definition("myrepo", "headsha", sample_code_repo, "formatCurrency")
    assert len(js_defs) == 1
    assert js_defs[0].kind == "function"
    assert js_defs[0].path == "index.js"


def test_language_references(sample_code_repo):
    refs = references("myrepo", "headsha", sample_code_repo, "Calculator")
    assert len(refs) >= 1
    assert any(r.path == "math_ops.py" and r.kind == "call" for r in refs)

    js_refs = references("myrepo", "headsha", sample_code_repo, "formatCurrency")
    assert len(js_refs) >= 1
    assert any(r.kind == "call" for r in js_refs)


def test_language_outline(sample_code_repo):
    items = outline("myrepo", "headsha", sample_code_repo, "math_ops.py")
    assert len(items) >= 3
    kinds = [it.kind for it in items]
    assert "class" in kinds
    assert "method" in kinds
    assert "function" in kinds


def test_composite_call_trace(sample_code_repo):
    graph = call_trace("myrepo", "headsha", sample_code_repo, "add", direction="callers")
    assert len(graph.nodes) >= 1
    symbols = {n.symbol for n in graph.nodes}
    assert "add" in symbols
    # compute_total calls calc.add
    assert "compute_total" in symbols
    assert len(graph.edges) >= 1
    assert any(e.from_node == "compute_total" and e.to_node == "add" for e in graph.edges)


def test_composite_impact_scan_table(sample_code_repo):
    table = impact_scan("myrepo", "headsha", sample_code_repo, "orders", kind="table")
    assert len(table.rows) == 2
    methods = {r.access_method for r in table.rows}
    assert "read" in methods   # SELECT
    assert "write" in methods  # UPDATE
    assert all("queries.sql" in r.entry for r in table.rows)


def test_analysis_engine(sample_code_repo, config):
    engine = AnalysisEngine(config=config)
    hits = engine.search(str(sample_code_repo), None, "Calculator")
    assert len(hits) >= 1
    assert hits[0].path == "math_ops.py"

    snippet = engine.read(str(sample_code_repo), None, "math_ops.py", start=1, end=2)
    assert snippet.start == 1

    engine.close()
