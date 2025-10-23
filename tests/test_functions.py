from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.append(str(SRC))

from tooliscode.functions import ToolFunctionEmitter  # noqa: E402


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
        "from typing import Any, Literal, Optional",
        "from pydantic import BaseModel, ConfigDict, Field",
        "from tooliscode.runtime import tool_call",
        "",
        "class GetWeatherArgs(BaseModel):",
        '    """Pydantic model for `get_weather` arguments."""',
        "    model_config = ConfigDict(populate_by_name=True)",
        "",
        "    city: str = Field(..., description='City name')",
        "    units: Literal['metric', 'imperial'] = Field('metric', description='Unit system')",
        "    include_hourly: bool = Field(None, description='Include hourly data', alias='include-hourly')",
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
        "    args = GetWeatherArgs(city=city, units=units, include_hourly=include_hourly)",
        "    return tool_call('session-123', 'get_weather', args)",
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
        "from typing import Any, Optional",
        "from pydantic import BaseModel, Field",
        "from tooliscode.runtime import tool_call",
        "",
        "class UpdateCountArgs(BaseModel):",
        '    """Pydantic model for `update_count` arguments."""',
        "    count: int = Field(None, description='Optional count override')",
        "",
        "def update_count(count: Optional[int] = None) -> Any:",
        '    """',
        "    Args:",
        "        count: Optional count override",
        '    """',
        "    args = UpdateCountArgs(count=count)",
        "    return tool_call('session-123', 'update_count', args)",
    ]
    expected = "\n".join(expected_lines) + "\n"

    assert generated == expected
