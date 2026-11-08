"""Target routing (granularity, then layer), filters, fallback, local-only, registry validation."""

import pytest

from procflow import registry as REG
from procflow import routing as RT
from procflow.engine import Refused, create_run
from procflow import Repo
from procflow.check import run_check
from procflow.jsonschema_lite import validate


def node(**blk):
    return {"id": "s1", "type": "task", "kind": "llm", "def": {"llm": blk}, "outputs": ["x"]}


def vals(**kv):
    """config values in the resolved form: key -> (value, layer)"""
    return {k.replace("__", "."): {"value": v[0], "layer": v[1], "source": f"L{v[1]} test"} for k, v in kv.items()}


@pytest.fixture
def reg(tmp_path, monkeypatch, write_reg):
    monkeypatch.setenv("PROCFLOW_ALLOW_FAKE_PROVIDERS", "1")
    base = {"provider": "fake", "family": "remote", "data_classes": ["public", "internal"]}
    write_reg(tmp_path, services={
        "svc-a": dict(base, classes=["cheap"], cost_per_call=1, models=["m1", "m2"], default_model="m1"),
        "svc-b": dict(base, classes=["cheap"], cost_per_call=5),
        "svc-c": dict(base, classes=["strong"], cost_per_call=9),
        "svc-off": dict(base, enabled=False, disabled_reason="maintenance"),
        "svc-local": {"provider": "fake", "family": "local", "data_classes": ["public", "internal", "restricted"]},
    }, classes={"default": "internal", "classes": {"public": {}, "internal": {}, "restricted": {"local_only": True},
                                                    "forbidden": {"never": True}}},
        routing={"llm": {"summarize": {"low": {"service": "svc-b"}, "high": {"service": "svc-c", "fallback": ["svc-a"]}}}})
    r, errs = REG.load(tmp_path / "_config")
    assert errs == []
    return r


def test_granularity_beats_layer_step_then_sop_then_routing_then_builtin(reg):
    n = node(task_class="summarize", difficulty="low")
    # 3 routing table
    r = RT.resolve(n, {}, reg)
    assert (r["granularity"], r["target"]) == ("routing", "svc-b")
    # 2 sop default beats the routing table even from a low layer
    r = RT.resolve(n, vals(llm__default=("svc-c", 2)), reg)
    assert (r["granularity"], r["target"]) == ("sop", "svc-c")
    # 1 step beats the sop default even when the sop default sits in a higher layer
    r = RT.resolve(n, vals(llm__default=("svc-c", 7), steps__s1__target=("svc-a", 2)), reg)
    assert (r["granularity"], r["target"]) == ("step", "svc-a")
    # 4 nothing declared and no task class: the built-in session target
    r = RT.resolve(node(), {}, reg)
    assert (r["granularity"], r["target"], r["provider"]) == ("builtin", "session", "session")


def test_within_a_granularity_the_higher_layer_wins(reg):
    # the definition's own service is layer 3; a config key at L2 loses to it, one at L4+ wins
    n = node(service="svc-a")
    assert RT.resolve(n, vals(steps__s1__target=("svc-b", 2)), reg)["target"] == "svc-a"
    assert RT.resolve(n, vals(steps__s1__target=("svc-b", 4)), reg)["target"] == "svc-b"
    assert RT.resolve(n, vals(steps__s1__target=("svc-b", 7)), reg)["layer"] == "L7"


def test_class_spec_orders_by_routing_table_then_cost_and_fallback_is_the_rest(reg):
    r = RT.resolve(node(), vals(llm__default=("class=cheap", 3)), reg)
    assert r["target"] == "svc-b" and [f["target"] for f in r["fallback"]] == ["svc-a"]   # table order first, then cost
    r = RT.resolve(node(task_class="summarize", difficulty="high"), {}, reg)
    assert r["target"] == "svc-c" and r["fallback"][0]["target"] == "svc-a"             # the spec's own fallback


