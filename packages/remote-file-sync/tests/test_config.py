import pytest

from remote_file_sync.core import Config
from remote_file_sync.core.errors import PolicyNotAllowed, SourceNotAllowed


def test_validate_unknown_source():
    with pytest.raises(ValueError):
        Config.from_dict({"destinations": {"data": "/d"},
                          "policies": {"p": {"source": "ghost"}}})


def test_validate_category_without_destination():
    with pytest.raises(ValueError):
        Config.from_dict({
            "destinations": {"data": "/d"},
            "sources": {"s": {"root": "/r"}},
            "policies": {"p": {"source": "s", "default_category": "config"}},
        })


def test_fail_closed_lookups():
    cfg = Config.from_dict({})
    with pytest.raises(PolicyNotAllowed):
        cfg.get_policy("x")
    with pytest.raises(SourceNotAllowed):
        cfg.get_source("x")
