"""Top-level package for tooliscode."""

from __future__ import annotations

import base64
import secrets
from importlib import metadata

from .functions import ToolFunctionEmitter

try:  # pragma: no cover - optional dependency
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    class OpenAI:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs) -> None:
            raise ModuleNotFoundError(
                "openai package not installed; run `pip install tooliscode[dev]` "
                "or provide a stub before using tooliscode.Client."
            )

__all__ = ["__version__", "ToolIsCode"]

try:
    __version__ = metadata.version("tooliscode")
except metadata.PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0"


_BASE_PATH = "/tmp/tooliscode"
_SDK_FILE = 'sdk.py'

_DEFAULT_CODE_TOOL = {
    "type": "function",
    "function": {
        "name": "python",
        "description": "Tool that is backed by a stateful python jupyter runtime.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "python code to execute"},
            },
            "required": ["code"],
        },
    },
}


class ToolIsCode:
    def __init__(self, tools: list[dict]):
        self._orig_tools = tools
        self._sid = base64.b64encode(secrets.token_bytes(12)).decode("ascii")
        self._tool_source = ToolFunctionEmitter(self._sid, self._orig_tools).render()

    def tools(self) -> str:
        return [_DEFAULT_CODE_TOOL] + [t for t in self._orig_tools if t.get("type") != "function"]

    def instructions(self) -> str:
        return (
            f"The functions.python tool has access to read and write files within {_BASE_PATH}/{self._sid}. " +
            "The sdk.py file in that path contains functions you can call to answer user questions. The responses to " +
            f"those functions can be large in size.  So you should write to a file within {_BASE_PATH}/{self._sid} and " +
            "selectively open it, to avoid blowing through your context window."
        )