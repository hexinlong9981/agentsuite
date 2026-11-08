import json

from procflow import compile_sop, mermaid


def ids(ir):
    return [n.id for n in ir.nodes]


def test_without_extension_keys_nodes_and_transitions_are_one_to_one(good):
    raw = good.definition("NIGHTLY-JOB")
    ir, errs = compile_sop("NIGHTLY-JOB", good)
    assert errs == []
    assert ids(ir) == [s["id"] for s in raw["steps"]]
    assert [(t.id, t.src, t.dst) for t in ir.transitions] == [(t["id"], t["from"], t["to"]) for t in raw["transitions"]]
    assert ir.merge["parent_id"] is None and not ir.sop.abstract


def test_interface_and_context_are_synthesized_when_undeclared(good):
    ir, _ = compile_sop("NIGHTLY-JOB", good)
    assert ir.interface.origin == "derived"
    assert ir.interface.inputs["targets"] == {"type": "any", "required": True, "from": "flow.inputs.targets"}
    assert set(ir.interface.outputs) == {"summary"}
    assert ir.context["result"] == {"type": "any", "origin": "derived"}
    assert ir.context["summary"]["origin"] == "derived"


def test_declared_interface_and_context_are_kept(good):
    ir, _ = compile_sop("RELEASE-PIPELINE", good)
    assert ir.interface.origin == "declared"
    assert ir.context["approved_flag"] == {"type": "bool", "init": False, "origin": "declared"}


def test_extends_composition_override_insert_super(good):
    ir, errs = compile_sop("BACKUP-AND-VERIFY", good)
    assert errs == []
    # base order kept; super spliced before the override; child-only step inserted after the step that precedes it
    assert ids(ir) == ["start", "prepare", "approve", "apply-super", "apply", "verify", "done"]
    assert ir.node("prepare").origin == "BACKUP-AND-VERIFY" and ir.node("prepare").override == "APPROVED-CHANGE.prepare"
    assert not ir.node("prepare").abstract                      # override replaced the abstract step
    assert ir.node("approve").origin == "APPROVED-CHANGE" and ir.node("approve").kind == "human"
    sup = ir.node("apply-super")
    assert sup.super_of == "apply" and sup.origin == "APPROVED-CHANGE" and sup.hook is True
    edges = {(t.src, t.dst) for t in ir.transitions}
    assert ("approve", "apply-super") in edges and ("apply-super", "apply") in edges   # entry retargeted
    assert ("apply", "done") not in edges and {("apply", "verify"), ("verify", "done")} <= edges  # T4 replaced
    assert ir.sop.implements == ["IF-APPROVAL@1"] and not ir.sop.abstract
    assert [o for o, _ in ir.runtime_layers] == []              # no runtime blocks declared


def test_abstract_base_reports_abstract_steps(good):
    ir, _ = compile_sop("APPROVED-CHANGE", good)
    assert ir.sop.abstract and ir.sop.declared_abstract and ir.sop.abstract_steps == ["prepare"]
    assert ir.node("apply").visibility == "protected" and ir.node("approve").visibility == "private"


def test_kind_is_derived_from_mode(lab):
    def f(d):
        lab.step(d, "run-one")["mode"] = "agent"
        lab.step(d, "report")["mode"] = "agent"
        lab.step(d, "report")["llm"] = {"difficulty": "low"}
    lab.edit("nightly-job.yaml", f)
    ir, _ = compile_sop("NIGHTLY-JOB", lab.repo())
    assert ir.node("run-one").kind == "agent" and ir.node("report").kind == "llm"


def test_interface_call_inlines_the_contract(good):
    ir, _ = compile_sop("RELEASE-PIPELINE", good)
    n = ir.node("gated-change")
    assert n.interface_spec["id"] == "IF-APPROVAL" and "change_ref" in n.interface_spec["inputs"]
    assert [(c.step, c.interface, c.dispatch) for c in ir.calls] == [("gated-change", "IF-APPROVAL@1", ["BACKUP-AND-VERIFY"])]


def test_ir_is_json_serialisable_and_carries_hash(good):
    ir, _ = compile_sop("BACKUP-AND-VERIFY", good)
    d = json.loads(json.dumps(ir.to_dict()))
    assert d["ir_version"] == 1 and d["sop"]["sha256"] and d["nodes"][0]["def"]["id"] == "start"
    assert set(d) >= {"sop", "interface", "context", "runtime_layers", "start", "execution", "nodes", "transitions", "calls"}


def test_mermaid_of_compiled_ir(good):
    ir, _ = compile_sop("NIGHTLY-JOB", good)
    assert mermaid(ir).startswith("flowchart TD")
