import pytest

from procflow import compile_sop
from procflow import config as cfg
from procflow.errors import ConfigError


def _ir(repo, sid="NIGHTLY-JOB"):
    return compile_sop(sid, repo)[0]


def ip():
    return ".".join(["10", "1", "2", "3"])          # built at runtime: no address literal in the source


def val(res, key):
    return res["values"][key]


def test_seven_layers_merge_with_sources(good, tmp_path):
    ir = _ir(good)
    cfg_file = tmp_path / "run.yaml"
    cfg_file.write_text("limits: {parallel: 2}\nparams: {targets: [a, b]}\n")
    res = cfg.resolve(ir, good, env="staging", config_file=cfg_file, sets=["limits.max_iterations=7"], params=None)
    assert val(res, "limits.max_depth") == {"value": 2, "source": "L1 default", "layer": 1}
    assert val(res, "retry.code")["layer"] == 4 and val(res, "retry.code")["value"] == 2      # L4 per-procedure
    assert val(res, "timeout.step")["layer"] == 5 and val(res, "timeout.step")["value"] == "1h"   # L5 env block
    assert val(res, "limits.parallel")["layer"] == 6
    assert val(res, "limits.max_iterations") == {"value": 7, "source": "L7 --set", "layer": 7}   # L7 beats L2's 50
    assert res["inputs"]["targets"]["value"] == ["a", "b"]
    assert [f["layer"] for f in res["files"]] == ["L2", "L4", "L6"] and all(len(f["blob_sha"]) == 40 for f in res["files"])


def test_layer3_comes_from_definition_and_loses_to_later_layers(good):
    ir = _ir(good, "BACKUP-AND-VERIFY")
    # abstract-free child: flow.execution.max_rounds (2) is inherited from the base as L3
    res = cfg.resolve(ir, good, params=["change_ref=abc"])
    assert val(res, "limits.max_rounds") == {"value": 2, "source": "L3 flow.execution.max_rounds", "layer": 3}
    assert val(res, "timeout.step")["value"] == "4h"
    res = cfg.resolve(ir, good, sets=["limits.max_rounds=3"], params=["change_ref=abc"])
    assert val(res, "limits.max_rounds")["layer"] == 7


def test_abstract_procedure_has_no_config(good):
    with pytest.raises(ConfigError) as e:
        cfg.resolve(_ir(good, "APPROVED-CHANGE"), good)
    assert "abstract" in str(e.value)


def test_hard_cap_and_minimum(good):
    ir = _ir(good)
    with pytest.raises(ConfigError) as e:
        cfg.resolve(ir, good, sets=["limits.max_depth=6", "retry.code=-1", "timeout.step=25h"])
    msg = str(e.value)
    assert "limits.max_depth 6 exceeds the hard cap 5" in msg and "below the minimum" in msg and "timeout.step 25h exceeds" in msg


def test_layer2_caps_can_only_narrow(good):
    # fixtures/_config/runtime.yaml caps limits.max_rounds at 4 (the hard cap is 5)
    with pytest.raises(ConfigError) as e:
        cfg.resolve(_ir(good), good, sets=["limits.max_rounds=5"])
    assert "exceeds the L2 cap 4" in str(e.value)
    assert val(cfg.resolve(_ir(good), good, sets=["limits.max_rounds=4"]), "limits.max_rounds")["value"] == 4


def test_locked_keys_cannot_be_changed_by_lower_layers(lab):
    lab.edit("nightly-job.yaml", lambda d: d.update(runtime={"limits": {"parallel": 3}, "locked": ["limits.parallel"]}))
    repo = lab.repo()
    ir = compile_sop("NIGHTLY-JOB", repo)[0]
    assert val(cfg.resolve(ir, repo), "limits.parallel")["value"] == 3
    with pytest.raises(ConfigError) as e:
        cfg.resolve(ir, repo, sets=["limits.parallel=2"])
    assert "limits.parallel is locked by L3 runtime:" in str(e.value)
    assert val(cfg.resolve(ir, repo, sets=["limits.parallel=3"]), "limits.parallel")["value"] == 3   # same value is fine


def test_child_cannot_change_a_key_its_base_locked(lab):
    lab.edit("approved-change.yaml", lambda d: d.update(runtime={"limits": {"max_depth": 1}, "locked": ["limits.max_depth"]}))
    lab.edit("backup-and-verify.yaml", lambda d: d.update(runtime={"limits": {"max_depth": 2}}))
    assert lab.has(lab.msgs("BACKUP-AND-VERIFY"), "limits.max_depth is locked by")


def test_keys_are_restricted_to_their_writer_layers(good):
    with pytest.raises(ConfigError) as e:
        cfg.resolve(_ir(good), good, sets=["limits.max_active_runs=2"])
    assert "cannot be written in this layer" in str(e.value)


