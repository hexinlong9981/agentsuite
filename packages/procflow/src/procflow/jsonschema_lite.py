"""JSON Schema validation for llm / agent outputs.

``validate(instance, schema)`` returns a list of readable error sentences (empty = valid). It uses the
``jsonschema`` package when it can be imported and otherwise a minimal built-in checker covering the keywords
procedures actually use: ``type``, ``enum``, ``const``, ``required``, ``properties``, ``additionalProperties``,
``items``, ``minItems`` / ``maxItems``, ``minLength`` / ``maxLength``, ``minimum`` / ``maximum``, ``pattern``,
``anyOf`` / ``oneOf`` / ``allOf``. An unsupported keyword is ignored by the lite checker (never silently
"passes" a keyword it understands). ``prefer_lib=False`` forces the lite checker (deterministic in tests).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import ProcflowError

try:  # pragma: no cover - depends on the environment
    import jsonschema as _jsonschema
except Exception:  # noqa: BLE001
    _jsonschema = None

MAX_ERRORS = 20


class SchemaError(ProcflowError):
    """The schema itself is unreadable or not an object/boolean schema."""


def load(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as x:
        raise SchemaError(f"{Path(path).name}: cannot read the schema ({type(x).__name__})") from x
    if not isinstance(d, (dict, bool)):
        raise SchemaError(f"{Path(path).name}: a schema must be a JSON object")
    return d


def parse_json_text(text):
    """Model output text -> (value, error). Tolerates one surrounding code fence."""
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            s = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(s), None
    except ValueError:
        return None, "the output is not valid JSON"


def have_library():
    return _jsonschema is not None


def validate(instance, schema, prefer_lib=True):
    if schema is None:
        return []
    if prefer_lib and _jsonschema is not None:
        try:
            v = _jsonschema.Draft202012Validator(schema)
            errs = sorted(v.iter_errors(instance), key=lambda e: [str(p) for p in e.absolute_path])
            return [f"{_where(list(e.absolute_path))}: {e.message}"[:300] for e in errs[:MAX_ERRORS]]
        except Exception as x:  # noqa: BLE001 - a broken schema must not look like a pass
            return [f"schema: cannot be applied ({type(x).__name__})"]
    errs = []
    _check(instance, schema, "$", errs)
    return errs[:MAX_ERRORS]


def _where(path):
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)


_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _check(v, s, where, errs):  # noqa: C901 - one flat keyword dispatch
    if s is True or s == {}:
        return
    if s is False:
        errs.append(f"{where}: no value is allowed here")
        return
    if not isinstance(s, dict):
        errs.append(f"{where}: unreadable schema")
        return
    t = s.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES.get(x, lambda _v: False)(v) for x in types):
            errs.append(f"{where}: must be of type {'/'.join(map(str, types))}")
            return
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{where}: must be one of {s['enum']}")
    if "const" in s and v != s["const"]:
        errs.append(f"{where}: must equal {s['const']!r}")
    if isinstance(v, dict):
        for k in s.get("required") or []:
            if k not in v:
                errs.append(f"{where}: missing required property {k!r}")
        props = s.get("properties") or {}
        for k, sub in props.items():
            if k in v:
                _check(v[k], sub, f"{where}.{k}", errs)
        ap = s.get("additionalProperties")
        if ap is not None:
            for k in v:
                if k not in props:
                    if ap is False:
                        errs.append(f"{where}: property {k!r} is not allowed")
                    elif isinstance(ap, dict):
                        _check(v[k], ap, f"{where}.{k}", errs)
    if isinstance(v, list):
        if "minItems" in s and len(v) < s["minItems"]:
            errs.append(f"{where}: needs at least {s['minItems']} item(s)")
        if "maxItems" in s and len(v) > s["maxItems"]:
            errs.append(f"{where}: allows at most {s['maxItems']} item(s)")
        if isinstance(s.get("items"), (dict, bool)):
            for i, x in enumerate(v):
                _check(x, s["items"], f"{where}[{i}]", errs)
    if isinstance(v, str):
        if "minLength" in s and len(v) < s["minLength"]:
            errs.append(f"{where}: shorter than {s['minLength']}")
        if "maxLength" in s and len(v) > s["maxLength"]:
            errs.append(f"{where}: longer than {s['maxLength']}")
        if "pattern" in s:
            try:
                if not re.search(s["pattern"], v):
                    errs.append(f"{where}: does not match the required pattern")
            except re.error:
                errs.append(f"{where}: the schema pattern is not a valid expression")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errs.append(f"{where}: below the minimum {s['minimum']}")
        if "maximum" in s and v > s["maximum"]:
            errs.append(f"{where}: above the maximum {s['maximum']}")
    for kw in ("allOf",):
        for sub in s.get(kw) or []:
            _check(v, sub, where, errs)
    if "anyOf" in s:
        if not any(not _sub(v, x, where) for x in s["anyOf"]):
            errs.append(f"{where}: matches none of the anyOf alternatives")
    if "oneOf" in s:
        n = sum(1 for x in s["oneOf"] if not _sub(v, x, where))
        if n != 1:
            errs.append(f"{where}: must match exactly one of the oneOf alternatives (matches {n})")


def _sub(v, s, where):
    e = []
    _check(v, s, where, e)
    return e