def test_filters_allow_list_model_disabled_and_unattended(reg):
    n = node(service="svc-b", fallback=["svc-off", "svc-a/zzz", "svc-a"])
    r = RT.resolve(n, {}, reg)
    why = {c["id"]: c["why"] for c in r["candidates"]}
    assert "disabled" in why["llm:svc-off"] and "no model zzz" in why["llm:svc-a/zzz"]
    assert [f["target"] for f in r["fallback"]] == ["svc-a"]
    r = RT.resolve(n, vals(ai__allowed=(["svc-a"], 4)), reg)           # the allow-list drops svc-b, so svc-a leads
    assert r["target"] == "svc-a" and "outside ai.allowed" in {c["id"]: c["why"] for c in r["candidates"]}["llm:svc-b"]
    r = RT.resolve(n, vals(run__unattended=(True, 4), ai__unattended_allowed=(["svc-a"], 4)), reg)
    assert r["target"] == "svc-a" and "unattended" in {c["id"]: c["why"] for c in r["candidates"]}["llm:svc-b"]
    r = RT.resolve(n, vals(run__unattended=(True, 4)), reg)             # unattended with no list: nothing direct passes
    assert r["target"] is None


def test_local_only_class_never_routes_outside_and_falls_to_a_person(reg, tmp_path):
    n = node(data_class="restricted", service="svc-a", fallback=["svc-b"])
    r = RT.resolve(n, {}, reg)
    assert r["target"] is None and r["to_human"]
    assert all("local-only" in c["why"] for c in r["candidates"])
    assert RT.findings({"nodes": [n]}, {}, reg) == []                   # a human fallback is not a finding
    r = RT.resolve(node(data_class="restricted", service="svc-a", fallback=["svc-local"]), {}, reg)
    assert r["target"] == "svc-local"                                   # a local target may take it
    assert "to_human" not in r


def test_never_class_and_unknown_class_route_nowhere_and_are_findings(reg):
    for dc in ("forbidden", "nonexistent"):
        n = node(data_class=dc, service="svc-a")
        assert RT.resolve(n, {}, reg)["target"] is None
        assert RT.findings({"nodes": [n]}, {}, reg)


def test_registry_validation_is_fail_closed(tmp_path, monkeypatch, write_reg):
    monkeypatch.delenv("PROCFLOW_ALLOW_FAKE_PROVIDERS", raising=False)
    write_reg(tmp_path, services={
        "f": {"provider": "fake"},                                      # fake needs the env flag
        "session": {"provider": "offload"},                              # reserved name
        "x": {"provider": "offload", "family": "remote", "data_classes": ["restricted"]},   # local-only class on a remote target
        "y": {"provider": "offload", "endpoint": "z"},                  # unknown key
        "h": {"provider": "offload", "description": "reach " + "10." + "1.2.3"},   # IP-like value, never echoed
    }, classes={"default": "internal", "classes": {"internal": {}, "restricted": {"local_only": True}}},
        routing={"llm": {"t": {"low": {"service": "ghost"}}}})
    _, errs = REG.load(tmp_path / "_config")
    text = " | ".join(errs)
    assert "only allowed when PROCFLOW_ALLOW_FAKE_PROVIDERS=1" in text and "reserved" in text
    assert "local-only" in text and "unknown key endpoint" in text and "IP address" in text
    assert "ghost" in text and ("10." + "1.2.3") not in text


def test_check_run_and_run_creation_refuse_an_unroutable_step(ai_root, write_reg):
    write_reg(ai_root, services={"only": {"provider": "disabled"}},
              routing={"llm": {"summarize": {"low": {"service": "only"}}}})
    msgs = [f.message for f in run_check(Repo(ai_root), ["AI-CHAIN"], run=True)]
    assert any("no execution target" in m and "disabled" in m for m in msgs)
    with pytest.raises(Refused, match="no execution target"):
        create_run(Repo(ai_root), "AI-CHAIN", runs_root=ai_root.parent / "runs", params=["topic=t"])
    assert not (ai_root.parent / "runs").exists() or not list((ai_root.parent / "runs").glob("*/*"))


def test_lite_schema_checker_matches_the_library_on_common_keywords():
    sch = {"type": "object", "required": ["a"], "additionalProperties": False,
           "properties": {"a": {"type": "integer", "minimum": 1}, "b": {"enum": ["x", "y"]},
                          "c": {"type": "array", "items": {"type": "string"}, "minItems": 1}}}
    good, bad = {"a": 2, "b": "x", "c": ["s"]}, {"a": 0, "b": "z", "c": [], "d": 1}
    assert validate(good, sch, prefer_lib=False) == [] and validate(good, sch) == []
    assert len(validate(bad, sch, prefer_lib=False)) >= 4 and len(validate(bad, sch)) >= 4
