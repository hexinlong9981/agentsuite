from mcp_runtime.core import Config


def test_from_dict_manifest():
    cfg = Config.from_dict({"tools": {"enabled": ["a", "b"]}})
    assert cfg.enabled == {"a", "b"}


def test_empty_is_fail_closed():
    cfg = Config.from_dict({})
    assert cfg.enabled == set()         # nothing exposed by default
