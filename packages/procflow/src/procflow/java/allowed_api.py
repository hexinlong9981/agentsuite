"""Static allowed-API checker for Java procedure definitions.

Ensures that Java procedure implementations maintain purity and lack hidden side-effects
by disallowing unauthorized standard library packages (networking, reflection, direct filesystem
writes, arbitrary process spawning) via static inspection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DISALLOWED_IMPORT_PATTERNS = [
    (re.compile(r"import\s+java\.net\."), "Networking (java.net) is disallowed in procedure definitions"),
    (re.compile(r"import\s+java\.lang\.reflect\."), "Reflection (java.lang.reflect) is disallowed"),
    (re.compile(r"import\s+java\.io\.File(Writer|OutputStream)?\b"), "Direct file write operations are disallowed"),
    (re.compile(r"import\s+java\.nio\.file\.Files\b"), "Arbitrary filesystem manipulation is disallowed"),
    (re.compile(r"import\s+sun\."), "Internal sun.* packages are strictly disallowed"),
    (re.compile(r"import\s+jdk\."), "Internal jdk.* packages are disallowed"),
]

DISALLOWED_CALL_PATTERNS = [
    (re.compile(r"\bRuntime\.getRuntime\(\)"), "Direct Runtime access is disallowed"),
    (re.compile(r"\bnew\s+ProcessBuilder\b"), "ProcessBuilder execution is disallowed"),
    (re.compile(r"\bSystem\.exit\b"), "System.exit is disallowed"),
]


@dataclass
class ApiViolation:
    file_path: str
    line_number: int
    matched_code: str
    rule_description: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "file": self.file_path,
            "line": self.line_number,
            "code": self.matched_code.strip(),
            "rule": self.rule_description,
        }


def check_source_content(source_code: str, file_label: str = "<source>") -> list[ApiViolation]:
    """Scan a single Java source string for disallowed APIs."""
    violations: list[ApiViolation] = []
    lines = source_code.splitlines()

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue

        # Check imports
        for pattern, desc in DISALLOWED_IMPORT_PATTERNS:
            if pattern.search(line):
                violations.append(
                    ApiViolation(
                        file_path=file_label,
                        line_number=idx,
                        matched_code=stripped,
                        rule_description=desc,
                    )
                )

        # Check calls
        for pattern, desc in DISALLOWED_CALL_PATTERNS:
            if pattern.search(line):
                violations.append(
                    ApiViolation(
                        file_path=file_label,
                        line_number=idx,
                        matched_code=stripped,
                        rule_description=desc,
                    )
                )

    return violations


def check_directory(dir_path: Path | str) -> list[ApiViolation]:
    """Recursively scan a directory of Java source files for API violations."""
    root = Path(dir_path).resolve()
    all_violations: list[ApiViolation] = []
    if not root.is_dir():
        return []

    for jf in root.rglob("*.java"):
        try:
            content = jf.read_text(encoding="utf-8", errors="replace")
            v = check_source_content(content, file_label=str(jf))
            all_violations.extend(v)
        except Exception:
            pass

    return all_violations
