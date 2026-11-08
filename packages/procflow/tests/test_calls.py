"""call child-runs, interface dispatch, typed exceptions, suspend propagation, narrowing, migrate, call tree."""

import datetime as dt
import json
import shutil
from pathlib import Path

import pytest
import yaml

from procflow import Repo, ScriptedApproval, create_run, open_engine
from procflow import config as cfg
from procflow import contract, history, records
from procflow.cli import main as cli_main
from procflow.engine import Refused, Usage
from procflow.tracer import FakeTracer

HERE = Path(__file__).parent
CALL_FIX = HERE / "fixtures" / "call"
GOOD = HERE / "fixtures" / "good"


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "sop"
    shutil.copytree(CALL_FIX, r)
    return r


@pytest.fixture
def runs(tmp_path):
    return tmp_path / "runs"


def start(root, runs, sop, approval=None, **kw):
    return create_run(Repo(root), sop, runs_root=runs, approval=approval, owner="tester", **kw)


def events(rd_or_eng):
    rd = getattr(rd_or_eng, "rd", rd_or_eng)
    return rd.read_state()


def child_of(eng, runs, step="gate", n=1):
    return records.find_run(runs, f"{eng.rd.run_id}~{step}-{n}")


def of(evs, name):
    return [e for e in evs if e["ev"] == name]


def cli(capsys, *argv):
    with pytest.raises(SystemExit) as x:
        cli_main(list(argv))
    cap = capsys.readouterr()
    return x.value.code, cap.out, cap.err


# ---- concrete call -------------------------------------------------------------------------------------------
def test_parent_calls_a_concrete_child_and_maps_returns(root, runs):
    tr = FakeTracer()
    eng, d = start(root, runs, "CONCRETE-PARENT", params=["value=v1"], tracer=tr)
    assert d["status"] == "done" and eng.st.ctx["result"] == "A:v1"
    crd = child_of(eng, runs)
    run = crd.read_run()
    assert run["status"] == "done" and run["sop"] == "ECHO-A" and run["depth"] == 1 and run["depth_limit"] == 2
    assert run["parent"] == {"run_id": eng.rd.run_id, "sop": "CONCRETE-PARENT", "step": "gate", "n": 1}
    assert run["call_path"] == ["CONCRETE-PARENT", "ECHO-A"] and run["dispatch"]["by"] == "sop"
    assert json.loads((crd.path / "outputs.json").read_text()) == {"echoed": "A:v1"}     # its own records
    assert crd.state_path.exists() and (crd.path / "config.snapshot.json").exists() and (crd.path / "ir.json").exists()
    calls = json.loads((eng.rd.path / "calls.json").read_text())
    assert [(c["step"], c["child"], c["status"]) for c in calls] == [("gate", crd.run_id, "done")]
    assert eng.rd.read_run()["children"] == [crd.run_id]
    evs = events(eng)
    assert [e["ev"] for e in evs if e["ev"].startswith("call_")] == ["call_start", "call_end"]
    assert of(evs, "call_start")[0]["depth"] == 1 and of(evs, "call_end")[0]["result"] == "ok"
    # one trace: the child's steps carry the "<call>-" prefix and it has no top-level run_start / run_end
    names = [(e["event"], e.get("step")) for e in tr.events]
    assert ("step_start", "gate-echo") in names and sum(1 for e in tr.events if e["event"] == "run_start") == 1
    assert [e["status"] for e in tr.events if e["event"] == "run_end"] == ["done"]
    # a child never counts as an extra active run of its own
    assert len([r for r in records.read_index(runs) if r["run_id"] == crd.run_id]) >= 1
    assert [r["parent"] for r in records.read_index(runs) if r["run_id"] == crd.run_id][-1] == eng.rd.run_id


# ---- dispatch ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind,want,by", [("a", "A:x", "dispatch"), ("b", "B:x", "dispatch"), ("zzz", "D:x", "default_impl")])
def test_interface_call_dispatches_by_predicate_then_default_impl(root, runs, kind, want, by):
    eng, d = start(root, runs, "DISPATCH-PARENT", params=["value=x", f"kind={kind}"])
    assert d["status"] == "done" and eng.st.ctx["result"] == want
    cs = of(events(eng), "call_start")[0]
    assert cs["dispatch"]["by"] == by and cs["interface"] == "IF-ECHO@1"
    if by == "dispatch":
        assert cs["dispatch"]["index"] == (0 if kind == "a" else 1)
    else:
        assert [e["value"] for e in cs["dispatch"]["evals"]] == [False, False]


