import subprocess
from unittest.mock import MagicMock

import pytest

from remote_file_sync.core.config import Source
from remote_file_sync.core.errors import TransportError
from remote_file_sync.core.transports import make_transport
from remote_file_sync.core.transports.ssh import SshTransport


def test_make_transport_ssh():
    src = Source(
        name="web-srv",
        transport="ssh",
        host="web.example.invalid",
        user="deploy",
        path="/var/log/app",
    )
    t = make_transport(src)
    assert isinstance(t, SshTransport)
    assert t.remote_dir == "/var/log/app"


def test_ssh_build_command_with_bastion_and_port():
    src = Source(
        name="db-srv",
        transport="ssh",
        host="db.internal.example.invalid",
        user="admin",
        port=2222,
        jump=["bastion1.example.invalid", "bastion2.example.invalid"],
        path="/etc/configs",
        ssh_options=["-o", "BatchMode=yes"],
    )
    t = SshTransport(src)
    cmd = t._build_cmd(["find", "/etc/configs", "-type", "f"])

    assert "-p" in cmd and "2222" in cmd
    assert "-J" in cmd and "bastion1.example.invalid,bastion2.example.invalid" in cmd
    assert "-o" in cmd and "BatchMode=yes" in cmd
    assert "admin@db.internal.example.invalid" in cmd
    assert cmd[-5:] == ["--", "find", "/etc/configs", "-type", "f"]


def test_ssh_list_parses_relative_paths():
    src = Source(
        name="app-srv",
        transport="ssh",
        host="app.example.invalid",
        path="/opt/app/logs",
    )

    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(
        args=["ssh"],
        returncode=0,
        stdout=b"/opt/app/logs/sys.log\n/opt/app/logs/audit/2026.log\n",
    )

    t = SshTransport(src, runner=mock_runner)
    files = t.list()

    assert files == ["audit/2026.log", "sys.log"]
    mock_runner.assert_called_once()


def test_ssh_read_success():
    src = Source(
        name="app-srv",
        transport="ssh",
        host="app.example.invalid",
        path="/opt/app/conf",
    )

    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(
        args=["ssh"],
        returncode=0,
        stdout=b"server_name = test\n",
    )

    t = SshTransport(src, runner=mock_runner)
    data = t.read("app.ini")

    assert data == b"server_name = test\n"
    # Verify cat path
    cmd_called = mock_runner.call_args[0][0]
    assert cmd_called[-2:] == ["cat", "/opt/app/conf/app.ini"]


def test_ssh_read_quotes_remote_path():
    # A filename with a space / metacharacter passes the traversal guard but must
    # be shell-quoted so the remote shell cannot split or inject on it.
    src = Source(
        name="app-srv",
        transport="ssh",
        host="app.example.invalid",
        path="/opt/app/conf",
    )
    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(
        args=["ssh"], returncode=0, stdout=b"",
    )
    t = SshTransport(src, runner=mock_runner)
    t.read("a b;id.ini")

    cmd = mock_runner.call_args[0][0]
    assert cmd[-1] == "'/opt/app/conf/a b;id.ini'"
    assert cmd[-2] == "cat"


def test_ssh_read_rejects_path_traversal():
    src = Source(
        name="app-srv",
        transport="ssh",
        host="app.example.invalid",
        path="/opt/app/conf",
    )
    t = SshTransport(src)

    with pytest.raises(TransportError, match="Path traversal rejected"):
        t.read("../../etc/shadow")

    with pytest.raises(TransportError, match="Path traversal rejected"):
        t.read("/absolute/path")
