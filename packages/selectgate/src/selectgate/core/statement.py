"""Statement guard — only a single read-only statement is allowed (fail-closed).

Conservative by design: comments and string literals are stripped before the
keyword scan, and anything that is not clearly read-only is refused.
"""

from __future__ import annotations

import re

from .errors import StatementRejected

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_STRING = re.compile(r"'(?:''|[^'])*'")

READ_LEADING = {"select", "with", "explain", "values", "pragma"}
WRITE_TOKENS = {
    "insert", "update", "delete", "replace", "drop", "create", "alter",
    "attach", "detach", "vacuum", "reindex", "begin", "commit", "rollback",
    "savepoint", "grant", "revoke", "truncate", "merge", "call", "copy",
    "set", "upsert", "lock", "analyze",
}


def _strip(sql: str) -> str:
    s = _BLOCK_COMMENT.sub(" ", sql)
    s = _LINE_COMMENT.sub(" ", s)
    s = _STRING.sub("''", s)        # drop string contents so they can't smuggle ';' / keywords
    return s.strip()


def assert_read_only(sql: str) -> str:
    """Validate and return the statement (trailing ';' removed) for execution."""
    if not sql or not sql.strip():
        raise StatementRejected("empty statement")

    body = _strip(sql).rstrip().rstrip(";").rstrip()
    if ";" in body:
        raise StatementRejected("multiple statements are not allowed")

    tokens = re.findall(r"[A-Za-z_]+", body.lower())
    if not tokens:
        raise StatementRejected("no SQL keyword found")
    if tokens[0] not in READ_LEADING:
        raise StatementRejected(
            f"statement must start with one of {sorted(READ_LEADING)}, got {tokens[0]!r}"
        )
    bad = sorted(set(tokens) & WRITE_TOKENS)
    if bad:
        raise StatementRejected(f"write/DDL keyword(s) not allowed: {bad}")
    if tokens[0] == "pragma" and "=" in body:
        raise StatementRejected("pragma assignment is not allowed")

    return sql.strip().rstrip(";").rstrip()


def assert_write_allowed(sql: str, allowed: set[str]) -> tuple[str, str]:
    """Validate a single write statement whose leading keyword is in ``allowed``.

    Returns (cleaned_sql, leading_keyword). Fail-closed: multiple statements or a
    leading keyword outside the allowlist are refused. (Write-mode is opt-in and
    the allowlist is configured per gateway; see Config.write.)
    """
    if not sql or not sql.strip():
        raise StatementRejected("empty statement")
    body = _strip(sql).rstrip().rstrip(";").rstrip()
    if ";" in body:
        raise StatementRejected("multiple statements are not allowed")
    tokens = re.findall(r"[A-Za-z_]+", body.lower())
    if not tokens:
        raise StatementRejected("no SQL keyword found")
    leading = tokens[0]
    allowed_l = {a.lower() for a in allowed}
    if leading not in allowed_l:
        raise StatementRejected(
            f"write statement {leading!r} is not in the write allowlist {sorted(allowed_l)}")
    return sql.strip().rstrip(";").rstrip(), leading
