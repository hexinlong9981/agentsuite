"""The execution engine (code / human / choice / loop / parallel / finally / rounds)."""

import datetime as dt
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from procflow import Repo, ScriptedApproval, create_run, open_engine
from procflow import dryrun, history, lock, programs, records
from procflow.approval import DecisionRefused, get_approval
from procflow.engine import Refused, Usage
from procflow.errors import ConfigError
from procflow.tracer import ALLOWED_KEYS, FakeTracer, JsonlTracer, get_tracer

HERE = Path(__file__).parent
RUN_FIX = HERE / "fixtures" / "run"
GOOD = HERE / "fixtures" / "good"


@pytest.fixture
def sop_root(tmp_path):
    root = tmp_path / "sop"
    shutil.copytree(RUN_FIX, root)
    return root


@pytest.fixture
def runs(tmp_path):
    return tmp_path / "runs"


@pytest.fixture
def good_root(tmp_path):
    root = tmp_path / "good"
    shutil.copytree(GOOD, root)
    return root


def start(root, runs, sop, approval=None, tracer=None, **kw):
    return create_run(Repo(root), sop, runs_root=runs, approval=approval, tracer=tracer, owner="tester", **kw)


def events(eng):
    return eng.rd.read_state()


def kinds(eng):
    return [e["ev"] for e in events(eng)]


# ---- code-only run -------------------------------------------------------------------------------
def test_code_only_runs_to_end_with_evidence_and_outputs(sop_root, runs):
    tr = FakeTracer()
    eng, d = start(sop_root, runs, "CODE-ONLY", tracer=tr)
    assert d["status"] == "done" and d["outcome"] == "success"
    evs = events(eng)
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))           # contiguous from 1
    assert evs[0]["ev"] == "start" and evs[-1]["ev"] == "run_end"
    ed = eng.rd.path / "1" / "first"
    assert sorted(p.name for p in ed.iterdir() if p.name[:2].isdigit()) == ["01_command.txt", "02_stdout.txt", "03_stderr.txt"]
    assert "hello from emit" in (ed / "02_stdout.txt").read_text()
    ends = [e for e in evs if e["ev"] == "step_end"]
    assert ends[0]["outputs"] == {"result": "demo"}                           # PROCFLOW_OUTPUTS honored (declared names only)
    assert ends[0]["dropped_outputs"] == ["summary", "undeclared"]
    assert json.loads((eng.rd.path / "outputs.json").read_text()) == {"summary": "done"}
    # the caller gets counts and locations, never the data itself
    p = d["progress"][0]
    assert p["stdout_bytes"] > 0 and p["evidence_dir"] == "1/first" and "hello" not in json.dumps(d)
    # records
    assert (eng.rd.path / "ir.json").exists() and (eng.rd.path / "config.snapshot.json").exists()
    assert json.loads((eng.rd.path / "inputs.json").read_text()) == {"label": "demo"}
    assert [r["status"] for r in records.read_index(runs) if r["run_id"] == eng.rd.run_id] == ["done"]
    assert (eng.rd.path / "audit.jsonl").exists()
    # tracer: ids and hashes only
    names = [e["event"] for e in tr.events]
    assert names[0] == "run_start" and "step_start" in names and "checkpoint" in names and names[-1] == "run_end"
    assert all(set(e) - {"event"} <= ALLOWED_KEYS for e in tr.events)
    assert any(e.get("state_sha256") for e in tr.events if e["event"] == "checkpoint")


def test_run_executes_off_its_start_snapshot(sop_root, runs):
    eng, _ = start(sop_root, runs, "GATE", ScriptedApproval({"approve": "approved"}))
    # edit the definition afterwards: the run still follows its snapshot
    (sop_root / "gate.yaml").write_text((sop_root / "gate.yaml").read_text().replace("Apply (nothing", "Changed (nothing"))
    eng2 = open_engine(runs, eng.rd.run_id, approval=ScriptedApproval({"approve": "approved"}), owner="tester")
    d = eng2.cmd_submit(step="approve", decision="approved")
    assert d["status"] == "done"


def test_secrets_in_program_output_are_masked(sop_root, runs):
    impl = sop_root / "_impl" / "LEAKY"
    impl.mkdir()
    (impl / "leak.py").write_text('key = "API_" + "TOKEN"\nval = "s3" + "cr3t" * 3\nprint(f"{key}={val}")\n')
    (sop_root / "leaky.yaml").write_text(
        "sop: {id: LEAKY, name: Leaky, version: '1.0'}\nflow: {start: start}\nsteps:\n"
        "  - {id: start, type: start}\n  - {id: go, type: task, mode: auto, action: x, impl: python3 leak.py}\n"
        "  - {id: done, type: end, outcome: success}\ntransitions:\n  - {id: T1, from: start, to: go}\n  - {id: T2, from: go, to: done}\n")
    eng, d = start(sop_root, runs, "LEAKY")
    out = (eng.rd.path / "1" / "go" / "02_stdout.txt").read_text()
    assert "s3cr3t" not in out and "MASKED" in out


