"""Code-line vetting and launching (fail-closed; argv only, never a shell).

A ``code`` step's lines come from its ``impl:`` (string or list). Each line is classified:

  local    the engine may launch it: argv is ``python3 <file.py> ...`` or ``<executable> ...`` whose
           *realpath* is under an allowed root (``<impl root>`` for the procedure, or a configured
           skill-scripts root); after placeholder substitution no ``<...>`` remains and there is no shell
           syntax (``| > < & ; ` $( ${``)
  command  anything else (outside the roots, placeholder left, shell syntax). The engine never launches it;
           the step becomes a work ticket that lists the lines and the reason.

A relative program path is resolved against the implementation root. ``python3 -m`` / ``-c`` are refused.
Placeholders: ``<evidence>`` and ``<run>`` (the step's evidence dir), ``<run_dir>``, ``<input>`` (path of the
inputs file handed to the program), and ``<name>`` for any input / context variable / loop variable
(strings and numbers only). The child gets PROCFLOW_INPUTS / PROCFLOW_OUTPUTS / PROCFLOW_EVIDENCE_DIR; a
written PROCFLOW_OUTPUTS file becomes the step's outputs.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

PYTHONS = {"python3", "/usr/bin/python3", "/usr/local/bin/python3"}
PLACEHOLDER_RE = re.compile(r"<[^<>\s][^<>]*>")
SHELL_TOKENS = {"|", "||", "&&", ";", ">", ">>", "<", "&", "2>", "2>&1"}
SHELL_CHARS_RE = re.compile(r"[|><&;`]|\$\(|\$\{")
MAX_OUTPUTS_BYTES = 1_000_000
ENV_PASS = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")


def lines_of(node):
    d = node.get("def") or {} if isinstance(node, dict) else {}
    impl = d.get("impl")
    if isinstance(impl, str):
        return [impl]
    if isinstance(impl, list):
        return [str(x) for x in impl]
    am = d.get("automation") if isinstance(d.get("automation"), dict) else {}
    return [str(x) for x in am.get("programs") or []]


def allowed_roots(impl_root, skill_roots=()):
    roots = [impl_root] + list(skill_roots or [])
    return [Path(r).resolve() for r in roots if r and Path(r).is_dir()]


def _under(path, roots):
    try:
        rp = Path(path).resolve()
    except OSError:
        return False
    return any(rp == r or r in rp.parents for r in roots)


def substitute(line, values):
    def rep(m):
        v = values.get(m.group(0)[1:-1])
        if isinstance(v, bool) or v is None or not isinstance(v, (str, int, float)):
            return m.group(0)
        return str(v)
    return PLACEHOLDER_RE.sub(rep, line)


def _resolve(prog, impl_root):
    """Relative program paths are anchored at the implementation root."""
    if os.path.isabs(prog) or not impl_root:
        return prog
    return str(Path(impl_root) / prog)


def classify(line, values, roots, impl_root=None):
    """{"kind": local|command, "line", "argv"?, "why"?} for one line."""
    raw = line.strip()
    text = substitute(raw, values)
    if PLACEHOLDER_RE.search(text):
        return {"kind": "command", "line": raw, "why": "unresolved placeholder: " + ", ".join(PLACEHOLDER_RE.findall(text))}
    if SHELL_CHARS_RE.search(text):
        return {"kind": "command", "line": raw, "why": "contains shell syntax"}
    try:
        argv = shlex.split(text)
    except ValueError as x:
        return {"kind": "command", "line": raw, "why": f"cannot split into arguments: {x}"}
    if not argv:
        return {"kind": "command", "line": raw, "why": "empty line"}
    if any(a in SHELL_TOKENS for a in argv):
        return {"kind": "command", "line": raw, "why": "contains shell syntax"}
    if argv[0] in PYTHONS:
        if len(argv) < 2 or argv[1].startswith("-") or not argv[1].endswith(".py"):
            return {"kind": "command", "line": raw, "why": "python3 must be followed by a .py file (-m / -c are not launched)"}
        script = _resolve(argv[1], impl_root)
        if not _under(script, roots) or not Path(script).is_file():
            return {"kind": "command", "line": raw, "why": "script is outside the allowed roots or missing"}
        return {"kind": "local", "line": raw, "argv": [argv[0], str(Path(script).resolve())] + argv[2:]}
    if argv[0] == "java-step":
        from .java.runner import resolve_step_command
        j_argv, j_env, err = resolve_step_command(argv[1:], steps_root=impl_root)
        if err:
            return {"kind": "command", "line": raw, "why": f"java-step resolution failed: {err}"}
        return {"kind": "local", "line": raw, "argv": j_argv, "extra_env": j_env}
    prog = _resolve(argv[0], impl_root)
    if _under(prog, roots) and Path(prog).is_file() and os.access(prog, os.X_OK):
        return {"kind": "local", "line": raw, "argv": [str(Path(prog).resolve())] + argv[1:]}
    return {"kind": "command", "line": raw, "why": "program is outside the allowed roots (or not an executable file)"}


def run_local(argv, cwd, timeout_s, inputs_path, outputs_path, extra_env=None):
    """Launch one vetted line. {"exit_code", "stdout", "stderr", "timeout", "seconds"}."""
    env = {k: os.environ[k] for k in ENV_PASS if k in os.environ}
    if extra_env:
        env.update(extra_env)
    env["PROCFLOW_INPUTS"] = str(inputs_path)
    env["PROCFLOW_OUTPUTS"] = str(outputs_path)
    env["PROCFLOW_EVIDENCE_DIR"] = str(cwd)
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, timeout=timeout_s, check=False,
                           shell=False, stdin=subprocess.DEVNULL)
        return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "timeout": False,
                "seconds": round(time.monotonic() - t0, 3)}
    except subprocess.TimeoutExpired as x:
        return {"exit_code": None, "stdout": x.stdout or b"", "stderr": x.stderr or b"", "timeout": True,
                "seconds": round(time.monotonic() - t0, 3)}
    except OSError as x:
        return {"exit_code": None, "stdout": b"", "stderr": str(x).encode(), "timeout": False,
                "seconds": round(time.monotonic() - t0, 3)}


def read_outputs(path):
    """The JSON map written to PROCFLOW_OUTPUTS ({} if none). ValueError if malformed or too large."""
    p = Path(path)
    if not p.exists():
        return {}
    if p.stat().st_size > MAX_OUTPUTS_BYTES:
        raise ValueError(f"outputs file too large ({p.stat().st_size} bytes, max {MAX_OUTPUTS_BYTES})")
    d = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ValueError("the outputs file must be a JSON map {name: value}")
    return d
