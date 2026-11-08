"""selectgate — a read-only SQL gateway.

Run SELECT statements safely against configured targets:

  * statement guard  — only read-only statements (fail-closed)
  * plan gate        — EXPLAIN the query first and refuse ones that would scan
                       more rows than the configured budget
  * timeout          — queries are interrupted past a deadline
  * multi-target     — targets are an allowlist (unknown target => refused)
  * audit            — every query (and refusal) is logged, without values

Drivers are pluggable; a stdlib SQLite driver ships in the box.
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
