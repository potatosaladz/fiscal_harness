# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

# uv is used to install the pinned dependency set from the lockfile.
COPY --from=ghcr.io/astral-sh/uv:0.5.31 /uv /uvx /bin/

WORKDIR /app

# Project metadata first so dependency layers cache independently of source edits.
# uv.lock is required: builds must be reproducible, so there is no unlocked fallback.
COPY pyproject.toml uv.lock ./

# Install runtime and dev dependencies without the project itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --frozen

# Application sources and tests.
COPY src ./src
COPY tests ./tests
COPY docs ./docs
COPY AGENTS.md README.md ./

# Install the project itself into the environment.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

CMD ["uvicorn", "fiscal_astra.api.app:app", "--host", "0.0.0.0", "--port", "8000"]