import shutil
from pathlib import Path

import pytest
import yaml

from procflow import Repo
from procflow.check import Checker

GOOD = Path(__file__).parent / "fixtures" / "good"


class Lab:
    """A scratch copy of the good sop-root that tests mutate to produce one violation each."""

    def __init__(self, root):
        self.root = root

    def edit(self, fname, fn):
        p = self.root / fname
        d = yaml.safe_load(p.read_text())
        fn(d)
        p.write_text(yaml.safe_dump(d, sort_keys=False))

    def write(self, fname, d):
        p = self.root / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(d, sort_keys=False))

    def step(self, d, sid):
        return next(s for s in d["steps"] if s["id"] == sid)

    def repo(self):
        return Repo(self.root)

    def msgs(self, sid):
        return Checker(self.repo()).check_sop(sid)

    @staticmethod
    def has(msgs, *needles):
        return any(all(n in m for n in needles) for m in msgs)

    def all(self):
        return Checker(self.repo()).check_all()


@pytest.fixture
def lab(tmp_path):
    root = tmp_path / "root"
    shutil.copytree(GOOD, root)
    return Lab(root)


@pytest.fixture
def good():
    return Repo(GOOD)



# ---- AI step helpers ------------------------------------------------------------------------
AI_FIX = Path(__file__).parent / "fixtures" / "ai"


@pytest.fixture
def ai_root(tmp_path, monkeypatch):
    """A scratch sop-root with the llm / agent procedures; fake providers are enabled for the test."""
    monkeypatch.setenv("PROCFLOW_ALLOW_FAKE_PROVIDERS", "1")
    monkeypatch.delenv("PROCFLOW_FAKE_SCRIPT", raising=False)
    from procflow import fakeprov
    fakeprov.set_script(None)
    root = tmp_path / "aisop"
    shutil.copytree(AI_FIX, root)
    yield root
    fakeprov.set_script(None)


@pytest.fixture
def write_reg():
    """``write_reg(root, services=..., agents=..., classes=..., routing=...)`` writes registry files under <root>/_config."""
    def _write(root, services=None, agents=None, classes=None, routing=None):
        cd = Path(root) / "_config"
        cd.mkdir(exist_ok=True)
        if services is not None:
            (cd / "llm_services.yaml").write_text(yaml.safe_dump({"services": services}))
        if agents is not None:
            (cd / "agents.yaml").write_text(yaml.safe_dump({"agents": agents}))
        if classes is not None:
            (cd / "data_classes.yaml").write_text(yaml.safe_dump(classes))
        if routing is not None:
            (cd / "llm_routing.yaml").write_text(yaml.safe_dump(routing))
    return _write


FAKE_LLM = {"provider": "fake", "family": "remote", "data_classes": ["public", "internal"], "cost_per_call": 1}