# ---- NIGHTLY-JOB: loop + finally ---------------------------------------------------------------
def params_file(tmp_path, **params):
    p = tmp_path / "params.yaml"
    p.write_text(json.dumps({"params": params}))
    return str(p)


def test_nightly_job_loop_and_finally(good_root, runs, tmp_path):
    eng, d = start(good_root, runs, "NIGHTLY-JOB", config_file=params_file(tmp_path, targets=["a", "b", "c"]))
    assert d["status"] == "done"
    k = kinds(eng)
    assert k.count("loop_iter") == 3 and k.count("iter_end") == 3 and "loop_end" in k
    assert eng.st.outputs["each-target"] == {"count": 3, "results": ["ok"] * 3, "all_passed": True, "limit_reached": False}
    assert k.index("final") > k.index("loop_end") and k.index("final") < k.index("run_end")   # finally runs last


def test_loop_beyond_limit_suspends_without_handler(good_root, runs, tmp_path):
    eng, d = start(good_root, runs, "NIGHTLY-JOB", config_file=params_file(tmp_path, targets=list(range(12))))
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "limit"
    assert eng.exit_code(d) == 4
    assert eng.st.outputs["each-target"]["limit_reached"] is True


def test_loop_variable_reaches_program_placeholders(sop_root, runs):
    impl = sop_root / "_impl" / "EACH"
    impl.mkdir()
    (impl / "echo.py").write_text("import sys\nprint('item=' + sys.argv[1])\n")
    (sop_root / "each.yaml").write_text(
        "sop: {id: EACH, name: Each, version: '1.0'}\ninterface:\n  inputs:\n    names: {type: 'list[string]', required: true}\n"
        "flow: {start: start, inputs: [{id: names, required: true}]}\nsteps:\n  - {id: start, type: start}\n"
        "  - {id: lp, type: loop, foreach: names, as: nm, max_iterations: 5, body: [one]}\n"
        "  - {id: one, type: task, mode: auto, action: x, impl: python3 echo.py <nm>}\n  - {id: done, type: end, outcome: success}\n"
        "transitions:\n  - {id: T1, from: start, to: lp}\n  - {id: T2, from: lp, to: done}\n")
    eng, d = start(sop_root, runs, "EACH", params=["names=[x, y]"])
    assert d["status"] == "done"
    outs = sorted((eng.rd.path / "1" / "one").glob("*_stdout.txt"))
    assert [p.read_text().strip() for p in outs] == ["item=x", "item=y"]


def test_failed_loop_body_is_an_iteration_failure_not_a_stop(sop_root, runs):
    (sop_root / "_impl" / "LOOPFAIL").mkdir()
    (sop_root / "_impl" / "LOOPFAIL" / "boom.py").write_text("import sys\nsys.exit(1)\n")
    (sop_root / "loopfail.yaml").write_text(
        "sop: {id: LOOPFAIL, name: L, version: '1.0'}\ninterface:\n  inputs:\n    xs: {type: 'list[string]', required: true}\n"
        "flow: {start: start, inputs: [{id: xs, required: true}]}\nsteps:\n  - {id: start, type: start}\n"
        "  - {id: lp, type: loop, foreach: xs, as: x, max_iterations: 3, body: [b]}\n"
        "  - {id: b, type: task, mode: auto, action: x, impl: python3 boom.py}\n  - {id: done, type: end, outcome: success}\n"
        "transitions:\n  - {id: T1, from: start, to: lp}\n  - {id: T2, from: lp, to: done}\n")
    eng, d = start(sop_root, runs, "LOOPFAIL", params=["xs=[p, q]"])
    assert d["status"] == "done"
    assert eng.st.outputs["lp"]["results"] == ["failed", "failed"] and eng.st.outputs["lp"]["all_passed"] is False


# ---- branching ----------------------------------------------------------------------------------
@pytest.mark.parametrize("mode,step", [("left", "left-step"), ("right", "right-step")])
def test_decide_by_routes_on_a_step_output(sop_root, runs, mode, step):
    eng, d = start(sop_root, runs, "BRANCH", params=[f"mode={mode}"])
    assert d["status"] == "done"
    taken = [e["to"] for e in events(eng) if e["ev"] == "transition"]
    assert step in taken and "ask" not in taken


