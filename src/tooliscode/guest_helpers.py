
from __future__ import annotations

import sys, json, os, time, threading, uuid

from typing import Any

__all__ = ["ToolCallError", "tool_call", "sidelog", "lp_read", "lp_write"]


SIDEDIR = os.path.dirname(__file__)          # <— this is the session dir
SIDELOG = os.path.join(SIDEDIR, "runtime.log")
os.chdir(SIDEDIR)

sys.path.append(os.path.dirname(__file__))

_IO_LOCK = threading.Lock()
_IO_OUT = sys.stdout
_IO_IN = sys.stdin


def sidelog(msg):
    try:
        with open(SIDELOG, "a", buffering=1) as f:  # line-buffered
            f.write(f"[{time.time():.3f}] {msg}\n")
    except Exception:
        pass

sidelog(f"shim boot: starting up {sys.stdin.isatty()}")


class ToolCallError(RuntimeError):
    """Raised when tool_call cannot complete."""



def tool_call(function_name: str, args: dict, *, timeout: float = 30.0) -> dict[str, Any]:
    """
    Invoke a function tool via the host side-channel.

    Args:
        function_name: Name of the function tool to invoke.
        args: dict containing validated arguments.
        timeout: Maximum time to wait for a response, in seconds.

    Returns:
        Parsed JSON dictionary returned by the host.
    """

    return ToolRuntime().invoke(function_name, args, timeout=timeout)


class ToolRuntime:

    _instance: "ToolRuntime | None" = None

    def __new__(cls) -> "ToolRuntime":
        if cls._instance is None:
            self = super().__new__(cls)
            self._closed = False
            cls._instance = self
        return cls._instance

    def __init__(self) -> None:
        # Avoid re-initialising singleton state.
        pass

    def invoke(self, function_name: str, args: dict, *, timeout: float) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        payload = {
            "type": "tool_request",
            "id": request_id,
            "name": function_name,
            "arguments": args,
        }
        with _IO_LOCK:
            lp_write(payload)
            return self._await_response(request_id, timeout)

    def _await_response(self, request_id: str, timeout: float) -> dict[str, Any]:
        if self._closed:
            raise ToolCallError("Tool runtime is shutting down")
        deadline = time.monotonic() + timeout if timeout and timeout > 0 else None
        while True:
            message = lp_read()
            if not isinstance(message, dict):
                continue
            if not message:
                continue
            if message.get("type") != "tool_result":
                raise ToolCallError(f"Unexpected message while waiting for tool result: {message!r}")
            if message.get("id") != request_id:
                raise ToolCallError(f"Mismatched tool result id: expected {request_id}, got {message.get('id')}")
            return message

    def shutdown(self) -> None:
        self._closed = True


def lp_read():
    fin = _IO_IN.buffer
    header = bytearray()
    while True:
        ch = fin.read(1)
        if not ch:
            return None  # EOF before header
        if ch == b"\n":
            break
        header += ch
    n = int(header.decode("ascii"))
    sidelog(f"_lp_read header={n}")
    if not n:
        return None
    data = bytearray()
    while len(data) < n:
        chunk = _IO_IN.buffer.read(n - len(data))
        sidelog(f"_lp_read payload_len={len(data)}")
        if not chunk:
            raise EOFError("stdin closed mid-frame")
        data.extend(chunk)
    return json.loads(data)


def lp_write(obj):
    b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sidelog(f"_lp_write payload_len={len(b)}")
    fout = _IO_OUT.buffer
    fout.write(str(len(b)).encode("ascii") + b"\n")
    fout.write(b)
    fout.flush()

