# Egress sidecar: a single pinned TCP relay for one validation run.
# It joins BOTH the sandbox's --internal network and the normal bridge, and forwards exclusively
# to the one pre-validated IP:port supplied on the command line at run time (never a static
# config). The sandbox container itself has no external route — this relay is its only way out.
FROM alpine:3.20

RUN apk add --no-cache socat

# No ENTRYPOINT/CMD: the socat invocation (with the per-run validated IP:port) is passed by
# run_in_sandbox at `docker run` time, so the allowlist is templated per run, never reused.
