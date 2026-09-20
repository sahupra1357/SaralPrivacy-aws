#!/usr/bin/env bash
set -euo pipefail
ruff check app --fix
ruff format app
