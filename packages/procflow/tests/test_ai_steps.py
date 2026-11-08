"""llm / agent steps -- providers, validation, retry-then-escalate, fallback, budgets, the queue, replay."""

import datetime as dt
import json
import threading

import pytest

from procflow import Repo, ScriptedApproval, create_run, fakeprov, lock, open_engine
from procflow import replay as RP
from procflow.approval import DecisionRefused
from procflow.engine import Refused
from procflow.workqueue import WorkQueue

SECRET_LINE = "API_" + "TOKEN=" + "s3cr3t" + "valueXYZ"


def reg_one_fake(write_reg, root, **svc):
    write_reg(root, services={"fake-1": dict({"provider": "fake", "family": "remote", "data_classes": ["public", "internal"],
                                              "cost_per_call": 1}, **svc)},
              routing={"llm": {"summarize": {"low": {"service": "fake-1"}}}})


def start(root, runs, sop="AI-CHAIN", approval=None, **kw):
    return create_run(Repo(root), sop, runs_root=runs, approval=approval, owner="tester", params=["topic=tides"], **kw)


def evs(eng, name):
    return [e for e in eng.rd.read_state() if e["ev"] == name]


@pytest.fixture
def runs(tmp_path):
    return tmp_path / "runs"


def good(text="a fine summary"):
    return {"output": {"summary": text}}


