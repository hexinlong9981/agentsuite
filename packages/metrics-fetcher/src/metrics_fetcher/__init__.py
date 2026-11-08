"""metrics-fetcher — fetch time-series metrics from pluggable sources to CSV.

- sources are pluggable and read-only (metrics are only read)
- granularity (period) is chosen automatically so a long range does not return
  too many points, and warns when the request cannot be met at native resolution
- CSV output is contained to a configured directory (no path traversal)
- sources are an allowlist; every fetch is audited
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
