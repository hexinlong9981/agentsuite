from term_operator.core.backends import make_backend
from term_operator.core.backends.ssh import SshBackend


def test_ssh_argv_includes_bastion_chain():
    # command assembly is pure and offline-testable (no connection is made)
    b = SshBackend(host="app", user="ci", port=2222, jump=["b1", "b2"],
                   ssh_options=["-o", "BatchMode=yes"])
    assert b.ssh_argv[0] == "ssh"
    assert b.ssh_argv[b.ssh_argv.index("-J") + 1] == "b1,b2"
    assert "ci@app" in b.ssh_argv


def test_ssh_backend_registered():
    b = make_backend("ssh", {"host": "app", "user": "ci", "jump": ["bastion"]})
    assert isinstance(b, SshBackend)
    assert "-J" in b.ssh_argv
