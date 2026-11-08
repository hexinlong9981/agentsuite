from agent_policy_kit import AllowAll, Callback, DenyAll


def test_deny_all():
    assert DenyAll().request("t", "submit", "preview") is False


def test_allow_all():
    assert AllowAll().request("t", "submit", "preview") is True


def test_callback_receives_args():
    seen = {}

    def fn(target, operation, preview):
        seen.update(target=target, operation=operation, preview=preview)
        return True

    assert Callback(fn).request("host", "submit", "do X") is True
    assert seen == {"target": "host", "operation": "submit", "preview": "do X"}
