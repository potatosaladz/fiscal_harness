"""Container entrypoint for the dedicated simulation worker."""

from __future__ import annotations


def main() -> None:
    """Keep a Python entrypoint for local tooling; Compose invokes Dramatiq CLI."""

    raise SystemExit(
        "Run the worker with: dramatiq fiscal_astra.worker.tasks --processes 1 --threads 2"
    )


if __name__ == "__main__":
    main()
