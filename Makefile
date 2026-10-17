.PHONY: install format lint typecheck test secrets migrate seed up down check

install:
	uv sync

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy

test:
	uv run pytest

secrets:
	uv run python scripts/check_secrets.py

migrate:
	uv run alembic upgrade head
	uv run python scripts/setup_checkpoints.py

seed:
	uv run python scripts/seed_demo.py

up:
	docker compose up --build

down:
	docker compose down

check: lint typecheck test secrets