def test_choice_ticket_and_label(sop_root, runs):
    eng, d = start(sop_root, runs, "BRANCH", params=["mode=other"])
    assert d["status"] == "waiting" and d["ticket"]["type"] == "choice"
    assert {c["label"] for c in d["ticket"]["candidates"]} == {"fast", "slow"}
    with pytest.raises(Usage):
        eng.cmd_submit(step="ask")                                   # a label is required
    with pytest.raises(Refused):
        eng.cmd_submit(step="ask", label="nonsense")                  # not a candidate
    assert eng.st.pending()                                           # state unchanged by the refusals
    d = eng.cmd_submit(step="ask", label="slow", by="tester")
    assert d["status"] == "done"
    assert "slow" in [e["to"] for e in events(eng) if e["ev"] == "transition"]


def test_exclusive_guard_matching_twice_suspends(sop_root, runs):
    (sop_root / "twice.yaml").write_text(
        "sop: {id: TWICE, name: T, version: '1.0'}\ninterface:\n  inputs:\n    n: {type: int, required: true}\n"
        "flow: {start: start, inputs: [{id: n, required: true}]}\nsteps:\n  - {id: start, type: start}\n"
        "  - {id: x, type: exclusive_choice}\n  - {id: a, type: end, outcome: success}\n  - {id: b, type: end, outcome: success}\n"
        "transitions:\n  - {id: T1, from: start, to: x}\n  - {id: T2, from: x, to: a, guard: 'n > 0'}\n  - {id: T3, from: x, to: b, guard: 'n > 1'}\n")
    eng, d = start(sop_root, runs, "TWICE", params=["n=5"])
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "error" and "more than one guard" in d["suspend"]["note"]


# ---- parallel / join ------------------------------------------------------------------------------
@pytest.mark.parametrize("limit", [None, "limits.parallel=1"])
def test_parallel_join(sop_root, runs, limit):
    eng, d = start(sop_root, runs, "FANOUT", ScriptedApproval({"branch-b": "approved"}), sets=[limit] if limit else None)
    assert d["status"] == "waiting" and d["ticket"]["step"] == "branch-b" and d["ticket"]["type"] == "human"
    assert "join_fire" not in kinds(eng)                               # the join waits for the slow branch
    d = eng.cmd_submit(step="branch-b", decision="approved", by="tester")
    assert d["status"] == "done"
    k = kinds(eng)
    assert k.count("join_arrive") == 3 and k.count("join_fire") == 1
    assert [e["to"] for e in events(eng) if e["ev"] == "transition"].count("after") == 1


# ---- human steps -----------------------------------------------------------------------------------
def test_human_step_waits_then_an_injected_approval_advances(sop_root, runs):
    appr = ScriptedApproval({"approve": "approved"})
    eng, d = start(sop_root, runs, "GATE", appr)
    assert d["status"] == "waiting" and d["ticket"]["type"] == "human" and d["pending"] == 1
    d = open_engine(runs, eng.rd.run_id, approval=appr, owner="tester").cmd_next()
    assert d["status"] == "waiting"                                    # next does not decide anything
    d = open_engine(runs, eng.rd.run_id, approval=appr, owner="tester").cmd_submit(step="approve", decision="approved", by="alice")
    assert d["status"] == "done" and d["outcome"] == "success"
    assert any(e["ev"] == "decision" and e["by"] == "alice" for e in events(eng))
    audit = [json.loads(x) for x in (eng.rd.path / "audit.jsonl").read_text().splitlines()]
    assert any(a.get("action") == "human_decision" and a["result"] == "allow" for a in audit)


def test_a_denied_decision_routes_to_the_default(sop_root, runs):
    appr = ScriptedApproval({"approve": "rejected"})
    eng, _ = start(sop_root, runs, "GATE", appr)
    d = eng.cmd_submit(step="approve", decision="rejected")
    assert d["status"] == "done" and d["outcome"] == "rejected"
    assert "apply" not in [e["to"] for e in events(eng) if e["ev"] == "transition"]


def test_decisions_are_never_self_approved(sop_root, runs):
    eng, _ = start(sop_root, runs, "GATE")                              # default source: DenyAll
    with pytest.raises(DecisionRefused):
        eng.cmd_submit(step="approve", decision="approved", by="agent")
    assert eng.st.pending() and eng.display_status() == "waiting"
    assert not any(e["ev"] == "decision" for e in events(eng))
    # a scripted source for a different decision does not confirm the claim either
    eng2 = open_engine(runs, eng.rd.run_id, approval=ScriptedApproval({"approve": "rejected"}), owner="tester")
    with pytest.raises(DecisionRefused):
        eng2.cmd_submit(step="approve", decision="approved")
    audit = [json.loads(x) for x in (eng.rd.path / "audit.jsonl").read_text().splitlines()]
    assert sum(1 for a in audit if a.get("result") == "deny" and a.get("action") == "human_decision") == 2


def test_human_wait_timeout_suspends(sop_root, runs):
    eng, _ = start(sop_root, runs, "GATE")
    later = lambda: records.now() + dt.timedelta(hours=9)  # noqa: E731
    d = open_engine(runs, eng.rd.run_id, owner="tester", clock=later).cmd_next()
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "timeout"


