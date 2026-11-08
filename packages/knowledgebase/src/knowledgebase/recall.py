"""Relevance recall: simple, explainable scoring over name / description / links."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .store import KnowledgeBase

MAX_LIMIT = 50
W_NAME, W_DESC, W_LINK, W_NEIGHBOR = 3.0, 2.0, 1.0, 0.5
_TOK = re.compile(r"\w+", re.UNICODE)


def tokens(text: str) -> set[str]:
    return {t for t in _TOK.findall(text.lower().replace("-", " ").replace("_", " ")) if len(t) > 1}


@dataclass(frozen=True)
class Hit:
    name: str
    score: float
    description: str
    reasons: tuple[str, ...]


def recall(kb: KnowledgeBase, query: str, limit: int = 5) -> list[Hit]:
    """Rank shelf items for `query`. Output is bounded (at most MAX_LIMIT)."""
    limit = max(0, min(int(limit), MAX_LIMIT))
    q = tokens(query)
    if not q or not limit:
        return []
    items = {i.name: i for i in kb.list()}
    base: dict[str, float] = {}
    why: dict[str, list[str]] = {}
    for n, it in items.items():
        s, r = 0.0, []
        nm = q & tokens(n)
        if nm:
            s += W_NAME * len(nm)
            r.append(f"name:{','.join(sorted(nm))}")
        ds = q & tokens(it.description)
        if ds:
            s += W_DESC * len(ds)
            r.append(f"description:{','.join(sorted(ds))}")
        lk = q & set().union(*(tokens(l) for l in it.links)) if it.links else set()
        if lk:
            s += W_LINK * len(lk)
            r.append(f"links:{','.join(sorted(lk))}")
        base[n], why[n] = s, r
    # link proximity: items connected (either direction) to a direct match gain a bonus
    final = dict(base)
    for n, it in items.items():
        for ln in it.links:
            if ln in items:
                if base[ln] > 0:
                    final[n] += W_NEIGHBOR
                    why[n].append(f"linked-to:{ln}")
                if base[n] > 0:
                    final[ln] += W_NEIGHBOR
                    why[ln].append(f"linked-from:{n}")
    hits = [Hit(n, s, items[n].description, tuple(why[n])) for n, s in final.items() if s > 0]
    hits.sort(key=lambda h: (-h.score, h.name))
    return hits[:limit]
