# Development

## Prerequisites

- Docker with Compose v2 (verified working with Docker 29.x and Compose v5.x).
- `make` (optional convenience wrapper).

All tooling runs inside the container image. Do not install project dependencies on the host.

## Layout

```text
src/fiscal_astra/        application package
  api/                   FastAPI app and entrypoint
  config/                environment-backed settings
tests/                   pytest suite
docs/                    implementation documentation
Dockerfile               application image
docker-compose.yml       api and tools services
Makefile                 quality-gate shortcuts
pyproject.toml           project metadata and tool config
uv.lock                  pinned dependency lock
```

## Environment configuration

Settings are read from environment variables with the `ASTRA_` prefix, optionally via a
local `.env` file. Copy `.env.example` to `.env` and adjust as needed. `.env` is gitignored
and must never be committed.

The `api` service treats `.env` as optional, so `docker compose up` works without it.

## Quality gates

```bash
make build        # build the image
make verify       # format-check, lint, typecheck, test
```

Or run individual gates:

```bash
make format-check
make lint
make typecheck
make test
make format       # apply formatting; persists to the working tree
make lock         # regenerate uv.lock
make shell        # interactive shell in the tools container
```

The `tools` service bind-mounts the repository, sets `PYTHONPATH=/app/src`, and redirects
tool caches to `/tmp` so the working tree stays clean.

## Adding a dependency

1. Edit `pyproject.toml` and state the technical reason.
2. Regenerate the lock inside the container: `make lock`.
3. Rebuild the image: `make build`.
4. Re-run `make verify`.

Do not add economic models, simulation engines, databases, queues, or LLM runtimes during
Fase 0.