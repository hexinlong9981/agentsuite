"""Pillar 2 — push guard (toggleable) + always-on input validation.

Two independent concerns:

- Input validation (ALWAYS on, not toggleable): reject option-injection vectors
  (``--upload-pack=<cmd>`` etc.) and malformed ref/URL names. This is a safety
  property, not a write-protection one.
- Write protection (the push guard proper, toggled by ``push_guard.enabled``):
  allowlist git subcommands/options and forbid push/send-pack. When disabled,
  the allowlist is not enforced (writes become possible) but input validation
  still applies.
"""

from __future__ import annotations

import re

from .errors import GuardViolation, RefNotAllowed

# Subcommands we may run at all (read-oriented).
ALLOWED_SUBCOMMANDS = {
    "clone", "fetch", "checkout", "rev-parse", "rev-list", "log", "show",
    "ls-remote", "ls-files", "remote", "cat-file", "diff", "grep", "config",
}
# Never allowed, even when the push guard is disabled is NOT the rule — these
# are blocked only while the guard is enabled (write protection).
FORBIDDEN_SUBCOMMANDS = {"push", "send-pack", "receive-pack", "upload-pack", "tag"}

# Options allowed globally (when the guard is enforced).
ALLOWED_OPTIONS = {"-q", "--quiet", "--no-pager"}
ALLOWED_OPTIONS_BY_SUBCOMMAND = {
    "clone": {"-b", "--branch", "--depth", "--single-branch",
              "--no-single-branch", "--no-tags", "-q", "--quiet"},
    "rev-list": {"--count", "--max-count", "-n"},
    "log": {"--grep", "-S", "--oneline", "--format", "--pretty",
            "-n", "--max-count", "--follow", "--name-only"},
    "config": {"--get", "--list", "-l"},
    "remote": set(),
    "diff": {"--name-only", "--stat", "--unified", "-U"},
}

# Always blocked — these let git run an arbitrary command or re-config itself.
DANGEROUS_OPTIONS = {
    "--upload-pack", "--receive-pack", "--exec", "-c", "-C", "--config",
    "--config-env",
}

# Ref must not start with '-' (that is what stops option injection via ref).
REF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
# A plausible git URL or local path (also used for `file://`/paths).
URL_RE = re.compile(r"^[A-Za-z0-9._~:/@+\-]{1,2048}$")


def _opt_name(token: str) -> str:
    return token.split("=", 1)[0]


def validate_ref(ref: str) -> str:
    """Always-on: reject refs that could be read as options or are malformed."""
    if not isinstance(ref, str) or not REF_NAME_RE.match(ref):
        raise RefNotAllowed(f"invalid ref name: {ref!r}")
    return ref


def validate_url(url: str) -> str:
    if not isinstance(url, str) or not URL_RE.match(url) or url.startswith("-"):
        raise GuardViolation(f"invalid repository URL/path: {url!r}")
    return url


def validate_git_command(argv: list[str], *, enforce: bool = True) -> None:
    """Validate a git argv (without the leading ``git``).

    ``enforce`` mirrors ``push_guard.enabled``. Dangerous option-injection
    tokens are rejected regardless of ``enforce``.
    """
    if not argv:
        raise GuardViolation("empty git command")

    # Always: block option-injection, regardless of enforce.
    for tok in argv:
        if tok.startswith("-") and _opt_name(tok) in DANGEROUS_OPTIONS:
            raise GuardViolation(
                f"option {tok!r} can run arbitrary commands / reconfigure git; refused"
            )

    if not enforce:
        return

    # Subcommand = first non-option token.
    sub = next((t for t in argv if not t.startswith("-")), None)
    if sub in FORBIDDEN_SUBCOMMANDS:
        raise GuardViolation(f"subcommand {sub!r} is forbidden by the push guard")
    if sub not in ALLOWED_SUBCOMMANDS:
        raise GuardViolation(
            f"subcommand {sub!r} not in allowlist {sorted(ALLOWED_SUBCOMMANDS)}"
        )

    allowed = ALLOWED_OPTIONS_BY_SUBCOMMAND.get(sub, ALLOWED_OPTIONS) | ALLOWED_OPTIONS
    for tok in argv:
        if tok.startswith("-") and _opt_name(tok) not in allowed:
            raise GuardViolation(
                f"option {tok!r} not allowed for subcommand {sub!r} "
                "(prevents arbitrary-command execution via option injection)"
            )
