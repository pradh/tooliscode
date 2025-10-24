# tooliscode

A prototype library to proxy LLM function calls through a python runtime in a [wasm environment](https://snarky.ca/state-of-wasi-support-for-cpython-march-2024/) (for isolation/safety).

Why?

* So that the LLM can navigate the potentially large number of tools like it browses code
* So that the LLM can control how much of the function call (or MCP call) response it consumes

This might be super slow for the common case, and might only make sense when the function-call results are huge or there are 100s of functions.

Note that Jupyter kernel running in a docker container is more mainstream but also heavy-weight. Wasm is comparatively lighter/faster, but is more constrained (lacking on threading, fifos, etc).

## API

The current API tracks the Responses API tool structure, but should be easy to tweak for other APIs.

```
# -> Let's assume you have a long list of function tools: `function_tools`


# -> Create an instance of ToolIsCode
tic = ToolIsCode(
    tools=function_tools,
    callback=function_callback,
)

response = openai.responses.create(
    # -> Pass in code tools wrapper
    tools=tic.tools,
    # -> Pass in (or append) instructions
    instructions=tic.instructions,   
    ...
)

# -> If there is a function_call item, call tool_call
fc_response_item = tic.tool_call(fc_item)


# -> A match-case on `func_name` to process the `args` and return result.
def function_callback(req_id: str, func_name: str, args: dict[str, Any]) -> dict[str, Any] | str:
    pass
```

## Development

```bash
# create and activate a uv-managed virtual environment
uv venv
source .venv/bin/activate

# install the project in editable mode with dev extras
uv pip install -e ".[dev]"

# run tests or linting
pytest
ruff check src tests

# run e2e validation
# needs OPENAI_API_KEY
python scripts/function_call.py
```

## One time manual install of CPython for [WASI](https://wasi.dev)

Before running, set PYTHON_WASM_HOME to the path of `python.wasm` binary and a `lib/` subdirectory.  The readymade ZIP is available [here](https://github.com/brettcannon/cpython-wasi-build/releases).


```
export PYTHON_WASM_HOME=/opt/wasm
```
