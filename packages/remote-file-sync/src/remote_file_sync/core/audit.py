"""Audit log — delegates to the shared core (agent_policy_kit.AuditLog)."""

from __future__ import annotations

from agent_policy_kit import AuditLog

from .config import Config


def log(config: Config, event: dict) -> None:
    AuditLog(config.audit.path).record(event)
