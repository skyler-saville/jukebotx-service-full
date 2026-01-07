.PHONY: bot api dev up up-d build down destroy logs ps restart tunnel \
        activity-install activity-dev activity-build activity-shell \
        db-shell db-reset db-backup db-restore fmt lint test smoke-suno smoke-playlist smoke-audio

.ONESHELL:
SHELL := /bin/bash

PYTHONPATH := apps/bot:apps/api:packages/core:packages/infra
DC := docker compose
CORE_SERVICES := db api worker bot activity minio cloudflared cloudflared-api

# ---- Load .env into Make ----
# This exports variables so recipes can use $(POSTGRES_USER), etc.
ifneq (,$(wildcard .env))
	include .env
	export
endif

# Optional sanity defaults (only used if .env missing)
POSTGRES_HOST ?= db
POSTGRES_PORT ?= 5432
POSTGRES_DB ?= jukebotx
POSTGRES_USER ?= jukebotx
POSTGRES_PASSWORD ?= jukebotx

# -------- Local python --------
bot:
	PYTHONPATH=$(PYTHONPATH) poetry run python -m jukebotx_bot.main

api:
	PYTHONPATH=$(PYTHONPATH) poetry run uvicorn jukebotx_api.main:app --reload

# -------- Docker --------
build:
	$(DC) build

up:
	$(DC) up --build $(CORE_SERVICES)

up-d:
	$(DC) up -d --build $(CORE_SERVICES)

dev:
	$(DC) up --build $(CORE_SERVICES)

down:
	# Safe: preserves named volumes (your Postgres data)
	$(DC) down

destroy:
	# Destructive: removes volumes (wipes Postgres data)
	$(DC) down -v --remove-orphans

restart:
	$(DC) restart

tunnel:
	$(DC) up -d cloudflared cloudflared-api

ps:
	$(DC) ps

logs:
	$(DC) logs -f

# -------- Activity (Astro) --------
activity-install:
	cd apps/activity && npm install

activity-dev:
	cd apps/activity && npm run dev

activity-build:
	cd apps/activity && npm run build

activity-shell:
	cd apps/activity && /bin/bash

# -------- Database helpers --------
db-shell:
	# psql session inside the container, using .env vars
	$(DC) exec -it db psql -U "$(POSTGRES_USER)" -d "$(POSTGRES_DB)"

db-reset:
	# Wipes the database volume and starts fresh (use intentionally)
	$(MAKE) destroy
	$(MAKE) up-d

db-backup:
	# Creates a compressed custom-format dump to ./backups
	mkdir -p backups
	$(DC) exec -T db pg_dump \
		-U "$(POSTGRES_USER)" \
		-d "$(POSTGRES_DB)" \
		--format=custom \
	> backups/$(POSTGRES_DB)_$$(date +%Y%m%d_%H%M%S).dump

# Usage: make db-restore FILE=backups/jukebotx_YYYYmmdd_HHMMSS.dump
db-restore:
	test -n "$(FILE)" || (echo "FILE is required. Example: make db-restore FILE=backups/$(POSTGRES_DB)_YYYYmmdd_HHMMSS.dump" && exit 1)
	cat "$(FILE)" | $(DC) exec -T db pg_restore \
		-U "$(POSTGRES_USER)" \
		-d "$(POSTGRES_DB)" \
		--clean --if-exists

# -------- Quality --------
fmt:
	poetry run ruff format .

lint:
	poetry run ruff check .
	poetry run mypy .

test:
	poetry run pytest -q

smoke-suno:
	@if [ -z "$(URL)" ] && [ -z "$(SUNO_SMOKE_URL)" ]; then \
		echo "ERROR: URL or SUNO_SMOKE_URL must be set"; \
		exit 1; \
	fi
	@URL_TO_USE="$(URL)"; \
	if [ -z "$$URL_TO_USE" ]; then URL_TO_USE="$(SUNO_SMOKE_URL)"; fi; \
	PYTHONPATH=$(PYTHONPATH) \
	poetry run python scripts/smoke_suno_client.py "$$URL_TO_USE"


smoke-playlist:
	@if [ -z "$(URL)" ] && [ -z "$(PLAYLIST_SMOKE_URL)" ]; then \
		echo "ERROR: URL or PLAYLIST_SMOKE_URL must be set"; \
		exit 1; \
	fi
	@URL_TO_USE="$(URL)"; \
	if [ -z "$$URL_TO_USE" ]; then URL_TO_USE="$(PLAYLIST_SMOKE_URL)"; fi; \
	PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra \
	poetry run python scripts/smoke_playlist_client.py "$$URL_TO_USE"


smoke-audio:
	PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra \
	poetry run python scripts/smoke_audio_urls.py
