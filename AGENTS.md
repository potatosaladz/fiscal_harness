# Fiscal Astra Project Development Guide

## Scope

Fiscal Astra is a local, auditable fiscal-policy decision-support system. This repository is currently being implemented from the specification documents in the repository root.

## Non-negotiable rules

1. Local LLMs must not perform numerical economic calculations.
2. All numerical calculations must be executed by a simulation engine or a deterministic computational tool invoked through a worker/tool boundary.
3. Never present a simulation result unless the numerical engine actually executed it.
4. Unexecuted numerical output must use `not_executed`; it must not use zero, placeholder values, or fabricated estimates.
5. Distinguish `observed_data`, `estimated_value`, `model_output`, `assumption`, `scenario`, and `claim`.
6. Every numeric value must carry unit, period, method, provenance, and uncertainty metadata.
7. Evidence and references must be verified before being used as factual foundations.
8. Audit events are append-only. Errors must add audit entries, not remove prior entries.
9. Preserve disagreement, uncertainty, limitations, and minority positions.
10. Recommendations are decision support, not automated policy decisions.

## Development workflow

Before changing code:

1. Read the relevant root specifications and `.clinerules` files.
2. Inspect the existing implementation and test conventions.
3. State the implementation plan.
4. Make the smallest coherent change.
5. Add or update relevant tests.

All implementation runs inside Docker containers. Run the quality gates from the
repository root:

```text
docker compose build
docker compose run --rm tools ruff format --check .
docker compose run --rm tools ruff check .
docker compose run --rm tools mypy src
docker compose run --rm tools pytest
```

Or use the Makefile shortcuts: `make build` and `make verify`.

Do not run the tools on the host. If the Docker daemon is unavailable, report that
fact explicitly and do not claim the check passed.

## Dependency policy

- Use the approved Python/FastAPI/Pydantic stack from the technical design.
- Do not add a dependency without documenting its purpose in the change summary.
- Prefer the Python standard library when it is sufficient.
- Do not add economic models, simulation engines, databases, queues, or LLM runtimes during Fase 0.
- Keep the container base image pinned to Python 3.12 to match `pyproject.toml`,
  Ruff `target-version`, and mypy `python_version`.

## Repository conventions

- Application code belongs under `src/fiscal_astra/`.
- Tests belong under `tests/`.
- Documentation belongs under `docs/`.
- Configuration is read from environment variables through `fiscal_astra.config.settings`.
- Secrets must never be committed. Use `.env.example` as the non-secret template.
- Use absolute paths in operational documentation where a filesystem path is required.

## Numerical boundary

The application/orchestrator may validate schemas, plan work, dispatch tools, and interpret returned results. It may not calculate fiscal or macroeconomic outputs inside an LLM prompt or response. Numeric results must reference a computation or simulation artifact produced by a trusted tool/worker.