# Architecture

Numerical engines run outside the HTTP process through a durable Redis/Dramatiq worker
boundary. The API persists the complete request manifest before it publishes only `run_id`.

```text
POST /runs
    -> PostgreSQL simulation_runs (pending)
    -> Redis/Dramatiq (run_id)
    -> worker container
       -> claim row with lock (validating)
       -> resolve engine registry
       -> validate -> run -> persist result/artifacts
       -> append audit events
```

## Containers

- `api`: FastAPI producer. It does not import or execute a numerical engine during `POST /runs`.
- `worker`: independently supervised Dramatiq process. It reconstructs the request from the
  PostgreSQL manifest, resolves the engine through the registry, and owns simulation execution.
- `redis`: append-only Redis broker with a persistent volume for queued messages.
- `postgres`: durable run, result, artifact metadata, and hash-chained audit storage.
- `minio` is optional under the `object-storage` Compose profile; the MVP engine uses the
  shared artifact volume directly.

## Worker guarantees

- Atomic row-lock claim makes duplicate delivery for the same `run_id` a no-op after the first
  claim or terminal state.
- Dramatiq retries transient worker exceptions up to `ASTRA_SIMULATION_MAX_ATTEMPTS`.
- Every retry/failure stores the complete traceback in the run and an append-only audit event.
- Results and artifacts are written before the terminal success event.