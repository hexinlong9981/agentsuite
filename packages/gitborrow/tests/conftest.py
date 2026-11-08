import os
import subprocess

import pytest

from gitborrow.core import Config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("GITBORROW_EPHEMERAL_DIR", "GITBORROW_CONFIG", "GITBORROW_SSH_KEY"):
        monkeypatch.delenv(var, raising=False)


def _git(args, cwd):
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def src_repo(tmp_path):
    """A small local git repo with planted secrets (for the filter test)."""
    repo = tmp_path / "source"
    repo.mkdir()
    (repo / "app.py").write_text("def hello():\n    return 1\n")
    (repo / "config.ini").write_text(
        "db_url = postgres://user:supersecret@db.host:5432/app\n"
        "API_TOKEN = abcd1234efgh5678ijkl\n"
    )
    (repo / "id_key.pem").write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAplaceholderplaceholder\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    _git(["init", "-q"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "initial"], repo)
    return repo


@pytest.fixture
def config(tmp_path):
    return Config.from_dict({
        "ephemeral": {"dir": str(tmp_path / "eph"), "ttl_hours": 12},
        "containment": {"protected_roots": [str(tmp_path / "protected")]},
        "repositories": {"allow_any": True},
        "secret_filter": {"enabled": True, "action": "mask"},
    })
