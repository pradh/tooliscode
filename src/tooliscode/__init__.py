"""Top-level package for tooliscode."""

from __future__ import annotations

from importlib import metadata
from typing import Optional, Any

from .functions import ToolFunctionEmitter
from .host import ToolCallback, WasiService

import json

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
    "name": "python",
    "description": "Tool that is backed by a stateful python jupyter runtime with basic python support and no internet access.",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "python code to execute"},
        },
        "required": ["code"],
    },
}

def NOP_CALLBACK(x: str, y: str, z: dict[str, object]) -> dict[str, object]:
    return {}


wasi_service: WasiService | None = None

class ToolIsCode:
    def __init__(
        self,
        tools: list[dict],
        callback: Optional[ToolCallback] = NOP_CALLBACK,
    ) -> None:
        global wasi_service
        if wasi_service is None:
            wasi_service = WasiService()

        self._orig_tools = tools
        self._tool_source = ToolFunctionEmitter(self._orig_tools).render()
        self._sid = wasi_service.create_session(callback, self._tool_source)

    @property
    def tools(self) -> list[dict]:
        return [_DEFAULT_CODE_TOOL] + [t for t in self._orig_tools if t.get("type") != "function"]

    @property
    def instructions(self) -> str:
        return (
            f"The python tool only has access to read and write files inside CWD, nowhere else. " +
            "In CWD, sdk.py module contains tools you can call to answer user questions. Call them as `sdk.<func-name>(...)`. "
            f"The responses to those functions can be large in size.  So you should write to a file within CWD "
            "and selectively open it, to avoid blowing through your context window. Below is the content of `sdk.py` for your convenience: "
            f"\n```{self.sdk_code}```"
        )
    
    def tool_call(self, func_call: dict[str, Any]) -> dict[str, Any]:
        assert func_call.get("type") == "function_call"
        assert func_call.get("name") == "python"
        try:
            args = json.loads(func_call.get("arguments", {}))
        except Exception as exc:
            print(func_call.get("arguments"))
            raise
        code = args.get("code")
        if code:
            result = wasi_service.exec_cell(self._sid, code)
            output = result.stdout if result.ok else result.error
        else:
            output = ""
        return {
            "type": "function_call_output",
            "call_id": func_call.get("call_id"),
            "output": output
        }

    @property
    def session_id(self) -> str:
        return self._sid
    
    @property
    def sdk_code(self) -> str:
        return self._tool_source