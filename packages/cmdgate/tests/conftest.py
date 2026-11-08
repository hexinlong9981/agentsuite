import pytest

from cmdgate.core import Config


def make_config(timeout=5.0):
    return Config.from_dict({
        "limits": {"timeout_seconds": timeout, "max_output_bytes": 1_000_000},
        "targets": {"local": {"kind": "local"}},
        "policies": {
            "readonly": {
                "targets": ["local"],
                "read_only": True,
                "allow": [
                    {"program": "echo"},
                    {"program": "true"},
                    {"program": "sleep"},
                    {"program": "git", "args_regex": r"^(status|log|diff|show)\b"},
                ],
            }
        },
    })


@pytest.fixture
def config():
    return make_config()


@pytest.fixture
def config_factory():
    return make_config
