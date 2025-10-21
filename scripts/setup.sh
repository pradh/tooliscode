#!/usr/bin/env bash
set -euo pipefail

# Ensure we operate from the repository root.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Creating virtual environment with uv..."
uv venv
# shellcheck source=/dev/null
source .venv/bin/activate

echo "Installing project (editable) with dev extras via uv..."
uv pip install -e ".[dev]"

echo "Verifying installation..."
python -c 'import tooliscode; print(f"tooliscode version: {tooliscode.__version__}")'