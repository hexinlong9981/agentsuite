import json

from agentflow.core import Recorder, RunStore


def test_record_and_load(tmp_path):
    rec = Recorder(str(tmp_path), flow="demo")
    rec.note("starting")
    with rec.step("fetch"):
        rec.tool("search", query="x")
    rec.finish("ok")

    meta, events = RunStore(str(tmp_path)).load(rec.run_id)
    assert meta.status == "ok" and meta.flow == "demo"
    assert [e.type for e in events] == ["note", "step_start", "tool", "step_end"]


def test_step_error_marks_status(tmp_path):
    rec = Recorder(str(tmp_path))
    try:
        with rec.step("boom"):
            raise ValueError("x")
    except ValueError:
        pass
    _, events = RunStore(str(tmp_path)).load(rec.run_id)
    end = [e for e in events if e.type == "step_end"][0]
    assert end.data.get("status") == "error"


def test_secrets_masked_in_log(tmp_path):
    rec = Recorder(str(tmp_path))
    rec.tool("curl", url="https://u:topsecret@h/x")
    _, events = RunStore(str(tmp_path)).load(rec.run_id)
    assert "topsecret" not in json.dumps(events[0].data)
