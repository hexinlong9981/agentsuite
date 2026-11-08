"""Per-run lease lock.

``<run>/.lock`` (JSON: owner, pid, host, acquired, expires, released) is rewritten under flock.

- the lease is ~15 minutes and is renewed whenever the run is advanced (next / submit / resume);
- a different owner inside the lease is refused (:class:`LockBusy`); after expiry the lock is *stale*:
  it is only taken over explicitly (``takeover=True``), otherwise :class:`LockExpired` (= lost lock);
- read-only commands (runs / show / log / status) never take the lock;
- the owner id is ``$PROCFLOW_SESSION`` (else ``user@host``), so separate processes of one session agree.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import getpass
import json
import os
import socket

from .errors import ProcflowError
from .records import iso, now, parse_iso

LEASE_SECONDS = 15 * 60


class LockBusy(ProcflowError):
    def __init__(self, holder):
        super().__init__(f"the run is being advanced by {holder.get('owner')} (lease until {holder.get('expires')}); "
                         "never advance one run from two sessions at once")
        self.holder = holder


class LockExpired(ProcflowError):
    def __init__(self, holder):
        super().__init__(f"the previous owner {holder.get('owner')} lost its lease (expired {holder.get('expires')}); "
                         "continue with `resume --takeover`")
        self.holder = holder


def owner_id():
    v = os.environ.get("PROCFLOW_SESSION")
    if v:
        return v[:200]
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = str(os.getuid())
    return f"{user}@{socket.gethostname()}"


def _io(path, fn):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    with os.fdopen(fd, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            raw = fh.read().strip()
            try:
                cur = json.loads(raw) if raw else None
            except ValueError:
                cur = None
            new, ret = fn(cur)
            if new != "keep":
                fh.seek(0)
                fh.truncate()
                fh.write("" if new is None else json.dumps(new, ensure_ascii=False))
                fh.flush()
            return ret
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _active(cur):
    return isinstance(cur, dict) and cur.get("owner") and not cur.get("released")


def _expired(cur, t):
    exp = parse_iso(cur.get("expires"))
    return exp is None or exp <= t


def acquire(path, owner=None, takeover=False, lease=LEASE_SECONDS, at=None):
    """Take (or renew) the lease. Returns {"took_over": previous holder | None}."""
    owner = owner or owner_id()
    t = at or now()

    def fn(cur):
        took = None
        mine = _active(cur) and cur["owner"] == owner
        if _active(cur) and not mine:
            if not _expired(cur, t):
                raise LockBusy(cur)
            if not takeover:
                raise LockExpired(cur)
            took = cur
        new = {"owner": owner, "pid": os.getpid(), "host": socket.gethostname(),
               "acquired": cur.get("acquired") if mine else iso(t),
               "expires": iso(t + _dt.timedelta(seconds=lease)), "released": False}
        return new, {"took_over": took}
    return _io(path, fn)


def release(path, owner=None, at=None):
    owner = owner or owner_id()
    t = at or now()

    def fn(cur):
        if _active(cur) and cur["owner"] == owner:
            cur = dict(cur)
            cur["released"] = True
            cur["released_at"] = iso(t)
            return cur, True
        return "keep", False
    return _io(path, fn)


def holder(path, at=None):
    """Current holder (read-only), ``{"owner", ..., "expired": bool}`` or None."""
    if not os.path.exists(path):
        return None
    t = at or now()

    def fn(cur):
        if not _active(cur):
            return "keep", None
        d = dict(cur)
        d["expired"] = _expired(cur, t)
        return "keep", d
    return _io(path, fn)
