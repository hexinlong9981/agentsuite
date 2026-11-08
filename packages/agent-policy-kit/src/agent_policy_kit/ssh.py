"""Build an ``ssh`` argv, including bastion / jump-host (ProxyJump) chains.

Pure and offline — no connection is made here; this only assembles the command a
caller will run. No host or credential is baked in; everything comes from the
caller's config at run time.
"""

from __future__ import annotations

import shlex


def build_ssh_argv(host: str, *, user: str | None = None, port: int | None = None,
                   jump: list[str] | str | None = None, options: list[str] | None = None,
                   remote_argv: list[str] | None = None, ssh: str = "ssh",
                   quote_remote: bool = True) -> list[str]:
    """Return an ssh command list.

    ``jump`` is one or more intermediate hosts (a bastion chain), rendered as
    ``-J h1,h2``. ``options`` are extra ``ssh`` flags (e.g. ``-o BatchMode=yes``).
    ``remote_argv``, when given, is the command to run on the remote host.

    Each ``remote_argv`` token is shell-quoted for a POSIX remote shell by
    default (``quote_remote``). This is required for safety: ``ssh`` joins the
    remote command with spaces and the *remote login shell re-parses it*, so an
    unquoted token containing spaces or metacharacters (``;`` ``|`` ``$()`` ...)
    would split into multiple arguments or inject commands. The ``--`` only
    stops *local* ``ssh`` option parsing, not remote shell interpretation. Pass
    ``quote_remote=False`` only when the caller has already quoted the tokens and
    deliberately wants remote shell expansion.
    """
    argv = [ssh]
    if port:
        argv += ["-p", str(port)]
    if jump:
        hops = jump if isinstance(jump, str) else ",".join(jump)
        if hops:
            argv += ["-J", hops]
    argv += list(options or [])
    argv.append(f"{user}@{host}" if user else host)
    if remote_argv is not None:
        remote = [shlex.quote(a) for a in remote_argv] if quote_remote else list(remote_argv)
        argv += ["--", *remote]
    return argv