def test_no_implementation_is_raised_and_caught(root, runs):
    eng, d = start(root, runs, "STRICT-PARENT", params=["value=x", "kind=q"])
    evs = events(eng)
    assert d["status"] == "done" and d["outcome"] == "handled"
    assert [(e["type"], e["source"]) for e in of(evs, "raise")] == [("NoImplementation", "engine")]
    assert of(evs, "catch")[0]["type"] == "NoImplementation" and not of(evs, "call_start")
    assert not list(runs.glob("*/*~*"))                                  # no child run was created


# ---- depth ---------------------------------------------------------------------------------------------------
def test_depth_limit_raises_limit_reached_and_it_propagates(root, runs):
    eng, d = start(root, runs, "DEPTH-1")
    assert d["status"] == "failed"
    run = eng.rd.read_run()
    assert run["raised"] == "LimitReached" and of(events(eng), "run_end")[0]["raised"] == "LimitReached"
    d2 = child_of(eng, runs).read_run()
    assert d2["sop"] == "DEPTH-2" and d2["depth"] == 1 and d2["raised"] == "LimitReached" and d2["status"] == "failed"
    d3 = records.find_run(runs, f"{eng.rd.run_id}~gate-1~gate-1")
    r3 = d3.read_run()
    assert r3["sop"] == "DEPTH-3" and r3["depth"] == 2 and r3["call_path"] == ["DEPTH-1", "DEPTH-2", "DEPTH-3"]
    raised = of(d3.read_state(), "raise")[0]
    assert raised["type"] == "LimitReached" and "exceeds the limit 2" in raised["note"]
    assert not list(runs.glob("*/*~gate-1~gate-1~*"))                    # DEPTH-4 was never started


def test_a_call_max_depth_lower_than_the_config_wins(root, runs):
    def lower(d):
        for s in d["steps"]:
            if s["id"] == "gate":
                s["max_depth"] = 1
    p = root / "depth-2.yaml"
    d = yaml.safe_load(p.read_text())
    lower(d)
    p.write_text(yaml.safe_dump(d, sort_keys=False))
    eng, st = start(root, runs, "DEPTH-1")
    r2 = child_of(eng, runs).read_state()
    assert of(r2, "raise")[0]["type"] == "LimitReached" and "limit 1" in of(r2, "raise")[0]["note"]


def test_a_recursive_call_inside_its_limits_terminates(root, runs):
    eng, d = start(root, runs, "RECURSE")
    assert d["status"] == "done"
    chain = [eng.rd.run_id, f"{eng.rd.run_id}~again-1", f"{eng.rd.run_id}~again-1~again-1"]
    assert [records.find_run(runs, c).read_run()["depth"] for c in chain] == [0, 1, 2]
    assert not list(runs.glob(f"*/{chain[2]}~*"))
    last = records.find_run(runs, chain[2]).read_state()
    assert [e["type"] for e in of(last, "raise")] == ["LimitReached"] and of(last, "catch")[0]["to"] == "stop"
    assert all(records.find_run(runs, c).read_run()["status"] == "done" for c in chain)


# ---- config narrowing ----------------------------------------------------------------------------------------
def test_child_config_is_narrowed_toward_the_parent_with_provenance(root, runs):
    eng, d = start(root, runs, "NARROW-PARENT", sets=["limits.max_iterations=8", "step_kinds.allowed=[code, human]"])
    assert d["status"] == "done"
    crd = child_of(eng, runs)
    snap = json.loads((crd.path / "config.snapshot.json").read_text())
    v = snap["values"]
    assert v["limits.max_iterations"]["value"] == 8 and "narrowed" in v["limits.max_iterations"]["source"]   # 15 (its L4) -> 8
    assert v["retry.code"]["value"] == 0 and "with_config:gate" in v["retry.code"]["source"]                  # with_config narrows
    assert v["step_kinds.allowed"]["value"] == ["code", "human"]                                              # intersected
    assert v["timeout.step"]["value"] == eng.values["timeout.step"]                                           # 3h -> the parent's
    call = snap["call"]
    assert call["parent"] == eng.rd.run_id and call["step"] == "gate" and call["with_config"] == {"retry.code": 0}
    assert {x["key"] for x in call["narrowed"]} >= {"limits.max_iterations", "retry.code", "step_kinds.allowed", "timeout.step"}
    notes = [e["note"] for e in of(crd.read_state(), "note")]
    assert any(n.startswith("call.narrowed by") and "limits.max_iterations->8" in n for n in notes)
    assert "limits.max_iterations" in of(events(eng), "call_start")[0]["narrowed"]