def test_human_step_without_a_matching_route_suspends(good_root, runs):
    # APPROVED-CHANGE is abstract: run the concrete child; deny leaves no route (guard only) -> suspend(error)
    eng, d = start(good_root, runs, "BACKUP-AND-VERIFY", ScriptedApproval({"approve": "denied"}), params=["change_ref=r1"])
    assert d["ticket"]["type"] == "human"
    d = eng.cmd_submit(step="approve", decision="denied")
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "error"


# ---- rounds ---------------------------------------------------------------------------------------------
def test_rounds_have_a_ceiling(sop_root, runs):
    eng, d = start(sop_root, runs, "ROUNDS")
    assert d["ticket"]["type"] == "choice" and eng.st.round == 1
    d = eng.cmd_submit(step="review", label="again")
    assert eng.st.round == 2 and d["ticket"]["step"] == "review"
    with pytest.raises(Refused, match="ceiling"):
        eng.cmd_submit(step="review", label="again")                  # max_rounds is 2
    assert eng.st.round == 2
    d = eng.cmd_submit(step="review", label="ok")
    assert d["status"] == "done"


# ---- llm / agent work tickets and failures ---------------------------------------------------------------
def test_agent_step_emits_a_work_ticket_satisfied_by_submit(sop_root, runs):
    eng, d = start(sop_root, runs, "AGENT-WORK", params=["topic=tides"])
    t = d["ticket"]
    assert d["status"] == "waiting" and t["type"] == "work" and t["outputs"] == ["note"]
    ctx = json.loads((eng.rd.path / "1" / "write" / t["context_file"]).read_text())
    assert ctx["inputs"] == {"topic": "tides"}
    with pytest.raises(Refused, match="outputs missing"):
        eng.cmd_submit(step="write")
    d = eng.cmd_submit(step="write", outputs={"note": "short text"})
    assert d["status"] == "done" and eng.st.ctx["note"] == "short text"


def test_work_ticket_error_without_a_handler_suspends(sop_root, runs):
    eng, _ = start(sop_root, runs, "AGENT-WORK", params=["topic=x"])
    d = eng.cmd_submit(step="write", result="error")
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "error"


def test_failing_code_step_is_retried_then_suspends_with_evidence(sop_root, runs):
    eng, d = start(sop_root, runs, "FAILS")
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "error"
    assert [e["attempt"] for e in events(eng) if e["ev"] == "enter" and e["step"] == "boom"] == [1, 2]   # retry.code default 1
    assert "about to fail" in (eng.rd.path / "1" / "boom" / "03_stderr.txt").read_text()
    row = history.steps_json(eng.rd)[-1]
    assert row["result"] == "error" and row["evidence_files"] >= 3


def test_unlaunchable_code_lines_become_a_command_ticket(sop_root, runs):
    (sop_root / "cmd.yaml").write_text(
        "sop: {id: CMD, name: C, version: '1.0'}\nflow: {start: start}\nsteps:\n  - {id: start, type: start}\n"
        "  - {id: go, type: task, mode: auto, action: x, impl: 'python3 -c print(1)'}\n  - {id: done, type: end, outcome: success}\n"
        "transitions:\n  - {id: T1, from: start, to: go}\n  - {id: T2, from: go, to: done}\n")
    eng, d = start(sop_root, runs, "CMD")
    assert d["ticket"]["type"] == "command" and "-c" in d["ticket"]["lines"][0]["why"]
    assert not any(p.name.endswith("_command.txt") for p in (eng.rd.path / "1" / "go").iterdir())   # never launched
    assert eng.cmd_submit(step="go")["status"] == "done"


# ---- call nodes ---------------------------------------------------------------------------------------------
def test_call_node_runs_a_child_run_and_maps_its_returns(sop_root, runs):
    eng, d = start(sop_root, runs, "CALLS")
    assert d["status"] == "done" and eng.st.ctx["got"] == "done"
    child = records.find_run(runs, f"{eng.rd.run_id}~delegate-1")
    assert child.read_run()["parent"]["run_id"] == eng.rd.run_id and child.read_run()["status"] == "done"


# ---- suspend / resume / cancel -------------------------------------------------------------------------------
def test_suspend_resume_round_trip_on_the_same_version(sop_root, runs):
    appr = ScriptedApproval({"approve": "approved"})
    eng, d = start(sop_root, runs, "GATE", appr)
    d = eng.cmd_suspend("going home")
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "manual" and eng.exit_code(d) == 4
    assert not eng.st.pending()                                        # the open ticket was discarded
    with pytest.raises(Refused):
        eng.cmd_submit(step="approve", decision="approved")           # suspended runs take no submissions
    eng2 = open_engine(runs, eng.rd.run_id, repo=Repo(sop_root), approval=appr, owner="tester")
    d = eng2.cmd_resume()
    assert d["status"] == "waiting" and d["ticket"]["ticket"] == "approve.1.2"      # a fresh attempt
    d = eng2.cmd_submit(step="approve", decision="approved")
    assert d["status"] == "done"
    assert [h["status"] for h in eng2.run["history"]] == ["running", "waiting", "suspended", "waiting", "done"]


