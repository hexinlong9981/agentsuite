"""Guard expressions and input defaults (``${...}``) — parsed without ``eval``.

Grammar::

    expr  := or
    or    := and ('or' and)*
    and   := not ('and' not)*
    not   := 'not' not | cmp
    cmp   := atom [ ('==' | '!=' | '<' | '<=' | '>' | '>=' | 'in' | 'not' 'in') atom ]
    atom  := string | number | true | false | null | reference | '(' expr ')' | '[' [atom (',' atom)*] ']'
    ref   := name ('.' name)*        (names may contain letters, digits, ``_`` and ``-``)

There are no function calls, arithmetic, indexing or arbitrary attribute access.
The parser yields a small tuple tree; :func:`refs` lists the names it references;
:func:`evaluate` interprets the tree against a resolver (still no ``eval``).
"""

from __future__ import annotations

import re

KEYWORDS = {"and", "or", "not", "in", "true", "false", "null", "True", "False", "None"}
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<str>'[^'\\]*'|"[^"\\]*")
  | (?P<num>-?\d+(?:\.\d+)?(?![\w-]))
  | (?P<op>==|!=|<=|>=|<|>)
  | (?P<punct>[()\[\],.])
  | (?P<name>[A-Za-z_][A-Za-z0-9_\-]*)
