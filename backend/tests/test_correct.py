"""Tests for the Gemini self-correction loop.

The loop-logic tests are hermetic: the sandbox and the Gemini corrector are both injected with
fakes, so no Docker, no network, no API key, no quota is used. One `-m live` test does a REAL
Gemini correction of a deliberately-broken Open-Meteo client through the real sandbox.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app import correct, sandbox
from app.correct import Attempt, CorrectionResult, self_correct

OPEN_METEO_SPEC_URL = "https://raw.githubusercontent.com/open-meteo/open-meteo/main/openapi/forecast.yml"
OPEN_METEO_BASE_URL = "https://api.open-meteo.com"

# A bogus required field the live response will never contain — forces a pydantic ValidationError
# after a successful HTTP call, i.e. the `verified_live_validation_failed` path.
_BROKEN_FIELD = "    bogus_required_field: int\n"


# --- hermetic loop logic (fake sandbox + fake corrector) ---------------------------------------

def _make_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "client_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "models.py").write_text("# broken model\n")
    (pkg / "client.py").write_text("# client\n")
    return pkg


def _report(status: str) -> dict:
    return {"status": status, "detail": f"detail for {status}"}


def test_stops_and_succeeds_on_first_fix(tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path)
    calls = {"corrector": 0, "sandbox": 0}

    def fake_corrector(model_id, call_spec, report, models_code, client_code):
        calls["corrector"] += 1
        return {"models_py": "# fixed model\n", "client_py": client_code}

    def fake_sandbox(pkg_dir, call_spec):
        calls["sandbox"] += 1
        return _report(sandbox.STATUS_PASS)  # first patch fixes it

    result = self_correct(
        pkg, {"base_url": OPEN_METEO_BASE_URL},
        _report(sandbox.STATUS_VALIDATION_FAILED),
        run_sandbox=fake_sandbox, corrector=fake_corrector,
    )

    assert result.succeeded
    assert calls == {"corrector": 1, "sandbox": 1}  # stopped as soon as it passed
    assert len(result.attempts) == 1
    assert result.attempts[0].changed_files == ["models.py"]
    assert (pkg / "models.py").read_text() == "# fixed model\n"


def test_cap_is_two_flash_then_one_pro_then_hard_fail(tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path)
    models_used: list[str] = []

    def never_fix_corrector(model_id, call_spec, report, models_code, client_code):
        models_used.append(model_id)
        return {"models_py": f"# attempt by {model_id}\n", "client_py": client_code}

    def always_fail_sandbox(pkg_dir, call_spec):
        return _report(sandbox.STATUS_CALL_FAILED)

    result = self_correct(
        pkg, {"base_url": OPEN_METEO_BASE_URL},
        _report(sandbox.STATUS_CALL_FAILED),
        run_sandbox=always_fail_sandbox, corrector=never_fix_corrector,
    )

    assert not result.succeeded
    assert models_used == [correct.GEMINI_FLASH, correct.GEMINI_FLASH, correct.GEMINI_PRO]
    assert len(result.attempts) == 3  # full history retained


def test_corrector_error_stops_the_ladder(tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path)

    def boom_corrector(model_id, call_spec, report, models_code, client_code):
        raise RuntimeError("quota exhausted")

    def unused_sandbox(pkg_dir, call_spec):  # must not be reached
        raise AssertionError("sandbox should not run after a corrector error")

    result = self_correct(
        pkg, {"base_url": OPEN_METEO_BASE_URL},
        _report(sandbox.STATUS_CALL_FAILED),
        run_sandbox=unused_sandbox, corrector=boom_corrector,
    )

    assert not result.succeeded
    assert len(result.attempts) == 1
    assert result.attempts[0].status_after == "corrector_error"


def test_refuses_to_correct_a_passing_run(tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path)
    with pytest.raises(AssertionError):
        self_correct(pkg, {}, _report(sandbox.STATUS_PASS))


def test_byok_pins_single_model_no_cross_model_escalation(tmp_path: Path) -> None:
    """BYOK (a chosen model) retries that ONE model up to the cap — never the shared-key
    flash→flash→pro escalation, which would spend the user's key on a costlier model."""
    pkg = _make_pkg(tmp_path)
    models_used: list[str] = []

    def never_fix_corrector(model_id, call_spec, report, models_code, client_code, *, api_key=None):
        models_used.append(model_id)
        return {"models_py": f"# {model_id}\n", "client_py": client_code}

    def always_fail_sandbox(pkg_dir, call_spec):
        return _report(sandbox.STATUS_CALL_FAILED)

    result = self_correct(
        pkg, {"base_url": OPEN_METEO_BASE_URL},
        _report(sandbox.STATUS_CALL_FAILED),
        run_sandbox=always_fail_sandbox, corrector=never_fix_corrector,
        api_key="sk-user", model="user-chosen-model",
    )

    assert not result.succeeded
    # Same attempt budget as the shared ladder, but all on the one chosen model.
    assert models_used == ["user-chosen-model"] * len(correct.DEFAULT_LADDER)
    assert correct.GEMINI_FLASH not in models_used and correct.GEMINI_PRO not in models_used


