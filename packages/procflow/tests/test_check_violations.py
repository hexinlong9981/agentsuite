"""One failing fixture per check class. Each test mutates a copy of fixtures/good and asserts the reason."""

import yaml

from procflow.check import run_check

CHILD = "backup-and-verify.yaml"
BASE = "approved-change.yaml"


def test_good_root_is_clean(good):
    from procflow.check import Checker
    assert [(t, e) for t, e in Checker(good).check_all() if e] == []


# ---- closed key set / required keys / reachability -------------------------------------
def test_unknown_step_key_rejected(lab):
    lab.edit("nightly-job.yaml", lambda d: lab.step(d, "report").update(bogus=1))
    assert lab.has(lab.msgs("NIGHTLY-JOB"), "report.bogus", "unknown key")


def test_unknown_top_level_and_sop_keys_rejected(lab):
    def f(d):
        d["extra"] = 1
        d["sop"]["owner_team"] = "x"
    lab.edit("nightly-job.yaml", f)
    m = lab.msgs("NIGHTLY-JOB")
    assert lab.has(m, "(top level).extra", "unknown") and lab.has(m, "sop.owner_team", "unknown")


def test_overrides_typo_gets_a_hint(lab):
    lab.edit(CHILD, lambda d: lab.step(d, "prepare").update(overrides="x"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "overrides", "override: <base-id>.<step-id>")


def test_missing_required_keys(lab):
    lab.edit("nightly-job.yaml", lambda d: d["sop"].pop("name"))
    assert lab.has(lab.msgs("NIGHTLY-JOB"), "sop.name is missing")


def test_unreachable_step(lab):
    lab.edit("nightly-job.yaml", lambda d: d["steps"].append({"id": "orphan", "type": "task", "mode": "auto", "action": "x"}))
    assert lab.has(lab.msgs("NIGHTLY-JOB"), "orphan is unreachable")


def test_bad_transition_endpoint_and_missing_end(lab):
    def f(d):
        d["transitions"].append({"id": "TX", "from": "report", "to": "nowhere"})
        d["steps"] = [s for s in d["steps"] if s["type"] != "end"]
        d["transitions"] = [t for t in d["transitions"] if t["to"] != "done"]
    lab.edit("nightly-job.yaml", f)
    m = lab.msgs("NIGHTLY-JOB")
    assert lab.has(m, "TX", "not a step") and lab.has(m, "at least one end step")


def test_unreadable_yaml_is_reported(lab):
    (lab.root / "broken.yaml").write_text("sop: [unclosed")
    assert any("cannot read as YAML" in e for t, errs in lab.all() for e in errs)


# ---- extends -------------------------------------------------------------------------------
def test_extends_base_missing(lab):
    lab.edit(CHILD, lambda d: d["sop"].update(extends="NOPE@1"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "base procedure NOPE does not exist")


def test_extends_base_not_abstract(lab):
    lab.edit(BASE, lambda d: d["sop"].update(abstract=False))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "is not abstract")


def test_extends_major_mismatch(lab):
    lab.edit(CHILD, lambda d: d["sop"].update(extends="APPROVED-CHANGE@2"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "major 2 does not match")


def test_extends_single_level_only(lab):
    lab.edit(BASE, lambda d: d["sop"].update(extends="NIGHTLY-JOB@1"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "inheritance is a single level")


def test_multiple_extends_rejected(lab):
    lab.edit(CHILD, lambda d: d["sop"].update(extends=["APPROVED-CHANGE@1", "NIGHTLY-JOB@1"]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "exactly one base")


def test_same_name_shadowing_requires_explicit_override(lab):
    lab.edit(CHILD, lambda d: lab.step(d, "prepare").pop("override"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "same-name shadowing is an error")


def test_override_target_missing_and_wrong_base(lab):
    lab.edit(CHILD, lambda d: lab.step(d, "prepare").update(override="APPROVED-CHANGE.ghost"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "override target APPROVED-CHANGE.ghost does not exist")
    lab.edit(CHILD, lambda d: lab.step(d, "prepare").update(override="OTHER-BASE.prepare"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "differs from extends")


def test_override_without_extends(lab):
    lab.edit("nightly-job.yaml", lambda d: lab.step(d, "report").update(override="APPROVED-CHANGE.prepare"))
    assert lab.has(lab.msgs("NIGHTLY-JOB"), "override may only be written on a procedure that extends")


def test_final_transition_cannot_be_overridden(lab):
    lab.edit(BASE, lambda d: next(t for t in d["transitions"] if t["id"] == "T4").update(final=True))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "transition T4 is final")


# ---- OOP / LSP -------------------------------------------------------------------------------
def test_implementation_gap_when_abstract_step_remains(lab):
    def f(d):
        d["steps"] = [s for s in d["steps"] if s["id"] != "prepare"]
    lab.edit(CHILD, f)
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "implementation gap", "prepare")


def test_overriding_a_final_step_is_refused(lab):
    lab.edit(CHILD, lambda d: d["steps"].append({"id": "approve", "type": "task", "override": "APPROVED-CHANGE.approve",
                                                 "mode": "manual", "action": "Approve differently"}))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "base step is final")


def test_overriding_a_private_step_is_refused(lab):
    def f(d):
        lab.step(d, "approve").pop("final")
    lab.edit(BASE, f)
    lab.edit(CHILD, lambda d: d["steps"].append({"id": "approve", "type": "task", "override": "APPROVED-CHANGE.approve",
                                                 "mode": "manual", "action": "Approve differently", "evidence": ["approval-record"],
                                                 "outputs": ["decision"], "raises": ["ApprovalDenied"]}))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "base step is private")


def _open_approve(lab, child_step):
    """Make base `approve` overridable, then override it in the child with ``child_step``."""
    def f(d):
        s = lab.step(d, "approve")
        s.pop("final")
        s["visibility"] = "protected"
    lab.edit(BASE, f)
    lab.edit(CHILD, lambda d: d["steps"].append({"id": "approve", "type": "task", "override": "APPROVED-CHANGE.approve",
                                                 "action": "Approve differently", **child_step}))


def test_lsp_human_step_cannot_become_automated(lab):
    _open_approve(lab, {"mode": "auto", "evidence": ["approval-record"], "outputs": ["decision"], "raises": ["ApprovalDenied"]})
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "LSP", "human step cannot be overridden to kind code")


def test_lsp_cannot_add_raises(lab):
    lab.edit(CHILD, lambda d: lab.step(d, "prepare").update(raises=["Oops"]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "LSP", "raises ['Oops']", "base step")


def test_lsp_cannot_drop_evidence(lab):
    lab.edit(BASE, lambda d: lab.step(d, "prepare").update(evidence=["plan-doc"]))
    # an unspecified evidence list is inherited, so the child must explicitly replace it to drop it
    assert not lab.has(lab.msgs("BACKUP-AND-VERIFY"), "evidence")
    lab.edit(CHILD, lambda d: lab.step(d, "prepare").update(evidence=["something-else"]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "LSP", "base evidence ['plan-doc'] was dropped")


def test_lsp_cannot_drop_outputs(lab):
    lab.edit(CHILD, lambda d: lab.step(d, "prepare").update(outputs=[]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "LSP", "base outputs ['plan'] are no longer produced")


def test_lsp_cannot_reduce_human_only(lab):
    lab.edit(CHILD, lambda d: d["sop"].update(human_only=[]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "LSP", "human_only ['approve'] of base")


def test_interface_compatibility_parent_child(lab):
    def f(d):
        d["interface"] = {"inputs": {"other": {"type": "string", "required": True}}, "outputs": {}, "raises": ["Extra"]}
    lab.edit(CHILD, f)
    m = lab.msgs("BACKUP-AND-VERIFY")
    assert lab.has(m, "input change_ref is no longer accepted")
    assert lab.has(m, "input other is optional (or absent) in the parent but required here")
    assert lab.has(m, "output approved is no longer produced")
    assert lab.has(m, "raises ['Extra'] not declared by the parent")


def test_super_cannot_call_an_abstract_base_step(lab):
    lab.edit(CHILD, lambda d: lab.step(d, "prepare").update(super="before"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "super cannot call the abstract base step")


def test_super_requires_override(lab):
    lab.edit(CHILD, lambda d: lab.step(d, "verify").update(super="after"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "super may only be written on an overriding step")


# ---- implements ----------------------------------------------------------------------------
def test_implements_interface_missing(lab):
    lab.edit(CHILD, lambda d: d["sop"].update(implements=["IF-GHOST@1"]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "interface IF-GHOST not found")


def test_implements_major_mismatch(lab):
    lab.edit(CHILD, lambda d: d["sop"].update(implements=["IF-APPROVAL@2"]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "major does not match")


def test_implements_required_input_and_output_contract(lab):
    def f(d):
        d["inputs"]["extra_in"] = {"type": "string", "required": True}
        d["outputs"]["extra_out"] = {"type": "string", "required": True}
    lab.edit("_interfaces/IF-APPROVAL.yaml", f)
    m = lab.msgs("BACKUP-AND-VERIFY")
    assert lab.has(m, "required input extra_in of the interface is not accepted")
    assert lab.has(m, "output extra_out of the interface is not produced")


def test_implements_cannot_raise_undeclared_exceptions(lab):
    lab.edit("_interfaces/IF-APPROVAL.yaml", lambda d: d.update(raises=[]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "raises ['ApprovalDenied'] not declared by the interface")


def test_implements_safety_requires_a_human_step(lab):
    lab.edit("_interfaces/IF-APPROVAL.yaml", lambda d: d["safety"].update(human_steps_required=["Countersign"]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "safety", "Countersign")


def test_cross_interface_same_name_type_conflict(lab):
    lab.write("_interfaces/IF-OTHER.yaml", {"id": "IF-OTHER", "version": "1.0", "name": "other",
                                            "inputs": {"change_ref": {"type": "int", "required": True}},
                                            "outputs": {}})
    lab.edit(CHILD, lambda d: d["sop"].update(implements=["IF-APPROVAL@1", "IF-OTHER@1"]))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "implements conflict", "inputs.change_ref")


def test_interface_file_is_validated(lab):
    lab.write("_interfaces/IF-BAD.yaml", {"id": "IF-WRONG", "version": "x", "name": "n", "inputs": {}, "outputs": {}, "nope": 1})
    errs = [e for t, es in lab.all() if t.endswith("IF-BAD.yaml") for e in es]
    assert any("differs from id" in e for e in errs) and any("version must be major.minor" in e for e in errs)
    assert any("(interface).nope is an unknown key" in e for e in errs)


# ---- control -------------------------------------------------------------------------------
def test_loop_requires_bound_and_valid_body(lab):
    def f(d):
        lp = lab.step(d, "each-target")
        lp.pop("max_iterations")
        lp["body"] = ["ghost"]
    lab.edit("nightly-job.yaml", f)
    m = lab.msgs("NIGHTLY-JOB")
    assert lab.has(m, "requires max_iterations") and lab.has(m, "loop body step ghost does not exist")


def test_loop_iteration_hard_cap(lab):
    lab.edit("nightly-job.yaml", lambda d: lab.step(d, "each-target").update(max_iterations=999))
    assert lab.has(lab.msgs("NIGHTLY-JOB"), "hard cap")


def test_finally_step_must_exist(lab):
    lab.edit("nightly-job.yaml", lambda d: d.update({"finally": ["cleanup", "ghost"]}))
    assert lab.has(lab.msgs("NIGHTLY-JOB"), "finally step ghost does not exist")


def test_call_target_missing(lab):
    lab.edit("release-pipeline.yaml", lambda d: lab.step(d, "gated-change")["dispatch"][0].update(sop="NO-SUCH"))
    lab.edit("_interfaces/IF-APPROVAL.yaml", lambda d: d.update(default_impl=None))
    assert lab.has(lab.msgs("RELEASE-PIPELINE"), "call target NO-SUCH does not exist")


def test_call_target_abstract(lab):
    lab.edit("release-pipeline.yaml", lambda d: lab.step(d, "gated-change")["dispatch"][0].update(sop="APPROVED-CHANGE"))
    assert lab.has(lab.msgs("RELEASE-PIPELINE"), "call target APPROVED-CHANGE is abstract")


def test_call_dispatch_target_must_implement_the_interface(lab):
    lab.edit("release-pipeline.yaml", lambda d: lab.step(d, "gated-change")["dispatch"][0].update(sop="NIGHTLY-JOB"))
    assert lab.has(lab.msgs("RELEASE-PIPELINE"), "dispatch target NIGHTLY-JOB does not implement IF-APPROVAL@1")


def test_call_needs_dispatch_or_default_impl(lab):
    lab.edit("release-pipeline.yaml", lambda d: lab.step(d, "gated-change").pop("dispatch"))
    lab.edit("_interfaces/IF-APPROVAL.yaml", lambda d: d.update(default_impl=None))
    assert lab.has(lab.msgs("RELEASE-PIPELINE"), "no dispatch and no default_impl")


def test_call_with_returns_catch_mapping(lab):
    def f(d):
        c = lab.step(d, "gated-change")
        c["with"] = {"bogus": "change_ref"}
        c["returns"] = {"nothing": "approved_flag"}
        c["catch"] = {"NeverRaised": "rejected"}
    lab.edit("release-pipeline.yaml", f)
    m = lab.msgs("RELEASE-PIPELINE")
    assert lab.has(m, "required input change_ref of IF-APPROVAL@1 is not passed in with")
    assert lab.has(m, "with.bogus is not an input") and lab.has(m, "returns nothing is not an output")
    assert lab.has(m, "catch NeverRaised is not an exception")


def test_call_catch_target_must_exist(lab):
    lab.edit("release-pipeline.yaml", lambda d: lab.step(d, "gated-change").update(catch={"ApprovalDenied": "ghost"}))
    assert lab.has(lab.msgs("RELEASE-PIPELINE"), "catch ApprovalDenied target ghost does not exist")


def test_call_mapped_types(lab):
    def f(d):
        d["context"]["approved_flag"]["type"] = "string"
        lab.step(d, "gated-change")["with"] = {"change_ref": 5}
    lab.edit("release-pipeline.yaml", f)
    m = lab.msgs("RELEASE-PIPELINE")
    assert lab.has(m, "returns approved (child output type bool) cannot go into approved_flag (type string)")


def test_call_with_config_is_narrowing_keys_only(lab):
    lab.edit("release-pipeline.yaml", lambda d: lab.step(d, "gated-change").update(
        with_config={"env": "prod", "limits.max_depth": 9}))
    m = lab.msgs("RELEASE-PIPELINE")
    assert lab.has(m, "with_config: env cannot be passed to a child")
    assert lab.has(m, "limits.max_depth 9 exceeds the hard cap")


def test_call_to_named_procedure_needs_reason(lab):
    def f(d):
        c = lab.step(d, "gated-change")
        for k in ("interface", "dispatch"):
            c.pop(k)
        c["sop"] = "BACKUP-AND-VERIFY"
    lab.edit("release-pipeline.yaml", f)
    assert lab.has(lab.msgs("RELEASE-PIPELINE"), "requires reason")


def _call_proc(sid, target, **extra):
    call = {"id": "c", "type": "call", "sop": target, "reason": "fixture", **extra}
    return {"sop": {"id": sid, "name": sid, "version": "1.0"}, "flow": {"start": "s"},
            "steps": [{"id": "s", "type": "start"}, call, {"id": "e", "type": "end"}],
            "transitions": [{"id": "T1", "from": "s", "to": "c"}, {"id": "T2", "from": "c", "to": "e"}]}


def test_call_cycle_requires_allow_recursion_and_max_depth_everywhere(lab):
    lab.write("cyc-a.yaml", _call_proc("CYC-A", "CYC-B"))
    lab.write("cyc-b.yaml", _call_proc("CYC-B", "CYC-A"))
    assert lab.has(lab.msgs("CYC-A"), "call cycle CYC-A -> CYC-B -> CYC-A", "allow_recursion")
    # only one of the two calls opted in: still refused
    lab.write("cyc-a.yaml", _call_proc("CYC-A", "CYC-B", allow_recursion=True, max_depth=3))
    assert lab.has(lab.msgs("CYC-A"), "call cycle", "CYC-B.c")
    # every call on the cycle opted in: accepted
    lab.write("cyc-b.yaml", _call_proc("CYC-B", "CYC-A", allow_recursion=True, max_depth=3))
    assert not lab.has(lab.msgs("CYC-A"), "call cycle")


def test_recursion_flag_needs_max_depth(lab):
    lab.write("rec.yaml", _call_proc("REC", "REC", allow_recursion=True))
    assert lab.has(lab.msgs("REC"), "allow_recursion requires max_depth")


def test_non_cyclic_call_chain_depth_cap(lab):
    for i in range(7):
        lab.write(f"chain-{i}.yaml", _call_proc(f"CH-{i}", f"CH-{i + 1}") if i < 6 else
                  {"sop": {"id": "CH-6", "name": "leaf", "version": "1.0"}, "flow": {"start": "s"},
                   "steps": [{"id": "s", "type": "start"}, {"id": "e", "type": "end"}],
                   "transitions": [{"id": "T1", "from": "s", "to": "e"}]})
    assert lab.has(lab.msgs("CH-0"), "call chain depth 6 exceeds the cap 5")
    assert not lab.has(lab.msgs("CH-1"), "call chain depth")


# ---- expressions -----------------------------------------------------------------------------
def test_guard_reference_must_be_declared(lab):
    lab.edit(BASE, lambda d: next(t for t in d["transitions"] if t["id"] == "T3").update(guard="ghost.value == 1"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "transition T3", "reference ghost.value is not declared")


def test_decide_by_and_dispatch_and_with_references(lab):
    def f(d):
        c = lab.step(d, "gated-change")
        c["dispatch"][0]["when"] = "ghost == 1"
        c["with"] = {"change_ref": "phantom"}
    lab.edit("release-pipeline.yaml", f)
    m = lab.msgs("RELEASE-PIPELINE")
    assert lab.has(m, "dispatch[0].when", "ghost") and lab.has(m, "with.change_ref", "phantom")


def test_decide_by_must_name_a_step(lab):
    def f(d):
        d["steps"].insert(2, {"id": "pick", "type": "exclusive_choice", "decide_by": "report.summary"})
    lab.edit("nightly-job.yaml", f)
    assert not lab.has(lab.msgs("NIGHTLY-JOB"), "decide_by must be <step-id>")
    lab.edit("nightly-job.yaml", lambda d: lab.step(d, "pick").update(decide_by="targets.x"))
    assert lab.has(lab.msgs("NIGHTLY-JOB"), "pick.decide_by must be <step-id>")


def test_config_reference_must_exist(lab):
    def f(d):
        d["interface"] = {"inputs": {"depth": {"type": "int", "default": "${config.limits.nope}"}}}
    lab.edit("nightly-job.yaml", f)
    assert lab.has(lab.msgs("NIGHTLY-JOB"), "config key limits.nope does not exist")
    lab.edit("nightly-job.yaml", lambda d: d["interface"]["inputs"]["depth"].update(default="${config.limits.max_depth}"))
    assert not lab.has(lab.msgs("NIGHTLY-JOB"), "config key")


def test_guard_config_reference_must_exist(lab):
    lab.edit(BASE, lambda d: next(t for t in d["transitions"] if t["id"] == "T3").update(guard="config.limits.ghost > 1"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "config key limits.ghost does not exist")


def test_guard_function_calls_are_rejected(lab):
    lab.edit(BASE, lambda d: next(t for t in d["transitions"] if t["id"] == "T3").update(guard="len(x) > 1"))
    assert lab.has(lab.msgs("APPROVED-CHANGE"), "unreadable guard expression", "function calls are not allowed")


# ---- human fixity ----------------------------------------------------------------------------
def test_manual_step_cannot_be_made_automated_kind(lab):
    lab.edit(BASE, lambda d: lab.step(d, "approve").update(kind="code"))
    assert lab.has(lab.msgs("APPROVED-CHANGE"), "manual step cannot be kind code", "human steps are fixed")


def test_human_only_phrase_cannot_be_done_by_an_automated_step(lab):
    lab.edit(CHILD, lambda d: lab.step(d, "verify").update(kind="code", action="approve the copy automatically"))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "human_only 'approve' is performed by a kind code step")


def test_kind_mode_conflict_and_llm_agent_blocks(lab):
    lab.edit("nightly-job.yaml", lambda d: lab.step(d, "run-one").update(kind="llm", agent={"difficulty": "low"}))
    m = lab.msgs("NIGHTLY-JOB")
    assert lab.has(m, "kind llm conflicts with mode auto") and lab.has(m, "kind llm step cannot have agent:")


# ---- runtime block / enum / abstract-run ------------------------------------------------------
def test_definition_runtime_block_is_schema_checked(lab):
    lab.edit("nightly-job.yaml", lambda d: d.update(runtime={"limits": {"max_rounds": 99, "max_active_runs": 2}, "bogus": 1}))
    m = lab.msgs("NIGHTLY-JOB")
    assert lab.has(m, "limits.max_rounds 99 exceeds the hard cap 5")
    assert lab.has(m, "limits.max_active_runs cannot be written in a definition")
    assert lab.has(m, "bogus is an unknown key")


def test_runtime_locked_must_be_lockable_and_known(lab):
    lab.edit("nightly-job.yaml", lambda d: d.update(runtime={"locked": ["limits.max_active_runs", "no.such.key"]}))
    m = lab.msgs("NIGHTLY-JOB")
    assert lab.has(m, "limits.max_active_runs cannot be locked") and lab.has(m, "no.such.key is unknown")


def test_credential_like_value_in_definition_runtime_is_rejected_without_echo(lab):
    secret = "Zq9" * 14
    lab.edit("nightly-job.yaml", lambda d: d.update(runtime={"targets": {"db": secret}}))
    m = lab.msgs("NIGHTLY-JOB")
    assert lab.has(m, "targets.db must not contain a credential-like value")
    assert not any(secret in x for x in m)


def test_check_run_refuses_abstract(good):
    f = run_check(good, ["APPROVED-CHANGE"], run=True)
    assert any("cannot be run directly" in x.message for x in f)
    assert not [x for x in run_check(good, ["BACKUP-AND-VERIFY"], run=True)]


def test_structured_findings(lab):
    lab.edit("nightly-job.yaml", lambda d: lab.step(d, "report").update(bogus=1))
    f = run_check(lab.repo())
    assert f and all(x.severity == "error" and x.target and x.message for x in f)
    assert f[0].to_dict().keys() == {"target", "severity", "message"}
