import pytest

from metrics_fetcher.core import Config


@pytest.fixture
def csv_root(tmp_path):
    root = tmp_path / "m"
    (root / "host1").mkdir(parents=True)
    rows = "\n".join(f"{1000 + i * 60},{i}" for i in range(20))
    (root / "host1" / "cpu.csv").write_text("epoch,value\n" + rows + "\n")
    return root


@pytest.fixture
def config(tmp_path, csv_root):
    return Config.from_dict({
        "output": {"dir": str(tmp_path / "out"), "max_points": 100},
        "sources": {
            "syn": {"type": "synthetic", "metrics": ["cpu", "mem"]},
            "files": {"type": "csv", "root": str(csv_root)},
        },
    })