""", re.X)


class GuardError(ValueError):
    pass


def tokenize(text):
    if not isinstance(text, str) or not text.strip():
        raise GuardError("empty expression")
    pos, out = 0, []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise GuardError(f"unreadable character {text[pos]!r} at position {pos}")
        pos = m.end()
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group(m.lastgroup)))
    return out


class _Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else (None, None)

    def take(self, val):
        if self.peek()[1] != val:
            raise GuardError(f"expected {val!r} but found {self.peek()[1]!r}")
        self.i += 1

    def parse(self):
        node = self.or_()
        if self.i != len(self.t):
            raise GuardError(f"trailing input {self.peek()[1]!r}")
        return node

    def or_(self):
        node = self.and_()
        while self.peek()[1] == "or":
            self.i += 1
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.peek()[1] == "and":
            self.i += 1
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.peek()[1] == "not" and self.peek(1)[1] != "in":
            self.i += 1
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        left = self.atom()
        k, v = self.peek()
        if k == "op":
            self.i += 1
            return ("cmp", v, left, self.atom())
        if v == "in":
            self.i += 1
            return ("cmp", "in", left, self.atom())
        if v == "not" and self.peek(1)[1] == "in":
            self.i += 2
            return ("cmp", "not in", left, self.atom())
        return left

    def atom(self):
        k, v = self.peek()
        if k is None:
            raise GuardError("expression ends unexpectedly")
        if k == "str":
            self.i += 1
            return ("lit", v[1:-1])
        if k == "num":
            self.i += 1
            return ("lit", float(v) if "." in v else int(v))
        if v in ("true", "True"):
            self.i += 1
            return ("lit", True)
        if v in ("false", "False"):
            self.i += 1
            return ("lit", False)
        if v in ("null", "None"):
            self.i += 1
            return ("lit", None)
        if v == "(":
            self.i += 1
            node = self.or_()
            self.take(")")
            return node
        if v == "[":
            self.i += 1
            items = []
            if self.peek()[1] != "]":
                items.append(self.atom())
                while self.peek()[1] == ",":
                    self.i += 1
                    items.append(self.atom())
            self.take("]")
            return ("list", items)
        if k == "name" and v not in KEYWORDS:
            self.i += 1
            parts = [v]
            while self.peek()[1] == ".":
                self.i += 1
                k2, v2 = self.peek()
                if k2 != "name":
                    raise GuardError(f"a name must follow '.' (found {v2!r})")
                self.i += 1
                parts.append(v2)
            if self.peek()[1] == "(":
                raise GuardError(f"function calls are not allowed: {'.'.join(parts)}(...)")
            return ("ref", tuple(parts))
        raise GuardError(f"{v!r} is not allowed here")


def parse(text):
    """Parse an expression into a tree. Raises :class:`GuardError` if it is not readable."""
    return _Parser(tokenize(text)).parse()


def refs(node):
    """The set of reference tuples used by a tree."""
    out = set()
    if node[0] == "ref":
        out.add(node[1])
    elif node[0] == "list":
        for x in node[1]:
            out |= refs(x)
    elif node[0] in ("and", "or"):
        out |= refs(node[1]) | refs(node[2])
    elif node[0] == "not":
        out |= refs(node[1])
    elif node[0] == "cmp":
        out |= refs(node[2]) | refs(node[3])
    return out


_MISSING = object()


def evaluate(node, resolve):
    """Evaluate a tree. ``resolve(parts)`` returns the value or raises ``KeyError``.

    Ordering comparisons between missing values or mismatched types raise GuardError
    (fail-closed: the caller must not guess).
    """
    t = node[0]
    if t == "lit":
        return node[1]
    if t == "list":
        return [evaluate(x, resolve) for x in node[1]]
    if t == "ref":
        try:
            return resolve(node[1])
        except KeyError:
            return None
    if t == "and":
        return bool(evaluate(node[1], resolve)) and bool(evaluate(node[2], resolve))
    if t == "or":
        return bool(evaluate(node[1], resolve)) or bool(evaluate(node[2], resolve))
    if t == "not":
        return not evaluate(node[1], resolve)
    if t == "cmp":
        op, a, b = node[1], evaluate(node[2], resolve), evaluate(node[3], resolve)
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op in ("in", "not in"):
            if not isinstance(b, (list, str)):
                raise GuardError("right side of 'in' must be a list or string")
            hit = a in b
            return hit if op == "in" else not hit
        if a is None or b is None or isinstance(a, bool) or isinstance(b, bool) \
                or not (isinstance(a, (int, float)) and isinstance(b, (int, float)) or
                        isinstance(a, str) and isinstance(b, str)):
            raise GuardError(f"cannot order-compare {type(a).__name__} and {type(b).__name__}")
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    raise GuardError(f"unknown node {t}")


# ---- input defaults ${...} ------------------------------------------------------
DEFAULT_EXPR_RE = re.compile(r"^\$\{([^}]*)\}$")
_DATE_FMT_RE = re.compile(r"^(YYYY|MM|DD|HH|mm|[-_/ :])+$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")


def parse_default(value):
    """Read an input default. Returns ("static", value) | ("config", key) | ("today", fmt|None)
    | ("now", None) | ("run.id", None) | ("env", None). Anything else raises GuardError."""
    if not isinstance(value, str) or "${" not in value:
        return ("static", value)
    m = DEFAULT_EXPR_RE.match(value.strip())
    if not m:
        raise GuardError("a default expression must be exactly one ${...} with nothing around it")
    body = m.group(1).strip()
    if body.startswith("config."):
        key = body[len("config."):]
        if not _KEY_RE.match(key):
            raise GuardError("malformed ${config.<key>} reference")
        return ("config", key)
    if body == "today":
        return ("today", None)
    if body.startswith("today:"):
        fmt = body[len("today:"):]
        if not _DATE_FMT_RE.match(fmt):
            raise GuardError("a date format may only use YYYY, MM, DD, HH, mm and separators")
        return ("today", fmt)
    if body in ("now", "run.id", "env"):
        return (body, None)
    raise GuardError("allowed default expressions: config.<key>, today[:format], now, run.id, env")


def format_today(fmt, when):
    out = fmt or "YYYY-MM-DD"
    for tok, val in (("YYYY", f"{when.year:04d}"), ("MM", f"{when.month:02d}"),
                     ("DD", f"{when.day:02d}"), ("HH", f"{when.hour:02d}"), ("mm", f"{when.minute:02d}")):
        out = out.replace(tok, val)
    return out
