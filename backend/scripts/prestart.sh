#!/usr/bin/env bash
set -euo pipefail
python app/backend_pre_start.py
alembic upgrade head
python app/initial_data.py
