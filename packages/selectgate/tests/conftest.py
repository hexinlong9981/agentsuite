import sqlite3

import pytest

from selectgate.core import Config


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, k TEXT, v INTEGER)")
    conn.execute("CREATE INDEX idx_k ON item(k)")
    conn.executemany(
        "INSERT INTO item (k, v) VALUES (?, ?)",
        [(f"k{i}", i) for i in range(50)],
    )
    conn.commit()
    conn.close()
    return str(path)


def _config(db, **limits):
    base = {"max_scanned_rows": 10, "timeout_seconds": 5, "max_rows": 1000}
    base.update(limits)
    return Config.from_dict({
        "limits": base,
        "targets": {"main": {"driver": "sqlite", "dsn": db}},
    })


@pytest.fixture
def config(db):
    return _config(db)


@pytest.fixture
def config_factory(db):
    def make(**limits):
        return _config(db, **limits)
    return make
