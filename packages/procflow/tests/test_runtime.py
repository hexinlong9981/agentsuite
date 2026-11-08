"""Tests for the runtime: Headless worker, metrics ingestion adapter, and MCP server."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from procflow import cli
from procflow import ingest as IG
from procflow import mcp_server as MCPS
from procflow import worker as WK
from procflow import workqueue as WQ


def test_worker_can_process_ticket():
    # Human or choice tickets MUST be rejected
    assert not WK.can_worker_process_ticket({"kind": "human"})[0]
    assert not WK.can_worker_process_ticket({"kind": "choice"})[0]

    # Tickets requiring interactive capabilities MUST be rejected
    assert not WK.can_worker_process_ticket({"kind": "agent", "needs": ["bash"]})[0]
    assert not WK.can_worker_process_ticket({"kind": "agent", "needs": ["web"]})[0]
    assert not WK.can_worker_process_ticket({"kind": "agent", "needs": ["mcp"]})[0]

    # Eligible automated ticket
    ok, _ = WK.can_worker_process_ticket({"kind": "agent", "needs": ["analyze"]})
    assert ok

    # Capabilities matching
    ok, _ = WK.can_worker_process_ticket(
        {"kind": "agent", "needs": ["analyze", "summarize"]},
        agent_caps={"analyze", "summarize", "other"},
    )
    assert ok

    ok, reason = WK.can_worker_process_ticket(
        {"kind": "agent", "needs": ["analyze", "special"]},
        agent_caps={"analyze"},
    )
    assert not ok
    assert "special" in reason


def test_worker_cycle_execution(tmp_path: Path):
    runs_root = tmp_path / "runs"
    wq = WQ.WorkQueue(runs_root)

    # 1. Ineligible ticket (human)
    wq.enqueue({
        "run_id": "run-001",
        "ticket": "t1",
        "step": "step1",
        "sop": "SOP-1",
        "kind": "human",
    })

    # 2. Ineligible ticket (interactive bash)
    wq.enqueue({
        "run_id": "run-001",
        "ticket": "t2",
        "step": "step2",
        "sop": "SOP-1",
        "kind": "agent",
        "needs": ["bash"],
    })

    # 3. Eligible automated ticket
    wq.enqueue({
        "run_id": "run-001",
        "ticket": "t3",
        "step": "step3",
        "sop": "SOP-1",
        "kind": "agent",
        "needs": ["compute"],
    })

    handled_tickets = []

    def dummy_handler(td: dict):
        handled_tickets.append(td["ticket"])
        return {"outputs": {"result": 42}}

    stats = WK.run_worker_cycle(
        runs_root=runs_root,
        agent_name="test-worker",
        agent_caps={"compute"},
        handler=dummy_handler,
    )

    assert stats.tickets_polled == 3
    assert stats.tickets_skipped == 2
    assert stats.tickets_claimed == 1
    assert stats.tickets_completed == 1
    assert handled_tickets == ["t3"]


def test_metrics_ingestion_parsing(tmp_path: Path):
    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True)

    (run_dir / "run.json").write_text(
        json.dumps({
            "run_id": "run-001",
            "sop": "SOP-TEST",
            "status": "done",
            "started_at": "2026-11-07T10:00:00Z",
            "ended_at": "2026-11-07T10:01:30Z",
        }),
        encoding="utf-8",
    )

    (run_dir / "state.jsonl").write_text(
        json.dumps({"ev": "step_start", "step": "step1"}) + "\n" +
        json.dumps({"ev": "step_done", "step": "step1"}) + "\n" +
        json.dumps({"ev": "step_start", "step": "step2"}) + "\n" +
        json.dumps({"ev": "step_fail", "step": "step2"}) + "\n",
        encoding="utf-8",
    )

    rec = IG.parse_run_record(run_dir)
    assert rec is not None
    assert rec.run_id == "run-001"
    assert rec.sop_id == "SOP-TEST"
    assert rec.status == "done"
    assert rec.duration_sec == 90.0
    assert rec.total_steps == 2
    assert rec.failed_steps == 1
    assert len(rec.fingerprint) == 16


def test_metrics_store_idempotence(tmp_path: Path):
    db_file = tmp_path / "metrics.db"
    store = IG.MetricsStore(db_file)

    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": "run-001", "sop": "SOP-A", "status": "done"}),
        encoding="utf-8",
    )

    # First ingest
    count1 = store.scan_and_ingest(tmp_path / "runs")
    assert count1 == 1

    # Second ingest without changes (idempotent, skips)
    count2 = store.scan_and_ingest(tmp_path / "runs")
    assert count2 == 0

    rows = store.query_metrics(sop_id="SOP-A")
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-001"

    store.close()


def test_mcp_server_protocol_and_manifest():
    # Initialize
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    init_resp = MCPS.process_mcp_message(init_req)
    assert init_resp["result"]["serverInfo"]["name"] == "procflow-mcp"

    # List tools
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    list_resp = MCPS.process_mcp_message(list_req)
    tools = list_resp["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    assert "procflow_check" in tool_names
    assert "procflow_compile" in tool_names
    assert "procflow_status" in tool_names
    assert "procflow_stats" in tool_names
    assert "procflow_smells" in tool_names
    assert "procflow_queue_list" in tool_names
    assert "procflow_eval_resume" in tool_names

    # Fail-closed: call unknown tool
    bad_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "non_existent_tool", "arguments": {}},
    }
    bad_resp = MCPS.process_mcp_message(bad_req)
    assert bad_resp["result"]["isError"]
    assert "Unknown tool" in bad_resp["result"]["error"]


def test_mcp_server_tool_execution(tmp_path: Path):
    runs_root = tmp_path / "runs"
    wq = WQ.WorkQueue(runs_root)
    wq.enqueue({
        "run_id": "run-99",
        "ticket": "t99",
        "step": "step1",
        "sop": "SOP-99",
    })

    # Call procflow_queue_list
    call_req = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "procflow_queue_list",
            "arguments": {"runs_root": str(runs_root)},
        },
    }
    call_resp = MCPS.process_mcp_message(call_req)
    assert "result" in call_resp
    assert "content" in call_resp["result"]
    content = call_resp["result"]["content"][0]["text"]
    data = json.loads(content)
    assert "pending_tickets" in data
    assert len(data["pending_tickets"]) == 1
    assert data["pending_tickets"][0]["ticket"] == "t99"


def test_cli_runtime_commands(tmp_path: Path, monkeypatch, capsys):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    # CLI: procflow worker
    with pytest.raises(SystemExit) as exc:
        cli.main(["worker", "--agent", "test-agent", "--runs-root", str(runs_root), "--json"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    stats = json.loads(out)
    assert stats["tickets_polled"] == 0

    # CLI: procflow ingest
    db_path = tmp_path / "cli_metrics.db"
    with pytest.raises(SystemExit) as exc:
        cli.main(["ingest", "--runs-root", str(runs_root), "--db", str(db_path), "--json"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    ingest_res = json.loads(out)
    assert ingest_res["ingested"] == 0

    # CLI: procflow ingest --query
    with pytest.raises(SystemExit) as exc:
        cli.main(["ingest", "--runs-root", str(runs_root), "--db", str(db_path), "--query", "--json"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    query_res = json.loads(out)
    assert isinstance(query_res, list)
