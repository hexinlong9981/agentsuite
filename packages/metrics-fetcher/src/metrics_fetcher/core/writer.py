"""Serialize a Series to CSV text."""

from __future__ import annotations

import csv
import io

from .types import Series, iso


def to_csv(series: Series) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["epoch", "iso", "value"])
    for p in series.points:
        w.writerow([p.ts, iso(p.ts), "" if p.value is None else p.value])
    return buf.getvalue()
