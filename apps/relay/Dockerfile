FROM python:3.11-slim

WORKDIR /app

ENV POETRY_VIRTUALENVS_CREATE=false \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        ffmpeg \
        fonts-liberation \
        pulseaudio \
        pulseaudio-utils \
        tini \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-interaction --no-ansi --no-root

COPY . .

RUN useradd --create-home --uid 10001 relay \
    && mkdir -p /data /tmp/jukebotx-relay-runtime /tmp/.X11-unix \
    && chown -R relay:relay /data /tmp/jukebotx-relay-runtime \
    && chmod 1777 /tmp/.X11-unix \
    && chmod +x /app/apps/relay/entrypoint.sh

USER relay

ENTRYPOINT ["/usr/bin/tini", "--", "/app/apps/relay/entrypoint.sh"]
CMD ["python", "-m", "jukebotx_relay.main"]
