"""Tests for the Gemini self-correction loop.

The loop-logic tests are hermetic: the sandbox and the Gemini corrector are both injected with
fakes, so no Docker, no network, no API key, no quota is used. One `-m live` test does a REAL
Gemini correction of a deliberately-broken Open-Meteo client through the real sandbox.
"""
from __future__ import annotations

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
