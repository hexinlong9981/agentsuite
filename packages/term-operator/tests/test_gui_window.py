import subprocess
from unittest.mock import MagicMock

import pytest

from term_operator.core.backends import make_backend
from term_operator.core.backends.gui_window import GuiWindowBackend
from term_operator.core.errors import BackendError


def test_make_backend_gui_window():
    b = make_backend("gui_window", {"send_mode": "mock"})
    assert isinstance(b, GuiWindowBackend)
    assert b.send_mode == "mock"


def test_gui_window_mock_lifecycle():
    b = GuiWindowBackend(
        window_title_template="Term - {session}",
        send_mode="mock",
    )
    b.open("srv1")

    # Read initial screen/buffer
    initial = b.read("srv1")
    assert "Attached to Term - srv1" in initial

    # Send command
    res = b.send("srv1", "whoami")
    assert "> whoami" in res
    assert "[mock output for whoami]" in res

    # Subsequent read includes history
    full = b.read("srv1")
    assert "> whoami" in full

    b.close("srv1")
    with pytest.raises(BackendError, match="not open"):
        b.read("srv1")


def test_gui_window_command_mode_runner():
    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(
        args=["send-cmd"],
        returncode=0,
        stdout=b"command executed in GUI window\n",
    )

    b = GuiWindowBackend(
        window_title_template="GUI-{session}",
        send_mode="command",
        send_cmd_template=["helper", "--win", "{window_title}", "--send", "{command}"],
        runner=mock_runner,
    )

    b.open("nodeA")
    out = b.send("nodeA", "ls -la")

    assert out == "command executed in GUI window\n"
    mock_runner.assert_called_once_with(
        ["helper", "--win", "GUI-nodeA", "--send", "ls -la"]
    )


def test_gui_window_read_from_log_dir(tmp_path):
    log_dir = tmp_path / "term_logs"
    log_dir.mkdir()
    log_file = log_dir / "sess1.log"
    log_file.write_text("line 1\nline 2\n[prompt]$ ", encoding="utf-8")

    b = GuiWindowBackend(
        send_mode="mock",
        log_dir=log_dir,
    )
    b.open("sess1")

    content = b.read("sess1")
    assert "line 1\nline 2\n[prompt]$ " in content


def test_gui_window_unopened_session_raises():
    b = GuiWindowBackend(send_mode="mock")
    with pytest.raises(BackendError, match="not open"):
        b.send("ghost", "echo 1")

    with pytest.raises(BackendError, match="not open"):
        b.read("ghost")