def test_the_parents_run_level_overrides_do_not_reach_the_child(root, runs):
    eng, _ = start(root, runs, "NARROW-PARENT", sets=["limits.max_iterations=30"])
    v = json.loads((child_of(eng, runs).path / "config.snapshot.json").read_text())["values"]
    assert v["limits.max_iterations"]["value"] == 15 and v["limits.max_iterations"]["source"].startswith("L4")   # not 30, not L7
    assert "narrowed" not in v["limits.max_iterations"]["source"]


def test_narrow_for_child_never_widens():
    def val(v, src="L1 default"):
        return {"value": v, "source": src, "layer": 1}
    parent = {"limits.max_iterations": val(5), "retry.code": val(1), "timeout.step": val("1h"), "human_wait.timeout": val("30m"),
              "budget.max_calls_per_run": val(10), "budget.max_cost_per_run": val(2.0),
              "step_kinds.allowed": val(["code", "llm"]), "ai.allowed": val(["svc-a", "svc-b"]),
              "ai.unattended_allowed": val(["svc-a"]), "run.unattended": val(True)}
    child = {"limits.max_iterations": val(20), "retry.code": val(0), "timeout.step": val("2h"), "human_wait.timeout": val("8h"),
             "budget.max_calls_per_run": val(100), "budget.max_cost_per_run": val(None),
             "step_kinds.allowed": val(["code", "llm", "agent", "human"]), "ai.allowed": val(["svc-b", "svc-c"]),
             "ai.unattended_allowed": val(None), "run.unattended": val(False)}
    notes = cfg.narrow_for_child(child, parent, {"calls": 4, "cost": 0.5}, {"retry.code": 5, "limits.max_iterations": 3,
                                                                          "step_kinds.allowed": ["llm", "human"]}, "gate")
    got = {k: v["value"] for k, v in child.items()}
    assert got["limits.max_iterations"] == 3                     # parent 5, then with_config 3
    assert got["retry.code"] == 0                                # the child's own 0 stays; with_config 5 would widen -> ignored
    assert got["timeout.step"] == "1h" and got["human_wait.timeout"] == "30m"      # durations compare by seconds
    assert got["budget.max_calls_per_run"] == 6 and got["budget.max_cost_per_run"] == 1.5   # what the parent has left
    assert got["step_kinds.allowed"] == ["llm"]                  # code/llm ∩ parent, ∩ with_config
    assert got["ai.allowed"] == ["svc-b"] and got["ai.unattended_allowed"] == ["svc-a"] and got["run.unattended"] is True
    ignored = [n for n in notes if n.get("ignored")]
    assert [n["key"] for n in ignored] == ["retry.code"] and all(n["by"] in ("parent", "with_config:gate") for n in notes)


# ---- exceptions ----------------------------------------------------------------------------------------------
def test_a_child_raises_a_declared_exception_caught_by_the_parent(root, runs):
    eng, d = start(root, runs, "RAISE-PARENT", params=["value=x", "kind=refuse"])
    assert d["status"] == "done" and d["outcome"] == "handled"
    crd = child_of(eng, runs)
    cr = crd.read_run()
    assert cr["status"] == "failed" and cr["raised"] == "EchoRefused"
    craise = of(crd.read_state(), "raise")[0]
    assert craise["type"] == "EchoRefused" and craise["declared"] is True and craise["source"] == "step"
    assert of(crd.read_state(), "run_end")[0]["raised"] == "EchoRefused"
    evs = events(eng)
    assert [(e["type"], e["source"], e.get("child")) for e in of(evs, "raise")] == [("EchoRefused", "child", crd.run_id)]
    assert of(evs, "catch")[0] == {**of(evs, "catch")[0], "type": "EchoRefused", "to": "handled"}
    assert of(evs, "call_end")[0]["result"] == "error" and of(evs, "call_end")[0]["raised"] == "EchoRefused"
    assert eng.st.raised is None                                   # caught


