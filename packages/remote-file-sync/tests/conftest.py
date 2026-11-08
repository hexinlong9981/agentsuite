import pytest

from remote_file_sync.core import Config


@pytest.fixture
def src_tree(tmp_path):
    root = tmp_path / "srv"
    (root / "etc").mkdir(parents=True)
    (root / "var" / "log").mkdir(parents=True)
    (root / "etc" / "app.conf").write_text("key = 1\n")
    (root / "etc" / "secret.key").write_text("should be excluded\n")
    (root / "var" / "log" / "x.log").write_text("a log line\n")
    return root


@pytest.fixture
def config(tmp_path, src_tree):
    return Config.from_dict({
        "destinations": {
            "config": str(tmp_path / "dst_config"),
            "data": str(tmp_path / "dst_data"),
        },
        "sources": {"srv": {"transport": "local", "root": str(src_tree)}},
        "policies": {
            "p": {
                "source": "srv",
                "include": ["etc/**", "var/**"],
                "exclude": ["**/*.key"],
                "default_category": "data",
                "classify": [{"pattern": "etc/**", "category": "config"}],
            }
        },
    })