# ---- an llm step runs through a provider and its output is validated ----------------------------------
def test_llm_step_runs_via_fake_validates_output_and_records_masked_attempt(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)
    fakeprov.set_script({"llm": {"fake-1": [good("fine text\n" + SECRET_LINE)]}})
    eng, d = start(ai_root, runs)
    assert d["status"] == "done" and eng.st.ctx["summary"].startswith("fine text")
    call = evs(eng, "ai_call")[0]
    assert call["result"] == "ok" and call["target"] == "fake-1" and call["via"] == "fake" and call["fb"] == 0
    att = json.loads((eng.rd.path / "1" / "summarize" / "ai-attempt-1.json").read_text())
    assert {"route", "target", "fallback_index", "tries", "model", "via", "params", "cost", "prompt", "output",
            "check_errors"} <= set(att)
    assert att["route"]["granularity"] == "routing" and att["cost"] == 1 and att["tries"] == 0
    assert "tides" in att["prompt"]                                   # the template was filled from the inputs
    # nothing secret-like is stored anywhere in the run directory
    blob = "".join(p.read_text() for p in eng.rd.path.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl", ".md", ".txt"))
    assert "valueXYZ" not in blob


def test_a_bad_output_is_retried_with_the_reason_then_escalated_to_a_human(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)
    fakeprov.set_script({"llm": {"fake-1": [{"text": "not json at all"}]}})        # every call is invalid
    eng, d = start(ai_root, runs, approval=ScriptedApproval({"summarize": "handled"}))
    # retry.llm = 2 -> three invalid answers, then a person
    assert [c["result"] for c in evs(eng, "ai_call")] == ["invalid"] * 3
    assert d["status"] == "waiting" and d["ticket"]["type"] == "human" and d["ticket"]["escalated"] is True
    assert evs(eng, "escalate")[0]["to"] == "human"
    assert "Why the previous answer was rejected" in json.loads(
        (eng.rd.path / "1" / "summarize" / "ai-attempt-2.json").read_text())["prompt"]       # retry-with-reason
    assert d["ticket"]["rejections"]
    # the person supplies the output; the approval source still has to confirm (never self-approved)
    bad = open_engine(runs, eng.rd.run_id, approval=None, owner="tester")
    with pytest.raises(DecisionRefused):
        bad.cmd_submit(step="summarize", decision="handled", outputs={"summary": "by hand"})
    with pytest.raises(Refused, match="not acceptable"):                                  # schema applies to a human too
        open_engine(runs, eng.rd.run_id, approval=ScriptedApproval({"summarize": "handled"}), owner="tester").cmd_submit(
            step="summarize", decision="handled", outputs={"summary": "x"})
    d = open_engine(runs, eng.rd.run_id, approval=ScriptedApproval({"summarize": "handled"}), owner="tester").cmd_submit(
        step="summarize", decision="handled", outputs={"summary": "by hand"})
    assert d["status"] == "done"


def test_a_later_valid_answer_is_accepted_after_a_rejection(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)
    fakeprov.set_script({"llm": {"fake-1": [{"output": {"summary": "x"}}, good()]}})   # too short, then fine
    eng, d = start(ai_root, runs)
    assert d["status"] == "done"
    assert [c["result"] for c in evs(eng, "ai_call")] == ["invalid", "ok"]
    assert "minLength" not in json.dumps(evs(eng, "ai_call")) and evs(eng, "ai_call")[0]["errors"]


def test_a_domain_rule_hook_rejects_then_accepts(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)
    fakeprov.set_script({"llm": {"fake-1": [good("forbidden words here"), good("clean text")]}})
    eng, d = start(ai_root, runs, domain_checks={"summarize": lambda out, ctx: ["contains forbidden words"]
                                                 if "forbidden" in out["summary"] else []})
    assert d["status"] == "done" and eng.st.ctx["summary"] == "clean text"
    assert "contains forbidden words" in evs(eng, "ai_call")[0]["errors"][0]


def test_a_rule_program_in_the_impl_root_is_a_domain_check(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)
    steps = ai_root / "_impl" / "AI-CHAIN" / "steps"
    steps.mkdir()
    (steps / "summarize.py").write_text(
        "import json, os\ni = json.load(open(os.environ['PROCFLOW_INPUTS']))\n"
        "e = ['must mention the topic'] if 'tides' not in i['output']['summary'] else []\n"
        "json.dump({'errors': e}, open(os.environ['PROCFLOW_OUTPUTS'], 'w'))\n")
    fakeprov.set_script({"llm": {"fake-1": [good("about nothing"), good("about the tides")]}})
    eng, d = start(ai_root, runs)
    assert d["status"] == "done" and "must mention the topic" in evs(eng, "ai_call")[0]["errors"][0]


# ---- fallback chain ---------------------------------------------------------------------------------------
def test_an_unavailable_target_advances_the_fallback_chain(ai_root, write_reg, runs):
    base = {"provider": "fake", "family": "remote", "data_classes": ["public", "internal"], "cost_per_call": 1}
    write_reg(ai_root, services={"fake-1": base, "fake-2": base},
              routing={"llm": {"summarize": {"low": {"service": "fake-1", "fallback": ["fake-2"]}}}})
    fakeprov.set_script({"llm": {"fake-1": [{"error": "unavailable"}], "fake-2": [good()]}})
    eng, d = start(ai_root, runs)
    assert d["status"] == "done"
    assert [(c["target"], c["result"], c["fb"]) for c in evs(eng, "ai_call")] == [("fake-1", "unavailable", 0), ("fake-2", "ok", 1)]
    assert [e["attempt"] for e in evs(eng, "enter")] == [1, 2]                      # every call is its own attempt


def test_every_target_failing_suspends_with_limit(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)
    fakeprov.set_script({"llm": {"fake-1": [{"error": "timeout"}]}})
    eng, d = start(ai_root, runs)
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "limit"


def test_a_refusal_is_not_sent_to_another_target_the_step_goes_to_a_person(ai_root, write_reg, runs):
    base = {"provider": "fake", "family": "remote", "data_classes": ["public", "internal"]}
    write_reg(ai_root, services={"fake-1": base, "fake-2": base},
              routing={"llm": {"summarize": {"low": {"service": "fake-1", "fallback": ["fake-2"]}}}})
    fakeprov.set_script({"llm": {"fake-1": [{"error": "refused"}], "fake-2": [good()]}})
    eng, d = start(ai_root, runs)
    assert d["ticket"]["type"] == "human" and [c["target"] for c in evs(eng, "ai_call")] == ["fake-1"]


# ---- local-only data ------------------------------------------------------------------------------------
LOCAL_CLASSES = {"default": "internal", "classes": {"public": {}, "internal": {}, "restricted": {"local_only": True}}}


def test_local_only_step_falls_to_a_human_when_no_local_target_passes(ai_root, write_reg, runs):
    write_reg(ai_root, classes=LOCAL_CLASSES, services={
        "remote-1": {"provider": "fake", "family": "remote", "data_classes": ["public", "internal"]}})
    fakeprov.set_script({"llm": {"remote-1": [{"output": {"label": "leaked"}}]}})
    eng, d = create_run(Repo(ai_root), "AI-LOCAL", runs_root=runs, owner="tester", params=["topic=t"],
                        sets=["llm.default=remote-1"], approval=ScriptedApproval({"classify": "done"}))
    assert d["status"] == "waiting" and d["ticket"]["type"] == "human" and d["ticket"]["escalated"]
    assert evs(eng, "ai_call") == [] and "never leaves" in evs(eng, "escalate")[0]["reason"]   # no provider was consulted
    d = eng.cmd_submit(step="classify", decision="done", outputs={"label": "internal-use"})
    assert d["status"] == "done" and eng.st.ctx["label"] == "internal-use"


def test_local_only_step_uses_a_local_target_when_one_exists(ai_root, write_reg, runs):
    write_reg(ai_root, classes=LOCAL_CLASSES, services={
        "remote-1": {"provider": "fake", "family": "remote", "data_classes": ["public", "internal"]},
        "local-1": {"provider": "fake", "family": "local", "data_classes": ["public", "internal", "restricted"]}})
    fakeprov.set_script({"llm": {"local-1": [{"output": {"label": "ok"}}], "remote-1": [{"output": {"label": "leak"}}]}})
    eng2, d2 = create_run(Repo(ai_root), "AI-LOCAL", runs_root=runs, owner="tester", params=["topic=t2"],
                          sets=["llm.default=remote-1,local-1"])
    assert d2["status"] == "done" and [c["target"] for c in evs(eng2, "ai_call")] == ["local-1"]


# ---- the work queue ---------------------------------------------------------------------------------------
def queued_run(ai_root, runs, write_reg):
    write_reg(ai_root, services={})          # a registry exists -> session tickets are queued
    return start(ai_root, runs, sets=["steps.summarize.target=session"])


def test_session_target_queues_a_ticket_and_releases_the_lock(ai_root, write_reg, runs):
    eng, d = queued_run(ai_root, runs, write_reg)
    t = d["ticket"]
    assert d["queued"] and t["type"] == "work" and t["queued"] is True and t["via"] == "session"
    assert (eng.rd.path / "1" / "summarize" / t["prompt_file"]).read_text().startswith("# Work ticket")
    assert lock.holder(eng.rd.file(".lock")) is None                           # any worker may take it now
    q = WorkQueue(runs)
    rows = q.list()
    assert [r["ticket"] for r in rows] == [t["ticket"]]


def test_exactly_one_worker_claims_a_ticket_and_only_the_claimant_submits(ai_root, write_reg, runs):
    eng, d = queued_run(ai_root, runs, write_reg)
    ticket = d["ticket"]["ticket"]
    q = WorkQueue(runs)
    got = []

    def go(w):
        got.append((w, q.claim(w, ticket=ticket)))
    ts = [threading.Thread(target=go, args=(f"w{i}",)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    winners = [w for w, doc in got if doc is not None]
    assert len(winners) == 1                                                  # atomic: one rename wins
    win = winners[0]
    other = "w-other"
    assert q.claim(other, ticket=ticket) is None                              # a second claim fails
    with pytest.raises(Refused, match="claimed by another worker"):
        open_engine(runs, eng.rd.run_id, owner=other).cmd_submit(step="summarize", outputs={"summary": "stolen text"}, by=other)
    with pytest.raises(Refused, match="must be claimed first"):               # an unclaimed ticket cannot be submitted either
        eng2, d2 = start(ai_root, runs, sets=["steps.summarize.target=session"], run_id="second", allow_same_input=True)
        open_engine(runs, "second", owner="x").cmd_submit(step="summarize", outputs={"summary": "no claim made"}, by="x")
    # a rejected submission re-issues the ticket (attempt 2, with the reasons) for a worker to claim again
    d = open_engine(runs, eng.rd.run_id, owner=win).cmd_submit(step="summarize", outputs={"summary": "x"}, by=win)
    t2 = d["ticket"]
    assert t2["attempt"] == 2 and t2["rejections"] and d["queued"]
    assert q.claimant(eng.rd.run_id, ticket) is None and q.claim(win, ticket=t2["ticket"]) is not None
    d = open_engine(runs, eng.rd.run_id, owner=win).cmd_submit(step="summarize", outputs={"summary": "a proper summary"}, by=win)
    assert d["status"] == "done"
    assert [r["run_id"] for r in q.list("all")] == ["second"]                   # this run's tickets left the queue
    rec = json.loads((eng.rd.path / "1" / "summarize" / "ai-attempt-2.json").read_text())
    assert rec["via"] == "session" and rec["result"] == "ok"


def test_an_expired_claim_goes_back_and_the_old_claimant_can_no_longer_submit(ai_root, write_reg, runs):
    eng, d = queued_run(ai_root, runs, write_reg)
    ticket = d["ticket"]["ticket"]
    now = [dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)]
    q = WorkQueue(runs, lease=60, clock=lambda: now[0])
    assert q.claim("slow", ticket=ticket) is not None and q.list() == []
    now[0] += dt.timedelta(seconds=61)
    assert q.claimant(eng.rd.run_id, ticket)["expired"] is True
    assert q.reap() == 1 and len(q.list()) == 1
    assert q.release("slow", ticket) is False                                    # not claimed any more
    assert q.claim("fast", ticket=ticket) is not None
    assert q.release("slow", ticket) is False and q.release("fast", ticket) is True    # only the claimant releases


# ---- budgets ----------------------------------------------------------------------------------------------
def test_call_budget_suspends_with_limit(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)
    fakeprov.set_script({"llm": {"fake-1": [good()]}})
    eng, d = start(ai_root, runs, sets=["budget.max_calls_per_run=0"])
    assert d["status"] == "suspended" and d["suspend"]["reason"] == "limit" and "max_calls_per_run" in d["suspend"]["note"]
    reg_one_fake(write_reg, ai_root)
    fakeprov.set_script({"llm": {"fake-1": [{"text": "bad"}]}})
    eng, d = start(ai_root, runs, sets=["budget.max_calls_per_run=2"], run_id="r2", allow_same_input=True)
    assert d["suspend"]["reason"] == "limit" and len(evs(eng, "ai_call")) == 2


def test_cost_ceiling_suspends_before_the_call_that_would_exceed_it(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)                                             # cost_per_call: 1
    fakeprov.set_script({"llm": {"fake-1": [{"text": "bad"}]}})
    eng, d = start(ai_root, runs, sets=["budget.max_cost_per_run=2.5"])
    assert d["suspend"]["reason"] == "limit" and "max_cost_per_run" in d["suspend"]["note"]
    assert len(evs(eng, "ai_call")) == 2 and eng.st.ai_cost == 2


# ---- agent steps -------------------------------------------------------------------------------------------
def agent_reg(write_reg, root):
    write_reg(root, agents={"fake-agent": {"provider": "fake", "family": "remote", "capabilities": ["fs-write"],
                                           "data_classes": ["public", "internal"]}},
              routing={"needs": {"fs-write": {"agent": "fake-agent"}}})


def test_agent_step_writes_only_inside_the_sandbox_and_is_checked(ai_root, write_reg, runs):
    agent_reg(write_reg, ai_root)
    fakeprov.set_script({"agent": {"fake-agent": [
        {"files": {"a.txt": "x"}, "summary": {"report": "r", "status": "broken"}},                          # agent check fails
        {"files": {"a.txt": "x"}, "summary": {"report": "r", "status": "built"}, "outside": ["../escape.txt"]},   # tried to leave
        {"files": {"sub/a.txt": "data"}, "summary": {"report": "r", "status": "built"}, "stray": ["stray.txt"]},   # wrote beside output/
        {"files": {"sub/a.txt": "data"}, "summary": {"report": "r", "status": "built"}},
    ]}})
    eng, d = start(ai_root, runs, sop="AI-AGENT", sets=["retry.llm=3"])
    assert d["status"] == "done" and eng.st.ctx["status"] == "built"
    results = [(c["result"], c["errors"]) for c in evs(eng, "ai_call")]
    assert [r for r, _ in results] == ["invalid", "invalid", "invalid", "ok"]
    assert "is false" in results[0][1][0] and "outside the output directory" in results[1][1][0] \
        and "outside the output directory" in results[2][1][0]
    att = json.loads((eng.rd.path / "1" / "build" / "ai-attempt-4.json").read_text())
    f = next(x for x in att["files"] if x["path"] == "sub/a.txt")
    assert f["path"] == "sub/a.txt" and f["bytes"] == 4 and len(f["sha256"]) == 64 and "data" not in json.dumps(att["files"])
    assert not (eng.rd.path.parent / "escape.txt").exists() and not (eng.rd.path / "1" / "build" / "escape.txt").exists()


def test_agent_needs_a_capability_no_target_has(ai_root, write_reg, runs):
    write_reg(ai_root, agents={"weak": {"provider": "fake", "family": "remote", "capabilities": ["read"],
                                        "data_classes": ["public", "internal"]}},
              routing={"needs": {"fs-write": {"agent": "weak"}}})
    with pytest.raises(Refused, match="lacks the required capabilities"):
        start(ai_root, runs, sop="AI-AGENT")


def test_fake_providers_are_refused_without_the_env_flag(ai_root, write_reg, runs, monkeypatch):
    reg_one_fake(write_reg, ai_root)
    monkeypatch.delenv("PROCFLOW_ALLOW_FAKE_PROVIDERS")
    with pytest.raises(Refused, match="PROCFLOW_ALLOW_FAKE_PROVIDERS"):
        start(ai_root, runs)


def test_without_any_registry_an_llm_step_is_still_a_manual_work_ticket(ai_root, runs):
    eng, d = start(ai_root, runs)
    assert d["ticket"]["type"] == "work" and not d["ticket"].get("queued")       # plain human behaviour, no queue
    d = eng.cmd_submit(step="summarize", outputs={"summary": "typed by a person"})
    assert d["status"] == "done"


def test_the_registry_is_pinned_into_the_run(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)
    fakeprov.set_script({"llm": {"fake-1": [good()]}})
    eng, d = start(ai_root, runs)
    pinned = json.loads((eng.rd.path / "registry.snapshot.json").read_text())
    assert "fake-1" in pinned["services"] and pinned["configured"] is True
    (ai_root / "_config" / "llm_services.yaml").unlink()                          # later edits do not change a started run
    assert "fake-1" in open_engine(runs, eng.rd.run_id).reg.services


# ---- replay ---------------------------------------------------------------------------------------------------
def tamper_state(eng, fn):
    p = eng.rd.state_path
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    fn(rows)
    p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))


