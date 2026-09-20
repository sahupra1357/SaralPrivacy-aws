#!/usr/bin/env bash
set -euo pipefail
ruff check app
ruff format --check app
mypy app
