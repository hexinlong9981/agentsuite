"""Run records: directory layout, the append-only ``state.jsonl`` (record of truth), index, evidence.

Layout under ``<runs_root>/<SOP-ID>/<run_id>/``::

    run.json               identity, version hashes, status, suspend/resume history, ``raised`` (the exception a failed
                           run ended with), ``migrations``; a child run also has parent / call_path / depth / depth_limit
    config.snapshot.json   the resolved config taken at run start (written once)
    ir.json                the IR snapshot taken at run start (written once)
    inputs.json            the resolved input values
    outputs.json           interface outputs, written when the run ends
    state.jsonl            transitions; ``seq`` from 1, flock-guarded appends, gaps/corruption stop the run
    audit.jsonl            allow/deny audit (human decisions, program launches, lock takeovers)
    calls.json             a parent's index of its child runs (one row per ``call``; derived from state.jsonl)
    ir.<n>.json            the IR before the n-th ``resume --migrate`` (``ir.json`` is the current one)
    .lock                  the per-run lease (lock.py)
    <round>/<step>/        evidence: <nn>_command.txt, <nn>_stdout.txt, <nn>_stderr.txt, <nn>_outputs.json,
                           attempt-<n>.json, ticket-<n>.json

``<runs_root>/_index.jsonl`` is an append-only index (one row per status change; ``run.json`` is the truth;
``runs --reindex`` rebuilds it). Evidence holds the data; callers get counts and locations only.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path

from agent_policy_kit import SecretMasker, contained_join, safe_name

from .errors import ProcflowError

UTC = _dt.timezone.utc
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,119}$")
_MASKER = SecretMasker()


class RecordError(ProcflowError):
    """The run record is missing, unsafe, or corrupt (state.jsonl gap / broken line)."""


def now():
    return _dt.datetime.now(UTC)


def iso(t=None):
    return (t or now()).isoformat(timespec="seconds")


def parse_iso(s):
    try:
        return _dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj):
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str))


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str) + "\n"


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json(path, obj):
    write_atomic(path, dumps(obj))


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError as x:
        raise RecordError(f"{Path(path).name} is not valid JSON") from x


def append_jsonl(path, record):
    """Append one line under flock. Returns (line number, sha256 of the line)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            n = sum(1 for x in fh if x.strip())
            fh.seek(0, os.SEEK_END)
            fh.write(line + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    return n + 1, sha256_text(line)


def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            try:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            out.append({"_broken": line[:80]})
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return out


def safe_dir(step_id):
    return safe_name(str(step_id), default="step").lower()


def valid_run_id(run_id):
    return bool(RUN_ID_RE.match(run_id or "")) and ".." not in run_id


class RunDir:
    """Where one run's records live. Every path is contained under the run directory."""

    def __init__(self, runs_root, sop_id, run_id):
        if not valid_run_id(run_id):
            raise RecordError("run_id may only use letters, digits and . _ ~ - (no separators, no '..')")
        self.runs_root = Path(runs_root)
        self.sop_id, self.run_id = sop_id, run_id
        self.path = contained_join(str(self.runs_root), f"{safe_name(sop_id)}/{run_id}")

    def file(self, name):
        return contained_join(str(self.path), name)

    @property
    def state_path(self):
        return self.path / "state.jsonl"

    def exists(self):
        return self.file("run.json").exists()

    def read_run(self):
        d = read_json(self.file("run.json"))
        if d is None:
            raise RecordError(f"run.json is missing for {self.run_id}")
        return d

    def write_run(self, d):
        write_json(self.file("run.json"), d)

    def step_dir(self, rnd, step_id, create=True):
        p = contained_join(str(self.path), f"{int(rnd)}/{safe_dir(step_id)}")
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def next_seq(d):
        mx = 0
        for p in Path(d).glob("[0-9][0-9]*_*"):
            m = re.match(r"^(\d+)_", p.name)
            if m:
                mx = max(mx, int(m.group(1)))
        return mx + 1

    # ---- state.jsonl ---------------------------------------------------------------------
    def read_state(self):
        evs = read_jsonl(self.state_path)
        for i, ev in enumerate(evs, 1):
            if "_broken" in ev:
                raise RecordError(f"state.jsonl line {i} is corrupt (unreadable JSON); the run is not advanced")
            if ev.get("seq") != i:
                raise RecordError(f"state.jsonl has a gap or duplicate at line {i} (seq={ev.get('seq')}); "
                                  "stopping as a corruption signal")
        return evs

    def append_state(self, ev, ts=None):
        """Add ``seq`` and ``ts`` and append under flock. Returns (record, line number, sha256)."""
        rec = {"seq": None, "ts": ts or iso()}
        rec.update(ev)
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                n = sum(1 for x in fh if x.strip())
                rec["seq"] = n + 1
                line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"), default=str)
                fh.seek(0, os.SEEK_END)
                fh.write(line + "\n")
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        return rec, n + 1, sha256_text(line)


# ---- evidence -----------------------------------------------------------------------------
def write_evidence(step_dir, name, data, masker=None):
    """Write ``<nn>_<name>`` in a step's evidence directory (text is secret-masked). Returns a locator."""
    masker = masker or _MASKER
    step_dir = Path(step_dir)
    step_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    text = masker.mask_text(str(data))
    nn = RunDir.next_seq(step_dir)
    p = step_dir / f"{nn:02d}_{safe_name(name, default='data')}"
    p.write_text(text, encoding="utf-8")
    return {"path": p.name, "bytes": len(text.encode("utf-8")), "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0)}


# ---- index --------------------------------------------------------------------------------
def index_path(runs_root):
    return Path(runs_root) / "_index.jsonl"


def index_entry(run):
    keys = ("run_id", "sop", "version", "status", "env", "created", "ended", "outcome", "round", "current",
            "inputs_sha256")
    e = {k: run.get(k) for k in keys}
    e["reason"] = (run.get("suspend") or {}).get("reason") if run.get("status") == "suspended" else None
    e["parent"] = (run.get("parent") or {}).get("run_id")
    e["ts"] = iso()
    return e


def append_index(runs_root, run):
    append_jsonl(index_path(runs_root), index_entry(run))


def read_index(runs_root):
    """Latest row per run (rows are appended on every status change)."""
    latest = {}
    for e in read_jsonl(index_path(runs_root)):
        if isinstance(e, dict) and e.get("run_id"):
            latest[e["run_id"]] = e
    return list(latest.values())


def scan_runs(runs_root):
    out = []
    for p in sorted(Path(runs_root).glob("*/*/run.json")):
        try:
            d = read_json(p)
        except RecordError:
            continue
        if isinstance(d, dict) and d.get("run_id"):
            out.append(d)
    return out


def reindex(runs_root):
    p = index_path(runs_root)
    if p.exists():
        p.unlink()
    runs = scan_runs(runs_root)
    for r in sorted(runs, key=lambda r: r.get("created") or ""):
        append_index(runs_root, r)
    return len(runs)


def find_run(runs_root, run_id):
    if not valid_run_id(run_id):
        raise RecordError("run_id may only use letters, digits and . _ ~ - (no separators, no '..')")
    hits = sorted(Path(runs_root).glob(f"*/{run_id}/run.json"))
    if not hits:
        raise RecordError(f"run {run_id} not found under the runs root")
    p = hits[0]
    return RunDir(runs_root, p.parent.parent.name, run_id)


def tree_sha256(path):
    """Hash of a directory's contents (the implementation root). None when absent."""
    p = Path(path) if path else None
    if p is None or not p.is_dir():
        return None
    h = hashlib.sha256()
    for f in sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
        h.update(str(f.relative_to(p)).encode("utf-8") + b"\0")
        h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode() + b"\n")
    return h.hexdigest()


# ---- call trees ----------------------------------------------------------------------------
CHILD_SEP = "~"


def child_run_id(parent_id, step, n):
    """``<parent>~<step>-<n>`` (the step id is reduced to the run-id alphabet)."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(step)).strip("._-") or "call"
    return f"{parent_id}{CHILD_SEP}{safe}-{n}"


def root_run_id(runs_root, run_id, run=None):
    """Follow ``parent`` links up to the root run id (stops on a missing record)."""
    rid, seen = run_id, set()
    cur = run
    while rid not in seen:
        seen.add(rid)
        if cur is None:
            try:
                cur = find_run(runs_root, rid).read_run()
            except RecordError:
                break
        par = (cur or {}).get("parent")
        if not par:
            break
        rid, cur = par["run_id"], None
    return rid


def call_tree(runs_root, run_id, _depth=0, _seen=None):
    """The call tree below ``run_id``: ``{run_id, sop, status, step, depth, raised, children: [...]}``."""
    seen = _seen if _seen is not None else set()
    rd = find_run(runs_root, run_id)
    run = rd.read_run()
    seen.add(run_id)
    node = {"run_id": run_id, "sop": run.get("sop"), "status": run.get("status"),
            "step": (run.get("parent") or {}).get("step"), "depth": int(run.get("depth") or 0),
            "raised": run.get("raised"), "outcome": run.get("outcome"), "children": []}
    for c in read_json(rd.file("calls.json"), []) or []:
        cid = c.get("child")
        if cid and cid not in seen:
            try:
                node["children"].append(call_tree(runs_root, cid, _depth + 1, seen))
            except RecordError:
                node["children"].append({"run_id": cid, "sop": c.get("sop"), "status": "missing", "step": c.get("step"),
                                         "depth": _depth + 1, "raised": None, "outcome": None, "children": []})
    return node
