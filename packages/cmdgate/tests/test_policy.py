from cmdgate.core import evaluate


def test_allowed_program(config):
    pol = config.policies["readonly"]
    assert evaluate(["echo", "hi"], pol).allowed
    assert evaluate(["git", "status"], pol).allowed


def test_unlisted_program_denied(config):
    d = evaluate(["cat", "/etc/passwd"], config.policies["readonly"])
    assert not d.allowed and "no allow rule" in d.reason


def test_irreversible_denied_when_readonly(config):
    d = evaluate(["rm", "-rf", "/tmp/x"], config.policies["readonly"])
    assert not d.allowed and "irreversible" in d.reason


def test_metacharacter_denied(config):
    assert not evaluate(["echo", "a;rm -rf /"], config.policies["readonly"]).allowed
    assert not evaluate(["echo", "$(whoami)"], config.policies["readonly"]).allowed


def test_args_regex_enforced(config):
    pol = config.policies["readonly"]
    assert evaluate(["git", "log"], pol).allowed
    assert not evaluate(["git", "push"], pol).allowed
    assert not evaluate(["git"], pol).allowed          # empty args fail the regex