def test_an_undeclared_raise_becomes_system_error(root, runs):
    eng, d = start(root, runs, "RAISE-PARENT", params=["value=x", "kind=bogus"])
    crd = child_of(eng, runs)
    craise = of(crd.read_state(), "raise")[0]
    assert craise["type"] == "SystemError" and craise["declared"] is False and craise["requested"] == "Bogus"
    assert crd.read_run()["raised"] == "SystemError"
    # the parent only catches EchoRefused: unhandled goes to task_error_target (a failed end) and the run records it
    assert d["status"] == "failed" and eng.rd.read_run()["raised"] == "SystemError"
    assert [e["type"] for e in of(events(eng), "raise")] == ["SystemError"] and not of(events(eng), "catch")


def test_submit_raise_on_a_work_ticket(tmp_path, runs):
    root = tmp_path / "s"
    root.mkdir()
    (root / "r.yaml").write_text(yaml.safe_dump({
        "sop": {"id": "RAISER", "name": "Raises from a ticket", "version": "1.0"},
        "flow": {"start": "start", "execution": {"task_error_target": "failed"}},
        "steps": [{"id": "start", "type": "start"},
                  {"id": "go", "type": "task", "mode": "auto", "action": "x", "impl": "python3 -c print(1)",
                   "raises": ["Declared"]},
                  {"id": "done", "type": "end", "outcome": "success"}, {"id": "failed", "type": "end", "outcome": "failure"}],
        "transitions": [{"id": "T1", "from": "start", "to": "go"}, {"id": "T2", "from": "go", "to": "done"},
                        {"id": "T3", "from": "go", "to": "failed", "guard": "1 == 2"}]}))
    eng, d = start(root, runs, "RAISER", run_id="r-declared")
    assert d["ticket"]["type"] == "command"
    with pytest.raises(Usage):
        eng.cmd_submit(step="go", result="ok", raise_="Declared")                  # --raise needs --result error
    d = eng.cmd_submit(step="go", result="error", raise_="Declared")
    assert d["status"] == "failed" and d["raised"] == "Declared"
    assert eng.rd.read_run()["raised"] == "Declared" and of(events(eng), "raise")[0]["declared"] is True
    eng2, _ = start(root, runs, "RAISER", run_id="r-undeclared")
    d = eng2.cmd_submit(step="go", result="error", raise_="NotDeclared")
    ev = of(events(eng2), "raise")[0]
    assert d["raised"] == "SystemError" and ev["type"] == "SystemError" and ev["requested"] == "NotDeclared"


# ---- interface contract --------------------------------------------------------------------------------------
def test_pre_condition_failure_is_system_error_and_no_child_is_created(root, runs):
    eng, d = start(root, runs, "DISPATCH-PARENT", params=["value=", "kind=a"])
    raised = of(events(eng), "raise")
    assert [(e["type"], e["source"]) for e in raised] == [("SystemError", "engine")] and "precondition" in raised[0]["note"]
    assert d["status"] == "failed" and not of(events(eng), "call_start") and not list(runs.glob("*/*~*"))


def test_post_condition_failure_is_system_error(root, runs):
    eng, d = start(root, runs, "CONTRACT-PARENT", params=["value=x", "kind=bad"])
    crd = child_of(eng, runs)
    assert crd.read_run()["status"] == "done"                          # the child itself finished
    raised = of(events(eng), "raise")[0]
    assert raised["type"] == "SystemError" and "contract" in raised["note"] and d["status"] == "failed"
    assert "result" not in eng.st.ctx or not eng.st.ctx["result"]      # nothing was mapped


def test_contract_evaluation_is_fail_closed():
    assert contract.check(["a == 1", "nonempty(b)"], {"a": 1, "b": "x"}) == []
    bad = contract.check(["a == 2", "mystery(a)", "a >"], {"a": 1})
    assert len(bad) == 3 and any("unknown predicate" in b for b in bad)
    assert contract.check_types({"n": {"type": "int", "required": True}, "s": {"type": "string", "pattern": "^x"}},
                                {"s": "y"}) == ["required n is missing", "s does not match pattern ^x"]


