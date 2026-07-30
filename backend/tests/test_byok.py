# backend/tests/test_byok.py
from unittest.mock import patch, MagicMock

import pytest

from app.byok import fetch_byok_key, run_with_optional_byok, ByokFetchError


def _mock_response(status_code, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    return resp


@patch("app.byok.requests.get")
def test_fetch_byok_key_success(mock_get):
    mock_get.return_value = _mock_response(
        200, {"apiKey": "sk-live-abcdef123456", "provider": "openai", "model": "gpt-5"}
    )
    api_key, provider, model = fetch_byok_key("https://worker.example", "run-1", "secret")
    assert api_key == "sk-live-abcdef123456"
    assert provider == "openai"
    assert model == "gpt-5"
    # Confirms the auth header is set correctly — the callback is the only
    # thing that can ever retrieve the key.
    called_headers = mock_get.call_args.kwargs["headers"]
    assert called_headers["Authorization"] == "Bearer secret"
    assert mock_get.call_args.args[0] == "https://worker.example/api/runs/run-1/byok-key"


@patch("app.byok.requests.get")
def test_fetch_byok_key_not_found_returns_none_not_error(mock_get):
    mock_get.return_value = _mock_response(404)
    api_key, provider, model = fetch_byok_key("https://worker.example", "run-1", "secret")
    assert api_key is None
    assert provider is None
    assert model is None


@patch("app.byok.requests.get")
def test_fetch_byok_key_unauthorized_raises(mock_get):
    mock_get.return_value = _mock_response(401)
    with pytest.raises(ByokFetchError):
        fetch_byok_key("https://worker.example", "run-1", "wrong-secret")


@patch("app.byok.requests.get")
def test_fetch_byok_key_network_error_raises_byok_error_not_generic(mock_get):
    import requests

    mock_get.side_effect = requests.ConnectionError("boom")
    with pytest.raises(ByokFetchError):
        fetch_byok_key("https://worker.example", "run-1", "secret")


@patch("app.byok.requests.get")
def test_run_with_optional_byok_passes_fetched_key_to_provider_call(mock_get):
    mock_get.return_value = _mock_response(
        200, {"apiKey": "sk-live-abcdef123456", "provider": "openai", "model": "gpt-5"}
    )
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


@patch("app.byok.requests.get")
def test_run_with_optional_byok_falls_back_gracefully_on_fetch_failure(mock_get):
    mock_get.return_value = _mock_response(401)
    seen = {}

    def provider_call(api_key, provider, model):
        seen.update(api_key=api_key, provider=provider, model=model)
        return "used-shared-key"

    # Must NOT raise — a byok-fetch failure should degrade to the shared
    # free-tier key, not crash the run (provider/model fall back to None too).
    result = run_with_optional_byok(
        "https://worker.example", "run-1", "secret", has_byok=True, provider_call=provider_call
    )
    assert result == "used-shared-key"
    assert seen == {"api_key": None, "provider": None, "model": None}
