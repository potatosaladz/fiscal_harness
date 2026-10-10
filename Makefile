COMPOSE := docker compose
TOOLS := $(COMPOSE) run --rm tools

.PHONY: help build up down logs health verify format format-check lint typecheck test lock shell

help:
	@echo "Fiscal Astra Fase 0 targets:"
	@echo "  make build         Build the docker image"
	@echo "  make up            Start the api service"
	@echo "  make down          Stop services"
	@echo "  make logs          Follow api logs"
	@echo "  make health        Curl the health endpoint"
	@echo "  make verify        Run format-check, lint, typecheck, test"
	@echo "  make format        Format code in container"
	@echo "  make format-check  Check formatting in container"
	@echo "  make lint          Lint in container"
	@echo "  make typecheck     Type check in container"
	@echo "  make test          Run tests in container"
	@echo "  make lock          Regenerate uv.lock in container"
	@echo "  make shell         Open a shell in the tools container"

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d api

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api

health:
	curl -fsS http://127.0.0.1:8000/health && echo

verify: format-check lint typecheck test

format:
	$(TOOLS) ruff format .

format-check:
	$(TOOLS) ruff format --check .

lint:
	$(TOOLS) ruff check .

typecheck:
	$(TOOLS) mypy src

test:
	$(TOOLS) pytest

lock:
	$(TOOLS) uv lock

shell:
	$(TOOLS) sh