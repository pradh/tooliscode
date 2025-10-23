from __future__ import annotations

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
MODULE_GLOBALS = runpy.run_path(SRC / "tooliscode" / "functions.py")
ToolFunctionEmitter = MODULE_GLOBALS["ToolFunctionEmitter"]


def test_tool_generation_includes_docstrings_and_aliases() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Fetch weather.",
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
                        "include-hourly": {
                            "type": "boolean",
                            "description": "Include hourly data",
                        },
                    },
                    "required": ["city"],
                },
            },
        }
    ]

    generated = ToolFunctionEmitter("session-123", tools).render()

    expected_lines = [
        "from __future__ import annotations",
        "import os, sys",
        "sys.path.append(os.path.dirname(__file__))",
        "",
        "from typing import Any, Literal, Optional",
        "from guest_helpers import tool_call",
        "",
        "def get_weather(city: str, units: Literal['metric', 'imperial'] = 'metric', include_hourly: Optional[bool] = None) -> Any:",
        '    """',
        "    Fetch weather.",
        "    ",
        "    Args:",
        "        city: City name",
        "        units: Unit system",
        "        include_hourly: Include hourly data (alias: `include-hourly`)",
        '    """',
        "    args: dict[str, Any] = {}",
        "    for _param_name, _param_value in [",
        "        ('city', city),",
        "        ('units', units),",
        "        ('include-hourly', include_hourly),",
        "    ]:",
        "        args[_param_name] = _param_value",
        "    return tool_call('get_weather', args)",
    ]
    expected = "\n".join(expected_lines) + "\n"

    assert generated == expected


def test_tool_generation_handles_nullable_types() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "update_count",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "count": {
                            "type": ["integer", "null"],
                            "description": "Optional count override",
                        }
                    },
                },
            },
        }
    ]

    generated = ToolFunctionEmitter("session-123", tools).render()

    expected_lines = [
        "from __future__ import annotations",
        "import os, sys",
        "sys.path.append(os.path.dirname(__file__))",
        "",
        "from typing import Any, Optional",
        "from guest_helpers import tool_call",
        "",
        "def update_count(count: Optional[int] = None) -> Any:",
        '    """',
        "    Args:",
        "        count: Optional count override",
        '    """',
        "    args: dict[str, Any] = {}",
        "    for _param_name, _param_value in [",
        "        ('count', count),",
        "    ]:",
        "        args[_param_name] = _param_value",
        "    return tool_call('update_count', args)",
    ]
    expected = "\n".join(expected_lines) + "\n"

    assert generated == expected
