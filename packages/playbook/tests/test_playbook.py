import dataclasses
from pathlib import Path

import pytest

from playbook import (PRINCIPLES, Principle, PrincipleNotFound, audit_text,
                      check, get, render)
from playbook.cli import main

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "OPERATING_GUIDE.md"


def test_well_formed_and_unique():
    assert len(PRINCIPLES) == 11
    assert len({p.id for p in PRINCIPLES}) == len(PRINCIPLES)
    assert check().ok


def test_get():
    assert get("show-full-paths").title
    with pytest.raises(PrincipleNotFound):
        get("nope")


def test_render_contains_titles():
    text = render()
    for p in PRINCIPLES:
        assert p.title in text and p.id in text


def test_committed_guide_is_current():
    assert GUIDE.read_text(encoding="utf-8") == render()


def test_check_fails_on_malformed():
    bad = [PRINCIPLES[0], dataclasses.replace(PRINCIPLES[0]),
           Principle("x", "", "r", "w", ("bogus",))]
    res = check(bad)
    assert not res.ok
    joined = " ".join(res.problems)
    assert "duplicate" in joined and "empty title" in joined and "unknown tag" in joined


def test_audit_text():
    res = audit_text("We follow commit-own-paths and Show full paths.")
    assert "commit-own-paths" in res.referenced
    assert "show-full-paths" in res.referenced
    assert "delegate-heavy-work" in res.missing
    assert not res.ok


def test_cli(tmp_path, capsys):
    assert main(["list"]) == 0
    assert "show-full-paths" in capsys.readouterr().out
    assert main(["show", "show-full-paths"]) == 0
    assert main(["show", "nope"]) == 3
    out = tmp_path / "g.md"
    assert main(["render", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == render()
    assert main(["render"]) == 0
    assert main(["check"]) == 0
    f = tmp_path / "a.md"
    f.write_text("commit-own-paths", encoding="utf-8")
    assert main(["audit", str(f)]) == 1
    f.write_text(" ".join(p.id for p in PRINCIPLES), encoding="utf-8")
    assert main(["audit", str(f)]) == 0
    assert main(["audit", str(tmp_path / "missing")]) == 2
