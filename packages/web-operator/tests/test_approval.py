from web_operator.core import Callback, FakeBackend, Operator


def test_callback_receives_preview(config, pages):
    seen = {}

    def hook(host, operation, preview):
        seen.update(host=host, operation=operation, preview=preview)
        return True

    op = Operator(backend=FakeBackend(pages), policy=config.policy, approval=Callback(hook))
    op.navigate("ex.example", "https://ex.example/login")
    op.submit("ex.example", "#loginform")
    assert seen["operation"] == "submit"
    assert "submit" in seen["preview"]
