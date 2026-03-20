FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# uv settings for Docker
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock README.md ./

# Phase 1: Install dependencies only (cached layer — only rebuilds on lockfile change)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Phase 2: Copy app code and install project
COPY main.py settings.py schemas.py ./
COPY tools/ ./tools/
COPY utils/ ./utils/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "main.py"]
