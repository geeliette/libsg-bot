import logging

import httpx
import pytest

import libsg.nlb_client as nc
from libsg.nlb_client import NLBClient, NLBError


def client_with(responses, monkeypatch, max_retries=2):
    """NLBClient whose HTTP calls return `responses` in order; sleeps are recorded, not slept."""
    calls, sleeps = [], []
    queue = list(responses)

    def handler(request):
        calls.append(request)
        return queue.pop(0)

    monkeypatch.setattr(nc.time, "sleep", sleeps.append)
    c = NLBClient("test-key", "test-app", transport=httpx.MockTransport(handler), max_retries=max_retries)
    return c, calls, sleeps


def r429(retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return httpx.Response(429, headers=headers,
                          json={"statusCode": 429, "error": "Too Many Requests", "message": "API calls quota exceeded"})


def test_logs_429_even_when_retry_recovers(monkeypatch, caplog):
    c, calls, sleeps = client_with([r429(), httpx.Response(200, json={"titles": []})], monkeypatch)
    with caplog.at_level(logging.INFO, logger="libsg.nlb_client"):
        assert c.search_titles("emma") == {"titles": []}
    assert len(calls) == 2 and sleeps == [2.0]
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs == [
        "NLB 429 on SearchTitles (attempt 1/3): retrying in 2s",
        "NLB SearchTitles succeeded after 1 rate-limited attempt(s)",
    ]
    assert caplog.records[0].levelno == logging.WARNING


def test_logs_each_429_and_giving_up(monkeypatch, caplog):
    c, calls, sleeps = client_with([r429(5), r429(), r429()], monkeypatch)
    with caplog.at_level(logging.INFO, logger="libsg.nlb_client"), pytest.raises(NLBError) as err:
        c.get_availability(123)
    assert err.value.status == 429
    assert sleeps == [5.0, 4.0]  # Retry-After honoured, then backoff
    assert [r.getMessage() for r in caplog.records] == [
        "NLB 429 on GetAvailabilityInfo (attempt 1/3): retrying in 5s",
        "NLB 429 on GetAvailabilityInfo (attempt 2/3): retrying in 4s",
        "NLB 429 on GetAvailabilityInfo (attempt 3/3): giving up",
    ]


def test_no_log_without_429(monkeypatch, caplog):
    c, calls, sleeps = client_with([httpx.Response(200, json={})], monkeypatch)
    with caplog.at_level(logging.INFO, logger="libsg.nlb_client"):
        c.get_branches()
    assert caplog.records == [] and sleeps == []


def test_logs_never_contain_keys(monkeypatch, caplog):
    c, *_ = client_with([r429(), r429(), r429()], monkeypatch)
    with caplog.at_level(logging.DEBUG), pytest.raises(NLBError):
        c.search_titles("emma")
    assert "test-key" not in caplog.text and "test-app" not in caplog.text
