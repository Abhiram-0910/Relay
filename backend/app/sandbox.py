"""Host-side Docker sandbox runner for validating a generated client against a live API.

One `--rm` container per run. The container is resource- and time-capped and its rootfs is
read-only; the generated client package and a call-spec are mounted read-only.

Network policy — read this before touching it:
  The only SSRF control that matters right now is the PRE-FLIGHT one: `resolve_and_validate_host`
  resolves the target hostname on the host and refuses to start a container if ANY resolved IP is
  private / loopback / link-local / CGNAT / reserved (e.g. cloud metadata at 169.254.169.254).
  We then pin that validated IP with `--add-host` so a DNS rebind can't swap it after the check
  (TOCTOU). We do NOT yet have a true per-host egress firewall — Docker has no native "allow
  exactly one host" without host iptables/nftables surgery, and it doesn't matter while the code
  we run is our OWN deterministic template output, which only ever calls the spec's target URL.
  # ponytail: real egress lockdown (block rogue direct-IP connections to other hosts) becomes
  # necessary in Task 9 when UNTRUSTED LLM-generated code runs here. Upgrade path: put the
  # container on a custom bridge with an nftables egress allowlist of the validated IP, or route
  # it through a pinned forward-proxy. Until then, do not claim network isolation we don't have.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

RUNNER_PATH = Path(__file__).parent.parent / "sandbox" / "runner.py"
SANDBOX_IMAGE = "relay-sandbox"
RESULT_PREFIX = "RELAY_RESULT:"

# Resource caps applied to every container. Named, not magic.
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MEMORY = "256m"
DEFAULT_CPUS = "1.0"
DEFAULT_PIDS_LIMIT = 128

# Only "verified_pass" counts as a pass in the validation report; the rest are failures that keep
# their distinct status so Task 9 knows whether to fix request-building or the response model.
STATUS_PASS = "verified_pass"
STATUS_VALIDATION_FAILED = "verified_live_validation_failed"
STATUS_CALL_FAILED = "call_failed"
STATUS_SSRF_BLOCKED = "ssrf_blocked"
# Statuses where a real HTTP call actually completed against the live target.
_VERIFIED_LIVE = {STATUS_PASS, STATUS_VALIDATION_FAILED}


class SSRFError(Exception):
    """The target host resolves to a private/reserved address and must not be contacted."""


class SandboxError(Exception):
    """The sandbox could not be run at all (e.g. missing image) — distinct from a call failing."""


def _is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """A publicly-routable address safe to contact from the sandbox.

    `is_global` already rejects private / loopback / link-local / reserved / unspecified AND
    CGNAT (100.64.0.0/10, which none of the individual is_* flags catch); multicast is the one
    global-scope range we still exclude explicitly.
    """
    return addr.is_global and not addr.is_multicast


def resolve_and_validate_host(base_url: str) -> str:
    """Resolve base_url's host and return a validated public IP to pin, else raise SSRFError.

    ALL resolved addresses (v4 and v6) must be public — we refuse if any one is private so a
    dual-stack host can't leak past the check on the family we didn't pin.
    """
    host = urlparse(base_url).hostname
    if not host:
        raise SSRFError(f"no host in base_url: {base_url!r}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFError(f"could not resolve {host!r}: {exc}") from exc

    ips = {info[4][0] for info in infos}
    pin: str | None = None
    for ip in ips:
        addr = ipaddress.ip_address(ip)
        if not _is_public(addr):
            raise SSRFError(f"{host!r} resolves to non-public address {ip} — refusing to contact")
        if pin is None or addr.version == 4:  # prefer an IPv4 to pin via --add-host
            pin = ip
    assert pin is not None  # ips is non-empty or getaddrinfo would have raised
    return pin


def _image_exists(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _parse_runner_output(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            return json.loads(line[len(RESULT_PREFIX):])
    return None


def _report(status: str, detail: str, *, endpoint: dict | None, resolved_ip: str | None,
            http_status: int | None = None) -> dict:
    """Build the honest per-run report. `passed` is true ONLY for a verified, validated call."""
    return {
        "endpoint": endpoint,
        "status": status,
        "passed": status == STATUS_PASS,
        "verified_live": status in _VERIFIED_LIVE,
        "http_status": http_status,
        "detail": detail,
        "resolved_ip": resolved_ip,
    }


def run_in_sandbox(
    pkg_dir: Path,
    call_spec: dict,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    memory: str = DEFAULT_MEMORY,
    cpus: str = DEFAULT_CPUS,
    pids_limit: int = DEFAULT_PIDS_LIMIT,
    image: str = SANDBOX_IMAGE,
) -> dict:
    """Run one generated client method in a locked-down container and return a structured report.

    `pkg_dir` is a directory holding the generated package (__init__.py, models.py, client.py).
    `call_spec` has keys: base_url, module, class_name, method, kwargs, and optional endpoint.
    """
    endpoint = call_spec.get("endpoint")

    if not _image_exists(image):
        raise SandboxError(
            f"{image!r} image not found — run "
            f"`docker build -t {image} -f sandbox/Dockerfile .` (or `make sandbox-build`) first"
        )

    try:
        resolved_ip = resolve_and_validate_host(call_spec["base_url"])
    except SSRFError as exc:
        return _report(STATUS_SSRF_BLOCKED, str(exc), endpoint=endpoint, resolved_ip=None)

    host = urlparse(call_spec["base_url"]).hostname
    container_name = f"relay-sbx-{uuid.uuid4().hex[:12]}"

    with tempfile.TemporaryDirectory() as spec_dir:
        spec_path = Path(spec_dir) / "call_spec.json"
        spec_path.write_text(json.dumps(call_spec))

        cmd = [
            "docker", "run", "--rm", "--name", container_name,
            "--network", "bridge",
            "--add-host", f"{host}:{resolved_ip}",     # pin validated IP; defeats DNS rebinding
            "--memory", memory, "--memory-swap", memory,  # equal => no swap
            "--cpus", cpus, "--pids-limit", str(pids_limit),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/tmp",
            "-v", f"{pkg_dir}:/sandbox/client_pkg:ro",
            "-v", f"{RUNNER_PATH}:/sandbox/runner.py:ro",
            "-v", f"{spec_path}:/sandbox/call_spec.json:ro",
            image,
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # --rm cleans up on normal exit; a wall-clock kill leaves the container, so force it.
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            return _report(
                STATUS_CALL_FAILED,
                f"wall-clock timeout after {timeout}s — container killed",
                endpoint=endpoint, resolved_ip=resolved_ip,
            )

    result = _parse_runner_output(proc.stdout)
    if result is None:
        detail = (proc.stderr or proc.stdout or "no output").strip()[:2000]
        return _report(
            STATUS_CALL_FAILED,
            f"no result from runner (exit {proc.returncode}): {detail}",
            endpoint=endpoint, resolved_ip=resolved_ip,
        )

    return _report(
        result["status"], result["detail"],
        endpoint=endpoint, resolved_ip=resolved_ip,
        http_status=result.get("http_status"),
    )