def test_release_pipeline_dispatches_to_backup_and_verify_and_refuses_a_contract_gap(tmp_path, runs):
    good = tmp_path / "good"
    shutil.copytree(GOOD, good)
    appr = ScriptedApproval({"approve": "approved"})
    eng, d = start(good, runs, "RELEASE-PIPELINE", appr, params=["change_ref=chg"])
    crd = child_of(eng, runs, "gated-change")
    cs = of(events(eng), "call_start")[0]
    assert cs["sop"] == "BACKUP-AND-VERIFY" and cs["dispatch"]["by"] == "dispatch"
    assert d["status"] == "waiting" and d["children"][0]["run_id"] == crd.run_id
    d = open_engine(runs, crd.run_id, approval=appr).cmd_submit(step="approve", decision="approved", by="alice")
    # the fixture's BACKUP-AND-VERIFY never produces the interface's required output `approved`: the contract is refused
    eng = open_engine(runs, eng.rd.run_id)
    raised = of(eng.rd.read_state(), "raise")[0]
    assert raised["type"] == "SystemError" and "approved is missing" in raised["note"]
    assert eng.st.status == "suspended" and eng.st.suspend["reason"] == "limit"        # no catch, no task_error_target


# ---- extends: hooks and super --------------------------------------------------------------------------------
def test_super_before_runs_in_order_and_unoverridden_hook_is_a_noop(tmp_path, runs):
    good = tmp_path / "good"
    shutil.copytree(GOOD, good)
    appr = ScriptedApproval({"approve": "approved"})
    eng, d = start(good, runs, "BACKUP-AND-VERIFY", appr, params=["change_ref=c"])
    assert d["ticket"]["step"] == "approve"
    d = eng.cmd_submit(step="approve", decision="approved")
    assert d["status"] == "done"
    order = [e["step"] for e in of(events(eng), "step_end")]
    assert order == ["prepare", "approve", "apply-super", "apply", "verify"]
    (good / "hook-only.yaml").write_text(yaml.safe_dump({
        "sop": {"id": "HOOK-ONLY", "name": "Overrides only prepare", "version": "1.0", "extends": "APPROVED-CHANGE@1",
                "human_only": ["approve"]},
        "steps": [{"id": "prepare", "type": "task", "override": "APPROVED-CHANGE.prepare", "mode": "auto",
                   "action": "Prepare", "outputs": ["plan"]}]}))
    eng, d = start(good, runs, "HOOK-ONLY", appr, params=["change_ref=c"])
    eng.cmd_submit(step="approve", decision="approved")
    ends = {e["step"]: e for e in of(events(eng), "step_end")}
    assert ends["apply"]["noop"] is True and ends["apply"]["result"] == "ok" and eng.st.status == "done"


# ---- suspend propagation -------------------------------------------------------------------------------------
def test_ticket_on_a_child_and_submit_advances_the_parents(root, runs):
    appr = ScriptedApproval({"approve": "approved"})
    eng, d = start(root, runs, "GATED-PARENT", appr, params=["value=v"])
    crd = child_of(eng, runs)
    assert d["status"] == "waiting" and d["ticket"] is None and d["children"][0]["ticket"]["step"] == "approve"
    with pytest.raises(Refused) as x:
        eng.cmd_submit(step="approve", decision="approved")
    assert crd.run_id in str(x.value)                                  # the ticket is on the child: submit to its id
    ceng = open_engine(runs, crd.run_id, approval=appr)
    d = ceng.cmd_submit(step="approve", decision="approved", by="alice")
    assert d["status"] == "done" and d["root"]["status"] == "done"
    root_eng = open_engine(runs, eng.rd.run_id)
    assert root_eng.st.status == "done" and root_eng.st.ctx["verdict"] == "approved"


def test_a_suspended_child_is_resumed_through_the_root_and_a_lone_resume_is_refused(root, runs):
    appr = ScriptedApproval({"approve": "approved"})
    eng, d = start(root, runs, "GATED-PARENT", appr, params=["value=v"])
    crd = child_of(eng, runs)
    late = lambda: dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)           # noqa: E731 (past human_wait.timeout)
    peng = open_engine(runs, eng.rd.run_id, approval=appr, clock=late)
    d = peng.cmd_next()
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "child"
    assert open_engine(runs, crd.run_id).st.status == "suspended" and open_engine(runs, crd.run_id).st.suspend["reason"] == "timeout"
    for what in ("resume", "cancel"):
        with pytest.raises(Refused) as x:
            getattr(open_engine(runs, crd.run_id), "cmd_" + what)()
        assert eng.rd.run_id in str(x.value) and "root" in str(x.value)
    d = open_engine(runs, eng.rd.run_id, repo=Repo(root), approval=appr, clock=late).cmd_resume()
    assert d["status"] == "waiting" and d["children"][0]["ticket"]["attempt"] == 2     # the child is waiting again
    assert [e["ev"] for e in events(crd) if e["ev"] in ("suspend", "resume")] == ["suspend", "resume"]
    d = open_engine(runs, crd.run_id, approval=appr).cmd_submit(step="approve", decision="approved")
    assert d["status"] == "done" and open_engine(runs, eng.rd.run_id).st.status == "done"


