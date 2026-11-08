#!/usr/bin/env python3
"""Pre-publish scanner for agentsuite OSS repository.

This catches *patterned* leaks automatically. It does NOT recognize arbitrary
organization / product / customer names (open-ended plain words) or every
corporate domain (e.g. ``.co.jp`` / ``.work``): a clean run is necessary but not
sufficient, so a human name-review of the diff remains a required pre-publish
step. See CONTRIBUTING / SECURITY for that checklist.

Detects (by regex):
1. Internal context by shape:
   - Private IPv4 addresses (10.x, 192.168.x, 172.16-31.x)
   - Internal hostnames on reserved internal TLDs (.corp/.internal/.local/.intra)
   - Internal ticket-key patterns
   - Absolute internal workspace paths
2. Committed secrets:
   - Private keys (BEGIN ... PRIVATE KEY)
   - Hardcoded password / token / API-key assignments (quoted literals)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Directories and files to skip during scanning
IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "egg-info",
}

IGNORED_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dylib",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
}

IGNORED_FILES = {
    "pre_publish_scan.py",
    "test_pre_publish_scan.py",
}

# Regex rules for internal context and secrets
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Private Key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    (
        "Private IPv4 Address",
        re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
    ),
    (
        "Internal Hostname / Domain (non-generic example)",
        re.compile(r"(?<!\[targets\.)(?<!target\.)(?<!\.)\b[a-zA-Z0-9_-]+\.(?:corp|internal|local|intra)\b", re.IGNORECASE),
    ),
    (
        "Internal Project / Ticket Pattern",
        re.compile(r"\b(?:CHO|DEV|KAI|SHO|OPE|JIM|UAT|STG|RES)-\d{3,6}\b"),
    ),
    (
        "Internal Path Pattern",
        re.compile(r"/(?:0[1-7]_[a-zA-Z0-9_]+|mnt/box|git_server)"),
    ),
    (
        "Hardcoded Password Assignment",
        re.compile(r"""(?i)(?:password|passwd|pwd|secret)\s*[:=]\s*["'][^"'\s]{8,}["']"""),
    ),
    (
        "Hardcoded API / Bearer Token Assignment",
        re.compile(r"""(?i)(?:bearer|api[_-]?key|access[_-]?token)\s*[:=]\s*["'][a-zA-Z0-9_\-\.]{16,}["']"""),
    ),
]

# Whitelist patterns for generic docs/tests (e.g. documentation or RFC examples)
WHITELIST_PATTERNS = [
    re.compile(r"example\.(?:com|org|net|invalid)"),
    re.compile(r"\b127\.0\.0\.1\b"),
    re.compile(r"\blocalhost\b"),
    re.compile(r"0\.0\.0\.0"),
    re.compile(r"app\.internal\.example\.invalid"),  # RFC 2606 / 6761 reserved example
    re.compile(r"db\.internal\.example\.invalid"),
    re.compile(r"bastion\d*\.example\.invalid"),
    re.compile(r"\[targets\.local\]"),               # TOML table identifier
    re.compile(r"test_secrets\.py"),                 # Masker test fixture
    re.compile(r"AAAA\\n-----END"),                  # Synthetic dummy key in test
    re.compile(r'b"dummy private key'),              # Synthetic dummy key in test
    re.compile(r"b3BlbnNzaC1rZXktdjEAAAA"),          # Synthetic dummy test fixture in gitborrow conftest
]


def is_whitelisted(line: str, path: Path | None = None) -> bool:
    if path and "tests" in path.parts:
        # In test fixtures for secret masking/filters, allow synthetic dummy PEM markers
        if "placeholderplaceholder" in line or "-----BEGIN OPENSSH PRIVATE KEY-----" in line:
            return True
    return any(p.search(line) for p in WHITELIST_PATTERNS)


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Scan a single text file. Returns list of (line_num, rule_name, matched_line)."""
    violations: list[tuple[int, str, str]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f, start=1):
                if is_whitelisted(line, path):
                    continue
                for name, pat in PATTERNS:
                    if pat.search(line):
                        violations.append((idx, name, line.strip()))
    except Exception as exc:
        violations.append((0, "File Read Error", str(exc)))
    return violations


def run_scan(root_dir: Path, verbose: bool = False) -> int:
    total_files = 0
    total_violations = 0

    print(f"Scanning repository for zero-business-info and zero-secrets: {root_dir}")

    for root, dirs, files in os.walk(root_dir):
        # Prune ignored directories in-place
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.endswith(".egg-info")]

        for file in files:
            p = Path(root) / file
            if p.suffix in IGNORED_EXTENSIONS or p.name in IGNORED_FILES:
                continue

            total_files += 1
            issues = scan_file(p)
            if issues:
                total_violations += len(issues)
                rel_path = p.relative_to(root_dir)
                print(f"\n[FAIL] {rel_path}:")
                for line_num, rule, text in issues:
                    print(f"  Line {line_num} [{rule}]: {text[:120]}")
            elif verbose:
                rel_path = p.relative_to(root_dir)
                print(f"[OK] {rel_path}")

    print(f"\nScan complete: {total_files} files inspected, {total_violations} violations found.")
    return 1 if total_violations > 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan agentsuite repository before publishing.")
    parser.add_argument("root", nargs="?", default=".", help="Root directory to scan (default: current dir)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output showing passed files")
    args = parser.parse_args()

    root_dir = Path(args.root).resolve()
    exit_code = run_scan(root_dir, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
