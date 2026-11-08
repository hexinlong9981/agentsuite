import pytest

from web_operator.core import Config


def test_unknown_category_rejected():
    with pytest.raises(ValueError):
        Config.from_dict({"hosts": {"h": {"allow": ["fly"]}}})


def test_empty_config_is_fail_closed():
    cfg = Config.from_dict({})
    assert cfg.policy.check("anything", "read").code == "scope"
