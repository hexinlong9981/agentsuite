"""Approval hooks — re-exports the shared core (agent_policy_kit.approval)."""

from __future__ import annotations

from agent_policy_kit.approval import AllowAll, Approval, Callback, DenyAll

__all__ = ["Approval", "DenyAll", "AllowAll", "Callback"]
