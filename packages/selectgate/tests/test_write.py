import pytest

from selectgate import core
from selectgate.core import AllowAll, Config, DenyAll
from selectgate.core.errors import StatementRejected, WriteNotApproved, WriteNotEnabled


def _cfg(db, **write):
    w = {"enabled": True, "allow": ["insert", "update", "delete"], "require_approval": True}
    w.update(write)
    return Config.from_dict({"targets": {"main": {"driver": "sqlite", "dsn": db}}, "write": w})


def test_write_insert_with_approval(db):
    cfg = _cfg(db)
    res = core.run_write("main", "INSERT INTO item (k, v) VALUES ('zz', 999)",
                         config=cfg, approval=AllowAll())
    assert res.statement == "insert" and res.rowcount == 1
    out, _ = core.run_select("main", "SELECT v FROM item WHERE k = 'zz'", config=cfg)
    assert out.rows[0][0] == 999


def test_write_not_enabled_by_default(db):
    cfg = Config.from_dict({"targets": {"main": {"driver": "sqlite", "dsn": db}}})
    with pytest.raises(WriteNotEnabled):
        core.run_write("main", "DELETE FROM item", config=cfg, approval=AllowAll())


def test_write_requires_approval(db):
    cfg = _cfg(db)
    with pytest.raises(WriteNotApproved):
        core.run_write("main", "DELETE FROM item", config=cfg, approval=DenyAll())
    with pytest.raises(WriteNotApproved):                 # default approval denies
        core.run_write("main", "DELETE FROM item", config=cfg)


def test_write_statement_allowlist(db):
    cfg = _cfg(db, allow=["insert"])
    with pytest.raises(StatementRejected):               # update not in allowlist
        core.run_write("main", "UPDATE item SET v = 0", config=cfg, approval=AllowAll())
    with pytest.raises(StatementRejected):               # DDL never in allowlist here
        core.run_write("main", "DROP TABLE item", config=cfg, approval=AllowAll())


def test_run_select_still_select_only(db):
    cfg = _cfg(db)                                        # write-mode on does not loosen reads
    with pytest.raises(StatementRejected):
        core.run_select("main", "DELETE FROM item", config=cfg)
