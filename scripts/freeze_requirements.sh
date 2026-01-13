#!/usr/bin/env bash
set -euo pipefail

python -m pip freeze | sort > requirements.lock
echo "Wrote requirements.lock"
