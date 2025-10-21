# tooliscode

Starter Python package scaffolded with `pyproject.toml` and `uv` tooling.

## Features

- `src/` layout with a `tooliscode` package and a basic CLI entry point.
- Modern packaging metadata in `pyproject.toml` using `setuptools`.
- Optional development extras (`pytest`, `ruff`) and matching `uv` dev dependencies.

## Quickstart

```bash
# create and activate a uv-managed virtual environment
uv venv
source .venv/bin/activate

# install the project in editable mode with dev extras
uv pip install -e ".[dev]"

# run tests or linting
pytest
ruff check src tests
```

## Next Steps

- Replace placeholder metadata (URLs, description, author details) with project specifics.
- Fill in real package code under `src/tooliscode/`.
- Add tests under `tests/` and extend the CLI as needed.
