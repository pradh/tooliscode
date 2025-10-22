"""Runtime helpers for tool function invocation inside the WASI guest."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, Tuple

from pydantic import BaseModel

__all__ = ["ToolCallError", "tool_call"]

_DEFAULT_REQ_NAME = "tool_req.pipe"
_DEFAULT_RESP_NAME = "tool_res.pipe"

_session_lock = threading.Lock()
_session_runtimes: Dict[Tuple[str, str], "SessionRuntime"] = {}


class ToolCallError(RuntimeError):
    """Raised when tool_call cannot complete."""


def tool_call(session_id: str, function_name: str, args: BaseModel, *, timeout: float = 30.0) -> Dict[str, Any]:
    """
    Invoke a function tool via the host side-channel.

    Args:
        session_id: ID of the session, that determines tool call scope.
        function_name: Name of the function tool to invoke.
        args: Pydantic model containing validated arguments.
        timeout: Maximum time to wait for a response, in seconds.

    Returns:
        Parsed JSON dictionary returned by the host.
    """

    if not isinstance(args, BaseModel):
        raise TypeError("args must be a pydantic BaseModel instance")

    runtime = _get_or_create_runtime(session_id)
    return runtime.invoke(function_name, args, timeout=timeout)


def _get_or_create_runtime(session_id: str) -> "SessionRuntime":
    with _session_lock:
        runtime = _session_runtimes.get(session_id)
        if runtime is None:
            runtime = SessionRuntime(session_id)
            _session_runtimes[session_id] = runtime
        return runtime


class SessionRuntime:
    """Per-session pipes and background response reader."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._req_path = _DEFAULT_REQ_NAME
        self._resp_path = _DEFAULT_RESP_NAME
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._last_error: ToolCallError | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._response_loop, name="tooliscode-resp", daemon=True)
        self._thread.start()

    def invoke(self, function_name: str, args: BaseModel, *, timeout: float) -> Dict[str, Any]:
        request_id = uuid.uuid4().hex
        payload = {
            "type": "function_call",
            "id": request_id,
            "name": function_name,
            "arguments": args.model_dump(mode="json"),
        }
        message = json.dumps(payload, ensure_ascii=False) + "\n"
        self._write_request(message)
        return self._wait_for_response(request_id, timeout)

    def _write_request(self, message: str) -> None:
        with self._write_lock:
            try:
                with open(self._req_path, "w", encoding="utf-8", buffering=1) as pipe:
                    pipe.write(message)
                    pipe.flush()
            except OSError as exc:
                raise ToolCallError(f"Failed to write to request pipe {self._req_path}: {exc}") from exc

    def _wait_for_response(self, request_id: str, timeout: float) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout if timeout and timeout > 0 else None
        with self._condition:
            while True:
                if request_id in self._pending:
                    return self._pending.pop(request_id)
                if self._last_error is not None:
                    raise self._last_error
                if deadline is None:
                    self._condition.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ToolCallError(f"Timed out waiting for tool response for id {request_id}")
                    self._condition.wait(timeout=remaining)

    def _response_loop(self) -> None:
        while not self._stop:
            try:
                with open(self._resp_path, "r", encoding="utf-8", buffering=1) as pipe:
                    with self._condition:
                        self._last_error = None
                    for line in pipe:
                        if self._stop:
                            return
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            message = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        response_id = message.get("id")
                        if not response_id:
                            continue
                        with self._condition:
                            self._pending[response_id] = message
                            self._condition.notify_all()
            except OSError as exc:
                with self._condition:
                    self._last_error = ToolCallError(
                        f"Failed to read response pipe {self._resp_path}: {exc}"
                    )
                    self._condition.notify_all()
                time.sleep(0.1)
