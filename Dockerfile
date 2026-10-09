FROM node:24-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY assets/icon.png /app/assets/icon.png
RUN npm run build

FROM python:3.13.16-slim-bookworm AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY data/starter_recipes.json ./data/starter_recipes.json
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python
COPY alembic.ini ./
COPY migrations ./migrations
COPY --from=frontend /app/frontend/dist ./frontend/dist
RUN groupadd --gid 10001 ratatouille && useradd --uid 10001 --gid 10001 --no-create-home ratatouille && mkdir /data && chown 10001:10001 /data
ENV PATH="/app/.venv/bin:$PATH" RATATOUILLE_DB_PATH=/data/ratatouille.sqlite3 RATATOUILLE_FRONTEND_DIR=/app/frontend/dist RATATOUILLE_MIGRATIONS_ROOT=/app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready',timeout=4)"
CMD ["uvicorn","ratatouille.server:production_app","--factory","--host","0.0.0.0","--port","8000","--no-access-log"]
