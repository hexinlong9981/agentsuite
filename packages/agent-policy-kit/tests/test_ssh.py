from agent_policy_kit import build_ssh_argv


def test_basic():
    assert build_ssh_argv("host", user="me") == ["ssh", "me@host"]


def test_jump_chain_and_port_and_remote():
    argv = build_ssh_argv("app", user="ci", port=2222, jump=["b1", "b2"],
                          options=["-o", "BatchMode=yes"], remote_argv=["uptime"])
    assert argv == ["ssh", "-p", "2222", "-J", "b1,b2", "-o", "BatchMode=yes",
                    "ci@app", "--", "uptime"]


def test_jump_string():
    assert "-J" in build_ssh_argv("h", jump="bastion")
    assert build_ssh_argv("h", jump="bastion")[build_ssh_argv("h", jump="bastion").index("-J") + 1] == "bastion"


def test_no_jump():
    assert "-J" not in build_ssh_argv("h")


def test_remote_argv_is_shell_quoted():
    # ssh re-parses the remote command through the remote login shell, so a token
    # with spaces / metacharacters must be a single quoted argument.
    argv = build_ssh_argv("h", remote_argv=["cat", "/d/a b;rm -rf ~"])
    assert argv[-3:] == ["--", "cat", "'/d/a b;rm -rf ~'"]


def test_remote_argv_plain_tokens_unchanged():
    argv = build_ssh_argv("h", remote_argv=["find", "/var/log", "-type", "f"])
    assert argv[-5:] == ["--", "find", "/var/log", "-type", "f"]


def test_quote_remote_can_be_disabled():
    argv = build_ssh_argv("h", remote_argv=["echo", "$HOME"], quote_remote=False)
    assert argv[-1] == "$HOME"
