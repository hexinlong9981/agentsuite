import pytest

from remote_file_sync.core import containment
from remote_file_sync.core.errors import DestinationError


def test_safe_join_ok(tmp_path):
    dest = containment.safe_join(str(tmp_path), "a/b.txt")
    assert str(dest).startswith(str(tmp_path.resolve()))


def test_safe_join_traversal_rejected(tmp_path):
    with pytest.raises(DestinationError):
        containment.safe_join(str(tmp_path), "../escape.txt")
