"""DSN parsing and dialect-specific connection kwargs.

A DSN URL (``scheme://user:pass@host:port/dbname``) means different things to
different driver modules: psycopg accepts the URL string verbatim, but pg8000,
pymysql, mysqlclient and mysql-connector want individual keyword arguments, and
even those disagree on the database keyword (``database`` vs ``db``). These pure
helpers turn one URL into the right call for each module, and are unit-tested
offline so the live connect paths stay correct.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse


def parse_dsn(dsn: str) -> dict[str, Any]:
    """Parse a DB URL into components, omitting absent ones.

    Returns a subset of ``{host, port, user, password, dbname}``. A value that is
    not a parseable URL yields an empty dict (the caller then passes the raw DSN
    to a module that accepts a conninfo string, e.g. psycopg).
    """
    if not isinstance(dsn, str):
        return {}
    try:
        u = urlparse(dsn)
    except (ValueError, TypeError):
        return {}
    out: dict[str, Any] = {}
    if u.hostname:
        out["host"] = u.hostname
    if u.port:
        out["port"] = int(u.port)
    if u.username:
        out["user"] = unquote(u.username)
    if u.password:
        out["password"] = unquote(u.password)
    db = (u.path or "").lstrip("/")
    if db:
        out["dbname"] = db
    return out


def postgres_kwargs(components: dict[str, Any]) -> dict[str, Any]:
    """Map parsed components to pg8000's connect() keyword arguments."""
    k: dict[str, Any] = {}
    for src in ("host", "port", "user", "password"):
        if src in components:
            k[src] = components[src]
    if "dbname" in components:
        k["database"] = components["dbname"]
    return k


def mysql_kwargs(module_name: str, components: dict[str, Any]) -> dict[str, Any]:
    """Map parsed components to a MySQL driver's connect() keyword arguments.

    ``MySQLdb`` (mysqlclient) uses ``db``; pymysql and mysql-connector use
    ``database`` (pymysql also accepts ``db``).
    """
    k: dict[str, Any] = {}
    for src in ("host", "port", "user", "password"):
        if src in components:
            k[src] = components[src]
    if "dbname" in components:
        if module_name == "MySQLdb":
            k["db"] = components["dbname"]
        else:
            k["database"] = components["dbname"]
    return k