def test_resume_refuses_a_changed_implementation_or_definition(sop_root, runs):
    eng, _ = start(sop_root, runs, "GATE")
    eng.cmd_suspend()
    (sop_root / "_impl" / "GATE").mkdir()
    (sop_root / "_impl" / "GATE" / "x.py").write_text("print(1)\n")
    with pytest.raises(Refused, match="implementation"):
        open_engine(runs, eng.rd.run_id, repo=Repo(sop_root), owner="tester").cmd_resume()
    shutil.rmtree(sop_root / "_impl" / "GATE")
    (sop_root / "gate.yaml").write_text((sop_root / "gate.yaml").read_text().replace("Approve the plan", "Approve it"))
    with pytest.raises(Refused, match="definition"):
        open_engine(runs, eng.rd.run_id, repo=Repo(sop_root), owner="tester").cmd_resume()


def test_cancel_closes_a_suspended_run_only(sop_root, runs):
    eng, _ = start(sop_root, runs, "GATE")
    with pytest.raises(Refused):
        eng.cmd_cancel()                                               # not suspended
    eng.cmd_suspend()
    d = eng.cmd_cancel("not needed")
    assert d["status"] == "cancelled"
    assert kinds(eng)[-2:] == ["cancel", "run_end"]
    with pytest.raises(Refused):
        eng.cmd_resume()
    assert [r["status"] for r in records.read_index(runs) if r["run_id"] == eng.rd.run_id][-1] == "cancelled"


# ---- locking -----------------------------------------------------------------------------------------------------
def test_second_owner_is_refused_while_the_lease_holds(sop_root, runs):
    eng, _ = start(sop_root, runs, "GATE")
    lock.acquire(eng.rd.file(".lock"), owner="other-session")
    other = open_engine(runs, eng.rd.run_id, owner="tester")
    with pytest.raises(lock.LockBusy):
        other.cmd_next()
    with pytest.raises(lock.LockBusy):
        other.cmd_submit(step="approve", decision="approved")
    assert history.show(eng.rd)["lock"]["owner"] == "other-session"    # read-only commands take no lock and still work
    lock.release(eng.rd.file(".lock"), owner="other-session")
    assert other.cmd_next()["status"] == "waiting"


def test_stale_lease_is_taken_over_explicitly(sop_root, runs):
    appr = ScriptedApproval({"approve": "approved"})
    eng, _ = start(sop_root, runs, "GATE", appr)
    past = records.now() - dt.timedelta(hours=1)
    lock.acquire(eng.rd.file(".lock"), owner="gone-session", at=past)
    me = open_engine(runs, eng.rd.run_id, repo=Repo(sop_root), approval=appr, owner="tester")
    with pytest.raises(lock.LockExpired):
        me.cmd_next()                                                  # a stale lease is never silently taken
    assert me.st.status == "suspended" and me.st.suspend["reason"] == "lost-lock"
    d = me.cmd_resume(takeover=True)
    assert d["status"] == "waiting"
    assert any(e["ev"] == "takeover" and e["previous"] == "gone-session" for e in events(me))
    assert me.cmd_submit(step="approve", decision="approved")["status"] == "done"


def test_lock_lease_is_renewed(tmp_path):
    p = tmp_path / ".lock"
    a = records.now()
    lock.acquire(p, owner="a", at=a)
    h = lock.holder(p, at=a + dt.timedelta(minutes=14))
    assert h["owner"] == "a" and not h["expired"]
    lock.acquire(p, owner="a", at=a + dt.timedelta(minutes=14))        # renewed by the same owner
    assert not lock.holder(p, at=a + dt.timedelta(minutes=28))["expired"]
    assert lock.holder(p, at=a + dt.timedelta(minutes=40))["expired"]


# ---- start-time guards ----------------------------------------------------------------------------------------------
def test_start_checks(good_root, sop_root, runs):
    with pytest.raises(Refused, match="abstract"):
        start(good_root, runs, "APPROVED-CHANGE", params=["change_ref=r"])
    with pytest.raises(Refused, match="does not exist"):
        start(sop_root, runs, "NOPE")
    with pytest.raises(Usage):
        start(sop_root, runs, "BRANCH")                                # required input missing
    with pytest.raises(ConfigError):
        start(sop_root, runs, "GATE", sets=["limits.max_rounds=999"])  # over the hard cap
    assert not runs.exists() or not list(runs.glob("*/*"))             # nothing was created by the refusals


