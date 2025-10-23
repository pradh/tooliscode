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


## Function Tool Bridging (Design Draft)

1. Provision per-session side-channel FIFOs (e.g., `<sid>/tool_req.pipe` and `<sid>/tool_res.pipe`) alongside the existing shim so guest code can communicate without using stdout or stderr.
2. Generate Python stubs inside the WASI guest for every Responses API function tool; each stub serializes a canonical `function_call` payload (name, id, arguments) and writes it to `tool_req.pipe`.
3. Block the stub on responses by reading `tool_res.pipe`, matching on the request id, then return a JSON object.
4. Run a host-side handler thread that watches `tool_req.pipe`, invokes the real API callback, and writes the `tool_result` payload back through `tool_res.pipe`, propagating errors as structured messages.
