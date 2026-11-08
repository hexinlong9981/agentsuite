"""A file-based work queue for queued (session) work tickets.

Layout under ``<runs_root>/_queue/``::

    pending/<name>.json                   a ticket nobody has taken
    claimed/<name>.claim-<worker>.json    a ticket taken by <worker> (``claim`` block: worker, at, expires)

``<name>`` = ``<run_id>__<ticket>``. **Claiming is one ``os.rename`` of the pending file** onto a name that
carries the worker, so exactly one of several concurrent claimants wins (the others see the file gone). A claim
has a lease; an expired claim goes back to pending (``reap``) and the former claimant may no longer submit.
Only the claimant may ``release`` or submit (the engine asks :func:`claimant`). The queue holds locations and
ids only -- the prompt and context live in the run directory -- and every written value is secret-masked.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path

from agent_policy_kit import SecretMasker, safe_name

from . import records as R

LEASE_SECONDS = 30 * 60
_MASK = SecretMasker()
CLAIM_RE = re.compile(r"^(?P<name>.+)\.claim-(?P<worker>[A-Za-z0-9._-]+)\.json$")


def queue_name(run_id, ticket):
    return f"{safe_name(run_id)}__{safe_name(ticket)}"


class WorkQueue:
    def __init__(self, runs_root, lease=LEASE_SECONDS, clock=None):
        self.root = Path(runs_root) / "_queue"
        self.pending = self.root / "pending"
        self.claimed = self.root / "claimed"
        self.lease = lease
        self.clock = clock or R.now

    def _dirs(self):
        self.pending.mkdir(parents=True, exist_ok=True)
        self.claimed.mkdir(parents=True, exist_ok=True)

    # ---- producer side ------------------------------------------------------------------------
    def enqueue(self, doc):
        """Add a ticket (idempotent per run + ticket). ``doc`` needs run_id, ticket, step, sop."""
        self._dirs()
        name = queue_name(doc["run_id"], doc["ticket"])
        if (self.pending / f"{name}.json").exists() or self._claim_path(name) is not None:
            return name
        doc = _MASK.mask_obj(dict(doc, queued_at=R.iso(self.clock())))
        R.write_json(self.pending / f"{name}.json", doc)
        return name

    def remove(self, run_id, ticket):
        """Drop a ticket whatever its state (the run closed or discarded it)."""
        name = queue_name(run_id, ticket)
        (self.pending / f"{name}.json").unlink(missing_ok=True)
        cp = self._claim_path(name)
        if cp is not None:
            cp.unlink(missing_ok=True)

    # ---- worker side --------------------------------------------------------------------------
    def _claim_path(self, name):
        if not self.claimed.is_dir():
            return None
        hits = sorted(self.claimed.glob(f"{name}.claim-*.json"))
        return hits[0] if hits else None

    def list(self, state="pending"):
        """Rows ``{name, state, run_id, ticket, step, sop, worker?, expires?}`` (``state``: pending | claimed | all)."""
        self.reap()
        rows = []
        if state in ("pending", "all") and self.pending.is_dir():
            for p in sorted(self.pending.glob("*.json")):
                d = R.read_json(p, {}) or {}
                rows.append({"name": p.stem, "state": "pending", **_brief(d)})
        if state in ("claimed", "all") and self.claimed.is_dir():
            for p in sorted(self.claimed.glob("*.json")):
                m = CLAIM_RE.match(p.name)
                d = R.read_json(p, {}) or {}
                rows.append({"name": m.group("name") if m else p.stem, "state": "claimed", **_brief(d),
                             "worker": (d.get("claim") or {}).get("worker") or (m.group("worker") if m else None),
                             "expires": (d.get("claim") or {}).get("expires")})
        return rows

    def claim(self, worker, ticket=None, run_id=None):
        """Take one pending ticket (a given one, else the oldest). Returns its doc, or None when none could be taken."""
        self.reap()
        wk = safe_name(worker, default="worker")
        if not self.pending.is_dir():
            return None
        if ticket:
            names = [p for p in self.pending.glob("*.json") if p.stem == ticket or p.stem.endswith(f"__{safe_name(ticket)}")]
            if run_id:
                names = [p for p in names if p.stem == queue_name(run_id, ticket)]
        else:
            names = sorted(self.pending.glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name))
        for src in names:
            self._dirs()
            dst = self.claimed / f"{src.stem}.claim-{wk}.json"
            try:
                os.rename(src, dst)           # atomic: only one claimant can move the file
            except (FileNotFoundError, OSError):
                continue
            d = R.read_json(dst, {}) or {}
            t = self.clock()
            d["claim"] = {"worker": wk, "at": R.iso(t), "expires": R.iso(t + _dt.timedelta(seconds=self.lease))}
            R.write_json(dst, d)
            return d
        return None

    def claimant(self, run_id, ticket):
        """``{"worker", "expires", "expired"}`` of the current claim, or None (pending / not queued)."""
        cp = self._claim_path(queue_name(run_id, ticket))
        if cp is None:
            return None
        m = CLAIM_RE.match(cp.name)
        d = R.read_json(cp, {}) or {}
        c = d.get("claim") or {}
        exp = R.parse_iso(c.get("expires"))
        if exp is None:  # crashed between rename and write: use the file time
            exp = _dt.datetime.fromtimestamp(cp.stat().st_mtime, _dt.timezone.utc) + _dt.timedelta(seconds=self.lease)
        return {"worker": c.get("worker") or (m.group("worker") if m else None), "expires": R.iso(exp),
                "expired": exp <= self.clock()}

    def release(self, worker, ticket, run_id=None):
        """Give a claimed ticket back (claimant only). Returns True when it went back to pending."""
        wk = safe_name(worker, default="worker")
        for p in sorted(self.claimed.glob("*.json")) if self.claimed.is_dir() else []:
            m = CLAIM_RE.match(p.name)
            if not m or m.group("worker") != wk:
                continue
            if m.group("name") == ticket or m.group("name").endswith(f"__{safe_name(ticket)}"):
                if run_id and m.group("name") != queue_name(run_id, ticket):
                    continue
                return self._back(p, m.group("name"))
        return False

    def _back(self, claim_path, name):
        d = R.read_json(claim_path, {}) or {}
        d.pop("claim", None)
        R.write_json(claim_path, d)
        try:
            os.rename(claim_path, self.pending / f"{name}.json")
        except OSError:
            return False
        return True

    def reap(self):
        """Return claims whose lease expired to pending. Returns how many."""
        n = 0
        for p in sorted(self.claimed.glob("*.json")) if self.claimed.is_dir() else []:
            m = CLAIM_RE.match(p.name)
            if not m:
                continue
            d = R.read_json(p, {}) or {}
            exp = R.parse_iso((d.get("claim") or {}).get("expires"))
            if exp is None:
                exp = _dt.datetime.fromtimestamp(p.stat().st_mtime, _dt.timezone.utc) + _dt.timedelta(seconds=self.lease)
            if exp <= self.clock() and self._back(p, m.group("name")):
                n += 1
        return n


def _brief(d):
    return {k: d.get(k) for k in ("run_id", "ticket", "step", "sop", "kind", "target", "queued_at", "path")}
