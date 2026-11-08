import json
from datetime import datetime, timedelta, timezone

import pytest

from knowledgebase import (Freshness, Inbox, Item, ItemExists, ItemNotFound, KnowledgeBase,
                           ValidationError, parse, recall, serialize)
from knowledgebase.cli import main


def mk(name="deploy-rollback-command", description="How to roll back a deploy", type="reference", links=None, body="Run the rollback script."):
    return Item(name, description, type, links or [], body)


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(str(tmp_path / "kb"))


# ---- model
def test_roundtrip():
    it = mk(links=["api-timeout-default"], description="Roll back: fast, safe")
    back = parse(serialize(it))
    assert back == it


@pytest.mark.parametrize("bad", [
    dict(name="Bad Name"), dict(name="../x"), dict(description=""), dict(description="a\nb"),
    dict(type="nope"), dict(links=["Bad Link"]), dict(links=["deploy-rollback-command"]),
])
def test_validation(bad):
    with pytest.raises(ValidationError):
        mk(**bad).validate()


def test_parse_errors():
    for text in ["no front matter", "---\nname: a\n", "---\nname: a-b\ntype: user\n---\nx", "---\n- list\n---\n"]:
        with pytest.raises(ValidationError):
            parse(text)


# ---- store
def test_store_crud_and_index(kb):
    kb.add(mk())
    kb.add(mk("api-timeout-default", "Default API timeout is 30s", "project"))
    assert kb.names() == ["api-timeout-default", "deploy-rollback-command"]
    assert kb.get("api-timeout-default").type == "project"
    with pytest.raises(ItemExists):
        kb.add(mk())
    kb.update("api-timeout-default", description="Default API timeout is 60s")
    idx = kb.read_index()
    assert idx["api-timeout-default"] == "Default API timeout is 60s"
    assert kb.check_consistency() == []
    kb.remove("api-timeout-default")
    assert "api-timeout-default" not in kb.read_index()
    with pytest.raises(ItemNotFound):
        kb.get("api-timeout-default")
    with pytest.raises(ValidationError):
        kb.update("deploy-rollback-command", name="x")


def test_path_containment(kb):
    with pytest.raises(ValidationError):
        kb.get("../../etc/passwd")


def test_secret_masked_on_write(kb):
    kb.add(mk(body="token: ghp_" + "a" * 36))
    assert "a" * 36 not in kb.get("deploy-rollback-command").body


def test_consistency_broken_index_and_dangling(kb):
    kb.add(mk(links=["ghost-item"]))
    kb.add(mk("oncall-escalation", "Who to page", "reference"))
    kb.index_path.write_text("# Index\n\n- [removed-item](items/removed-item.md) — gone\n"
                             "- [oncall-escalation](items/oncall-escalation.md) — stale text\n")
    (kb.items_dir / "broken.md").write_text("not front matter")
    codes = {(i.code, i.name) for i in kb.check_consistency()}
    assert ("index-stale-entry", "removed-item") in codes
    assert ("index-mismatch", "oncall-escalation") in codes
    assert ("index-missing-entry", "deploy-rollback-command") in codes
    assert ("dangling-link", "deploy-rollback-command") in codes
    assert ("bad-front-matter", "broken") in codes
    dl = [i for i in kb.check_consistency() if i.code == "dangling-link"][0]
    assert dl.severity == "warning"
    kb.build_index()
    assert ("index-stale-entry", "removed-item") not in {(i.code, i.name) for i in kb.check_consistency()}


# ---- inbox
def test_inbox_flow(kb):
    ib = Inbox(kb)
    ib.propose(mk())
    assert kb.names() == [] and ib.names() == ["deploy-rollback-command"]
    with pytest.raises(ItemExists):
        ib.propose(mk())
    ib.promote("deploy-rollback-command")
    assert kb.names() == ["deploy-rollback-command"] and ib.names() == []
    assert "deploy-rollback-command" in kb.read_index()
    ib.propose(mk())
    with pytest.raises(ItemExists):
        ib.promote("deploy-rollback-command")
    ib.reject("deploy-rollback-command")
    assert ib.list_inbox() == []
    with pytest.raises(ItemNotFound):
        ib.reject("deploy-rollback-command")


def test_audit_written(tmp_path):
    from agent_policy_kit import AuditLog
    log = tmp_path / "a.jsonl"
    k = KnowledgeBase(str(tmp_path / "kb"), audit=AuditLog(str(log)))
    k.add(mk())
    assert json.loads(log.read_text().splitlines()[0])["op"] == "add"


# ---- freshness
def test_freshness(kb):
    kb.add(mk()); kb.add(mk("api-timeout-default", "Timeout", "project"))
    fr = Freshness(kb)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    fr.mark_used("api-timeout-default", now - timedelta(days=5))
    fr.mark_reviewed("api-timeout-default", now - timedelta(days=10))
    fr.mark_used("deploy-rollback-command", now - timedelta(days=200))
    fr.mark_reviewed("deploy-rollback-command", now - timedelta(days=300))
    rep = {e.name: e.reasons for e in fr.staleness_report(now)}
    assert "api-timeout-default" not in rep
    assert rep["deploy-rollback-command"] == ("unused>90d", "unreviewed>180d")
    with pytest.raises(ItemNotFound):
        fr.mark_used("missing-item")
    kb.add(mk("oncall-escalation", "Who to page", "reference"))
    assert {"never-used", "never-reviewed"} <= set(
        {e.name: e.reasons for e in fr.staleness_report(now)}["oncall-escalation"])


# ---- recall
def test_recall_ranking(kb):
    kb.add(mk("deploy-rollback-command", "Command to roll back a failed deploy", "reference", ["oncall-escalation"]))
    kb.add(mk("api-timeout-default", "Default timeout for api calls", "project"))
    kb.add(mk("oncall-escalation", "Who to page when paging fails", "reference"))
    hits = recall(kb, "rollback deploy")
    assert hits[0].name == "deploy-rollback-command"
    assert "oncall-escalation" in [h.name for h in hits]      # via link proximity
    assert "api-timeout-default" not in [h.name for h in hits]
    assert recall(kb, "timeout api")[0].name == "api-timeout-default"
    assert recall(kb, "zzz") == [] and recall(kb, "deploy", limit=0) == []
    assert len(recall(kb, "deploy", limit=1)) == 1


# ---- cli
def test_cli_smoke(tmp_path, capsys):
    r = str(tmp_path / "kb")
    run = lambda *a: main(["--root", r, *a])
    assert run("add", "--name", "api-timeout-default", "--description", "Default timeout", "--type", "project", "--body", "30s") == 0
    assert run("propose", "--name", "oncall-escalation", "--description", "Who to page", "--type", "reference",
               "--links", "api-timeout-default") == 0
    assert run("list") == 0
    assert "api-timeout-default" in capsys.readouterr().out
    assert run("promote", "oncall-escalation") == 0
    assert run("get", "oncall-escalation") == 0
    assert run("recall", "page") == 0
    assert "oncall-escalation" in capsys.readouterr().out
    assert run("freshness", "--mark-used", "oncall-escalation") == 0
    assert run("freshness") == 0
    assert run("check") == 0
    assert run("get", "nope-item") == 4
    assert run("add", "--name", "api-timeout-default", "--description", "x", "--type", "project") == 5
    (tmp_path / "kb" / "INDEX.md").write_text("# Index\n")
    assert run("check") == 1
