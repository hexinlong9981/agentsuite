"""Select a policy, evaluate, then execute as an argv vector (no shell)."""

from __future__ import annotations

import subprocess
import time

from agent_policy_kit import build_ssh_argv

from . import audit
from . import policy as policy_mod
from .config import Config, Target
from .errors import CommandTimeout, ExecutionError, PolicyDenied
from .types import CommandResult


def _select_policy(config: Config, target_name: str, policy_name: str | None):
    if policy_name:
        pol = config.policies.get(policy_name)
        if pol is None or target_name not in pol.targets:
            raise PolicyDenied(f"policy {policy_name!r} does not apply to target {target_name!r}")
        return pol
    applicable = config.policies_for_target(target_name)
    if not applicable:
        raise PolicyDenied(f"no policy applies to target {target_name!r} (fail-closed)")
    return applicable[0]


def _exec_argv(target: Target, argv: list[str]) -> list[str]:
    if target.kind == "ssh":
        return build_ssh_argv(str(target.host), user=target.user, port=target.port,
                              jump=target.jump, options=target.ssh_options, remote_argv=argv)
    return list(argv)


def run(target_name: str, argv: list[str], *, config: Config,
        policy_name: str | None = None) -> CommandResult:
    target = config.get_target(target_name)
    pol = _select_policy(config, target_name, policy_name)

    decision = policy_mod.evaluate(argv, pol)
    if not decision.allowed:
        audit.log(config, {"action": "run", "target": target_name,
                           "program": argv[0] if argv else "", "result": "denied",
                           "policy": decision.policy, "reason": decision.reason})
        raise PolicyDenied(decision.reason)

    exec_argv = _exec_argv(target, argv)
    start = time.monotonic()
    try:
        proc = subprocess.run(exec_argv, capture_output=True, text=True,
                              timeout=config.limits.timeout_seconds)
    except subprocess.TimeoutExpired:
        audit.log(config, {"action": "run", "target": target_name, "program": argv[0],
                           "result": "timeout", "policy": pol.name})
        raise CommandTimeout(f"command exceeded {config.limits.timeout_seconds}s") from None
    except (OSError, ValueError) as exc:
        audit.log(config, {"action": "run", "target": target_name, "program": argv[0],
                           "result": "error", "policy": pol.name})
        raise ExecutionError(str(exc)) from None

    duration_ms = int((time.monotonic() - start) * 1000)
    cap = config.limits.max_output_bytes
    out, err = proc.stdout or "", proc.stderr or ""
    truncated = len(out) > cap or len(err) > cap
    audit.log(config, {"action": "run", "target": target_name, "program": argv[0],
                       "result": "ok", "policy": pol.name, "returncode": proc.returncode})
    return CommandResult(
        target=target_name, returncode=proc.returncode,
        stdout=out[:cap], stderr=err[:cap], duration_ms=duration_ms,
        truncated=truncated, allowed_by=f"{decision.policy}:{decision.program}",
    )


def list_allowed(config: Config, target_name: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for pol in config.policies_for_target(target_name):
        for rule in pol.allow:
            rows.append((pol.name, rule.program, rule.args_regex or ""))
    return rows
