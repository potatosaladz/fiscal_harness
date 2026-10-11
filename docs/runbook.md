# Local Deployment Runbook

## Fase 0 procedures

### Pre-flight checks

```bash
docker version              # daemon is running
docker compose config       # compose file parses; verifies optional .env handling
```

### Start the service

```bash
docker compose up -d postgres redis api worker
```

The API producer and simulation worker are separate containers. Optional object storage can be
started with `docker compose --profile object-storage up -d minio`.

The API listens on `ASTRA_PORT` (default 8000) inside the container and is published on the
same host port.

### Health checks

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

`/health` is the process-level liveness probe. `/ready` reports downstream capability status
and is the endpoint that must stay truthful when later phases add engines and data sources.
In Fase 0 it reports `"database": "not_configured"` and `"simulation": "not_executed"`.

### Stop the service

```bash
make down
```

### Rebuild after dependency or code changes

```bash
make build
make verify
```

### Failure recovery

- API does not start: check `docker compose logs api`, confirm the port is not in use, rebuild the image.
- Worker does not consume jobs: check `docker compose logs worker`, Redis health, and the
  `ASTRA_BROKER_URL` value in both API and worker containers.
- A run remains queued: inspect Redis and the worker logs; do not execute the engine in the API
  container as a workaround. The broker message can be safely redelivered by the worker.
- `make verify` fails: fix the reported issue; do not bypass a gate.
- Image build fails on `uv sync --frozen`: the lock is out of date. Run `make lock`,
  rebuild, and commit `uv.lock`.
- Healthcheck reports `unhealthy`: inspect the container logs before restarting.

### Backup

Fase 0 has no persistent service state. The repository is the source of truth. When later
phases add data snapshots and generated artifacts, add snapshot and artifact backup steps
here.