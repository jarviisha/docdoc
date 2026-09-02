# One image, two process types.
#
# The API and the worker run the same code with the same dependencies and differ
# only in which entry point starts. Two images would be two things to build, tag,
# scan, and keep in step, to express a difference that is one argv apart — and a
# deployment running mismatched API and worker images is a bug with no error
# message, because both would start cleanly and disagree about the run table.
#
# Selected at runtime, not at build time (FR-075):
#
#   docker run <image>                       # the API, via the default CMD
#   docker run <image> docdoc worker         # a worker
#   docker run <image> docdoc migrate        # schema, explicitly, never on boot
#
# Build from the repository root, not from this directory:
#   docker build -f packaging/docker/Dockerfile -t docdoc .

FROM python:3.11-slim-bookworm AS builder

# uv resolves and installs; it is not in the runtime image.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /build

# Dependency metadata first, so a source-only change does not re-resolve. The
# README and LICENSE are copied because the wheel build reads both.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

# `--all-extras` is deliberately NOT used. The image carries what a deployment
# runs: the HTTP interface, run state, shared storage, and the provider adapters.
# It does not carry `docdoc[pdf]`, because PyMuPDF is AGPL-3.0 and baking it into
# a distributed image is an obligation this project's own README warns about
# (ADR-0001). A deployment that wants the native PDF path installs it knowingly.
RUN uv pip install --system --no-cache \
        ".[api,postgres,s3,google,azure,gcv]"

# ---------------------------------------------------------------------------

FROM python:3.11-slim-bookworm

# Not root. Artifacts hold extracted values and blobs hold whole source
# documents; the store is created readable only by the owning account
# (ADR-0010 §1), which is worth nothing if that account is root.
RUN useradd --create-home --uid 10001 docdoc

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/docdoc /usr/local/bin/docdoc

USER docdoc
WORKDIR /home/docdoc

# `DOCDOC_STORE_ROOT` is deliberately unset. ADR-0010 §1 gives the store no
# default because where documents come to rest is an operator's decision, and an
# image that picked one would be making it for them — silently, inside a
# container layer that disappears on restart.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Liveness only. Readiness is strict and covers the database (FR-087), so using
# it here would make Docker restart a container whose only fault is that Postgres
# is down — turning a dependency outage into a restart loop. The orchestrator
# probes /readyz; the container runtime probes /healthz.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "--factory", "docdoc.api.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
