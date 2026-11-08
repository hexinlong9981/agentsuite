"""Render a run as text or a self-contained HTML timeline (no external deps)."""

from __future__ import annotations

import html as _html

from .types import Event, RunMeta


def render_text(meta: RunMeta, events: list[Event]) -> str:
    lines = [
        f"run {meta.run_id}  flow={meta.flow}  status={meta.status}",
        f"  started {meta.started_at}  finished {meta.finished_at}",
    ]
    for e in events:
        lines.append(f"  [{e.ts}] {e.type:<10} {e.step:<16} {e.data}")
    return "\n".join(lines)


def render_html(meta: RunMeta, events: list[Event]) -> str:
    items = "".join(
        f"<li><code>{_html.escape(e.ts)}</code> <b>{_html.escape(e.type)}</b> "
        f"{_html.escape(e.step)} — {_html.escape(str(e.data))}</li>"
        for e in events
    )
    return (
        "<!doctype html><meta charset=\"utf-8\">"
        f"<title>agentflow {_html.escape(meta.run_id)}</title>"
        "<body style=\"font-family:system-ui,sans-serif;background:#fff;color:#0f172a\">"
        f"<h1>{_html.escape(meta.run_id)}</h1>"
        f"<p>flow={_html.escape(meta.flow)} · status={_html.escape(meta.status)} · "
        f"started={_html.escape(meta.started_at)} · finished={_html.escape(str(meta.finished_at))}</p>"
        f"<ol>{items}</ol></body>"
    )
