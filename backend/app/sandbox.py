"""Host-side Docker sandbox runner for validating a generated client against a live API.

One `--rm` container per run. The container is resource- and time-capped and its rootfs is
read-only; the generated client package and a call-spec are mounted read-only.

Network policy — two composed layers, read before touching:
  1. PRE-FLIGHT (`resolve_and_validate_host`): resolve the target host and refuse to start
     anything if ANY resolved IP is private / loopback / link-local / CGNAT / reserved (e.g. cloud
     metadata at 169.254.169.254). This decides the single IP the sandbox is allowed to reach.
  2. NETWORK ISOLATION (this module): the sandbox container joins ONLY a per-run `--internal`
     Docker network with no external route at all. A socat sidecar joins both that internal
     network and the normal bridge, and forwards exclusively to the one pre-validated IP:port —
     that allowlist is the socat argv, rebuilt every run, never a shared/static config. The
     sandbox pins the target hostname to the sidecar's internal IP via `--add-host`, so the
     unchanged generated client's normal call to self.base_url is the ONLY reachable destination.
     Any other host/IP the code tries — even a perfectly legitimate public one — has no route.
     TLS stays end-to-end: socat is a raw byte pipe, so the real server presents its real cert
     against the real SNI; nothing terminates or re-resolves in between.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

RUNNER_PATH = Path(__file__).parent.parent / "sandbox" / "runner.py"
SANDBOX_IMAGE = "relay-sandbox"
SIDECAR_IMAGE = "relay-sidecar"
RESULT_PREFIX = "RELAY_RESULT:"
SIDECAR_READY_TIMEOUT = 5.0  # seconds to wait for socat to bind its listener
_DOCKER_CMD_TIMEOUT = 30     # seconds for individual docker control-plane calls

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
# HOST-level wall-clock kill only (the whole container ran past `timeout` and was force-killed) —
# distinct from an in-container request-level timeout, which runner.py still reports as
# STATUS_CALL_FAILED (requests.Timeout is a requests.RequestException there; out of scope here).
STATUS_TIMEOUT = "sandbox_timeout"
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


def _target_port(base_url: str) -> int:
    parsed = urlparse(base_url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _docker(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True,
        timeout=_DOCKER_CMD_TIMEOUT, check=check,
    )


def _container_ip(name: str, network: str) -> str:
    result = _docker(
        "inspect", "-f",
        '{{ (index .NetworkSettings.Networks "' + network + '").IPAddress }}',
        name,
    )
    ip = result.stdout.strip()
    if not ip:
        raise SandboxError(f"could not determine {name}'s IP on {network}: {result.stderr.strip()}")
    return ip


def _wait_sidecar_ready(name: str, timeout: float = SIDECAR_READY_TIMEOUT) -> None:
    """Block until socat logs that it has bound its listener, else raise."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        logs = _docker("logs", name)
        if "listening on" in (logs.stdout + logs.stderr).lower():
            return
        time.sleep(0.1)
    raise SandboxError(f"sidecar {name} did not start listening within {timeout}s")


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

    for required_image, dockerfile in ((image, "Dockerfile"), (SIDECAR_IMAGE, "sidecar.Dockerfile")):
        if not _image_exists(required_image):
            raise SandboxError(
                f"{required_image!r} image not found — run "
                f"`docker build -t {required_image} -f sandbox/{dockerfile} .` "
                f"(or `make sandbox-build`) first"
            )

    try:
        resolved_ip = resolve_and_validate_host(call_spec["base_url"])
    except SSRFError as exc:
        return _report(STATUS_SSRF_BLOCKED, str(exc), endpoint=endpoint, resolved_ip=None)

    host = urlparse(call_spec["base_url"]).hostname
    port = _target_port(call_spec["base_url"])
    run_id = uuid.uuid4().hex[:12]
    network = f"relay-net-{run_id}"
    sidecar = f"relay-sidecar-{run_id}"
    container = f"relay-sbx-{run_id}"

    with tempfile.TemporaryDirectory() as spec_dir:
        spec_path = Path(spec_dir) / "call_spec.json"
        spec_path.write_text(json.dumps(call_spec))

        try:
            # Internal network: no external route for anything attached to it.
            _docker("network", "create", "--internal", network, check=True)

            # Sidecar: the ONLY egress. Its allowlist is this argv — the one validated IP:port,
            # forced IPv4 to match the pinned address. Rebuilt per run, never a shared config.
            _docker(
                "run", "-d", "--name", sidecar, "--network", network,
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
                SIDECAR_IMAGE,
                "socat", "-d", "-d",
                f"TCP4-LISTEN:{port},fork,reuseaddr", f"TCP4:{resolved_ip}:{port}",
                check=True,
            )
            # Give the sidecar (and only the sidecar) external access.
            _docker("network", "connect", "bridge", sidecar, check=True)
            sidecar_ip = _container_ip(sidecar, network)
            _wait_sidecar_ready(sidecar)

            cmd = [
                "docker", "run", "--rm", "--name", container,
                "--network", network,                    # internal only — no direct external route
                "--add-host", f"{host}:{sidecar_ip}",    # target hostname resolves to the sidecar
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
                # --rm cleans up on normal exit; a wall-clock kill leaves it, so force it.
                _docker("rm", "-f", container)
                return _report(
                    STATUS_TIMEOUT,
                    f"wall-clock timeout after {timeout}s — container killed",
                    endpoint=endpoint, resolved_ip=resolved_ip,
                )
        except subprocess.CalledProcessError as exc:
            raise SandboxError(f"sandbox network setup failed: {exc.stderr or exc}") from exc
        finally:
            # Always tear down, even on timeout/setup failure. --rm may have handled the sandbox.
            _docker("rm", "-f", container)
            _docker("rm", "-f", sidecar)
            _docker("network", "rm", network)

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
