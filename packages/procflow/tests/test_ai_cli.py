"""CLI (queue / replay / catalog / scaffold / config show --steps) and the resource catalogue."""

import json

import pytest
import yaml

from procflow import Repo, catalog as CAT, fakeprov
from procflow.cli import main


def cli(capsys, *argv):
    with pytest.raises(SystemExit) as x:
        main(list(argv))
    cap = capsys.readouterr()
    return x.value.code, cap.out, cap.err


def base(root, runs):
    return ["--runs-root", str(runs), "--sop-root", str(root)]


# ---- config show --steps --------------------------------------------------------------------------------------
def test_config_show_steps_resolves_each_step_target_and_fallback(ai_root, write_reg, capsys):
    b = {"provider": "fake", "family": "remote", "data_classes": ["public", "internal"]}
    write_reg(ai_root, services={"svc-a": b, "svc-b": b},
              routing={"llm": {"summarize": {"low": {"service": "svc-a", "fallback": ["svc-b"]}}}})
    code, out, _ = cli(capsys, "config", "show", "AI-CHAIN", "--sop-root", str(ai_root), "--param", "topic=t", "--steps")
    assert code == 0
    row = next(x for x in out.splitlines() if x.startswith("summarize"))
    assert "llm:svc-a" in row and "fallback-> llm:svc-b" in row and "routing" in row
    # a config override at the finest granularity changes the answer and says where it came from
    code, out, _ = cli(capsys, "config", "show", "AI-CHAIN", "--sop-root", str(ai_root), "--param", "topic=t", "--steps",
                       "--set", "steps.summarize.target=svc-b", "--json")
    r = json.loads(out)[0]
    assert (r["granularity"], r["target"], r["layer"]) == ("step", "svc-b", "L7")


def test_config_show_steps_notes_when_nothing_passes_and_for_local_only(ai_root, write_reg, capsys):
    write_reg(ai_root, services={"gone": {"provider": "disabled", "disabled_reason": "retired"}},
              routing={"llm": {"summarize": {"low": {"service": "gone"}}}},
              classes={"default": "internal", "classes": {"internal": {}, "restricted": {"local_only": True}}})
    _, out, _ = cli(capsys, "config", "show", "AI-CHAIN", "--sop-root", str(ai_root), "--param", "topic=t", "--steps")
    assert "(no target)" in out and "retired" in out
    _, out, _ = cli(capsys, "config", "show", "AI-LOCAL", "--sop-root", str(ai_root), "--param", "topic=t", "--steps")
    assert "a person" in out and "restricted" in out
    code, _, err = cli(capsys, "check", "AI-CHAIN", "--run", "--sop-root", str(ai_root))
    assert code == 1


