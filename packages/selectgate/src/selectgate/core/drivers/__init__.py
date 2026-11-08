"""Pluggable driver interface. A driver must only ever read unless write-mode is explicit."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..errors import DriverError, ExecutionError
from ..types import PlanEstimate, QueryResult, WriteResult


class Driver(ABC):
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @abstractmethod
    def connect(self) -> "Driver": ...

    @abstractmethod
    def explain(self, sql: str) -> PlanEstimate:
        """Return a scan estimate for ``sql`` without executing it for real."""

    @abstractmethod
    def execute(self, sql: str, *, max_rows: int, timeout: float) -> QueryResult: ...

    def execute_write(self, sql: str, *, timeout: float) -> WriteResult:
        """Execute a write and commit. Drivers that support write-mode override this."""
        raise ExecutionError("this driver does not support write-mode")

    def close(self) -> None:  # pragma: no cover - trivial
        pass


_REGISTRY: dict[str, type[Driver]] = {}


def register(name: str, cls: type[Driver]) -> None:
    _REGISTRY[name] = cls


def get_driver(name: str) -> type[Driver]:
    if name not in _REGISTRY:
        raise DriverError(f"no such driver: {name!r} (available: {sorted(_REGISTRY)})")
    return _REGISTRY[name]


# Register built-in drivers.
from .mysql_driver import MysqlDriver  # noqa: E402
from .postgres_driver import PostgresDriver  # noqa: E402
from .sqlite_driver import SqliteDriver  # noqa: E402

register("sqlite", SqliteDriver)
register("postgres", PostgresDriver)
register("postgresql", PostgresDriver)
register("mysql", MysqlDriver)
register("mariadb", MysqlDriver)
