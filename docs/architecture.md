# Architecture

Numerical engines run outside the HTTP process through a durable Redis/Dramatiq worker
boundary. The API persists the complete request manifest before it publishes only `run_id`.

```text
POST /runs
    -> PostgreSQL simulation_runs (received + correlation_id)
    -> Redis/Dramatiq orchestrator queue (run_id)
    -> orchestrator container
       -> durable state machine: validate -> plan -> prepare data
       -> Redis/Dramatiq simulation queue (run_id)
    -> simulation worker container
       -> claim row with lock
       -> resolve engine registry
       -> validate -> run -> persist result/artifacts
       -> signal orchestrator resume
    -> orchestrator
       -> parameter extraction -> agents -> deliberation -> evidence -> report
       -> completed (or coded failed/cancelled)
```

## Containers

- `api`: FastAPI producer. It does not import or execute a numerical engine during `POST /runs`.
- `orchestrator`: independently supervised Dramatiq process. It owns the validated, durable
  policy-pipeline state machine. State transitions are row-locked, resumable after restart,
  and append an audit event with the run correlation ID.
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
- The orchestrator never treats `not_executed` as numeric output and cannot enter parameter
  extraction or policy-agent analysis without a persisted `verified_numeric` result.
- `completed_stages` and stored job IDs make stage retries safe; the simulation worker's row-lock
  claim prevents duplicate numerical execution if a queue message is delivered twice.