def test_same_input_and_concurrency_guards(sop_root, runs):
    start(sop_root, runs, "AGENT-WORK", params=["topic=a"])
    with pytest.raises(Refused, match="same inputs"):
        start(sop_root, runs, "AGENT-WORK", params=["topic=a"])
    start(sop_root, runs, "AGENT-WORK", params=["topic=b"])            # different input: fine
    start(sop_root, runs, "AGENT-WORK", params=["topic=a"], allow_same_input=True)
    for i in range(3):                                                 # three runs in flight (status running)
        records.append_index(runs, {"run_id": f"busy-{i}", "sop": "X", "status": "running"})
    with pytest.raises(Refused, match="max_active_runs"):
        start(sop_root, runs, "GATE")


def test_quiet_window_blocks_starts(sop_root, runs):
    (sop_root / "_config").mkdir()
    (sop_root / "_config" / "runtime.yaml").write_text("defaults:\n  quiet_window: {range: '12:00-13:00'}\n")
    noon = lambda: dt.datetime(2030, 1, 1, 12, 30, tzinfo=dt.timezone.utc)  # noqa: E731
    with pytest.raises(Refused, match="quiet window"):
        start(sop_root, runs, "GATE", clock=noon)
    evening = lambda: dt.datetime(2030, 1, 1, 18, 0, tzinfo=dt.timezone.utc)  # noqa: E731
    assert start(sop_root, runs, "GATE", clock=evening)[1]["status"] == "waiting"


def test_run_id_and_paths_stay_contained(sop_root, runs):
    for bad in ("../x", "a/b", "..", ""):
        with pytest.raises((Usage, records.RecordError)):
            start(sop_root, runs, "GATE", run_id=bad) if bad else records.find_run(runs, bad)
    eng, _ = start(sop_root, runs, "GATE", run_id="fixed-id")
    assert eng.rd.path == (runs / "GATE" / "fixed-id").resolve()
    assert eng.rd.step_dir(1, "../../evil", create=False).is_relative_to(eng.rd.path.resolve())


# ---- record integrity ------------------------------------------------------------------------------------------------------
def test_state_gaps_and_corruption_stop_the_run(sop_root, runs):
    eng, _ = start(sop_root, runs, "GATE")
    p = eng.rd.state_path
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines[:1] + lines[2:]) + "\n")             # drop line 2 -> seq gap
    with pytest.raises(records.RecordError, match="gap"):
        open_engine(runs, eng.rd.run_id, owner="tester")
    p.write_text("\n".join(lines[:1] + ["{not json"]) + "\n")
    with pytest.raises(records.RecordError, match="corrupt"):
        open_engine(runs, eng.rd.run_id, owner="tester")


def test_index_can_be_rebuilt(sop_root, runs):
    start(sop_root, runs, "GATE")
    records.index_path(runs).unlink()
    assert records.reindex(runs) == 1
    assert history.runs(runs)[0]["status"] == "waiting"


# ---- programs ----------------------------------------------------------------------------------------------------------------
@pytest.fixture
def roots(tmp_path):
    impl = tmp_path / "impl"
    impl.mkdir()
    (impl / "ok.py").write_text("print(1)\n")
    sh = impl / "tool.sh"
    sh.write_text("#!/bin/sh\necho hi\n")
    sh.chmod(sh.stat().st_mode | stat.S_IXUSR)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "bad.py").write_text("print(2)\n")
    return impl, outside, [impl.resolve()]


def kind(line, roots, values=None):
    impl, _o, rts = roots
    return programs.classify(line, values or {}, rts, impl)


@pytest.mark.parametrize("line", ["python3 ok.py | cat", "python3 ok.py > out.txt", "python3 ok.py && echo x", "python3 ok.py; id",
                                  "python3 ok.py `id`", "python3 ok.py $(id)", "python3 ok.py ${HOME}", "python3 ok.py &"])
def test_programs_reject_shell_metacharacters(roots, line):
    c = kind(line, roots)
    assert c["kind"] == "command" and "shell" in c["why"]


def test_programs_reject_out_of_root_and_unsafe_forms(roots):
    impl, outside, _ = roots
    assert kind(f"python3 {outside / 'bad.py'}", roots)["kind"] == "command"
    assert kind("python3 ../outside/bad.py", roots)["kind"] == "command"
    assert kind("python3 -c print(1)", roots)["kind"] == "command"
    assert kind("python3 -m http.server", roots)["kind"] == "command"
    assert kind("/bin/sh -c id", roots)["kind"] == "command"
    assert kind("python3 missing.py", roots)["kind"] == "command"
    assert kind("python3 ok.py <unknown>", roots)["why"].startswith("unresolved placeholder")
    (impl / "escape.py").symlink_to(outside / "bad.py")                # a symlink may not lead out of the root
    assert kind("python3 escape.py", roots)["kind"] == "command"


