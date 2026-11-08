"""Extractor for Java procedure definitions into neutral procflow definition documents.

Provides pure-Python AST/regex parsing to extract procedure metadata, steps, and transitions
from annotated Java source files, as well as an interface to invoke the external Java-based
extractor when a full build environment is configured.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PROC_ANNOT_RE = re.compile(
    r"@Procedure\s*\(\s*(?:id\s*=\s*)?\"([^\"]+)\"(?:.*?name\s*=\s*\"([^\"]+)\")?(?:.*?version\s*=\s*\"([^\"]+)\")?.*?\)",
    re.DOTALL,
)

STEP_ANNOT_RE = re.compile(
    r"@Step\s*\((.*?)\)\s*(?:@\w+\s*)*\s*(?:protected|private|public)?\s*(?:abstract\s+)?(?:void|\w+)\s+(\w+)\s*\(",
    re.DOTALL,
)

TRANSITION_CALL_RE = re.compile(
    r"b\.transition\(\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\"\s*\)(.*?);",
    re.DOTALL,
)

STEP_CALL_RE = re.compile(
    r"b\.step\(\s*\"([^\"]+)\"\s*\)(.*?);",
    re.DOTALL,
)

START_CALL_RE = re.compile(r"b\.start\(\s*\"([^\"]+)\"\s*\)", re.DOTALL)
END_CALL_RE = re.compile(r"b\.end\(\s*\"([^\"]+)\"(?:\s*,\s*\"([^\"]+)\")?\s*\)", re.DOTALL)


def _extract_annot_field(attrs: str, field_name: str) -> str | None:
    m = re.search(rf'{field_name}\s*=\s*"([^"]+)"', attrs)
    if m:
        return m.group(1)
    return None


def _extract_annot_array(attrs: str, field_name: str) -> list[str]:
    m = re.search(rf'{field_name}\s*=\s*\{{([^\}}]+)\}}', attrs)
    if m:
        raw = m.group(1)
        return [s.strip().strip('"') for s in raw.split(",") if s.strip()]
    return []


def extract_from_source(source_code: str) -> dict[str, Any] | None:
    """Parse a single Java source string and extract a procedure definition dictionary."""
    proc_match = PROC_ANNOT_RE.search(source_code)
    if not proc_match:
        return None

    sop_id = proc_match.group(1)
    sop_name = proc_match.group(2) or sop_id
    sop_version = proc_match.group(3) or "1.0.0"

    definition: dict[str, Any] = {
        "id": sop_id,
        "name": sop_name,
        "version": sop_version,
        "sop": {
            "id": sop_id,
            "name": sop_name,
            "version": sop_version,
        },
        "steps": [],
        "transitions": [],
    }

    # Extract start nodes
    for sm in START_CALL_RE.finditer(source_code):
        definition["steps"].append({"id": sm.group(1), "type": "start"})

    # Extract annotated task steps
    step_ids_seen = {s["id"] for s in definition["steps"]}
    for sm in STEP_ANNOT_RE.finditer(source_code):
        attrs = sm.group(1)
        sid = _extract_annot_field(attrs, "id") or sm.group(2)
        if sid in step_ids_seen:
            continue
        step_ids_seen.add(sid)

        s_obj: dict[str, Any] = {
            "id": sid,
            "type": "task",
        }
        name = _extract_annot_field(attrs, "name")
        if name:
            s_obj["name"] = name
        action = _extract_annot_field(attrs, "action")
        if action:
            s_obj["action"] = action

        inputs = _extract_annot_array(attrs, "inputs")
        if inputs:
            s_obj["inputs"] = inputs

        outputs = _extract_annot_array(attrs, "outputs")
        if outputs:
            s_obj["outputs"] = outputs

        # Timeout
        to_m = re.search(r"timeoutSec\s*=\s*(\d+)", attrs)
        if to_m:
            s_obj["timeout_sec"] = int(to_m.group(1))

        definition["steps"].append(s_obj)

    # Extract end nodes
    for em in END_CALL_RE.finditer(source_code):
        eid = em.group(1)
        outcome = em.group(2) or "success"
        if eid not in step_ids_seen:
            definition["steps"].append({"id": eid, "type": "end", "outcome": outcome})
            step_ids_seen.add(eid)

    # Extract transitions
    for tm in TRANSITION_CALL_RE.finditer(source_code):
        tid = tm.group(1)
        tfrom = tm.group(2)
        tto = tm.group(3)
        chain = tm.group(4)

        t_obj: dict[str, Any] = {
            "id": tid,
            "from": tfrom,
            "to": tto,
        }
        gm = re.search(r'\.guard\(\s*"([^"]+)"\s*\)', chain)
        if gm:
            t_obj["guard"] = gm.group(1)
        if ".byDefault()" in chain:
            t_obj["default"] = True

        definition["transitions"].append(t_obj)

    return definition


def extract_from_file(file_path: Path | str) -> dict[str, Any] | None:
    """Extract a procedure definition from a Java source file."""
    p = Path(file_path).resolve()
    if not p.is_file():
        return None
    content = p.read_text(encoding="utf-8", errors="replace")
    return extract_from_source(content)


def scan_and_extract_directory(dir_path: Path | str) -> list[dict[str, Any]]:
    """Scan a directory for Java files and extract all procedures."""
    p = Path(dir_path).resolve()
    results: list[dict[str, Any]] = []
    if not p.is_dir():
        return results

    for jf in p.rglob("*.java"):
        d = extract_from_file(jf)
        if d:
            results.append(d)

    return results
