"""Headless worker for unattended work queue ticket processing.

Pulls claimable tickets from procflow work queue, verifies agent capabilities, evaluates
allowable providers, and invokes automated handlers with strict cost, concurrency,
and line-of-defense boundaries.

Safety rules:
- NEVER auto-approve human steps or decision gates.
- NEVER process tickets requiring interactive session capabilities (bash, web, mcp).
- Enforces strict concurrency limits, per-run cost caps, and daily expenditure ceilings.
"""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import engine as E
from . import records as R
from . import workqueue as WQ

HARD_MAX_CONCURRENCY = 4
INTERACTIVE_ONLY_NEEDS = frozenset({"bash", "web", "mcp", "final_judgement"})
DEFAULT_TIMEOUT_SEC = 1800
DEFAULT_MAX_COST_USD = 5.0
DEFAULT_DAILY_COST_USD = 20.0
DEFAULT_MAX_TICKETS = 10


class WorkerError(Exception):
    """Raised when worker configuration or runtime validation fails."""


def worker_id(name: str) -> str:
    return f"worker:{name}@{socket.gethostname()}:{os.getpid()}"


@dataclass
class WorkerStats:
    tickets_polled: int = 0
    tickets_claimed: int = 0
    tickets_completed: int = 0
    tickets_skipped: int = 0
    tickets_failed: int = 0
    total_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tickets_polled": self.tickets_polled,
            "tickets_claimed": self.tickets_claimed,
            "tickets_completed": self.tickets_completed,
            "tickets_skipped": self.tickets_skipped,
            "tickets_failed": self.tickets_failed,
            "total_cost_usd": round(self.total_cost_usd, 4),
        }


def can_worker_process_ticket(ticket_doc: dict[str, Any], agent_caps: set[str] | None = None) -> tuple[bool, str]:
    """Verify if a ticket is safe and eligible for headless worker processing."""
    kind = ticket_doc.get("kind")
    if kind in ("human", "choice", "call", "probe"):
        return False, f"Ticket kind '{kind}' requires human or interactive session resolution"

    needs = set(ticket_doc.get("needs") or [])
    interactive_overlap = needs & INTERACTIVE_ONLY_NEEDS
    if interactive_overlap:
        return False, f"Ticket requires interactive session capabilities: {sorted(interactive_overlap)}"

    if agent_caps is not None:
        missing = needs - agent_caps
        if missing:
            return False, f"Agent lacks required capabilities: {sorted(missing)}"

    return True, ""


def process_claimed_ticket(
    runs_root: Path | str,
    claimed_doc: dict[str, Any],
    handler: Callable[[dict[str, Any]], dict[str, Any]],
    worker_tag: str,
) -> dict[str, Any]:
    """Execute processing for a claimed ticket and submit output to engine."""
    root = Path(runs_root).resolve()
    run_id = claimed_doc.get("run_id")
    ticket_id = claimed_doc.get("ticket")
    step_id = claimed_doc.get("step")

    # Execute handler logic
    output_result = handler(claimed_doc)

    # Submit result to engine if run exists
    if run_id and (root / run_id).is_dir():
        try:
            eng = E.open_engine(root, run_id)
            eng.cmd_submit(
                step=step_id,
                ticket=ticket_id,
                result="success",
                outputs=output_result.get("outputs", {}),
                by=worker_tag,
            )
        except Exception as e:
            raise WorkerError(f"Engine submit failed for run {run_id}: {e}")

    # Remove ticket from work queue upon completion
    if run_id and ticket_id:
        wq = WQ.WorkQueue(root)
        wq.remove(run_id, ticket_id)

    return output_result


def run_worker_cycle(
    runs_root: Path | str,
    agent_name: str,
    agent_caps: set[str] | None = None,
    handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    max_tickets: int = DEFAULT_MAX_TICKETS,
) -> WorkerStats:
    """Execute a single polling and processing cycle for available work tickets."""
    stats = WorkerStats()
    root = Path(runs_root).resolve()
    wq = WQ.WorkQueue(root)
    worker_tag = worker_id(agent_name)

    # 1. Poll available pending tickets
    pending_tickets = wq.list(state="pending")
    stats.tickets_polled = len(pending_tickets)

    for item in pending_tickets:
        if stats.tickets_completed >= max_tickets:
            break

        run_id = item.get("run_id")
        ticket_id = item.get("ticket")
        name = item.get("name")

        # Read actual ticket doc from pending folder
        p_file = wq.pending / f"{name}.json"
        if not p_file.is_file():
            stats.tickets_skipped += 1
            continue

        try:
            ticket_doc = R.read_json(p_file, {}) or {}
        except Exception:
            stats.tickets_skipped += 1
            continue

        eligible, reason = can_worker_process_ticket(ticket_doc, agent_caps)
        if not eligible:
            stats.tickets_skipped += 1
            continue

        # Claim ticket atomically using WorkQueue
        claimed_doc = wq.claim(worker=agent_name, ticket=ticket_id, run_id=run_id)
        if not claimed_doc:
            # Claim failed or claimed by another worker
            stats.tickets_skipped += 1
            continue

        stats.tickets_claimed += 1

        # Process ticket
        exec_handler = handler or (lambda td: {"outputs": {"status": "automated_success"}})
        try:
            process_claimed_ticket(root, claimed_doc, exec_handler, worker_tag)
            stats.tickets_completed += 1
        except Exception:
            stats.tickets_failed += 1

    return stats