def test_unknown_key_and_unknown_env(good, tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("limits: {nope: 1}\n")
    with pytest.raises(ConfigError) as e:
        cfg.resolve(_ir(good), good, config_file=f, sets=["bogus.key=1"], env="mars")
    m = str(e.value)
    assert "limits.nope is an unknown key" in m and "bogus.key is an unknown key" in m and "env is not one of" in m


def test_set_keys_narrow_but_never_widen(good):
    res = cfg.resolve(_ir(good), good, sets=["step_kinds.allowed=[code, human]"])
    assert val(res, "step_kinds.allowed")["value"] == ["code", "human"]
    with pytest.raises(ConfigError) as e:
        cfg.resolve(_ir(good), good, sets=["step_kinds.allowed=[code, shell]"])
    assert "cannot be widened" in str(e.value)


@pytest.mark.parametrize("make,reason", [
    (lambda: ip(), "IP address"),
    (lambda: "db://" + "x" + "@" + "svc" + ":5432/db", "connection string"),
    (lambda: "svc." + "prod." + "example.invalid", "hostname"),
    (lambda: "A1b2" * 10, "credential-like value"),
    (lambda: "my_api_" + "key_value", "credential-like value"),
])
def test_credential_ip_hostname_connection_values_are_rejected_and_never_echoed(good, make, reason):
    bad = make()
    with pytest.raises(ConfigError) as e:
        cfg.resolve(_ir(good), good, sets=[f"targets.db={bad}"])
    assert f"targets.db must not contain a {reason}" in str(e.value)
    assert bad not in str(e.value)


def test_logical_names_are_accepted(good):
    res = cfg.resolve(_ir(good), good, sets=["targets.db=primary-replica"])
    assert val(res, "targets.db")["value"] == "primary-replica"


def test_forbidden_value_in_a_config_file_layer(good, tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text(f"targets:\n  db: '{ip()}'\n")
    with pytest.raises(ConfigError) as e:
        cfg.resolve(_ir(good), good, config_file=f)
    assert ip() not in str(e.value) and "IP address" in str(e.value)


def test_inputs_required_default_and_type(lab):
    def f(d):
        d["interface"] = {"inputs": {"n": {"type": "int", "default": 3}, "day": {"type": "string", "default": "${today:YYYY}"},
                                     "must": {"type": "string", "required": True}}, "outputs": {"summary": {"type": "any"}}}
    lab.edit("nightly-job.yaml", f)
    repo = lab.repo()
    ir = compile_sop("NIGHTLY-JOB", repo)[0]
    res = cfg.resolve(ir, repo)
    assert res["inputs"]["n"]["value"] == 3 and len(res["inputs"]["day"]["value"]) == 4
    assert any("required input must is missing" in e for e in res["input_errors"])
    with pytest.raises(ConfigError) as e:
        cfg.resolve(ir, repo, strict_inputs=True)
    assert e.value.code == 2
    ok = cfg.resolve(ir, repo, params=["must=x", "n=5"])
    assert ok["input_errors"] == [] and ok["inputs"]["n"]["value"] == 5
    bad = cfg.resolve(ir, repo, params=["must=x", "n=five", "ghost=1"])
    assert any("does not fit type int" in e for e in bad["input_errors"]) and any("ghost" in e for e in bad["input_errors"])


def test_show_masks_and_renders(good):
    res = cfg.resolve(_ir(good), good, sets=["targets.db=primary-replica"], params=["targets=[a]"])
    text = cfg.render_table(res)
    assert "targets.db" in text and "primary-replica" in text and "L2 runtime.yaml" in text
    masked = cfg.mask_result({"values": {"k": {"value": {"api_key": "x"}}}, "inputs": {}})
    assert masked["values"]["k"]["value"]["api_key"] == "***MASKED***"


def test_config_files_are_validated(lab):
    (lab.root / "_config" / "runtime.yaml").write_text("defaults:\n  limits: {max_depth: 9}\nbogus: 1\ncaps:\n  env: dev\n")
    lab.write("_config/procedures/GHOST.yaml", {"sop": "GHOST"})
    lab.write("_config/procedures/APPROVED-CHANGE.yaml", {"sop": "APPROVED-CHANGE"})
    errs = [e for _t, es in lab.all() for e in es]
    assert any("max_depth 9 exceeds the hard cap" in e for e in errs)
    assert any("top-level bogus is unknown" in e for e in errs)
    assert any("env is not numeric" in e for e in errs)
    assert any("no procedure GHOST" in e for e in errs)
    assert any("APPROVED-CHANGE is abstract" in e for e in errs)


def test_call_narrowing_helper():
    assert cfg.check_with_config("c", {"retry.code": 1, "step_kinds.allowed": ["code"]}) == []
    assert cfg.check_with_config("c", {"targets.db": "x"})
