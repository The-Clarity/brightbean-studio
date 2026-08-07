#!/bin/sh

set -eu

cd /app

pip check
pip-audit -r requirements.lock --disable-pip
(cd theme/static_src && npm audit --audit-level=high)
ruff check .
ruff format --check .
SECRET_KEY=ci-typecheck-only \
ENCRYPTION_KEY_SALT=ci-typecheck-only \
mypy apps/ config/ providers/ tests/ --ignore-missing-imports
SECRET_KEY=ci-production-check-only-not-a-runtime-secret-0123456789abcdef \
ENCRYPTION_KEY_SALT=ci-production-check-only-not-a-runtime-secret \
ALLOWED_HOSTS=brightbean.theclarity.us \
python manage.py check --deploy --fail-level WARNING --settings=config.settings.production
python manage.py migrate --noinput
pytest --cov=apps --cov-report=term-missing
