import json
from pathlib import Path

import pytest

from procflow import compile_sop, dot, mermaid, render
from procflow.cli import main

GOOD = str(Path(__file__).parent / "fixtures" / "good")


def run(capsys, *args):
    with pytest.raises(SystemExit) as e:
        main(list(args))
    out = capsys.readouterr()
    return e.value.code, out.out, out.err


def test_mermaid_shapes_edges_and_loop_body(good):
    ir = compile_sop("NIGHTLY-JOB", good)[0]
    m = mermaid(ir)
    assert m.splitlines()[0] == "flowchart TD"
    assert 'n_each_target[/"each-target loop <= 10"/]' in m
    assert 'n_each_target -.->|"body"| n_run_one' in m and "n_start --> n_each_target" in m
    m2 = mermaid(compile_sop("BACKUP-AND-VERIFY", good)[0])
    assert "extends APPROVED-CHANGE@1 implements IF-APPROVAL@1" in m2 and "approve.decision == 'approved'" in m2
    assert 'n_prepare["prepare (code)"]' in m2 and "approve (human) final" in m2


def test_dot_output_and_render_dispatch(good):
    ir = compile_sop("RELEASE-PIPELINE", good)[0]
    d = dot(ir)
    assert d.startswith('digraph "RELEASE-PIPELINE" {') and d.rstrip().endswith("}")
    assert "shape=box3d" in d and "shape=oval" in d
    assert render(ir, "dot") == d and render(ir) == mermaid(ir)
    with pytest.raises(ValueError):
        render(ir, "svg")


def test_cli_check_ok_and_failure(capsys, tmp_path):
    code, out, _ = run(capsys, "check", "--sop-root", GOOD)
    assert code == 0 and "OK: no findings" in out
    import shutil
    bad = tmp_path / "bad"
    shutil.copytree(GOOD, bad)
    p = bad / "nightly-job.yaml"
    p.write_text(p.read_text().replace("    action: Write the summary", "    action: Write the summary\n    bogus: 1"))
    code, out, _ = run(capsys, "check", "--sop-root", str(bad), "--json")
    data = json.loads(out)
    assert code == 1 and any("bogus" in f["message"] for f in data) and data[0]["severity"] == "error"


def test_cli_check_run_rejects_abstract(capsys):
    code, out, _ = run(capsys, "check", "APPROVED-CHANGE", "--sop-root", GOOD)
    assert code == 0
    code, out, _ = run(capsys, "check", "APPROVED-CHANGE", "--run", "--sop-root", GOOD)
    assert code == 1 and "cannot be run directly" in out


def test_cli_compile_to_file_and_stdout(capsys, tmp_path):
    out_file = tmp_path / "x" / "ir.json"
    code, out, _ = run(capsys, "compile", "BACKUP-AND-VERIFY", "--sop-root", GOOD, "--out", str(out_file))
    assert code == 0 and json.loads(out_file.read_text())["sop"]["id"] == "BACKUP-AND-VERIFY"
    code, out, _ = run(capsys, "compile", "NIGHTLY-JOB", "--sop-root", GOOD)
    assert code == 0 and json.loads(out)["interface"]["origin"] == "derived"


def test_cli_compile_refuses_when_check_fails(capsys, tmp_path):
    import shutil
    bad = tmp_path / "bad"
    shutil.copytree(GOOD, bad)
    p = bad / "backup-and-verify.yaml"
    p.write_text(p.read_text().replace("extends: APPROVED-CHANGE@1", "extends: NOPE@1"))
    code, _, err = run(capsys, "compile", "BACKUP-AND-VERIFY", "--sop-root", str(bad))
    assert code == 1 and "refusing to compile" in err


def test_cli_graph(capsys):
    code, out, _ = run(capsys, "graph", "NIGHTLY-JOB", "--sop-root", GOOD)
    assert code == 0 and out.startswith("flowchart TD")
    code, out, _ = run(capsys, "graph", "NIGHTLY-JOB", "--format", "dot", "--sop-root", GOOD)
    assert code == 0 and out.startswith("digraph")
    code, _, err = run(capsys, "graph", "NOPE", "--sop-root", GOOD)
    assert code == 1 and "does not exist" in err


def test_cli_config_show(capsys):
    code, out, _ = run(capsys, "config", "show", "NIGHTLY-JOB", "--sop-root", GOOD, "--env", "staging",
                       "--set", "limits.max_iterations=9", "--param", "targets=[a]")
    assert code == 0 and "limits.max_iterations" in out and "L7 --set" in out and "timeout.step" in out
    code, out, _ = run(capsys, "config", "show", "NIGHTLY-JOB", "--sop-root", GOOD, "--json", "--param", "targets=[a]")
    assert code == 0 and json.loads(out)["sop"] == "NIGHTLY-JOB"
    code, _, err = run(capsys, "config", "show", "NIGHTLY-JOB", "--sop-root", GOOD, "--set", "limits.max_depth=9")
    assert code == 1 and "exceeds the hard cap" in err
    code, _, err = run(capsys, "config", "show", "NIGHTLY-JOB", "--sop-root", GOOD)
    assert code == 2 and "required input targets is missing" in err
