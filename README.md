# Fiscal Astra

Local, auditable fiscal-policy decision-support harness. The language model plans,
orchestrates, interprets evidence, and writes reports; it never performs the numerical
economic calculation. All numeric results must come from a simulation engine or a
deterministic computational tool invoked through a worker/tool boundary.

## Status

Fase 0 — application shell only. There is no economic model, simulation engine, database,
queue, or live LLM runtime yet. The readiness endpoint reports this honestly:

```json
{"status": "ready", "database": "not_configured", "simulation": "not_executed"}
```

## Specifications

The normative design and prompts live in the repository root:

| File | Purpose |
|---|---|
| `00-MASTER-PROMPT.md` | Master prompt. |
| `01-orchestrator.md` | Orchestrator agent prompt. |
| `02-simulation-agents.md` | Simulation agent prompts (HANK, ABM, DSGE, macroeconometric). |
| `03-policy-experts.md` | Fiscal policy expert sub-agent prompts. |
| `04-output-audit-schema.md` | Output, evidence, uncertainty, Pareto, risk, audit trail schema. |

## Quick start (Docker)

All builds and quality gates run inside containers.

```bash
cp .env.example .env        # optional local overrides
make build                  # build the image
make verify                 # ruff format --check, ruff check, mypy, pytest
make up                     # start the API on http://127.0.0.1:8000
curl -fsS http://127.0.0.1:8000/health
make down
```

Equivalent raw commands:

```bash
docker compose build
docker compose run --rm tools ruff format --check .
docker compose run --rm tools ruff check .
docker compose run --rm tools mypy src
docker compose run --rm tools pytest
```

See `AGENTS.md` for contribution rules and `docs/` for implementation documentation.