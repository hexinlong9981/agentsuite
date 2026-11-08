"""Pillar 3 — secret filter.

Runs over the checkout *before* a consumer reads it. Best-effort, regex-based:
a defense-in-depth layer, not the only barrier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import FilterError
from .types import MaskReport

MASK = "***MASKED***"


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: "re.Pattern[str]"


def builtin_rules() -> list[Rule]:
    return [
        Rule("private_key", re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.S)),
        Rule("url_credentials", re.compile(
            r"(?P<scheme>[a-z][a-z0-9+.\-]*://[^\s:/@]+:)[^\s/@]+(?P<at>@)")),
        Rule("env_secret", re.compile(
            r"(?im)^(?P<k>[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY)"
            r"[A-Z0-9_]*\s*[=:]\s*)\S+")),
    ]


def _apply(text: str, rules: list[Rule]) -> tuple[str, int]:
    hits = 0

    def make_repl(rule: Rule):
        def repl(m: "re.Match[str]") -> str:
            nonlocal hits
            hits += 1
            if rule.id == "url_credentials":
                return m.group("scheme") + MASK + m.group("at")
            if rule.id == "env_secret":
                return m.group("k") + MASK
            return MASK
        return repl

    for rule in rules:
        text = rule.pattern.sub(make_repl(rule), text)
    return text, hits


def scan_and_filter(tree: Path, config: Config) -> MaskReport:
    sf = config.secret_filter
    if not sf.enabled:
        return MaskReport(enabled=False, action=sf.action)

    rules = builtin_rules() if sf.builtin_rules else []
    for i, pat in enumerate(sf.extra_rules):
        rules.append(Rule(f"extra_{i}", re.compile(pat, re.S)))

    scanned = hits_total = masked = deleted = 0
    for path in tree.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            if path.stat().st_size > sf.max_file_bytes:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw:            # binary: skip content scan
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        scanned += 1
        new_text, hits = _apply(text, rules)
        if not hits:
            continue
        hits_total += hits
        if sf.action == "fail":
            raise FilterError(
                f"secret detected in {path.relative_to(tree)} (secret_filter.action=fail)")
        if sf.action == "delete":
            try:
                path.unlink()
                deleted += 1
            except OSError:
                pass
        else:  # mask
            try:
                path.write_text(new_text, encoding="utf-8")
                masked += 1
            except OSError:
                pass
    return MaskReport(enabled=True, action=sf.action, files_scanned=scanned,
                      hits=hits_total, files_masked=masked, files_deleted=deleted)
