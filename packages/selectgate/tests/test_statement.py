import pytest

from selectgate.core import statement
from selectgate.core.errors import StatementRejected


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from item where k = 'x'",
    "WITH c AS (SELECT 1 AS n) SELECT n FROM c",
    "SELECT * FROM item WHERE k = 'a;b'",     # ';' inside a string literal is fine
    "VALUES (1), (2)",
])
def test_read_only_accepted(sql):
    assert statement.assert_read_only(sql)


@pytest.mark.parametrize("sql", [
    "INSERT INTO item VALUES (1)",
    "UPDATE item SET v = 1",
    "DELETE FROM item",
    "DROP TABLE item",
    "ATTACH DATABASE 'x' AS y",
    "SELECT 1; DROP TABLE item",              # multiple statements
    "SELECT 1; SELECT 2",
    "PRAGMA journal_mode = WAL",              # pragma assignment
    "WITH c AS (SELECT 1) INSERT INTO item SELECT * FROM c",
    "",
])
def test_non_read_only_rejected(sql):
    with pytest.raises(StatementRejected):
        statement.assert_read_only(sql)


def test_trailing_semicolon_stripped():
    assert statement.assert_read_only("SELECT 1;").endswith("1")
