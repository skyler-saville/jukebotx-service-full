FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    ffmpeg \
    libffi-dev \
    libsodium-dev \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

ENV POETRY_VIRTUALENVS_CREATE=false \
    PYTHONUNBUFFERED=1

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-interaction --no-ansi --no-root

COPY . .

CMD ["python", "-m", "jukebotx_bot.main"]
