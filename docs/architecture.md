# Architecture

Reserved for the component, deployment, and trust-boundary diagrams described by the
technical design. Numerical engines will run outside the LLM adapter through a worker/tool
boundary.

## Fase 0 boundaries (implemented)

- API container: exposes `/health` and `/ready`, owns no state.
- Tools container: ephemeral, binds the working tree, runs formatter, linter, type checker,
  and tests.

No numerical computation exists yet. The readiness endpoint must continue to reflect that.