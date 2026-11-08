import json

import pytest

from metrics_fetcher.core.config import SourceConfig
from metrics_fetcher.core.errors import SourceError
from metrics_fetcher.core.sources import make_source
from metrics_fetcher.core.sources.http_json import HttpJsonSource
from metrics_fetcher.core.types import MetricRef


def _transport(points):
    def t(url, headers):
        return json.dumps({"points": points})
    return t


def test_fetch_filters_range_and_downsamples():
    pts = [{"ts": 1000, "value": 1}, {"ts": 1030, "value": 2},
           {"ts": 1060, "value": 3}, {"ts": 5000, "value": 9}]
    src = HttpJsonSource("h", {"url_template": "http://x/{metric}?s={start}&e={end}"},
                         transport=_transport(pts))
    series = src.fetch(MetricRef("h", "cpu"), 1000, 2000, 60)
    assert {p.ts for p in series.points} == {960, 1020}      # 5000 excluded; 1030/1060 share a bucket


def test_url_template_required():
    src = HttpJsonSource("h", {}, transport=lambda u, hh: "{}")
    with pytest.raises(SourceError):
        src.fetch(MetricRef("h", "cpu"), 0, 10, 1)


def test_list_metrics_from_options():
    src = HttpJsonSource("h", {"metrics": ["cpu", "mem"], "url_template": "x"})
    assert {r.name for r in src.list_metrics()} == {"cpu", "mem"}


def test_auth_env_sent_as_header(monkeypatch):
    captured = {}

    def t(url, headers):
        captured.update(headers)
        return '{"points": []}'

    monkeypatch.setenv("MY_TOKEN", "a-secret")
    src = HttpJsonSource("h", {"url_template": "http://x",
                               "auth_env": {"Authorization": "MY_TOKEN"}}, transport=t)
    src.fetch(MetricRef("h", "cpu"), 0, 10, 1)
    assert captured.get("Authorization") == "a-secret"


def test_sources_registered():
    s = make_source(SourceConfig(name="h", type="http_json", options={"url_template": "x"}))
    assert type(s).__name__ == "HttpJsonSource"
    c = make_source(SourceConfig(name="c", type="cloudwatch", options={}))
    assert type(c).__name__ == "CloudWatchSource"
