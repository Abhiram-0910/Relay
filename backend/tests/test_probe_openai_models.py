"""Hermetic tests for scripts/probe_openai_models.py: classification, retry/abort control flow, and
key scrubbing. requests.post is always a fake; nothing touches the network."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import requests

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "probe_openai_models.py"
_spec = importlib.util.spec_from_file_location("probe_openai_models", _SCRIPT)
probe_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe_mod)

FAKE_KEY = "sk-proj-FAKEkey_1234567890abcdefXYZ"


class _Resp:
    def __init__(self, status: int, data=None, text: str = ""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def _ok(finish="stop"):
    return _Resp(200, {"choices": [{"finish_reason": finish, "message": {"content": "{}"}}]})


def _err(status, code, message, type_="invalid_request_error"):
    return _Resp(status, {"error": {"code": code, "type": type_, "message": message}})


def _fake_post(responses):
    """Returns (post, calls). Each element of `responses` is a response or an exception to raise."""
    calls = []

    def post(url, *, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return post, calls


def _no_sleep(_):
    return None


def test_body_has_no_temperature_and_caps_completion_tokens() -> None:
    b = probe_mod.body("o3")
    assert "temperature" not in b and "max_tokens" not in b
    assert b["max_completion_tokens"] == 64
    rf = b["response_format"]["json_schema"]
    assert rf["strict"] is True and rf["schema"]["additionalProperties"] is False
    assert probe_mod.body("gpt-4o", temperature=0.0)["temperature"] == 0.0


def test_classify_success_and_error_fields() -> None:
    assert probe_mod.classify(_ok("length")) == {
        "status": 200, "code": None, "type": None, "message": None, "finish_reason": "length"}
    rec = probe_mod.classify(_err(400, "unsupported_value", "x" * 500))
    assert (rec["status"], rec["code"], rec["type"]) == (400, "unsupported_value",
                                                         "invalid_request_error")
    assert rec["message"] == "x" * 200
    assert probe_mod.classify(_Resp(502, None, "bad gateway"))["message"] == "bad gateway"


def test_probe_runs_p2_only_for_p1_successes_with_temperature_zero() -> None:
    post, calls = _fake_post([_ok(), _err(404, "model_not_found", "nope"), _ok(),
                              _err(400, "unsupported_value", "temperature not supported"), _ok()])
    results = probe_mod.probe(FAKE_KEY, ["a", "b", "c"], post=post, sleep=_no_sleep)

    p1_bodies, p2_bodies = [c["json"] for c in calls[:3]], [c["json"] for c in calls[3:]]
    assert all("temperature" not in b for b in p1_bodies)
    assert [b["model"] for b in p2_bodies] == ["a", "c"]
    assert all(b["temperature"] == 0.0 for b in p2_bodies)
    assert all(c["headers"]["Authorization"] == f"Bearer {FAKE_KEY}" and c["timeout"] == 120
               for c in calls)
    assert "p2" not in results["b"]
    assert results["a"]["p2"]["code"] == "unsupported_value"
    assert results["c"]["p2"]["status"] == 200


@pytest.mark.parametrize("status", [401, 403])
def test_probe_aborts_on_first_auth_failure(status) -> None:
    post, calls = _fake_post([_err(status, "invalid_api_key", "bad key")])
    with pytest.raises(probe_mod.AuthAbort):
        probe_mod.probe(FAKE_KEY, ["a", "b"], post=post, sleep=_no_sleep)
    assert len(calls) == 1


def test_later_401_is_recorded_not_aborted() -> None:
    post, _ = _fake_post([_ok(), _err(403, None, "no access to model"), _ok()])
    results = probe_mod.probe(FAKE_KEY, ["a", "b"], post=post, sleep=_no_sleep)
    assert results["b"]["p1"]["status"] == 403


def test_429_retries_three_times_then_records() -> None:
    post, calls = _fake_post([_err(429, "rate_limit_exceeded", "slow")] * 3 + [_ok()])
    assert probe_mod.call(FAKE_KEY, {}, post=post, sleep=_no_sleep)["status"] == 200
    assert len(calls) == 4

    post, calls = _fake_post([_err(429, "rate_limit_exceeded", "slow")] * 4)
    assert probe_mod.call(FAKE_KEY, {}, post=post, sleep=_no_sleep)["status"] == 429
    assert len(calls) == 4


def test_per_id_exception_is_recorded_and_probe_continues() -> None:
    post, _ = _fake_post([requests.ConnectionError("reset"), _ok(), _ok()])
    results = probe_mod.probe(FAKE_KEY, ["a", "b"], post=post, sleep=_no_sleep)
    assert results["a"]["p1"] == {"status": None, "exception": "ConnectionError: reset"}
    assert results["b"]["p2"]["status"] == 200


def test_scrub_masks_a_key_echoed_in_an_error_message() -> None:
    echoed = f"Incorrect API key provided: {FAKE_KEY}. Also sk-proj-****************wxyz."
    post, _ = _fake_post([_ok(), _err(401, "invalid_api_key", echoed)])
    results = probe_mod.probe(FAKE_KEY, ["a", "b"], post=post, sleep=_no_sleep)

    text = probe_mod.scrub_and_dump(results, FAKE_KEY)
    assert FAKE_KEY not in text and "wxyz" not in text
    assert json.loads(text)["b"]["p1"]["message"] == "Incorrect API key provided: sk-***. Also sk-***."


def test_scrub_refuses_when_a_non_sk_key_survives() -> None:
    key = "plainkey_0123456789abcdef"
    with pytest.raises(RuntimeError) as exc:
        probe_mod.scrub_and_dump({"a": {"p1": {"message": f"bad {key}"}}}, key)
    assert key not in str(exc.value)


def test_main_writes_nothing_on_auth_abort(monkeypatch, tmp_path) -> None:
    out = tmp_path / "openai-probe.json"
    monkeypatch.setattr(probe_mod, "OUT_PATH", out)
    monkeypatch.setattr(probe_mod.getpass, "getpass", lambda _prompt: FAKE_KEY)
    monkeypatch.setattr(probe_mod.time, "sleep", _no_sleep)
    monkeypatch.setattr(probe_mod.requests, "post", lambda *a, **k: _err(401, "invalid_api_key", "x"))
    assert probe_mod.main() == 1
    assert not out.exists()


def test_summary_counts() -> None:
    results = {
        "a": {"p1": {"status": 200}, "p2": {"status": 400, "code": "unsupported_value"}},
        "b": {"p1": {"status": 200}, "p2": {"status": 400, "code": "unsupported_value"}},
        "c": {"p1": {"status": 200}, "p2": {"status": None, "exception": "Timeout: slow"}},
        "d": {"p1": {"status": 404}},
        "e": {"p1": {"status": 400}},
        "f": {"p1": {"status": None, "exception": "ConnectionError: x"}},
        "g": {"p1": {"status": 200}, "p2": {"status": 200}},
    }
    s = probe_mod.summary(results)
    assert "P1: 200=4 400=1 404=1 other=1" in s
    assert "failures: 3" in s
    assert "unsupported_value: 2" in s and "Timeout: 1" in s
