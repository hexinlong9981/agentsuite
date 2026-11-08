"""Policy evaluation — fail-closed allowlist with a read-only safety net."""

from __future__ import annotations

import re

from .config import Policy
from .types import Decision

# Shell metacharacters refused unless a policy explicitly sets allow_shell.
_METACHARS = set(";|&`$><\n")

# Refused when a policy is read_only, even if a rule would match.
IRREVERSIBLE = {
    "rm", "rmdir", "mv", "dd", "mkfs", "shutdown", "reboot", "halt", "poweroff",
    "kill", "pkill", "killall", "chown", "chmod", "chgrp", "truncate", "shred",
    "tee", "sed",  # sed -i / tee can overwrite; refuse under read_only
}


def evaluate(argv: list[str], policy: Policy) -> Decision:
    if not argv or not argv[0]:
        return Decision(False, "empty command", policy.name)
    program = argv[0]
    argstr = " ".join(argv[1:])

    if not policy.allow_shell:
        for tok in argv:
            if any(ch in tok for ch in _METACHARS):
                return Decision(False, f"argument contains a shell metacharacter: {tok!r}",
                                policy.name, program)

    if policy.read_only and program in IRREVERSIBLE:
        return Decision(False, f"program {program!r} is irreversible and the policy is read-only",
                        policy.name, program)

    for rule in policy.allow:
        if rule.program != program:
            continue
        if rule.args_regex and not re.search(rule.args_regex, argstr):
            continue
        return Decision(True, f"matched {policy.name}:{program}", policy.name, program)

    return Decision(False, f"no allow rule in policy {policy.name!r} matches program {program!r}",
                    policy.name, program)
