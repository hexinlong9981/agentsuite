"""Harvest and draft synthesis for procflow SOPs.

Collects execution traces from logs and event records, identifies repeated step sequences,
and synthesizes draft procedure definitions (SOP YAML) for human review and refinement.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TraceStep:
    name: str
    action: str
    actor: str = "agent"  # agent, human, script
    tool: str | None = None
    status: str = "success"
    outputs: list[str] = field(default_factory=list)


@dataclass
class WorkTrace:
    trace_id: str
    source_kind: str  # "eventlog", "session", "proposal"
    steps: list[TraceStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PatternCandidate:
    pattern_id: str
    step_names: list[str]
    frequency: int
    source_trace_ids: list[str] = field(default_factory=list)


def parse_trace_from_jsonl(path: Path | str) -> WorkTrace | None:
    """Parse work trace from a JSONL event log."""
    p = Path(path)
    if not p.exists():
        return None

    steps: list[TraceStep] = []
    trace_id = p.stem

    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                name = ev.get("step") or ev.get("name") or ev.get("action")
                if not name:
                    continue
                action = ev.get("action") or ev.get("detail") or name
                actor = ev.get("actor") or ("human" if ev.get("ev") == "approval" else "agent")
                tool = ev.get("tool")
                status = ev.get("status") or ("success" if ev.get("ev") != "error" else "failed")
                steps.append(TraceStep(name=str(name), action=str(action), actor=actor, tool=tool, status=status))
    except Exception:
        return None

    if not steps:
        return None

    return WorkTrace(trace_id=trace_id, source_kind="eventlog", steps=steps)


def parse_trace_from_markdown(path: Path | str) -> WorkTrace | None:
    """Parse steps from Markdown proposal or workflow notes."""
    p = Path(path)
    if not p.exists():
        return None

    steps: list[TraceStep] = []
    text = p.read_text(encoding="utf-8")

    # Match numbered step list lines like "1. Run database migration" or "2. Verify health check"
    step_re = re.compile(r"^\s*(\d+)[\.\)]\s+(.+)$", re.MULTILINE)
    for match in step_re.finditer(text):
        content = match.group(2).strip()
        name = content.split(":", 1)[0].strip() if ":" in content else content[:40].strip()
        steps.append(TraceStep(name=name, action=content, actor="agent"))

    if not steps:
        return None

    return WorkTrace(trace_id=p.stem, source_kind="proposal", steps=steps)


def cluster_step_patterns(traces: list[WorkTrace], min_frequency: int = 2, n_gram: int = 3) -> list[PatternCandidate]:
    """Identify frequent consecutive step patterns across traces."""
    sequences: Counter[tuple[str, ...]] = Counter()
    trace_map: dict[tuple[str, ...], set[str]] = {}

    for t in traces:
        names = [s.name for s in t.steps]
        if len(names) < n_gram:
            if names:
                seq = tuple(names)
                sequences[seq] += 1
                trace_map.setdefault(seq, set()).add(t.trace_id)
            continue

        for i in range(len(names) - n_gram + 1):
            seq = tuple(names[i : i + n_gram])
            sequences[seq] += 1
            trace_map.setdefault(seq, set()).add(t.trace_id)

    candidates: list[PatternCandidate] = []
    idx = 1
    for seq, count in sequences.most_common():
        if count >= min_frequency:
            candidates.append(
                PatternCandidate(
                    pattern_id=f"PAT-{idx:03d}",
                    step_names=list(seq),
                    frequency=count,
                    source_trace_ids=sorted(trace_map.get(seq, set())),
                )
            )
            idx += 1

    return candidates


def synthesize_draft_yaml(
    title: str,
    pattern: PatternCandidate,
    sop_id: str = "SOP-DRAFT-001",
    purpose: str = "Automated draft synthesized from observed execution traces.",
) -> str:
    """Generate a clean SOP YAML definition from a recognized pattern candidate."""
    steps_list = []
    for i, sname in enumerate(pattern.step_names, start=1):
        step_id = f"step_{i:02d}_{re.sub(r'[^a-zA-Z0-9_]+', '_', sname.lower()).strip('_')}"
        steps_list.append({
            "id": step_id,
            "name": sname,
            "type": "code",
            "action": f"Execute action: {sname}",
        })

    sop_dict = {
        "id": sop_id,
        "name": title,
        "version": "0.1.0",
        "description": purpose,
        "steps": steps_list,
    }

    return yaml.dump(sop_dict, sort_keys=False, allow_unicode=True)