def test_programs_accept_vetted_lines_and_substitute_placeholders(roots, tmp_path):
    impl, _o, _r = roots
    c = kind("python3 ok.py <name> <evidence> <n>", roots, {"name": "x", "evidence": str(tmp_path), "n": 3})
    assert c["kind"] == "local" and c["argv"][0] == "python3" and c["argv"][1] == str((impl / "ok.py").resolve())
    assert c["argv"][2:] == ["x", str(tmp_path), "3"]
    assert kind("tool.sh a", roots)["kind"] == "local"                 # an executable under the root
    assert kind("python3 ok.py <lst>", roots, {"lst": [1]})["kind"] == "command"   # only strings/numbers substitute


def test_programs_run_with_a_minimal_environment_and_timeout(roots, tmp_path, monkeypatch):
    impl, _o, rts = roots
    (impl / "env.py").write_text("import os\nprint(sorted(k for k in os.environ if k.startswith('PROCFLOW_')))\nprint('LEAK' in os.environ)\n")
    (impl / "slow.py").write_text("import time\ntime.sleep(5)\n")
    monkeypatch.setenv("LEAK", "1")
    c = kind("python3 env.py", roots)
    r = programs.run_local(c["argv"], tmp_path, 20, tmp_path / "i.json", tmp_path / "o.json")
    out = r["stdout"].decode()
    assert "PROCFLOW_INPUTS" in out and "PROCFLOW_OUTPUTS" in out and out.strip().endswith("False")
    r = programs.run_local(kind("python3 slow.py", roots)["argv"], tmp_path, 0.3, tmp_path / "i.json", tmp_path / "o.json")
    assert r["timeout"] is True


def test_executable_under_the_impl_root_runs_in_a_real_step(sop_root, runs):
    impl = sop_root / "_impl" / "EXE"
    impl.mkdir()
    sh = impl / "go.sh"
    sh.write_text("#!/bin/sh\necho ran-$1\n")
    sh.chmod(0o755)
    (sop_root / "exe.yaml").write_text(
        "sop: {id: EXE, name: E, version: '1.0'}\nflow: {start: start}\nsteps:\n  - {id: start, type: start}\n"
        "  - {id: go, type: task, mode: auto, action: x, impl: './go.sh <run_dir>'}\n  - {id: done, type: end, outcome: success}\n"
        "transitions:\n  - {id: T1, from: start, to: go}\n  - {id: T2, from: go, to: done}\n")
    eng, d = start(sop_root, runs, "EXE")
    assert d["status"] == "done"
    assert (eng.rd.path / "1" / "go" / "02_stdout.txt").read_text().startswith("ran-")


# ---- dry-run / history ------------------------------------------------------------------------------------------------------------
def test_dry_run_enumerates_paths(sop_root):
    from procflow import compile_sop
    ird = compile_sop("BRANCH", Repo(sop_root))[0].to_dict()
    res = dryrun.enumerate_paths(ird)
    assert res["count"] == 4 and not res["truncated"]
    ends = sorted(p["path"].split(" > ")[-1] for p in res["paths"])
    assert ends == ["done(end)"] * 4
    assert any("left-step" in p["path"] for p in res["paths"]) and any("slow" in p["path"] for p in res["paths"])
    fan = dryrun.enumerate_paths(compile_sop("FANOUT", Repo(sop_root))[0].to_dict())
    assert fan["count"] == 1 and "{branch-a(code) || branch-b(human) || branch-c(code)}" in fan["paths"][0]["path"]
    assert fan["paths"][0]["human_steps"] == ["branch-b"]
    rounds = dryrun.enumerate_paths(compile_sop("ROUNDS", Repo(sop_root))[0].to_dict())
    assert {p["terminal"] for p in rounds["paths"]} <= {"end", "cut"} and rounds["count"] >= 2
    call = dryrun.enumerate_paths(compile_sop("CALLS", Repo(sop_root))[0].to_dict())
    assert call["paths"][0]["terminal"] == "end" and "delegate(call)" in call["paths"][0]["path"] and call["call_nodes"] == ["delegate"]
    assert not list(sop_root.parent.glob("runs"))                      # nothing was created


def test_dry_run_on_the_nightly_job(good_root):
    from procflow import compile_sop
    res = dryrun.enumerate_paths(compile_sop("NIGHTLY-JOB", Repo(good_root))[0].to_dict())
    assert res["count"] == 1 and "each-target(loop each targets [run-one])" in res["paths"][0]["path"] and res["finally"] == ["cleanup"]


def test_history_views(sop_root, runs):
    eng, _ = start(sop_root, runs, "CODE-ONLY")
    s = history.show(eng.rd)
    assert s["run"]["status"] == "done" and s["steps"] == {"count": 2, "ok": 2, "error": 0} and s["lock"] is None or s["lock"]["released"] is False
    log = history.log(eng.rd, tail=3)
    assert len(log) == 3 and "run_end" in log[-1]
    rows = history.steps_json(eng.rd)
    assert [r["step"] for r in rows] == ["first", "second"] and rows[0]["evidence_files"] == 3 and rows[0]["output_names"] == ["result"]
    assert [r["run_id"] for r in history.runs(runs, status="done")] == [eng.rd.run_id]


