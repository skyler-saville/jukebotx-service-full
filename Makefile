.PHONY: help bot api dev up up-d up-dev up-dev-d up-prod up-prod-d build down destroy logs ps restart tunnel \
        activity-install activity-dev activity-build activity-shell \
        db-shell db-reset db-backup db-restore fmt lint test smoke-suno smoke-playlist smoke-audio

.ONESHELL:
SHELL := /bin/bash

PYTHONPATH := apps/bot:apps/api:packages/core:packages/infra
DC := docker compose

# ---- Compose layering & env files ----
# - Default stack: docker-compose.yml + .env
# - Dev stack: docker-compose.yml + docker-compose.dev.yml + .env.development
# - Prod stack: docker-compose.yml + docker-compose.prod.yml + .env.production
# - Tunnel stack: docker-compose.yml + docker-compose.tunnel.yml
ENV_FILE ?= .env
COMPOSE_FILES ?= -f docker-compose.yml
DEV_COMPOSE_FILES := -f docker-compose.yml -f docker-compose.dev.yml
PROD_COMPOSE_FILES := -f docker-compose.yml -f docker-compose.prod.yml
TUNNEL_COMPOSE_FILES := -f docker-compose.yml -f docker-compose.tunnel.yml
DEV_ENV_FILE := .env.development
PROD_ENV_FILE := .env.production
CORE_SERVICES := db api worker bot activity minio
DEV_SERVICES := db api minio

# ---- Usage ----
# make up-dev      # dev stack (db/api/minio)
# make up-prod     # prod stack (full services)
# make tunnel      # start cloudflared with tunnel compose layer
# make up COMPOSE_FILES="-f docker-compose.yml -f docker-compose.tunnel.yml" ENV_FILE=.env
help:
	@echo "Make targets:"
	@echo "  up / up-d          Start full stack with COMPOSE_FILES + ENV_FILE"
	@echo "  up-dev / up-dev-d  Start dev stack (db/api/minio) with .env.development"
	@echo "  up-prod / up-prod-d Start prod stack with .env.production"
	@echo "  tunnel             Start cloudflared services with tunnel compose layer"
	@echo "  api / bot          Run local python services with PYTHONPATH"

# ---- Load .env into Make ----
# This exports variables so recipes can use $(POSTGRES_USER), etc.
ifneq (,$(wildcard $(ENV_FILE)))
	include $(ENV_FILE)
	export
endif

ifneq (,$(wildcard $(ENV_FILE)))
	DC_ENV_OPT := --env-file $(ENV_FILE)
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
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) build

up:
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) up --build $(CORE_SERVICES)

up-d:
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) up -d --build $(CORE_SERVICES)

dev:
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) up --build $(CORE_SERVICES)

up-dev:
	$(MAKE) up ENV_FILE=$(DEV_ENV_FILE) COMPOSE_FILES="$(DEV_COMPOSE_FILES)" CORE_SERVICES="$(DEV_SERVICES)"

up-dev-d:
	$(MAKE) up-d ENV_FILE=$(DEV_ENV_FILE) COMPOSE_FILES="$(DEV_COMPOSE_FILES)" CORE_SERVICES="$(DEV_SERVICES)"

up-prod:
	$(MAKE) up ENV_FILE=$(PROD_ENV_FILE) COMPOSE_FILES="$(PROD_COMPOSE_FILES)"

up-prod-d:
	$(MAKE) up-d ENV_FILE=$(PROD_ENV_FILE) COMPOSE_FILES="$(PROD_COMPOSE_FILES)"

down:
	# Safe: preserves named volumes (your Postgres data)
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) down

destroy:
	# Destructive: removes volumes (wipes Postgres data)
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) down -v --remove-orphans

restart:
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) restart

tunnel:
	$(DC) $(TUNNEL_COMPOSE_FILES) $(DC_ENV_OPT) up -d cloudflared cloudflared-api

ps:
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) ps

logs:
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) logs -f

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
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) exec -it db psql -U "$(POSTGRES_USER)" -d "$(POSTGRES_DB)"

db-reset:
	# Wipes the database volume and starts fresh (use intentionally)
	$(MAKE) destroy
	$(MAKE) up-d

db-backup:
	# Creates a compressed custom-format dump to ./backups
	mkdir -p backups
	$(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) exec -T db pg_dump \
		-U "$(POSTGRES_USER)" \
		-d "$(POSTGRES_DB)" \
		--format=custom \
	> backups/$(POSTGRES_DB)_$$(date +%Y%m%d_%H%M%S).dump

# Usage: make db-restore FILE=backups/jukebotx_YYYYmmdd_HHMMSS.dump
db-restore:
	test -n "$(FILE)" || (echo "FILE is required. Example: make db-restore FILE=backups/$(POSTGRES_DB)_YYYYmmdd_HHMMSS.dump" && exit 1)
	cat "$(FILE)" | $(DC) $(COMPOSE_FILES) $(DC_ENV_OPT) exec -T db pg_restore \
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