# ---- the queue through the CLI --------------------------------------------------------------------------------
def test_queue_cli_claims_atomically_and_the_worker_submits(ai_root, write_reg, tmp_path, capsys):
    runs = tmp_path / "runs"
    b = base(ai_root, runs)
    write_reg(ai_root, services={})
    code, out, _ = cli(capsys, "run", "AI-CHAIN", *b, "--run-id", "q1", "--param", "topic=t", "--set", "steps.summarize.target=session")
    assert code == 0 and "PROCFLOW_NEXT=work" in out and "queued: waiting for a worker" in out
    code, out, _ = cli(capsys, "queue", "--runs-root", str(runs))
    assert "pending" in out and "1 ticket(s)" in out
    code, out, _ = cli(capsys, "queue", "--runs-root", str(runs), "--claim", "--worker", "w1", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["worker"] == "w1" and doc["run_id"] == "q1" and doc["prompt_file"].endswith("prompt-1.md")
    assert "Summarise the topic" in open(doc["prompt_file"]).read()
    code, _, err = cli(capsys, "queue", "--runs-root", str(runs), "--claim", "--worker", "w2")
    assert code == 1 and "nothing could be claimed" in err
    code, _, err = cli(capsys, "submit", "q1", *b, "--step", "summarize", "--output", "summary=stolen by w2", "--by", "w2")
    assert code == 1 and "claimed by another worker" in err
    code, out, _ = cli(capsys, "queue", "--runs-root", str(runs), "--release", "--ticket", doc["ticket"], "--worker", "w1")
    assert code == 0 and "released" in out
    code, _, _ = cli(capsys, "queue", "--runs-root", str(runs), "--claim", "--worker", "w3", "--ticket", doc["ticket"])
    assert code == 0
    code, out, _ = cli(capsys, "submit", "q1", *b, "--step", "summarize", "--output", "summary=a proper summary", "--by", "w3")
    assert code == 0 and "PROCFLOW_STATUS=done" in out
    code, out, _ = cli(capsys, "replay", "q1", *b)
    assert code == 0 and "accepted" in out


def test_replay_cli_exits_1_for_an_unaccepted_run(ai_root, write_reg, tmp_path, capsys):
    runs = tmp_path / "runs"
    b = base(ai_root, runs)
    b2 = ["--runs-root", str(runs), "--sop-root", str(ai_root)]
    write_reg(ai_root, services={})
    cli(capsys, "run", "AI-CHAIN", *b, "--run-id", "r1", "--param", "topic=t", "--set", "steps.summarize.target=session")
    cli(capsys, "queue", "--runs-root", str(runs), "--claim", "--worker", "w")
    cli(capsys, "submit", "r1", *b, "--step", "summarize", "--output", "summary=a proper summary", "--by", "w")
    code, out, _ = cli(capsys, "replay", "--all", *b2)
    assert code == 0 and "accepted 1" in out
    f = runs / "AI-CHAIN" / "r1" / "1" / "summarize" / "ai-attempt-1.json"
    rec = json.loads(f.read_text())
    rec["output"] = {"summary": "x"}
    f.write_text(json.dumps(rec))
    code, out, _ = cli(capsys, "replay", "--all", "--sop", "AI-CHAIN", *b2)
    assert code == 1 and "rejected" in out
    code, _, err = cli(capsys, "replay", *b2)
    assert code == 2 and "run id or --all" in err


# ---- scaffold ---------------------------------------------------------------------------------------------------
def test_scaffold_writes_templates_without_overwriting(ai_root, capsys):
    code, out, _ = cli(capsys, "scaffold", "AI-AGENT", "--sop-root", str(ai_root))
    assert code == 0 and out.count("would write") == 3
    impl = ai_root / "_impl" / "AI-AGENT"
    (impl / "schemas" / "report.json").write_text('{"type": "object", "title": "mine"}')          # exists already
    code, out, _ = cli(capsys, "scaffold", "AI-AGENT", "--sop-root", str(ai_root), "--write")
    assert code == 0 and "exists, kept" in out and out.count("wrote") == 2
    assert json.loads((impl / "schemas" / "report.json").read_text())["title"] == "mine"        # never overwritten
    assert (impl / "prompts" / "build.md").read_text().startswith("# Work ticket {{ticket}}")
    assert "PROCFLOW_OUTPUTS" in (impl / "steps" / "build.py").read_text()
    code, out, _ = cli(capsys, "scaffold", "AI-AGENT", "--sop-root", str(ai_root), "--json")
    assert sorted(json.loads(out)["files"]) == ["prompts/build.md", "schemas/report.json", "steps/build.py"]


# ---- the catalogue ----------------------------------------------------------------------------------------------
def test_catalog_build_list_show_impact_and_check_detect_a_removed_resource(ai_root, write_reg, tmp_path, capsys, monkeypatch):
    b = {"provider": "fake", "family": "remote", "data_classes": ["public", "internal"]}
    write_reg(ai_root, services={"svc-a": b, "svc-b": dict(b, enabled=False, disabled_reason="paused")})
    # a procedure that names a service, a fallback and a tool explicitly
    (ai_root / "named.yaml").write_text(yaml.safe_dump({
        "sop": {"id": "NAMED", "name": "Named refs", "version": "1.0"}, "flow": {"start": "start"},
        "steps": [{"id": "start", "type": "start"},
                  {"id": "work", "type": "task", "mode": "agent", "kind": "llm", "action": "x", "outputs": ["o"],
                   "tools": ["search-tool"], "llm": {"service": "svc-a", "fallback": ["svc-b"]}},
                  {"id": "done", "type": "end", "outcome": "success"}],
        "transitions": [{"id": "T1", "from": "start", "to": "work"}, {"id": "T2", "from": "work", "to": "done"}]}))
    src = tmp_path / "tools.json"
    src.write_text(json.dumps({"tools": ["search-tool"]}))
    monkeypatch.setenv("PROCFLOW_CATALOG_SOURCES", str(src))
    out = tmp_path / "cat"
    args = ["--sop-root", str(ai_root), "--out", str(out)]
    code, o, _ = cli(capsys, "catalog", "build", *args)
    assert code == 0 and "catalogued" in o and (out / "catalog.json").exists()
    code, o, _ = cli(capsys, "catalog", "list", *args)
    assert "llm:svc-a" in o and "available" in o and "llm:svc-b" in o and "disabled" in o
    code, o, _ = cli(capsys, "catalog", "show", "tool:search-tool", *args)
    assert json.loads(o)["referenced_by"] == [{"sop": "NAMED", "step": "work"}]
    code, o, _ = cli(capsys, "catalog", "impact", "llm:svc-a", *args)
    assert "NAMED.work" in o
    code, o, _ = cli(capsys, "catalog", "check", *args)
    assert code == 0 and "[warning] llm:svc-b is disabled" in o                      # disabled: a warning only
    # the tool disappears from the source while a step still references it
    src.write_text(json.dumps({"tools": []}))
    code, o, _ = cli(capsys, "catalog", "check", *args)
    assert code == 1 and "tool:search-tool is missing but still referenced by NAMED.work" in o
    # ... and so does a registered service that was removed
    src.write_text(json.dumps({"tools": ["search-tool"]}))
    write_reg(ai_root, services={"svc-b": b})
    code, o, _ = cli(capsys, "catalog", "check", *args)
    assert code == 1 and "llm:svc-a is missing" in o
    code, o, _ = cli(capsys, "catalog", "build", *args)
    assert "~2" in o                                                  # a diff against the previous build
    assert any(p.name.startswith("diff_") for p in out.iterdir())


def test_catalog_without_a_tool_source_does_not_claim_a_tool_is_missing(ai_root, tmp_path, monkeypatch):
    monkeypatch.delenv("PROCFLOW_CATALOG_SOURCES", raising=False)
    cat = CAT.build(Repo(ai_root))
    assert not CAT.check(cat)[0]
    assert cat["resources"]["kind:llm"]["status"] == "available"
