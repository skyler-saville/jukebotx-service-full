.PHONY: bot api up down logs fmt lint test

PYTHONPATH := apps/bot:apps/api:packages/core:packages/infra

bot:
	PYTHONPATH=$(PYTHONPATH) poetry run python -m jukebotx_bot.main

api:
	PYTHONPATH=$(PYTHONPATH) poetry run uvicorn jukebotx_api.main:app --reload

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

fmt:
	poetry run ruff format .

lint:
	poetry run ruff check .
	poetry run mypy .

test:
	poetry run pytest -q

smoke-suno:
	PYTHONPATH=$(PYTHONPATH) poetry run python scripts/smoke_suno_client.py "$(URL)"
