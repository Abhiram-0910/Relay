# backend/tests/test_byok.py
from unittest.mock import patch, MagicMock

import pytest
import requests

from app.byok import ByokFetchError, ByokKeyMissingError, fetch_byok_key, run_with_optional_byok

_KEY_BODY = {"apiKey": "sk-live-abcdef123456", "provider": "openai", "model": "gpt-5"}


def _mock_response(status_code, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    return resp


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr("app.byok.time.sleep", lambda s: None)


@patch("app.byok.requests.get")
def test_fetch_byok_key_success(mock_get):
    mock_get.return_value = _mock_response(200, _KEY_BODY)
    api_key, provider, model = fetch_byok_key("https://worker.example", "run-1", "secret")
    assert api_key == "sk-live-abcdef123456"
    assert provider == "openai"
    assert model == "gpt-5"
    # Confirms the auth header is set correctly — the callback is the only
    # thing that can ever retrieve the key.
    called_headers = mock_get.call_args.kwargs["headers"]
    assert called_headers["Authorization"] == "Bearer secret"
    assert mock_get.call_args.args[0] == "https://worker.example/api/runs/run-1/byok-key"
    assert mock_get.call_count == 1


@patch("app.byok.requests.get")
def test_fetch_byok_key_retries_once_on_5xx_then_succeeds(mock_get):
    mock_get.side_effect = [_mock_response(500), _mock_response(200, _KEY_BODY)]
    assert fetch_byok_key("https://worker.example", "run-1", "secret")[0] == "sk-live-abcdef123456"
    assert mock_get.call_count == 2


# Every BYOK failure mode: (responses/exceptions requests.get yields in order, expected error,
# expected number of fetch attempts). 4xx is never retried; network/5xx get exactly one retry.
_FAILURES = {
    "404_missing": ([_mock_response(404)], ByokKeyMissingError, 1),
    "401_unauthorized": ([_mock_response(401)], ByokFetchError, 1),
    "403_other_4xx": ([_mock_response(403)], ByokFetchError, 1),
    "500_twice": ([_mock_response(500), _mock_response(503)], ByokFetchError, 2),
    "network_twice": ([requests.ConnectionError("boom"), requests.Timeout("slow")], ByokFetchError, 2),
    "500_then_404": ([_mock_response(500), _mock_response(404)], ByokKeyMissingError, 2),
    "200_without_key": ([_mock_response(200, {"provider": "openai"})], ByokFetchError, 1),
}


@pytest.mark.parametrize("responses,expected,calls", _FAILURES.values(), ids=_FAILURES.keys())
def test_byok_failure_fails_closed_and_never_reaches_provider_call(responses, expected, calls):
    provider_call = MagicMock(name="provider_call")  # stands in for _live_validate → self_correct
    with patch("app.byok.requests.get", side_effect=responses) as mock_get:
        with pytest.raises(expected):
            run_with_optional_byok("https://worker.example", "run-1", "secret",
                                   has_byok=True, provider_call=provider_call)
    assert mock_get.call_count == calls
    provider_call.assert_not_called()


@patch("app.byok.requests.get")
def test_run_with_optional_byok_passes_fetched_key_to_provider_call(mock_get):
    mock_get.return_value = _mock_response(200, _KEY_BODY)
    seen = {}

    def provider_call(api_key, provider, model):
        seen.update(api_key=api_key, provider=provider, model=model)
        return "provider-result"

    result = run_with_optional_byok(
        "https://worker.example", "run-1", "secret", has_byok=True, provider_call=provider_call
    )
    assert result == "provider-result"
    # provider + model must reach the provider call, not just the key — that's
    # what routes the run to the right adapter and pins the model (Step 2).
    assert seen == {"api_key": "sk-live-abcdef123456", "provider": "openai", "model": "gpt-5"}


@patch("app.byok.requests.get")
def test_run_with_optional_byok_network_error_then_success(mock_get):
    mock_get.side_effect = [requests.ConnectionError("blip"), _mock_response(200, _KEY_BODY)]
    provider_call = MagicMock(return_value="ok")
    assert run_with_optional_byok("https://worker.example", "run-1", "secret",
                                  has_byok=True, provider_call=provider_call) == "ok"
    provider_call.assert_called_once_with("sk-live-abcdef123456", "openai", "gpt-5")


def test_run_with_optional_byok_skips_fetch_when_no_byok():
    with patch("app.byok.requests.get") as mock_get:
        seen = {}

        def provider_call(api_key, provider, model):
            seen.update(api_key=api_key, provider=provider, model=model)
            return "ok"

        result = run_with_optional_byok(
            "https://worker.example", "run-1", "secret", has_byok=False, provider_call=provider_call
        )
        assert result == "ok"
        assert seen == {"api_key": None, "provider": None, "model": None}
        mock_get.assert_not_called()
