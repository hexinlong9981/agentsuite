from cmdgate.core import Config
from cmdgate.core.config import Target
from cmdgate.core.runner import _exec_argv


def test_ssh_argv_with_jump_chain():
    t = Target(name="box", kind="ssh", host="app", user="ci", port=2222,
               jump=["b1", "b2"], ssh_options=["-o", "BatchMode=yes"])
    argv = _exec_argv(t, ["uptime"])
    assert argv[0] == "ssh"
    assert argv[argv.index("-J") + 1] == "b1,b2"        # bastion chain
    assert "ci@app" in argv
    assert argv[-2:] == ["--", "uptime"]


def test_local_argv_unchanged():
    assert _exec_argv(Target(name="l", kind="local"), ["echo", "hi"]) == ["echo", "hi"]


def test_config_parses_jump_and_port():
    cfg = Config.from_dict({"targets": {"box": {
        "kind": "ssh", "host": "app", "user": "ci", "port": 2222, "jump": ["b1"]}}})
    t = cfg.targets["box"]
    assert t.jump == ["b1"] and t.port == 2222
