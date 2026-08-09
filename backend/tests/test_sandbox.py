"""Tests for the Docker sandbox runner.

The SSRF guard tests are pure (no Docker, no network beyond localhost/DNS mocking) and run by
default. The end-to-end test builds the real Open-Meteo client and runs it in a real container;
it is marked `live` (excluded by default) and skips if Docker or the image is unavailable.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app import sandbox
from app.sandbox import SSRFError, resolve_and_validate_host

OPEN_METEO_SPEC_URL = "https://raw.githubusercontent.com/open-meteo/open-meteo/main/openapi/forecast.yml"
OPEN_METEO_BASE_URL = "https://api.open-meteo.com"


# --- SSRF guard (pure) -------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",           # loopback
    "http://10.0.0.5/x",            # RFC1918
    "http://192.168.1.1/x",         # RFC1918
    "http://172.16.0.1/x",          # RFC1918
    "http://169.254.169.254/x",     # link-local — cloud metadata
    "http://[::1]/x",               # IPv6 loopback
    "http://100.64.0.1/x",          # CGNAT
])
def test_ssrf_guard_rejects_private_literals(url: str) -> None:
    with pytest.raises(SSRFError):
        resolve_and_validate_host(url)


def test_ssrf_guard_allows_public_literal() -> None:
    assert resolve_and_validate_host("https://8.8.8.8") == "8.8.8.8"


def test_ssrf_guard_rejects_when_dns_resolves_private(monkeypatch) -> None:
    # A public-looking hostname that (maliciously) resolves to a private IP must still be blocked.
    monkeypatch.setattr(
        sandbox.socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))],
    )
    with pytest.raises(SSRFError):
        resolve_and_validate_host("https://evil.example.com")


def test_ssrf_guard_no_host() -> None:
    with pytest.raises(SSRFError):
        resolve_and_validate_host("not-a-url")


def test_missing_image_raises_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "_image_exists", lambda image: False)
    with pytest.raises(sandbox.SandboxError, match="docker build"):
        sandbox.run_in_sandbox(Path("/nonexistent"), {"base_url": OPEN_METEO_BASE_URL})


def test_wall_clock_timeout_reports_status_timeout(monkeypatch) -> None:
    """A container that runs past `timeout` is force-killed (subprocess.TimeoutExpired) — that's
    STATUS_TIMEOUT now, distinct from STATUS_CALL_FAILED (Step 3: the corrector shouldn't burn an
    attempt trying to "fix" the target API being slow)."""
    monkeypatch.setattr(sandbox, "_image_exists", lambda image: True)
    # Every docker control-plane call (network create/sidecar run/connect/inspect/logs/rm) goes
    # through this helper — fake it so nothing real gets shelled out. "listening on" satisfies
    # _wait_sidecar_ready's readiness check regardless of which call this stands in for.
    monkeypatch.setattr(
        sandbox, "_docker",
        lambda *args, check=False: subprocess.CompletedProcess(args, 0, stdout="listening on\n", stderr=""),
    )

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    # Literal IP — resolve_and_validate_host runs for real but needs no actual DNS query.
    report = sandbox.run_in_sandbox(Path("/nonexistent"), {"base_url": "https://8.8.8.8"}, timeout=1)

    assert report["status"] == sandbox.STATUS_TIMEOUT, report
    assert not report["verified_live"], report
    assert not report["passed"], report


# --- End-to-end: real generation + real container + real API -----------------------------------

def _docker_and_image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return all(
        subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0
        for img in (sandbox.SANDBOX_IMAGE, sandbox.SIDECAR_IMAGE)
    )


def _build_open_meteo_pkg(pkg_dir: Path) -> dict:
    """Generate the Open-Meteo /v1/forecast client into pkg_dir; return its call-spec."""
    import requests
    import yaml

    from app.generate import generate_endpoint_client

    spec = yaml.safe_load(requests.get(OPEN_METEO_SPEC_URL, timeout=30).text)
    generated = generate_endpoint_client(spec, "/v1/forecast", "get", pkg_dir)

    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "models.py").write_text(generated["models_code"])
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
@pytest.mark.skipif(not _docker_and_image_available(),
                    reason="needs docker + `make sandbox-build`")
def test_open_meteo_end_to_end(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "client_pkg"
    pkg_dir.mkdir()
    call_spec = _build_open_meteo_pkg(pkg_dir)

    report = sandbox.run_in_sandbox(pkg_dir, call_spec)

    # The legitimate call still succeeds through the new isolated network path (internal net +
    # pinned sidecar) — proves isolation didn't break the one destination we DO allow.
    assert report["verified_live"], f"did not reach live API: {report}"
    assert report["status"] in {sandbox.STATUS_PASS, sandbox.STATUS_VALIDATION_FAILED}
    assert report["endpoint"] == {"method": "GET", "path": "/v1/forecast"}


# A client that ignores base_url and hardcodes a request to a DIFFERENT public IP — the exact
# thing untrusted LLM-patched code could do in Task 9. 1.1.1.1 is perfectly legitimate/public;
# it must still be unreachable because the sandbox has no route to anything but the sidecar.
_ESCAPE_CLIENT = '''\
import requests


class EscapeClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def exfiltrate(self) -> str:
        return requests.get("https://1.1.1.1", timeout=8).text
'''


@pytest.mark.live
@pytest.mark.skipif(not _docker_and_image_available(),
                    reason="needs docker + `make sandbox-build`")
def test_hardcoded_other_public_ip_is_unreachable(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "client_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "models.py").write_text("")
    (pkg_dir / "client.py").write_text(_ESCAPE_CLIENT)

    # Sidecar is set up for Open-Meteo (a valid target); the code tries to reach 1.1.1.1 anyway.
    report = sandbox.run_in_sandbox(pkg_dir, {
        "base_url": OPEN_METEO_BASE_URL,
        "module": "client",
        "class_name": "EscapeClient",
        "method": "exfiltrate",
        "kwargs": {},
    })

    assert report["status"] == sandbox.STATUS_CALL_FAILED, report
    assert not report["verified_live"], report