# ---- tracer / approval adapters ---------------------------------------------------------------------------------------------------------
def test_tracer_never_carries_values(tmp_path):
    t = get_tracer(f"jsonl:{tmp_path / 't.jsonl'}")
    assert isinstance(t, JsonlTracer)
    t.event("step_end", run_id="r1", step="s", result="ok", outputs={"k": "v"}, note="free text", payload=[1])
    row = json.loads((tmp_path / "t.jsonl").read_text())
    assert row == {"event": "step_end", "run_id": "r1", "step": "s", "result": "ok"}
    with pytest.raises(ValueError):
        get_tracer("nope")


def test_approval_sources():
    assert get_approval(None).request("r/s", "decision:approved", "") is False         # DenyAll by default
    with pytest.raises(ValueError):
        get_approval("allow")                                                          # AllowAll is not offered to the CLI


# ---- CLI --------------------------------------------------------------------------------------------------------------------------------------
def cli(capsys, *argv):
    from procflow.cli import main
    with pytest.raises(SystemExit) as x:
        main(list(argv))
    cap = capsys.readouterr()
    return x.value.code, cap.out, cap.err


def test_cli_end_to_end_human_gate(sop_root, runs, tmp_path, capsys):
    base = ["--runs-root", str(runs), "--sop-root", str(sop_root)]
    code, out, _ = cli(capsys, "run", "GATE", *base, "--run-id", "cli-1")
    assert code == 0 and "PROCFLOW_NEXT=human" in out and "PROCFLOW_RUN=cli-1" in out
    code, out, err = cli(capsys, "submit", "cli-1", *base, "--step", "approve", "--decision", "approved")
    assert code == 1 and "never self-approved" in err                                   # DenyAll by default
    appr = tmp_path / "approvals.json"
    appr.write_text(json.dumps({"approve": "approved"}))
    code, out, _ = cli(capsys, "submit", "cli-1", *base, "--step", "approve", "--decision", "approved",
                       "--approval", f"file:{appr}", "--by", "alice")
    assert code == 0 and "PROCFLOW_NEXT=END" in out
    code, out, _ = cli(capsys, "status", "cli-1", *base)
    assert code == 0 and "PROCFLOW_STATUS=done" in out
    code, out, _ = cli(capsys, "runs", "--runs-root", str(runs), "--json")
    assert code == 0 and json.loads(out)[0]["status"] == "done"
    code, out, _ = cli(capsys, "log", "cli-1", "--runs-root", str(runs))
    assert code == 0 and "run_end" in out
    code, out, _ = cli(capsys, "steps-json", "cli-1", "--runs-root", str(runs))
    assert code == 0 and any(r["step"] == "approve" and r["decision"] == "approved" for r in json.loads(out))
    code, out, _ = cli(capsys, "show", "cli-1", "--runs-root", str(runs))
    assert code == 0 and json.loads(out)["run"]["status"] == "done"


def test_cli_exit_codes(sop_root, runs, capsys):
    base = ["--runs-root", str(runs), "--sop-root", str(sop_root)]
    code, out, _ = cli(capsys, "run", "FAILS", *base, "--run-id", "c1")
    assert code == 4 and "PROCFLOW_NEXT=SUSPENDED" in out                               # suspended
    code, _, err = cli(capsys, "run", "BRANCH", *base)
    assert code == 2 and "required input" in err                                        # parameter error
    code, _, _ = cli(capsys, "run", "NOPE", *base)
    assert code == 1                                                                    # check failure
    code, _, err = cli(capsys, "next", "c1", "--sop-root", str(sop_root))
    assert code == 2 and "--runs-root" in err
    code, _, _ = cli(capsys, "cancel", "c1", *base)
    assert code == 0
    code, out, _ = cli(capsys, "dry-run", "BRANCH", "--sop-root", str(sop_root))
    assert code == 0 and "4 path(s)" in out
    code, _, _ = cli(capsys, "resume", "c1", *base)
    assert code == 1                                                                    # cancelled runs cannot resume


def test_cli_suspend_resume(sop_root, runs, capsys):
    base = ["--runs-root", str(runs), "--sop-root", str(sop_root)]
    cli(capsys, "run", "GATE", *base, "--run-id", "g1")
    code, out, _ = cli(capsys, "suspend", "g1", *base, "--note", "pause")
    assert code == 4 and "suspended: manual: pause" in out
    code, out, _ = cli(capsys, "resume", "g1", *base)
    assert code == 0 and "PROCFLOW_NEXT=human" in out
