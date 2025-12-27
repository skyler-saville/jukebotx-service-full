FROM python:3.11-slim

WORKDIR /app

# Basic hygiene: fewer surprises, better logs
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false

# Install Poetry (pinning is better for reproducibility; adjust if you like)
RUN pip install --no-cache-dir poetry

# Copy only dependency manifests first for layer caching
COPY pyproject.toml poetry.lock ./

# Install dependencies ONLY (don't install your project package)
RUN poetry install --no-interaction --no-ansi --no-root

# Now bring in the actual source
COPY . .

EXPOSE 8000

CMD ["uvicorn", "jukebotx_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
