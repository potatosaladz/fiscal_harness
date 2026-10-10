"""Run the local API with ``python -m fiscal_astra.api``."""

import uvicorn

from fiscal_astra.config.settings import get_settings


def main() -> None:
    """Start the development server."""

    settings = get_settings()
    uvicorn.run(
        "fiscal_astra.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
