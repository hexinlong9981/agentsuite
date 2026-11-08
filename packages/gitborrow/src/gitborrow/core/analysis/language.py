"""Language-aware analysis layer for gitborrow (read-only).

Extracts definitions, references, and outlines across programming languages.
Uses Python's stdlib ast for Python files, and structural regex/token
matchers for TypeScript, JavaScript, Java, Go, Rust, and others without
requiring heavy binary dependencies.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from gitborrow.core.analysis.primitives import (
    detect_language,
    is_binary_file,
    _matches_globs,
)
from gitborrow.core.analysis.types import (
    Def,
    OutlineItem,
    Ref,
    make_citation,
)
from agent_policy_kit.paths import is_under


# Generic regex patterns for common languages
# (kind, pattern, extract_name_group, extract_sig_group)
GENERIC_PATTERNS: dict[str, list[tuple[str, re.Pattern, int, int]]] = {
    "javascript": [
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_$]+)\s*(\([^)]*\))"), 1, 2),
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+([a-zA-Z0-9_$]+)(?:\s+extends\s+[a-zA-Z0-9_$]+)?"), 1, 0),
        ("method", re.compile(r"^\s*(?:static\s+)?(?:async\s+)?([a-zA-Z0-9_$]+)\s*(\([^)]*\))\s*\{"), 1, 2),
        ("variable", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*="), 1, 0),
    ],
    "typescript": [
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_$]+)\s*(?:<[^>]+>)?\s*(\([^)]*\))"), 1, 2),
        ("class", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([a-zA-Z0-9_$]+)"), 1, 0),
        ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([a-zA-Z0-9_$]+)"), 1, 0),
        ("type", re.compile(r"^\s*(?:export\s+)?type\s+([a-zA-Z0-9_$]+)"), 1, 0),
        ("method", re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:async\s+)?([a-zA-Z0-9_$]+)\s*(\([^)]*\))"), 1, 2),
        ("variable", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)(?:\s*:\s*[^=]+)?\s*="), 1, 0),
    ],
    "java": [
        ("class", re.compile(r"^\s*(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?(?:abstract\s+)?class\s+([a-zA-Z0-9_$]+)"), 1, 0),
        ("interface", re.compile(r"^\s*(?:public|protected|private)?\s*interface\s+([a-zA-Z0-9_$]+)"), 1, 0),
        ("method", re.compile(r"^\s*(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?(?:[\w<>\[\], ]+)\s+([a-zA-Z0-9_$]+)\s*(\([^)]*\))"), 1, 2),
    ],
    "go": [
        ("function", re.compile(r"^func\s+([a-zA-Z0-9_]+)\s*(\([^)]*\))"), 1, 2),
        ("method", re.compile(r"^func\s+\((?:[a-zA-Z0-9_*\s]+)\)\s*([a-zA-Z0-9_]+)\s*(\([^)]*\))"), 1, 2),
        ("type", re.compile(r"^type\s+([a-zA-Z0-9_]+)\s+(?:struct|interface)"), 1, 0),
    ],
    "rust": [
        ("function", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([a-zA-Z0-9_]+)\s*(\([^)]*\))"), 1, 2),
        ("struct", re.compile(r"^\s*(?:pub\s+)?struct\s+([a-zA-Z0-9_]+)"), 1, 0),
        ("enum", re.compile(r"^\s*(?:pub\s+)?enum\s+([a-zA-Z0-9_]+)"), 1, 0),
        ("trait", re.compile(r"^\s*(?:pub\s+)?trait\s+([a-zA-Z0-9_]+)"), 1, 0),
    ],
}


def _extract_python_definitions(
    repo: str,
    commit_sha: str,
    rel_path: str,
    content: str,
    symbol_filter: str | None = None,
    kind_filter: str | None = None,
) -> list[Def]:
    defs: list[Def] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return defs

    def walk_node(node: ast.AST, container: str = "") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "method" if container else "function"
            name = node.name
            if (not symbol_filter or symbol_filter == name) and (not kind_filter or kind_filter == kind):
                # Build simple signature
                args = [a.arg for a in node.args.args]
                sig = f"({', '.join(args)})"
                defs.append(
                    Def(
                        citation=make_citation(repo, commit_sha, rel_path, node.lineno),
                        path=rel_path,
                        line=node.lineno,
                        kind=kind,
                        signature=sig,
                        container=container,
                    )
                )
            for child in node.body:
                walk_node(child, container=f"{container}.{name}" if container else name)

        elif isinstance(node, ast.ClassDef):
            kind = "class"
            name = node.name
            if (not symbol_filter or symbol_filter == name) and (not kind_filter or kind_filter == kind):
                bases = [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else []
                sig = f"({', '.join(bases)})" if bases else ""
                defs.append(
                    Def(
                        citation=make_citation(repo, commit_sha, rel_path, node.lineno),
                        path=rel_path,
                        line=node.lineno,
                        kind=kind,
                        signature=sig,
                        container=container,
                    )
                )
            for child in node.body:
                walk_node(child, container=f"{container}.{name}" if container else name)

        elif isinstance(node, ast.Module):
            for child in node.body:
                walk_node(child, container)

    walk_node(tree)
    return defs


def _extract_generic_definitions(
    repo: str,
    commit_sha: str,
    rel_path: str,
    content: str,
    lang: str,
    symbol_filter: str | None = None,
    kind_filter: str | None = None,
) -> list[Def]:
    defs: list[Def] = []
    patterns = GENERIC_PATTERNS.get(lang, [])
    if not patterns:
        return defs

    current_container = ""
    lines = content.splitlines()

    for line_idx, line in enumerate(lines):
        for kind, pat, name_grp, sig_grp in patterns:
            if kind_filter and kind_filter != kind:
                continue
            m = pat.search(line)
            if m:
                name = m.group(name_grp)
                sig = m.group(sig_grp) if sig_grp > 0 else ""
                if kind in ("class", "interface", "struct", "trait"):
                    current_container = name

                if not symbol_filter or symbol_filter == name:
                    defs.append(
                        Def(
                            citation=make_citation(repo, commit_sha, rel_path, line_idx + 1),
                            path=rel_path,
                            line=line_idx + 1,
                            kind=kind,
                            signature=sig,
                            container=current_container if kind == "method" else "",
                        )
                    )
                break

    return defs


def definition(
    repo: str,
    commit_sha: str,
    root_path: str | Path,
    symbol: str,
    *,
    lang: str | None = None,
    kind: str | None = None,
    globs: list[str] | None = None,
) -> list[Def]:
    """Find definitions of a symbol across the repository."""
    root = Path(root_path).resolve()
    target_lang = lang.lower() if lang else None

    defs: list[Def] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")

        for fname in filenames:
            fpath = Path(dirpath) / fname
            if is_binary_file(fpath):
                continue

            rel_str = str(fpath.relative_to(root)).replace("\\", "/")
            if not _matches_globs(rel_str, globs):
                continue

            file_lang = detect_language(fpath)
            if target_lang and file_lang != target_lang:
                continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Quick check if symbol appears in file before parsing
            if symbol not in content:
                continue

            if file_lang == "python":
                file_defs = _extract_python_definitions(
                    repo, commit_sha, rel_str, content, symbol_filter=symbol, kind_filter=kind
                )
            else:
                file_defs = _extract_generic_definitions(
                    repo, commit_sha, rel_str, content, file_lang, symbol_filter=symbol, kind_filter=kind
                )
            defs.extend(file_defs)

    return defs


def references(
    repo: str,
    commit_sha: str,
    root_path: str | Path,
    symbol: str,
    *,
    lang: str | None = None,
    scope: list[str] | None = None,
    max_results: int = 500,
) -> list[Ref]:
    """Find references (calls, imports, reads, writes) to a symbol."""
    root = Path(root_path).resolve()
    target_lang = lang.lower() if lang else None

    # Word boundary pattern for identifier
    sym_pat = re.compile(rf"\b{re.escape(symbol)}\b")

    refs: list[Ref] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")

        for fname in filenames:
            fpath = Path(dirpath) / fname
            if is_binary_file(fpath):
                continue

            rel_str = str(fpath.relative_to(root)).replace("\\", "/")
            if not _matches_globs(rel_str, scope):
                continue

            file_lang = detect_language(fpath)
            if target_lang and file_lang != target_lang:
                continue

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            if symbol not in content:
                continue

            lines = content.splitlines()
            current_enclosing = ""

            for line_idx, line in enumerate(lines):
                # Track simple enclosing def (Python / generic)
                strip_line = line.strip()
                if strip_line.startswith(("def ", "function ", "func ", "fn ", "class ", "interface ")):
                    parts = strip_line.split()
                    if len(parts) >= 2:
                        current_enclosing = parts[1].split("(")[0]

                if sym_pat.search(line):
                    line_no = line_idx + 1

                    # Deduce reference kind
                    kind = "read"
                    if "import " in line or "from " in line or "require(" in line:
                        kind = "import"
                    elif f"{symbol}(" in line or f".{symbol}(" in line:
                        kind = "call"
                    elif f"{symbol} =" in line or f"{symbol} :=" in line or f"{symbol} +=" in line:
                        kind = "write"

                    refs.append(
                        Ref(
                            citation=make_citation(repo, commit_sha, rel_str, line_no),
                            path=rel_str,
                            line=line_no,
                            kind=kind,
                            enclosing=current_enclosing,
                            text=strip_line,
                        )
                    )
                    if len(refs) >= max_results:
                        return refs

    return refs


def outline(
    repo: str,
    commit_sha: str,
    root_path: str | Path,
    path: str,
    *,
    lang: str | None = None,
) -> list[OutlineItem]:
    """Extract structural outline (classes, functions, methods) from a file."""
    root = Path(root_path).resolve()
    target_path = (root / path).resolve()
    if not is_under(target_path, root):
        raise ValueError(f"Path escapes repository root: {path}")

    if not target_path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    rel_str = str(target_path.relative_to(root)).replace("\\", "/")
    file_lang = (lang or detect_language(target_path)).lower()

    try:
        content = target_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise IOError(f"Could not read {path}: {e}") from e

    if file_lang == "python":
        defs = _extract_python_definitions(repo, commit_sha, rel_str, content)
    else:
        defs = _extract_generic_definitions(repo, commit_sha, rel_str, content, file_lang)

    return [
        OutlineItem(
            citation=d.citation,
            kind=d.kind,
            name=f"{d.container}.{d.signature}" if d.container else d.signature or d.kind,
            line=d.line,
            signature=d.signature,
        )
        if not d.container
        else OutlineItem(
            citation=d.citation,
            kind=d.kind,
            name=f"{d.container}::{d.signature or d.kind}",
            line=d.line,
            signature=d.signature,
        )
        for d in defs
    ]
