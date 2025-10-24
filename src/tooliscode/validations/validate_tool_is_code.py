"""Example driver for :class:`tooliscode.ToolIsCode`.

This script wires up a simple callback that returns mock weather data, writes the
generated ``sdk.py`` into the active ToolIsCode session directory, and executes a
short Python snippet inside the WASI runtime to demonstrate the end-to-end
`tool_call` flow.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, Dict

from tooliscode import ToolIsCode, _BASE_PATH, wasi_service

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


def weather_callback(name: str, request_id: str, arguments: Dict[str, object]) -> Dict[str, object]:
    """Respond to tool requests issued by the WASI guest."""
    if name != "get_weather":
        return {
            "type": "tool_result",
            "id": request_id,
            "error": {
                "type": "UnknownTool",
                "message": f"Unhandled tool name: {name}",
            },
        }

    city = str(arguments.get("city", "Unknown City"))
    units = str(arguments.get("units", "metric"))
    mock_result = {
        "city": city,
        "units": units,
        "temperature": 21 if units == "metric" else 70,
        "conditions": "partly cloudy",
    }
    return {
        "type": "tool_result",
        "id": request_id,
        "content": mock_result,
    }


def main() -> None:
    # ``ToolIsCode`` depends on python.wasm. Surface a friendly error if it is missing.
    try:
        client = ToolIsCode(TOOLS, callback=weather_callback)
    except FileNotFoundError as exc:
        print("Unable to initialise ToolIsCode:", exc)
        print("Set the PYTHON_WASM environment variable to the wasm build of CPython.")
        return

    print(f"Session id: {client.session_id}")
    print("\nInstructions for operators:")
    print(textwrap.indent(client.instructions, "  "))

    print("\nTools advertised to the model:")
    print(textwrap.indent(json.dumps(client.tools, indent=2), "  "))

    print("\nGenerated sdk.py contents:")
    print(textwrap.indent(client.sdk_code, "  "))

    code = textwrap.dedent(
        """
        import json
        from sdk import get_weather

        result = get_weather(city="Paris", units="metric")
        print(json.dumps(result))
        """
    ).strip()

    print("\nRunning tool_call through WASI runtime...")
    try:
        response = client.tool_call(
            {
                "type": "function_call",
                "name": "python",
                "call_id": "demo-call-1",
                "arguments": json.dumps({"code": code})
            }
        )
    except Exception as exc:  # pragma: no cover - demo script
        print("tool_call failed:", exc)
    else:
        print(textwrap.indent(json.dumps(response, indent=2), "  "))
    finally:
        if wasi_service is not None:
            wasi_service.close(client.session_id())


if __name__ == "__main__":
    main()
