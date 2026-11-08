"""One-time-password providers (for bastion / 2FA auto-input).

A provider yields the *current* code only. Any secret (a TOTP seed) is read from
the environment / credential store — never stored in the repo, returned, or
logged. The RFC 6238 ``TotpProvider`` uses only the standard library.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Callable

from .errors import OtpError


class OtpProvider(ABC):
    @abstractmethod
    def code(self) -> str:
        """Return the current one-time code."""


class TotpProvider(OtpProvider):
    """RFC 6238 TOTP. ``secret_env`` names the env var holding the base32 seed."""

    def __init__(self, secret_env: str, *, digits: int = 6, period: int = 30,
                 algorithm: str = "sha1", _now: Callable[[], float] | None = None) -> None:
        self.secret_env = secret_env
        self.digits = digits
        self.period = period
        self.algorithm = algorithm
        self._now = _now or time.time

    def _secret(self) -> bytes:
        raw = os.environ.get(self.secret_env)
        if not raw:
            raise OtpError(f"TOTP secret env {self.secret_env!r} is not set")
        s = raw.strip().replace(" ", "").upper()
        s += "=" * ((8 - len(s) % 8) % 8)
        try:
            return base64.b32decode(s, casefold=True)
        except Exception:
            raise OtpError("TOTP secret is not valid base32") from None

    def code(self) -> str:
        counter = int(self._now() // self.period)
        digestmod = getattr(hashlib, self.algorithm, None)
        if digestmod is None:
            raise OtpError(f"unknown TOTP algorithm: {self.algorithm!r}")
        mac = hmac.new(self._secret(), struct.pack(">Q", counter), digestmod).digest()
        offset = mac[-1] & 0x0F
        value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
        return str(value % (10 ** self.digits)).zfill(self.digits)


class CallbackOtpProvider(OtpProvider):
    """Ask a callable for the code (e.g. prompt a human)."""

    def __init__(self, fn: Callable[[], str]) -> None:
        self._fn = fn

    def code(self) -> str:
        return str(self._fn())


class CommandOtpProvider(OtpProvider):
    """Read the code from the stdout of an external command (argv list)."""

    def __init__(self, argv: list[str], *, timeout: float = 10.0) -> None:
        self.argv = list(argv)
        self.timeout = timeout

    def code(self) -> str:
        try:
            proc = subprocess.run(self.argv, capture_output=True, text=True, timeout=self.timeout)
        except Exception as exc:
            raise OtpError(f"OTP command failed: {type(exc).__name__}") from None
        if proc.returncode != 0:
            raise OtpError("OTP command returned a non-zero exit code")
        return proc.stdout.strip()