def test_suspending_or_cancelling_a_parent_reaches_its_running_children(root, runs):
    appr = ScriptedApproval({"approve": "approved"})
    eng, _ = start(root, runs, "GATED-PARENT", appr, params=["value=v"])
    crd = child_of(eng, runs)
    eng.cmd_suspend("pause")
    ch = open_engine(runs, crd.run_id)
    assert ch.st.status == "suspended" and ch.st.suspend["reason"] == "parent"
    d = open_engine(runs, eng.rd.run_id).cmd_cancel("stop")
    assert d["status"] == "cancelled" and open_engine(runs, crd.run_id).st.status == "cancelled"
    assert json.loads((eng.rd.path / "calls.json").read_text())[0]["status"] == "cancelled"


# ---- migrate -------------------------------------------------------------------------------------------------
def _edit(root, fname, fn):
    p = root / fname
    d = yaml.safe_load(p.read_text())
    fn(d)
    p.write_text(yaml.safe_dump(d, sort_keys=False))


def _gate_root(tmp_path):
    r = tmp_path / "sop"
    shutil.copytree(HERE / "fixtures" / "run", r)
    return r


def test_migrate_moves_a_run_to_a_compatible_new_version(tmp_path, runs):
    sop = _gate_root(tmp_path)
    appr = ScriptedApproval({"approve": "approved"})
    eng, d = start(sop, runs, "GATE", appr)
    eng.cmd_suspend("deploying a new version")

    def add_step(d):
        d["sop"]["version"] = "1.1"
        d["steps"].insert(2, {"id": "audit", "type": "task", "mode": "auto", "action": "Record the decision"})
        d["transitions"] = [t for t in d["transitions"] if t["id"] != "T4"] + [
            {"id": "T4", "from": "apply", "to": "audit"}, {"id": "T5", "from": "audit", "to": "done"}]
    _edit(sop, "gate.yaml", add_step)
    with pytest.raises(Refused) as x:                                  # without --migrate the old version is pinned
        open_engine(runs, eng.rd.run_id, repo=Repo(sop), approval=appr).cmd_resume()
    assert "--migrate" in str(x.value)
    d = open_engine(runs, eng.rd.run_id, repo=Repo(sop), approval=appr).cmd_resume(migrate=True)
    assert d["migrated"][0]["version"] == "1.1" and d["ticket"]["step"] == "approve"
    run = eng.rd.read_run()
    assert run["version"] == "1.1" and run["migrations"][0]["from_version"] == "1.0" and run["migrations"][0]["old_ir"] == "ir.1.json"
    assert json.loads((eng.rd.path / "ir.1.json").read_text())["sop"]["version"] == "1.0"
    assert json.loads((eng.rd.path / "ir.json").read_text())["sop"]["version"] == "1.1"
    assert of(events(eng), "migrate")[0]["to_version"] == "1.1"
    d = open_engine(runs, eng.rd.run_id, approval=appr).cmd_submit(step="approve", decision="approved")
    assert d["status"] == "done"
    assert [e["step"] for e in of(events(eng), "step_end")][-3:] == ["approve", "apply", "audit"]       # the new flow ran


