"""Secret/credential sanitization for an agent's OWN work trees.

Scope: this sanitizes procflow's *evidence and workspace* trees (step outputs,
scratch dirs, anything that may be shown to an AI step) so no secret leaks into a
prompt or output. Fetching external source repositories and guarding their push
remotes is a *different theme* that belongs to a source-fetch tool (e.g.
a dedicated source-fetch tool), not here — see docs/DECISIONS.md.

Features:
- ``SourceCodeFilter``: mask/redact secrets in files and, within a *contained*
  directory tree, block known-sensitive files. Operates only inside the given
  root and never follows symlinks out of it.
- ``PushGuard``: a declarative toggle expressing procflow's "never push" stance
  (procflow's own GitOps never pushes; this is the single place that states it).
- Fail-closed redaction of private keys (whole blocks), API tokens and passwords.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from agent_policy_kit import SecretMasker, is_under

_MASKER = SecretMasker()

# Regex patterns for sensitive files and credentials
DEFAULT_SECRET_FILE_PATTERNS = [
    r"^\.env(\..+)?$",
    r"^.*\.pem$",
    r"^.*\.key$",
    r"^.*\.p12$",
    r"^.*\.pfx$",
    r"^id_rsa.*$",
    r"^id_ed25519.*$",
    r"^credentials\.json$",
    r"^secrets\.yaml$",
    r"^.*_secret.*$",
]

# Whole private-key block (header .. footer). DOTALL so the key BODY is redacted,
# not just the BEGIN line.
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
# A truncated key (BEGIN present but no END): redact from the header to end-of-text.
_PRIVATE_KEY_TRUNC = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*\Z",
    re.DOTALL,
)

DEFAULT_SECRET_CONTENT_PATTERNS = [
    re.compile(r"(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9_\-+=/]{16,}['\"]", re.IGNORECASE),
    re.compile(r"(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE),
]


@dataclass
class FilterResult:
    files_scanned: int = 0
    files_masked: int = 0
    sensitive_files_blocked: list[str] = field(default_factory=list)
    secrets_redacted_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "files_scanned": self.files_scanned,
            "files_masked": self.files_masked,
            "sensitive_files_blocked": list(self.sensitive_files_blocked),
            "secrets_redacted_count": self.secrets_redacted_count,
        }


class PushGuard:
    """Configurable Git push guard for procedure execution and autonomous revision."""

    def __init__(self, enabled: bool | None = None):
        if enabled is None:
            # Configurable via environment variable (default: True for fail-safe)
            env_val = os.environ.get("PROCFLOW_PUSH_GUARD_ENABLED", "1").strip().lower()
            self._enabled = env_val not in ("0", "false", "no", "off", "disable")
        else:
            self._enabled = bool(enabled)

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def verify_can_push(self, remote_name: str = "origin", branch: str = "") -> tuple[bool, str]:
        """Check if pushing is allowed under the current guard policy."""
        if not self._enabled:
            return True, "Push guard is disabled by configuration"

        # Under push guard, remote pushing is strictly forbidden
        return False, f"Push guard is ENABLED: remote pushes to '{remote_name}' ({branch}) are blocked"


class SourceCodeFilter:
    """Filters downloaded source code and workspace trees to mask or remove sensitive data."""

    def __init__(
        self,
        enabled: bool = True,
        secret_file_patterns: Sequence[str] | None = None,
        max_file_size_bytes: int = 2 * 1024 * 1024,  # 2MB
    ):
        self.enabled = enabled
        self.max_file_size = max_file_size_bytes
        self.file_patterns = [re.compile(p) for p in (secret_file_patterns or DEFAULT_SECRET_FILE_PATTERNS)]

    def is_sensitive_filename(self, path: Path | str) -> bool:
        name = Path(path).name
        return any(pat.match(name) for pat in self.file_patterns)

    def filter_text_content(self, text: str) -> tuple[str, int]:
        """Apply secret masking and redaction to text."""
        if not self.enabled:
            return text, 0

        redactions = 0

        # Redact entire private-key blocks (body included) FIRST, before any other
        # transform can alter the header and hide the body; then any truncated key.
        masked, n = _PRIVATE_KEY_BLOCK.subn("[REDACTED_PRIVATE_KEY]", text)
        redactions += n
        if "PRIVATE KEY-----" in masked and "-----BEGIN" in masked:
            masked2, n2 = _PRIVATE_KEY_TRUNC.subn("[REDACTED_PRIVATE_KEY]", masked)
            if n2:
                redactions += n2
                masked = masked2

        before_mask = masked
        masked = _MASKER.mask_text(masked)
        if masked != before_mask:
            redactions += 1

        for pat in DEFAULT_SECRET_CONTENT_PATTERNS:
            matches = pat.findall(masked)
            if matches:
                redactions += len(matches)
                masked = pat.sub("[REDACTED_CREDENTIAL]", masked)

        return masked, redactions

    def filter_file(self, file_path: Path | str, in_place: bool = True) -> tuple[bool, int]:
        """Sanitize a single source code file."""
        if not self.enabled:
            return False, 0

        p = Path(file_path).resolve()
        if not p.is_file():
            return False, 0

        if p.stat().st_size > self.max_file_size:
            return False, 0

        try:
            raw_text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False, 0

        cleaned_text, redaction_count = self.filter_text_content(raw_text)
        if redaction_count > 0 and in_place:
            p.write_text(cleaned_text, encoding="utf-8")
            return True, redaction_count

        return redaction_count > 0, redaction_count

    def filter_directory(self, root_dir: Path | str, block_sensitive_files: bool = True) -> FilterResult:
        """Scan and sanitize a directory tree in place.

        Contained to ``root_dir``: symlinks are never followed or acted on, and any
        path that resolves outside the root is skipped, so a symlink cannot cause a
        delete or rewrite outside the intended tree. ``block_sensitive_files``
        removes known-sensitive files (in place) so they cannot reach an AI step.
        """
        result = FilterResult()
        root = Path(root_dir).resolve()
        if not root.is_dir():
            return result

        for p in root.rglob("*"):
            try:
                if p.is_symlink():          # never follow/act on symlinks
                    continue
                if not p.is_file():
                    continue
                rp = p.resolve()
                if not is_under(rp, root):  # containment: stay inside the root
                    continue
            except OSError:
                continue

            result.files_scanned += 1

            if self.is_sensitive_filename(p):
                result.sensitive_files_blocked.append(str(p.relative_to(root)))
                if block_sensitive_files:
                    try:
                        rp.unlink(missing_ok=True)
                    except OSError:
                        pass
                continue

            changed, count = self.filter_file(p, in_place=True)
            if changed:
                result.files_masked += 1
                result.secrets_redacted_count += count

        return result
