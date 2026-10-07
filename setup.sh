#!/usr/bin/env bash
# Project bootstrap helper.
set -euo pipefail

PYTHON="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
fi

.venv/bin/pip install --disable-pip-version-check --quiet -r requirements.txt
.venv/bin/python manage.py check

cat <<'EOF'
Setup complete.

  source .venv/bin/activate
  python manage.py runserver        # start the API on http://localhost:8000
  pytest                            # run the test suite
  open http://localhost:8000/api/docs  # Swagger UI
EOF
