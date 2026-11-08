import pytest

from agent_policy_kit import PolicyKitError

from agentflow.core import Recorder, RunStore
from agentflow.core.errors import RunNotFound


def test_list_runs(tmp_path):
    Recorder(str(tmp_path), run_id="r1").finish()
    Recorder(str(tmp_path), run_id="r2").finish()
    ids = {m.run_id for m in RunStore(str(tmp_path)).list_runs()}
    assert {"r1", "r2"} <= ids


def test_load_missing(tmp_path):
    with pytest.raises(RunNotFound):
        RunStore(str(tmp_path)).load("nope")


def test_load_traversal_refused(tmp_path):
    with pytest.raises(PolicyKitError):      # contained_join rejects an escaping run id
        RunStore(str(tmp_path)).load("../../etc")
