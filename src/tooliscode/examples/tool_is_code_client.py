"""Example driver for :class:`tooliscode.ToolIsCode`."""

from __future__ import annotations

import json
import os
import textwrap
from typing import Any

from tooliscode import ToolIsCode

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Fetch weather information for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "units": {
                        "type": "string",
                        "enum": ["metric", "imperial"],
                        "description": "Unit system",
                        "default": "metric",
                    },
                },
                "required": ["city"],
            },
        },
    }
]


def main() -> None:
    # ``ToolIsCode`` depends on python.wasm. Surface a friendly error if it is missing.
    try:
        client = ToolIsCode(TOOLS)
    except FileNotFoundError as exc:
        print("Unable to initialise ToolIsCode:", exc)
        print("Set the PYTHON_WASM environment variable to the wasm build of CPython.")
        return

    print(f"Session id: {client.session_id()}")
    print("\nInstructions for operators:")
    print(textwrap.indent(client.instructions(), "  "))

    print("\nTools advertised to the model:")
    print(textwrap.indent(json.dumps(client.tools(), indent=2), "  "))



if __name__ == "__main__":
    main()
