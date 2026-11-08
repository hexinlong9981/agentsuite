import pytest

from agent_policy_kit import CallbackOtpProvider, CommandOtpProvider, TotpProvider
from agent_policy_kit.errors import OtpError


def test_totp_rfc6238_vector(monkeypatch):
    # RFC 6238 Appendix B: ASCII seed "12345678901234567890" (base32 below),
    # SHA1, T=59 -> 8-digit 94287082, so 6-digit = 287082.
    monkeypatch.setenv("OTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    p = TotpProvider("OTP_SECRET", digits=6, period=30, algorithm="sha1", _now=lambda: 59)
    assert p.code() == "287082"


def test_totp_missing_secret(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    with pytest.raises(OtpError):
        TotpProvider("NOPE", _now=lambda: 0).code()


def test_callback_provider():
    assert CallbackOtpProvider(lambda: "123456").code() == "123456"


def test_command_provider():
    assert CommandOtpProvider(["echo", "654321"]).code() == "654321"


def test_command_provider_failure():
    with pytest.raises(OtpError):
        CommandOtpProvider(["false"]).code()
