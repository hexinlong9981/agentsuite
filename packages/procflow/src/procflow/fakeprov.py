"""Scripted fake providers (tests only; nothing is ever sent anywhere).

A registry entry with ``provider: fake`` is accepted only when ``PROCFLOW_ALLOW_FAKE_PROVIDERS=1``. The replies come
from, in order of precedence: the entry's inline ``script`` (a list), an in-memory script set with
:func:`set_script`, or the JSON file named by ``PROCFLOW_FAKE_SCRIPT``::

    {"llm":   {"<service>": [<reply>, ...]},
     "agent": {"<agent>":   [<reply>, ...]}}

A reply is consumed per call, in order; the last one repeats once the list is used up. An llm reply is
``{"text": "<body>"}`` | ``{"output": {...}}`` (serialised as JSON) | ``{"error": "<kind>", "message": "..."}`` with
optional ``"cost"`` / ``"model"``. An agent reply is ``{"files": {"<name>": {...} | "<text>"}, "summary": {...},
"outside": ["<relative path to try to write outside the sandbox>"]}`` or an ``error`` reply. Error kinds:
unavailable | failed | refused | timeout | quota | invalid_output.

When the script comes from a file, call counts are kept in ``<file>.state.json`` (so separate CLI processes
continue the same script) and each call is logged in ``<file>.calls.jsonl`` (prompt / workdir hashes only).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path

ENV_SCRIPT = "PROCFLOW_FAKE_SCRIPT"
_MEM = {"script": None, "counts": {}}


def set_script(script):
    """Install (or, with None, remove) an in-memory script and reset its counters."""
    _MEM["script"], _MEM["counts"] = script, {}


def sha256_text(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _file_script():
    p = os.environ.get(ENV_SCRIPT)
    if not p:
        return None, None
    try:
        return Path(p), json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Path(p), None


def next_reply(kind, name, record=None, inline=None):
    """The next scripted reply for ``kind`` ("llm" | "agent") and target ``name`` (``unavailable`` when none)."""
    key = f"{kind}.{name}"
    if inline:
        i = _MEM["counts"].get(key, 0)
        _MEM["counts"][key] = i + 1
        return inline[min(i, len(inline) - 1)]
    if _MEM["script"] is not None:
        seq = (_MEM["script"].get(kind) or {}).get(name)
        if not seq:
            return {"error": "unavailable", "message": f"no script for {key}"}
        i = _MEM["counts"].get(key, 0)
        _MEM["counts"][key] = i + 1
        return seq[min(i, len(seq) - 1)]
    path, d = _file_script()
    if d is None:
        return {"error": "unavailable", "message": f"no fake script ({ENV_SCRIPT} unset or unreadable)"}
    seq = (d.get(kind) or {}).get(name)
    if not seq:
        return {"error": "unavailable", "message": f"no script for {key}"}
    with open(str(path) + ".lock", "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            st = Path(str(path) + ".state.json")
            cnt = json.loads(st.read_text()) if st.exists() else {}
            i = cnt.get(key, 0)
            cnt[key] = i + 1
            st.write_text(json.dumps(cnt))
            with open(str(path) + ".calls.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(record or {}, kind=kind, name=name, index=i), ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)
    return seq[min(i, len(seq) - 1)]
