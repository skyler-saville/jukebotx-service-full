.PHONY: bot api up up-d build down destroy logs ps restart \
        db-shell db-reset db-backup db-restore fmt lint test smoke-suno smoke-suno-pair smoke-lavalink smoke-lavalink-docker smoke-playlist smoke-audio smoke-relay smoke-browser-relay smoke-worker-gif

.ONESHELL:
SHELL := /bin/bash

PYTHONPATH := apps/bot:apps/api:apps/relay:packages/core:packages/infra
DC := docker compose

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
	$(DC) up --build

up-d:
	$(DC) up -d --build

down:
	# Safe: preserves named volumes (your Postgres data)
	$(DC) down

destroy:
	# Destructive: removes volumes (wipes Postgres data)
	$(DC) down -v --remove-orphans

restart:
	$(DC) restart

ps:
	$(DC) ps

logs:
	$(DC) logs -f

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

smoke-suno-pair:
	@if [ -z "$(SUNO_SMOKE_URL)" ]; then \
		echo "ERROR: SUNO_SMOKE_URL must be set in .env"; \
		exit 1; \
	fi
	PYTHONPATH=$(PYTHONPATH) \
	poetry run python scripts/smoke_suno_client.py \
	"https://suno.com/s/TMUtLmNitvmFPPIl" \
	"$(SUNO_SMOKE_URL)"

smoke-lavalink:
	@if [ -z "$(LAVALINK_PASSWORD)" ]; then \
		echo "ERROR: LAVALINK_PASSWORD must be set in .env"; \
		exit 1; \
	fi
	@HOST_TO_USE="$(HOST)"; \
	PORT_TO_USE="$(PORT)"; \
	if [ -z "$$HOST_TO_USE" ]; then HOST_TO_USE="127.0.0.1"; fi; \
	if [ -z "$$PORT_TO_USE" ]; then PORT_TO_USE="$(LAVALINK_PORT_HOST_DEV)"; fi; \
	IDENTIFIER_ARG=""; \
	if [ -n "$(URL)" ]; then IDENTIFIER_ARG="--identifier $(URL)"; \
	elif [ -n "$(SUNO_SMOKE_URL)" ]; then IDENTIFIER_ARG="--identifier $(SUNO_SMOKE_URL)"; \
	fi; \
	PYTHONPATH=$(PYTHONPATH) \
	poetry run python scripts/smoke_lavalink.py \
		--host "$$HOST_TO_USE" \
		--port "$$PORT_TO_USE" \
		--password "$(LAVALINK_PASSWORD)" \
		$$IDENTIFIER_ARG

smoke-lavalink-docker:
	@if [ -z "$(LAVALINK_PASSWORD)" ]; then \
		echo "ERROR: LAVALINK_PASSWORD must be set in .env"; \
		exit 1; \
	fi
	@IDENTIFIER_ARG=""; \
	if [ -n "$(URL)" ]; then IDENTIFIER_ARG="--identifier $(URL)"; \
	elif [ -n "$(SUNO_SMOKE_URL)" ]; then IDENTIFIER_ARG="--identifier $(SUNO_SMOKE_URL)"; \
	fi; \
	$(DC) exec -T bot python scripts/smoke_lavalink.py \
		--host "lavalink" \
		--port "2333" \
		--password "$(LAVALINK_PASSWORD)" \
		$$IDENTIFIER_ARG


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

smoke-relay:
	poetry run python scripts/smoke_audio_relay.py \
		--base-url "$${RELAY_SMOKE_BASE_URL:-http://127.0.0.1:18090}" \
		--token "$${AUDIO_RELAY_TOKEN:-}"

smoke-browser-relay:
	@if [ -z "$(URL)" ]; then \
		echo "ERROR: URL must be set"; \
		exit 1; \
	fi
	poetry run python scripts/smoke_audio_relay.py \
		--base-url "$${RELAY_SMOKE_BASE_URL:-http://127.0.0.1:18090}" \
		--token "$${AUDIO_RELAY_TOKEN:-}" \
		--source-url "$(URL)"

smoke-worker-gif:
	@if [ -z "$(URL)" ] && [ -z "$(SUNO_SMOKE_URL)" ]; then \
		echo "ERROR: URL or SUNO_SMOKE_URL must be set"; \
		exit 1; \
	fi
	@URL_TO_USE="$(URL)"; \
	if [ -z "$$URL_TO_USE" ]; then URL_TO_USE="$(SUNO_SMOKE_URL)"; fi; \
	UPLOAD_ARG=""; \
	if [ "$(UPLOAD)" = "1" ] || [ "$(UPLOAD)" = "true" ]; then UPLOAD_ARG="--upload"; fi; \
	PYTHONPATH=$(PYTHONPATH) \
	poetry run python scripts/smoke_worker_gif.py "$$URL_TO_USE" $$UPLOAD_ARG
