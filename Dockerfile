FROM node:22.20-bookworm-slim AS assets

WORKDIR /build/theme/static_src

COPY theme/static_src/package.json theme/static_src/package-lock.json ./
RUN npm ci

COPY theme/static_src/src ./src
COPY theme/static_src/tailwind.config.js ./tailwind.config.js
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .
COPY --from=assets /build/theme/static/css/dist/styles.css theme/static/css/dist/styles.css

# Collect static files
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    SECRET_KEY=build-placeholder \
    DATABASE_URL=sqlite:///tmp/build.db \
    python manage.py collectstatic --noinput

RUN groupadd --system brightbean \
    && useradd --system --gid brightbean --home-dir /app brightbean \
    && chown -R brightbean:brightbean /app

USER brightbean

EXPOSE 8000

CMD gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 2
