"""MCP (Model Context Protocol) server for procflow procedures and toolkit operations.

Exposes procflow operations (check, compile, status, stats, smells, queue, eval-resume)
via standard MCP stdio protocol with a fail-closed tool manifest and strict credential isolation.

Guarantees:
- Zero credential-returning tools: credentials remain in isolated stores and are never emitted.
- All tool outputs are automatically sanitized through agent_policy_kit.SecretMasker.
- Fail-closed: only explicitly registered tools are callable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from agent_policy_kit import SecretMasker

from . import check as CHK
from . import compiler as CMP
from . import metrics as M
from . import records as R
from . import resume_eval as RE
from . import smells as SM
from . import workqueue as WQ
from .repo import Repo

_MASKER = SecretMasker()

TOOL_MANIFEST = [
    {
        "name": "procflow_check",
        "description": "Statically validate procedure definitions against OOP rules, reachability, and contracts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sop_id": {"type": "string", "description": "Procedure identifier (e.g. SOP-01)"},
                "sop_root": {"type": "string", "description": "Root directory containing procedures"},
            },
            "required": ["sop_id"],
        },
    },
    {
        "name": "procflow_compile",
        "description": "Compile a procedure definition into intermediate representation (IR).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sop_id": {"type": "string", "description": "Procedure identifier"},
                "sop_root": {"type": "string", "description": "Root directory containing procedures"},
            },
            "required": ["sop_id"],
        },
    },
    {
        "name": "procflow_status",
        "description": "Get current status, active steps, and progress of a procedure run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Run identifier"},
                "runs_root": {"type": "string", "description": "Runs directory"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "procflow_stats",
        "description": "Aggregate execution statistics, durations, and failure rates across procedure runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "runs_root": {"type": "string", "description": "Runs directory"},
                "sop_id": {"type": "string", "description": "Optional procedure filter"},
            },
            "required": ["runs_root"],
        },
    },
    {
        "name": "procflow_smells",
        "description": "Detect static design smells and dynamic execution bottlenecks in a procedure.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sop_id": {"type": "string", "description": "Procedure identifier"},
                "sop_root": {"type": "string", "description": "Root directory"},
                "runs_root": {"type": "string", "description": "Runs directory"},
            },
            "required": ["sop_id"],
        },
    },
    {
        "name": "procflow_queue_list",
        "description": "List pending and claimable tickets from the work queue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "runs_root": {"type": "string", "description": "Runs directory"},
            },
            "required": ["runs_root"],
        },
    },
    {
        "name": "procflow_eval_resume",
        "description": "Evaluate deterministic resumption predicates on suspended runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "runs_root": {"type": "string", "description": "Runs directory"},
                "run_id": {"type": "string", "description": "Optional run identifier filter"},
            },
            "required": ["runs_root"],
        },
    },
]


def handle_procflow_check(arguments: dict[str, Any]) -> dict[str, Any]:
    sop_id = arguments["sop_id"]
    sop_root = arguments.get("sop_root", ".")
    repo = Repo(sop_root)
    findings = CHK.run_check(repo, [sop_id])
    return {
        "valid": len(findings) == 0,
        "findings": [f.to_dict() for f in findings],
    }


def handle_procflow_compile(arguments: dict[str, Any]) -> dict[str, Any]:
    sop_id = arguments["sop_id"]
    sop_root = arguments.get("sop_root", ".")
    repo = Repo(sop_root)
    ir, errs = CMP.compile_sop(sop_id, repo)
    if errs:
        return {"compiled": False, "errors": errs}
    return {"compiled": True, "ir": ir.to_dict() if ir else None}


def handle_procflow_status(arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments["run_id"]
    runs_root = Path(arguments.get("runs_root", "runs")).resolve()
    try:
        run_desc = R.find_run(runs_root, run_id)
        rf = run_desc.file("run.json")
        if rf.is_file():
            data = json.loads(rf.read_text(encoding="utf-8"))
            return {"found": True, "status": data.get("status"), "details": data}
        return {"found": False, "error": f"run.json missing in {run_desc.path}"}
    except Exception as e:
        return {"found": False, "error": str(e)}


def handle_procflow_stats(arguments: dict[str, Any]) -> dict[str, Any]:
    runs_root = arguments["runs_root"]
    sop_id = arguments.get("sop_id")
    agg = M.aggregate_metrics(runs_root, sop_id=sop_id)
    return {"stats": agg.to_dict()}


def handle_procflow_smells(arguments: dict[str, Any]) -> dict[str, Any]:
    sop_id = arguments["sop_id"]
    sop_root = arguments.get("sop_root", ".")
    runs_root = arguments.get("runs_root")

    repo = Repo(sop_root)
    ir, errs = CMP.compile_sop(sop_id, repo)
    if not ir:
        return {"error": f"Could not compile SOP {sop_id}: {errs}"}

    agg = M.aggregate_metrics(runs_root, sop_id=sop_id) if runs_root else None
    smells = SM.detect_all_smells(ir.to_dict(), agg=agg)
    return {"smells": [s.to_dict() for s in smells]}


def handle_procflow_queue_list(arguments: dict[str, Any]) -> dict[str, Any]:
    runs_root = arguments["runs_root"]
    wq = WQ.WorkQueue(runs_root)
    tickets = wq.list(state="pending")
    return {"pending_tickets": tickets}


def handle_procflow_eval_resume(arguments: dict[str, Any]) -> dict[str, Any]:
    runs_root = arguments["runs_root"]
    run_id = arguments.get("run_id")
    if run_id:
        res = RE.evaluate_run_resumption(Path(runs_root) / run_id)
        results = [res] if res else []
    else:
        results = RE.scan_and_evaluate_all(runs_root)
    return {"evaluations": [r.to_dict() for r in results]}


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "procflow_check": handle_procflow_check,
    "procflow_compile": handle_procflow_compile,
    "procflow_status": handle_procflow_status,
    "procflow_stats": handle_procflow_stats,
    "procflow_smells": handle_procflow_smells,
    "procflow_queue_list": handle_procflow_queue_list,
    "procflow_eval_resume": handle_procflow_eval_resume,
}


def dispatch_tool_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call with fail-closed handling and automated secret masking."""
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return {
            "error": f"Unknown tool: {tool_name}. Allowed: {sorted(TOOL_HANDLERS)}",
            "isError": True,
        }

    try:
        raw_result = handler(arguments)
        # Apply strict secret masking
        sanitized = json.loads(_MASKER.mask_text(json.dumps(raw_result)))
        return {"content": [{"type": "text", "text": json.dumps(sanitized, indent=2)}]}
    except Exception as e:
        return {
            "error": f"Tool execution failed: {_MASKER.mask_text(str(e))}",
            "isError": True,
        }


def process_mcp_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process a single JSON-RPC 2.0 MCP message."""
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "procflow-mcp", "version": "0.1.0"},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOL_MANIFEST},
        }

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        res = dispatch_tool_call(name, args)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": res,
        }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def run_stdio_server() -> None:
    """Run MCP server loop over stdin/stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = process_mcp_message(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()