def test_provider_dispatch_resolves_wired_and_rejects_unknown() -> None:
    """All five providers are wired; an UNKNOWN provider (typo/unsupported) fails loudly rather
    than silently falling back to Gemini — that fail-loud guard is why _resolve_corrector exists."""
    assert correct._resolve_corrector("gemini") is correct.gemini_corrector
    assert correct._resolve_corrector(None) is correct.gemini_corrector  # default
    for provider in ("openai", "grok", "openrouter", "anthropic"):
        assert callable(correct._resolve_corrector(provider))
    with pytest.raises(NotImplementedError):
        correct._resolve_corrector("mistral")  # never registered


# --- openai_compatible adapter (OpenAI / Grok / OpenRouter — one wire protocol) ----------------

def _fake_chat_response(patch: dict):
    """A MagicMock shaped like a requests Response whose body is an OpenAI chat-completion carrying
    `patch` as the strict-json_schema content string."""
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": json.dumps(patch)}}]}
    return resp


def test_openai_compatible_request_shape_and_parse() -> None:
    from unittest.mock import patch as mock_patch

    fixed = {"models_py": "# fixed model\n", "client_py": "# client\n"}
    corrector = correct.openai_compatible_corrector("https://api.openai.com/v1")

    with mock_patch("app.correct.requests.post", return_value=_fake_chat_response(fixed)) as post:
        result = corrector("gpt-5", {"base_url": OPEN_METEO_BASE_URL, "endpoint": "/v1/forecast"},
                           _report(sandbox.STATUS_CALL_FAILED), "# m\n", "# c\n", api_key="sk-user")

    # Parsed into the SAME Patch dict shape gemini_corrector returns.
    assert result == fixed

    url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-user"

    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "gpt-5"
    rf = payload["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["required"] == ["models_py", "client_py"]
    assert rf["json_schema"]["schema"]["additionalProperties"] is False


def test_openai_compatible_registered_base_urls() -> None:
    """OpenAI/Grok/OpenRouter share the adapter but must hit their own base URLs."""
    from unittest.mock import patch as mock_patch

    expected = {
        "openai": "https://api.openai.com/v1/chat/completions",
        "grok": "https://api.x.ai/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    }
    fixed = {"models_py": "# m\n", "client_py": "# c\n"}
    for provider, want_url in expected.items():
        with mock_patch("app.correct.requests.post",
                        return_value=_fake_chat_response(fixed)) as post:
            correct.CORRECTORS[provider]("some-model", {}, _report(sandbox.STATUS_CALL_FAILED),
                                         "# m\n", "# c\n", api_key="sk-user")
        url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
        assert url == want_url, provider


def test_openai_compatible_requires_api_key() -> None:
    """BYOK-only: no shared key exists for these providers, so a missing key is a hard error, not a
    silent shared-key fallback (that path is Gemini-only)."""
    corrector = correct.openai_compatible_corrector("https://api.openai.com/v1")
    with pytest.raises(ValueError):
        corrector("gpt-5", {}, _report(sandbox.STATUS_CALL_FAILED), "# m\n", "# c\n")


def test_openai_compatible_routes_through_self_correct(tmp_path: Path) -> None:
    """End-to-end hermetic: self_correct(provider="openai", model=...) routes to the openai adapter,
    pins the chosen model, and its parsed patch flows through the normal loop."""
    from unittest.mock import patch as mock_patch

    pkg = _make_pkg(tmp_path)
    fixed = {"models_py": "# fixed model\n", "client_py": "# client\n"}

    def fake_sandbox(pkg_dir, call_spec):
        return _report(sandbox.STATUS_PASS)  # first patch fixes it

    with mock_patch("app.correct.requests.post", return_value=_fake_chat_response(fixed)) as post:
        result = self_correct(
            pkg, {"base_url": OPEN_METEO_BASE_URL}, _report(sandbox.STATUS_CALL_FAILED),
            run_sandbox=fake_sandbox, api_key="sk-user", provider="openai", model="gpt-5",
        )

    assert result.succeeded
    assert post.call_count == 1  # one model, fixed on first try (no cross-model escalation)
    assert post.call_args.kwargs["json"]["model"] == "gpt-5"
    url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert (pkg / "models.py").read_text() == "# fixed model\n"


# --- anthropic adapter (Messages API, forced tool-use structured output) -----------------------

def _fake_anthropic_response(patch: dict, *, extra_blocks: list | None = None):
    """A MagicMock shaped like a requests Response whose body is an Anthropic Messages reply
    carrying `patch` in a tool_use block. `extra_blocks` are prepended (e.g. a text block) to prove
    the parser scans for tool_use rather than assuming position."""
    from unittest.mock import MagicMock

    content = list(extra_blocks or [])
    content.append({"type": "tool_use", "name": "emit_client_patch", "input": patch})
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"content": content}
    return resp


def test_anthropic_request_shape_and_parse() -> None:
    from unittest.mock import patch as mock_patch

    fixed = {"models_py": "# fixed model\n", "client_py": "# client\n"}

    with mock_patch("app.correct.requests.post",
                    return_value=_fake_anthropic_response(fixed)) as post:
        result = correct.anthropic_corrector(
            "claude-opus-4-6", {"base_url": OPEN_METEO_BASE_URL, "endpoint": "/v1/forecast"},
            _report(sandbox.STATUS_CALL_FAILED), "# m\n", "# c\n", api_key="sk-ant-user")

    assert result == fixed  # same Patch dict shape as every other adapter

    url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
    assert url == "https://api.anthropic.com/v1/messages"

    headers = post.call_args.kwargs["headers"]
    assert headers["x-api-key"] == "sk-ant-user"       # NOT Authorization: Bearer
    assert headers["anthropic-version"] == "2023-06-01"

    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "claude-opus-4-6"
    assert payload["max_tokens"] >= 8192               # must cover two whole files
    assert payload["system"] == correct._SYSTEM_INSTRUCTION  # rules in system, not user msg
    # Structured output via forced tool-use, not response_format.
    assert "response_format" not in payload
    assert payload["tool_choice"] == {"type": "tool", "name": "emit_client_patch"}
    tool = payload["tools"][0]
    assert tool["name"] == "emit_client_patch"
    assert tool["input_schema"]["required"] == ["models_py", "client_py"]


def test_anthropic_parses_tool_use_among_other_blocks() -> None:
    """A leading text block must not confuse the parser — it scans for the tool_use block."""
    from unittest.mock import patch as mock_patch

    fixed = {"models_py": "# fixed\n", "client_py": "# c\n"}
    fake = _fake_anthropic_response(fixed, extra_blocks=[{"type": "text", "text": "here you go"}])
    with mock_patch("app.correct.requests.post", return_value=fake):
        result = correct.anthropic_corrector(
            "claude-opus-4-6", {}, _report(sandbox.STATUS_CALL_FAILED),
            "# m\n", "# c\n", api_key="sk-ant-user")
    assert result == fixed


def test_anthropic_requires_api_key() -> None:
    """BYOK-only: no shared Anthropic key, so a missing key is a hard error, not a fallback."""
    with pytest.raises(ValueError):
        correct.anthropic_corrector("claude-opus-4-6", {}, _report(sandbox.STATUS_CALL_FAILED),
                                    "# m\n", "# c\n")


def test_anthropic_routes_through_self_correct(tmp_path: Path) -> None:
    """End-to-end hermetic: self_correct(provider="anthropic", model=...) routes to the anthropic
    adapter, pins the chosen model, and its parsed patch flows through the loop."""
    from unittest.mock import patch as mock_patch

    pkg = _make_pkg(tmp_path)
    fixed = {"models_py": "# fixed model\n", "client_py": "# client\n"}

    def fake_sandbox(pkg_dir, call_spec):
        return _report(sandbox.STATUS_PASS)

    with mock_patch("app.correct.requests.post",
                    return_value=_fake_anthropic_response(fixed)) as post:
        result = self_correct(
            pkg, {"base_url": OPEN_METEO_BASE_URL}, _report(sandbox.STATUS_CALL_FAILED),
            run_sandbox=fake_sandbox, api_key="sk-ant-user",
            provider="anthropic", model="claude-opus-4-6",
        )

    assert result.succeeded
    assert post.call_count == 1
    assert post.call_args.kwargs["json"]["model"] == "claude-opus-4-6"
    url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
    assert url == "https://api.anthropic.com/v1/messages"
    assert (pkg / "models.py").read_text() == "# fixed model\n"


# --- live: real Gemini fixes a real broken client through the real sandbox ----------------------

def _live_ready() -> bool:
    if shutil.which("docker") is None or not os.environ.get("GEMINI_API_KEY"):
        return False
    return all(
        subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0
        for img in (sandbox.SANDBOX_IMAGE, sandbox.SIDECAR_IMAGE)
    )


def _build_broken_open_meteo_pkg(pkg_dir: Path) -> dict:
    """Generate the real Open-Meteo client, then inject a bogus required field into the response
    model so a live call validates-fails. Returns the call-spec."""
    import requests
    import yaml

    from app.generate import generate_endpoint_client

    spec = yaml.safe_load(requests.get(OPEN_METEO_SPEC_URL, timeout=30).text)
    generated = generate_endpoint_client(spec, "/v1/forecast", "get", pkg_dir)

    # Insert the bogus field right after the response model's `class ...:` line.
    lines = generated["models_code"].splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("class ") and generated["model_name"] in line:
            lines.insert(i + 1, _BROKEN_FIELD)
            break
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "models.py").write_text("".join(lines))
    (pkg_dir / "client.py").write_text(generated["client_code"])

    return {
        "base_url": OPEN_METEO_BASE_URL,
        "module": "client",
        "class_name": generated["class_name"],
        "method": generated["method_name"],
        "kwargs": {"latitude": 52.52, "longitude": 13.41},
        "endpoint": generated["endpoint"],
    }


@pytest.mark.live
@pytest.mark.skipif(not _live_ready(),
                    reason="needs docker + `make sandbox-build` + GEMINI_API_KEY")
def test_gemini_fixes_broken_open_meteo_client(tmp_path: Path) -> None:
    pkg = tmp_path / "client_pkg"
    pkg.mkdir()
    call_spec = _build_broken_open_meteo_pkg(pkg)

    pre = sandbox.run_in_sandbox(pkg, call_spec)
    assert pre["status"] == sandbox.STATUS_VALIDATION_FAILED, pre  # broken as intended

    result = self_correct(pkg, call_spec, pre)

    assert result.succeeded, [a.__dict__ for a in result.attempts]
    assert result.final_report["status"] == sandbox.STATUS_PASS
    assert "models.py" in result.attempts[0].changed_files
