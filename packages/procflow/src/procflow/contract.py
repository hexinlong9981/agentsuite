"""Interface contracts at run time: ``pre`` / ``post`` evaluation and typed input / output checking.

``pre`` and ``post`` entries are guard expressions (the same language as transition guards, evaluated by
``guard.evaluate``, never ``eval``) or one predicate call ``name(ref, ...)``. Predicates are plain code listed in
:data:`PREDICATES`; an unknown predicate or an expression that cannot be evaluated is *false* (fail-closed: a broken
contract never lets a call through). Nothing here asks an LLM for a truth value.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import guard
from .defschema import value_matches

_PRED_RE = re.compile(r"^([a-z_][a-z0-9_]*)\(([A-Za-z0-9_.,\s]*)\)$")


def _nonempty(v):
    return v not in (None, "", [], {})


def _exists(path):
    return bool(path) and Path(str(path)).exists()


PREDICATES = {"nonempty": _nonempty, "exists": _exists}


def register_predicate(name, fn):
    """Add a predicate (deployments extend the set from code; a definition file cannot)."""
    if not re.match(r"^[a-z_][a-z0-9_]*$", str(name)):
        raise ValueError("a predicate name is lower_snake_case")
    PREDICATES[str(name)] = fn


def _walk(v, rest):
    for p in rest:
        v = v.get(p) if isinstance(v, dict) else None
    return v


def evaluate(expr, values):
    """One ``pre`` / ``post`` entry over ``values`` (name -> value). Returns ``(truth, reason | None)``."""
    text = str(expr).strip()

    def res(parts):
        return _walk(values.get(parts[0]), parts[1:])

    m = _PRED_RE.match(text)
    if m:
        fn = PREDICATES.get(m.group(1))
        if fn is None:
            return False, f"unknown predicate {m.group(1)} (available: {sorted(PREDICATES)})"
        args = [res(tuple(a.split("."))) for a in (x.strip() for x in m.group(2).split(",")) if a]
        try:
            ok = bool(fn(*args))
        except TypeError as x:
            return False, f"predicate {m.group(1)}: wrong arguments: {x}"
        return ok, None if ok else f"{text} is false"
    try:
        ok = bool(guard.evaluate(guard.parse(text), res))
    except guard.GuardError as x:
        return False, f"cannot evaluate {text!r}: {x}"
    return ok, None if ok else f"{text} is false"


def check(exprs, values):
    """Evaluate a ``pre`` / ``post`` list. Returns the reasons that fail (empty = the contract holds)."""
    return [why for ok, why in (evaluate(x, values) for x in exprs or []) if not ok]


def check_types(spec_part, values):
    """Values against an interface ``inputs`` / ``outputs`` block: required present, type and ``pattern`` fit."""
    out = []
    for name, sp in (spec_part or {}).items():
        sp = sp or {}
        v = values.get(name)
        if v is None:
            if sp.get("required", False) and "default" not in sp:
                out.append(f"required {name} is missing")
            continue
        if not value_matches(v, sp.get("type", "any")):
            out.append(f"{name} does not fit type {sp.get('type')}")
        if sp.get("pattern") and isinstance(v, str) and not re.search(str(sp["pattern"]), v):
            out.append(f"{name} does not match pattern {sp['pattern']}")
    return out


def with_defaults(spec_inputs, values):
    """``values`` plus the interface's declared defaults for inputs that were not supplied."""
    out = dict(values)
    for name, sp in (spec_inputs or {}).items():
        if name not in out and isinstance(sp, dict) and "default" in sp:
            out[name] = sp["default"]
    return out
