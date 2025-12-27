FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN pip install --no-cache-dir poetry

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-interaction --no-ansi --no-root

COPY . .

CMD ["python", "-m", "jukebotx_bot.main"]