def test_replay_accepts_a_good_run_without_calling_providers_and_rejects_tampering(ai_root, write_reg, runs):
    reg_one_fake(write_reg, ai_root)
    fakeprov.set_script({"llm": {"fake-1": [good()]}})
    eng, d = start(ai_root, runs)
    assert d["status"] == "done"
    fakeprov.set_script(None)                                                   # any provider call would now be "unavailable"
    rows = RP.replay_all(runs, Repo(ai_root))
    assert [r["verdict"] for r in rows] == ["accepted"] and rows[0]["checked"] >= 3 and RP.all_accepted(rows)
    assert RP.replay_run(eng.rd, Repo(ai_root), current=True)["verdict"] == "accepted"
    assert evs(eng, "ai_call").__len__() == 1                                    # replay appended nothing
    # a recorded output that no longer satisfies the checks
    f = eng.rd.path / "1" / "summarize" / "ai-attempt-1.json"
    rec = json.loads(f.read_text())
    rec["output"] = {"summary": "x"}
    f.write_text(json.dumps(rec))
    r = RP.replay_run(eng.rd, Repo(ai_root))
    assert r["verdict"] == "rejected" and "no longer passes the checks" in r["problems"][0]
    # a transition that is not in the IR
    rec["output"] = {"summary": "a fine summary"}
    f.write_text(json.dumps(rec))
    tamper_state(eng, lambda rows: [r.update(to="done") for r in rows if r["ev"] == "transition" and r.get("transition") == "T1"])
    r = RP.replay_run(eng.rd, Repo(ai_root))
    assert r["verdict"] == "rejected" and "not in the IR" in r["problems"][0]


def test_replay_rechecks_guards_decide_by_and_a_missing_record(tmp_path):
    import shutil
    from pathlib import Path
    root = tmp_path / "sop"
    shutil.copytree(Path(__file__).parent / "fixtures" / "run", root)
    runs = tmp_path / "runs"
    eng, d = create_run(Repo(root), "BRANCH", runs_root=runs, owner="tester", params=["mode=left"])
    assert d["status"] == "done" and RP.replay_run(eng.rd, Repo(root))["verdict"] == "accepted"

    def to_right(rows):
        for r in rows:
            if r["ev"] == "transition" and r.get("transition") == "T3":
                r.update(transition="T4", to="right-step")
    tamper_state(eng, to_right)
    r = RP.replay_run(eng.rd, Repo(root))
    assert r["verdict"] == "rejected" and "decide_by" in r["problems"][0]
    (eng.rd.path / "ir.json").unlink()
    assert RP.replay_run(eng.rd, Repo(root))["verdict"] == "undecidable"         # never silently accepted
