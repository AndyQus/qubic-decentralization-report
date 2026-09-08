"""The RPC client must ride out rate limits.

The ingest worker polls for months on end; the public RPC answers 429 when it
feels crowded and 5xx now and then. Treating either as fatal would stop the
report until a human restarted something, so these pin the retry behaviour.
"""
import json
import pathlib
import tempfile
import types

import pytest

import qdr.client as C


class _Resp:
    def __init__(self, code, body='{"ok":1}', headers=None):
        self.status_code = code
        self.text = body
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


def _client(monkeypatch, responses):
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return responses[len(calls) - 1]

    monkeypatch.setattr(
        C, "requests", types.SimpleNamespace(get=fake_get, RequestException=Exception))
    monkeypatch.setattr(C, "RETRY_BASE_S", 0.001)   # keep the suite fast

    c = C.CachedClient.__new__(C.CachedClient)
    c.base_url = "http://rpc.test"
    c.cache_dir = pathlib.Path(tempfile.mkdtemp())
    c.offline = False
    c.timeout = 5
    c.min_interval_s = 0
    c._last_call = 0.0
    return c, calls


def test_retries_a_rate_limit_and_succeeds(monkeypatch):
    c, calls = _client(monkeypatch, [
        _Resp(429, "slow down", {"Retry-After": "0"}),
        _Resp(429, "slow down"),
        _Resp(200, '{"ok":1}'),
    ])
    assert c._get("/v1/thing", use_cache=False) == {"ok": 1}
    assert len(calls) == 3


def test_retries_a_transient_server_error(monkeypatch):
    c, calls = _client(monkeypatch, [_Resp(503, "nope"), _Resp(200, '{"ok":2}')])
    assert c._get("/v1/thing", use_cache=False) == {"ok": 2}
    assert len(calls) == 2


def test_gives_up_after_the_attempt_budget(monkeypatch):
    c, calls = _client(monkeypatch, [_Resp(429, "slow down")] * C.RETRY_ATTEMPTS)
    with pytest.raises(C.QubicRPCError):
        c._get("/v1/thing", use_cache=False)
    assert len(calls) == C.RETRY_ATTEMPTS


def test_a_real_error_is_not_retried(monkeypatch):
    """404 means the answer is no — retrying it just wastes the rate budget."""
    c, calls = _client(monkeypatch, [_Resp(404, "not found")])
    with pytest.raises(C.QubicRPCError):
        c._get("/v1/thing", use_cache=False)
    assert len(calls) == 1
