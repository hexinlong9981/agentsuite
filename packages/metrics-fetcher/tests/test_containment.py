from metrics_fetcher.core import containment


def test_safe_name_sanitizes():
    assert containment.safe_name("a/b c*.csv") == "a_b_c_.csv"


def test_safe_join_stays_under_root(tmp_path):
    # traversal characters are neutralised by safe_name, so the result stays put
    dest = containment.safe_join(str(tmp_path), "../../etc/passwd")
    assert str(dest).startswith(str(tmp_path.resolve()))
