"""The gateway: statement guard -> connect -> plan gate -> execute -> audit.

Reads go through ``run_select`` (SELECT-only + plan gate). Writes go through
``run_write`` and are **opt-in**: refused unless write-mode is enabled, the
statement's leading keyword is in the write allowlist, and (by default) a human
approves the preview.
"""

from __future__ import annotations

from agent_policy_kit import DenyAll

from . import audit, plangate, statement
from .config import Config
from .drivers import get_driver
from .errors import WriteNotApproved, WriteNotEnabled
from .types import PlanEstimate, QueryResult, WriteResult


def run_select(target_name: str, sql: str, *, config: Config,
               max_rows: int | None = None) -> tuple[QueryResult, PlanEstimate]:
    # Guard first (cheap, no connection). Log refusals.
    try:
        target = config.get_target(target_name)
        norm = statement.assert_read_only(sql)
    except Exception as exc:
        audit.log(config, {"action": "query", "target": target_name,
                           "result": "rejected", "reason": type(exc).__name__})
        raise

    driver = get_driver(target.driver)(target.dsn).connect()
    try:
        estimate = driver.explain(norm)
        plangate.evaluate(estimate, config.limits)
        limit = config.limits.max_rows if max_rows is None else max(1, min(int(max_rows), config.limits.max_rows))
        result = driver.execute(norm, max_rows=limit, timeout=config.limits.timeout_seconds)
    except Exception as exc:
        audit.log(config, {"action": "query", "target": target_name,
                           "result": type(exc).__name__})
        driver.close()
        raise
    driver.close()
    audit.log(config, {"action": "query", "target": target_name, "result": "ok",
                       "rows": result.rowcount, "scanned_est": estimate.max_scanned_rows,
                       "truncated": result.truncated})
    return result, estimate


def explain_query(target_name: str, sql: str, *, config: Config) -> PlanEstimate:
    target = config.get_target(target_name)
    norm = statement.assert_read_only(sql)
    driver = get_driver(target.driver)(target.dsn).connect()
    try:
        return driver.explain(norm)
    finally:
        driver.close()


def run_write(target_name: str, sql: str, *, config: Config, approval=None) -> WriteResult:
    """Execute an opt-in write (allowlist + approval + audit). Fail-closed."""
    if not config.write.enabled:
        audit.log(config, {"action": "write", "target": target_name,
                           "result": "rejected", "reason": "WriteNotEnabled"})
        raise WriteNotEnabled("write-mode is not enabled (set [write].enabled and an allowlist)")

    try:
        target = config.get_target(target_name)
        norm, stmt = statement.assert_write_allowed(sql, config.write.allow)
    except Exception as exc:
        audit.log(config, {"action": "write", "target": target_name,
                           "result": "rejected", "reason": type(exc).__name__})
        raise

    if config.write.require_approval:
        approver = approval or DenyAll()
        if not approver.request(target_name, stmt, norm):
            audit.log(config, {"action": "write", "target": target_name,
                               "result": "not_approved", "statement": stmt})
            raise WriteNotApproved(f"write not approved: {stmt}")

    driver = get_driver(target.driver)(target.dsn).connect()
    try:
        result = driver.execute_write(norm, timeout=config.limits.timeout_seconds)
    except Exception as exc:
        audit.log(config, {"action": "write", "target": target_name, "result": type(exc).__name__})
        driver.close()
        raise
    driver.close()
    result = WriteResult(statement=stmt, rowcount=result.rowcount)
    audit.log(config, {"action": "write", "target": target_name, "result": "ok",
                       "statement": stmt, "rows": result.rowcount})
    return result
