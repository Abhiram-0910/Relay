# Minimal image for running a generated client against a live API, one --rm container per run.
# Built ONCE and reused (see `make sandbox-build`), never pip-installed per run.
FROM python:3.12-slim

# Only what a generated client needs: requests (HTTP) + pydantic v2 (response models).
RUN pip install --no-cache-dir requests==2.* pydantic==2.*

# Never write bytecode into the read-only mounted client package.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Drop to a non-root user; rootfs is mounted read-only at run time anyway.
RUN useradd --create-home --uid 10001 sandbox
USER sandbox
WORKDIR /sandbox

ENTRYPOINT ["python", "/sandbox/runner.py"]
