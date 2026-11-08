from remote_file_sync.core import classify as clf


def test_classify_routes(config):
    pol = config.policies["p"]
    assert clf.classify("etc/app.conf", pol) == "config"
    assert clf.classify("var/log/x.log", pol) == "data"   # falls through to default


def test_matches():
    assert clf.matches("etc/a.conf", ["etc/**"])
    assert clf.matches("var/log/x.log", ["var/**"])
    assert not clf.matches("bin/x", ["etc/**"])
