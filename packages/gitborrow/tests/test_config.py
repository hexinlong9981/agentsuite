import pytest

from gitborrow.core.config import Config


def test_defaults():
    cfg = Config.load()
    assert cfg.push_guard.enabled is True
    assert cfg.secret_filter.enabled is True
    assert cfg.repositories.allow_any is False      # fail-closed by default


def test_from_dict_roundtrip():
    cfg = Config.from_dict({
        "push_guard": {"enabled": False},
        "secret_filter": {"enabled": True, "action": "delete"},
        "repositories": {
            "allow_any": False,
            "demo": {"url": "https://example.invalid/demo.git", "allowed_refs": ["main"]},
        },
    })
    assert cfg.push_guard.enabled is False
    assert cfg.secret_filter.action == "delete"
    assert "demo" in cfg.repositories.entries
    assert cfg.repositories.entries["demo"].allowed_refs == ["main"]


def test_invalid_action_rejected():
    with pytest.raises(ValueError):
        Config.from_dict({"secret_filter": {"action": "nope"}})


def test_invalid_credentials_source_rejected():
    with pytest.raises(ValueError):
        Config.from_dict({"credentials": {"source": "cloud"}})
