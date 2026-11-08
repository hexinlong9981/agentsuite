"""Secret masking for text and structured data (best-effort, defense-in-depth)."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_MASK = "***MASKED***"

_SECRET_KEY_RE = re.compile(
    r"(?i)(secret|token|password|passwd|api[_-]?key|authorization|credential|private[_-]?key)")


@dataclass(frozen=True)
class _Rule:
    id: str
    pattern: "re.Pattern[str]"


def _builtin_rules() -> list[_Rule]:
    return [
        _Rule("private_key", re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
        _Rule("url_credentials", re.compile(
            r"(?P<scheme>[a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s/@]+(?P<at>@)")),
        _Rule("kv_secret", re.compile(
            r"(?im)^(?P<k>[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY)"
            r"[A-Z0-9_]*\s*[=:]\s*)\S+")),
    ]


class SecretMasker:
    def __init__(self, *, builtin: bool = True, extra_patterns: list[str] | None = None,
                 mask: str = DEFAULT_MASK) -> None:
        self.mask = mask
        self.rules = _builtin_rules() if builtin else []
        for i, pat in enumerate(extra_patterns or []):
            self.rules.append(_Rule(f"extra_{i}", re.compile(pat, re.S)))

    def mask_text(self, text: str) -> str:
        if not isinstance(text, str):
            return text

        def repl(rule: _Rule):
            def _r(m: "re.Match[str]") -> str:
                if rule.id == "url_credentials":
                    return m.group("scheme") + self.mask + m.group("at")
                if rule.id == "kv_secret":
                    return m.group("k") + self.mask
                return self.mask
            return _r

        for rule in self.rules:
            text = rule.pattern.sub(repl(rule), text)
        return text

    def mask_obj(self, obj):
        """Recurse through dict/list/str; redact values whose key names a secret."""
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if isinstance(k, str) and _SECRET_KEY_RE.search(k) and isinstance(v, (str, int, float)):
                    out[k] = self.mask
                else:
                    out[k] = self.mask_obj(v)
            return out
        if isinstance(obj, list):
            return [self.mask_obj(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(self.mask_obj(v) for v in obj)
        if isinstance(obj, str):
            return self.mask_text(obj)
        return obj