@pytest.mark.parametrize("why,edit", [
    ("major", lambda d: d["sop"].update(version="2.0")),
    ("current node", lambda d: d.update(steps=[s for s in d["steps"] if s["id"] != "approve"],
                                          transitions=[{"id": "T1", "from": "start", "to": "apply"},
                                                       {"id": "T4", "from": "apply", "to": "done"}])),
    ("kind", lambda d: [s.update(mode="auto") for s in d["steps"] if s["id"] == "approve"]),
])
def test_migrate_refuses_an_incompatible_version(tmp_path, runs, why, edit):
    sop = _gate_root(tmp_path)
    eng, _ = start(sop, runs, "GATE", ScriptedApproval({"approve": "approved"}))
    eng.cmd_suspend("x")
    before = (eng.rd.path / "ir.json").read_text()
    _edit(sop, "gate.yaml", edit)
    with pytest.raises(Refused) as x:
        open_engine(runs, eng.rd.run_id, repo=Repo(sop)).cmd_resume(migrate=True)
    assert (eng.rd.path / "ir.json").read_text() == before and not list(eng.rd.path.glob("ir.[0-9]*.json"))
    assert not of(events(eng), "migrate") and open_engine(runs, eng.rd.run_id).st.status == "suspended"
    assert why in ("major", "current node", "kind") and str(x.value)


def test_migrate_moves_the_child_runs_together_from_the_root(root, runs):
    appr = ScriptedApproval({"approve": "approved"})
    eng, _ = start(root, runs, "GATED-PARENT", appr, params=["value=v"])
    crd = child_of(eng, runs)
    eng.cmd_suspend("deploy")

    def add_step(d):
        d["sop"]["version"] = "1.1"
        d["steps"].insert(2, {"id": "log", "type": "task", "mode": "auto", "action": "Log the verdict"})
        d["transitions"] = [{"id": "T1", "from": "start", "to": "approve"}, {"id": "T2", "from": "approve", "to": "log"},
                            {"id": "T3", "from": "log", "to": "done"}]
    _edit(root, "gated-child.yaml", add_step)
    d = open_engine(runs, eng.rd.run_id, repo=Repo(root), approval=appr).cmd_resume(migrate=True)
    assert [m["run_id"] for m in d["migrated"]] == [crd.run_id]                       # the root's own IR did not change
    assert crd.read_run()["version"] == "1.1" and "migrations" not in eng.rd.read_run()
    assert of(events(crd), "migrate")[0]["to_version"] == "1.1" and not of(events(eng), "migrate")
    d = open_engine(runs, crd.run_id, approval=appr).cmd_submit(step="approve", decision="approved")
    assert d["status"] == "done" and "log" in [e["step"] for e in of(events(crd), "step_end")]


# ---- show --tree / cli ---------------------------------------------------------------------------------------
def test_show_tree_prints_the_call_tree(root, runs, capsys):
    eng, _ = start(root, runs, "RECURSE")
    rid = eng.rd.run_id
    code, out, _ = cli(capsys, "show", rid, "--tree", "--runs-root", str(runs))
    lines = out.splitlines()
    assert code == 0 and len(lines) == 3
    assert lines[0].startswith(rid) and "RECURSE  done" in lines[0]
    assert lines[1].lstrip().startswith("`- ") and f"{rid}~again-1" in lines[1] and "(call again, depth 1)" in lines[1]
    assert f"{rid}~again-1~again-1" in lines[2] and "depth 2" in lines[2]
    code, out, _ = cli(capsys, "show", rid, "--tree", "--json", "--runs-root", str(runs))
    tree = json.loads(out)
    assert tree["children"][0]["children"][0]["depth"] == 2 and tree["children"][0]["children"][0]["children"] == []
    assert history.tree_lines(tree) == lines


def test_cli_child_routing_and_root_only_resume(root, runs, capsys):
    base = ["--runs-root", str(runs), "--sop-root", str(root), "--approval", "deny"]
    code, out, _ = cli(capsys, "run", "GATED-PARENT", *base, "--run-id", "g1", "--param", "value=v")
    crd = "g1~gate-1"
    assert code == 0 and "PROCFLOW_NEXT=CHILD" in out and f"PROCFLOW_CHILD={crd}" in out
    code, out, err = cli(capsys, "submit", "g1", "--step", "approve", *base)
    assert code == 1 and crd in err
    code, out, _ = cli(capsys, "suspend", crd, *base, "--note", "alone")
    assert code == 4 and f"resume the root run g1" in out
    code, _, err = cli(capsys, "resume", crd, *base)
    assert code == 1 and "root run g1" in err
    code, out, _ = cli(capsys, "next", "g1", *base)                    # the parent notices the suspended child
    assert code == 4 and "child" in out
    code, out, _ = cli(capsys, "resume", "g1", *base)
    assert code == 0 and f"PROCFLOW_CHILD={crd}" in out
