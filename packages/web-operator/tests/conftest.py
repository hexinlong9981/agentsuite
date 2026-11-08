import pytest

from web_operator.core import Config, DenyAll, FakeBackend, Operator


@pytest.fixture
def pages():
    return {
        "https://ex.example/login": {
            "text": "Login page",
            "elements": [{"id": "user"}, {"id": "pass"}],
        }
    }


@pytest.fixture
def config():
    return Config.from_dict({
        "hosts": {
            "ex.example": {"allow": ["navigate", "read", "download", "interact", "mutate"]},
            "*.readonly.example": {"allow": ["navigate", "read"]},
        },
    })


@pytest.fixture
def operator(config, pages):
    return Operator(backend=FakeBackend(pages), policy=config.policy,
                    approval=DenyAll(), audit=config.audit)
