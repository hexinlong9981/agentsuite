"""Pull engine: list the source, filter, classify, contain, copy to destinations.

The source is only read; nothing is ever written back to it.
"""

from __future__ import annotations

from . import audit
from . import classify as clf
from . import containment
from .config import Config
from .errors import LimitExceeded
from .transports import make_transport
from .types import FetchedFile, SyncResult


def sync(policy_name: str, *, config: Config, dry_run: bool = False) -> SyncResult:
    policy = config.get_policy(policy_name)
    source = config.get_source(policy.source)
    transport = make_transport(source)

    fetched: list[FetchedFile] = []
    skipped = 0
    total = 0
    by_category: dict[str, int] = {}

    for relpath in transport.list():
        if policy.include and not clf.matches(relpath, policy.include):
            skipped += 1
            continue
        if policy.exclude and clf.matches(relpath, policy.exclude):
            skipped += 1
            continue

        category = clf.classify(relpath, policy)
        dest = containment.safe_join(config.dest_root(category), relpath)

        data = transport.read(relpath)
        size = len(data)
        if config.limits.max_file_bytes and size > config.limits.max_file_bytes:
            skipped += 1
            continue
        if len(fetched) + 1 > config.limits.max_files:
            raise LimitExceeded(f"max_files ({config.limits.max_files}) exceeded")
        if total + size > config.limits.max_bytes:
            raise LimitExceeded(f"max_bytes ({config.limits.max_bytes}) exceeded")

        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        fetched.append(FetchedFile(path=relpath, category=category, dest=str(dest), bytes=size))
        total += size
        by_category[category] = by_category.get(category, 0) + 1

    audit.log(config, {"action": "sync", "policy": policy_name, "source": policy.source,
                       "result": "dry_run" if dry_run else "ok",
                       "files": len(fetched), "bytes": total, "by_category": by_category})
    return SyncResult(policy=policy_name, source=policy.source, fetched=fetched,
                      skipped=skipped, total_bytes=total, by_category=by_category, dry_run=dry_run)
