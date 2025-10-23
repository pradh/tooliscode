from __future__ import annotations

import io
import json
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.append(str(SRC))

from tooliscode.guest_helpers import ToolCallError, tool_call, ToolRuntime
from tooliscode.host import _Session


def _read_frame(stream) -> dict:
    header = bytearray()
    while True:
        chunk = stream.read(1)
        if not chunk:
            raise EOFError("guest stream closed")
        header += chunk
        if header.endswith(b"\n"):
            break
        if len(header) > 64:
            raise ValueError("invalid frame header")
    length = int(header.strip() or b"0")
    data = bytearray()
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            raise EOFError("guest stream closed mid-frame")
        data.extend(chunk)
    return json.loads(data.decode("utf-8"))


def _write_frame(stream, message: dict) -> None:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    stream.write(f"{len(payload)}\n".encode("ascii"))
    stream.write(payload)
    stream.flush()


@contextmanager
def _redirect_guest_io() -> Iterator[tuple[io.BufferedWriter, io.BufferedReader]]:
    orig_stdin, orig_stdout = sys.stdin, sys.stdout

    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()

    guest_stdin = io.BufferedReader(os.fdopen(stdin_r, "rb", buffering=0))
    guest_stdout = io.BufferedWriter(os.fdopen(stdout_w, "wb", buffering=0))

    class _FakeStdin:
        def __init__(self, buffer: io.BufferedReader) -> None:
            self.buffer = buffer

        def read(self, n: int = -1) -> str:
            data = self.buffer.read(n)
            return data.decode("utf-8") if data else ""

        def readline(self, size: int = -1) -> str:
            data = self.buffer.readline(size)
            return data.decode("utf-8") if data else ""

        def fileno(self) -> int:
            raw = getattr(self.buffer, "raw", None)
            if raw is not None and hasattr(raw, "fileno"):
                return raw.fileno()
            return 0

        def isatty(self) -> bool:
            return False

        def close(self) -> None:
            self.buffer.close()

    class _FakeStdout:
        def __init__(self, buffer: io.BufferedWriter) -> None:
            self.buffer = buffer

        def write(self, data: str) -> int:
            encoded = data.encode("utf-8")
            written = self.buffer.write(encoded)
            self.buffer.flush()
            return written

        def flush(self) -> None:
            self.buffer.flush()

        def isatty(self) -> bool:
            return False

        def close(self) -> None:
            self.buffer.close()

    sys.stdin = _FakeStdin(guest_stdin)
    sys.stdout = _FakeStdout(guest_stdout)
    ToolRuntime._instance = None

    host_stdin = os.fdopen(stdin_w, "wb", buffering=0)
    host_stdout = os.fdopen(stdout_r, "rb", buffering=0)

    try:
        yield host_stdin, host_stdout
    finally:
        sys.stdin.close()
        sys.stdout.close()
        host_stdin.close()
        host_stdout.close()
        sys.stdin = orig_stdin
        sys.stdout = orig_stdout
        ToolRuntime._instance = None


def test_tool_call_round_trip():
    with _redirect_guest_io() as (host_stdin, host_stdout):
        ack = threading.Event()

        def host_worker() -> None:
            request = _read_frame(host_stdout)
            ack.set()
            assert request["type"] == "tool_request"
            response = {
                "type": "tool_result",
                "id": request["id"],
                "content": {"echo": request["arguments"]["text"]},
            }
            _write_frame(host_stdin, response)

        thread = threading.Thread(target=host_worker, daemon=True)
        thread.start()

        result = tool_call("echo", {"text": "hello"}, timeout=2.0)

        assert ack.is_set(), "host did not receive request"
        assert result["type"] == "tool_result"
        assert result["content"]["echo"] == "hello"
        thread.join(timeout=1.0)


def test_tool_call_timeout():
    with _redirect_guest_io():
        with pytest.raises(ToolCallError):
            tool_call("noop", {"text": "late"}, timeout=0.1)


def test_session_handle_tool_request():
    responses: list[dict[str, object]] = []

    class Buffer(io.BytesIO):
        def write(self, data: bytes) -> int:
            return super().write(data)

        def flush(self) -> None:
            pass

    stdin_buffer = Buffer()

    def callback(name: str, request_id: str, arguments: dict[str, object]) -> dict[str, object]:
        responses.append({"name": name, "arguments": arguments})
        return {"id": request_id, "type": "tool_result", "content": {"ok": True}}

    session = _Session.__new__(_Session)  # type: ignore[misc]
    session.sid = "alpha"  # type: ignore[attr-defined]
    session.callback = callback  # type: ignore[attr-defined]
    session._stdin = stdin_buffer  # type: ignore[attr-defined]

    request = {"type": "tool_request", "id": "abc", "name": "echo", "arguments": {"text": "hi"}}
    _Session._handle_tool_request(session, request)

    written = stdin_buffer.getvalue()
    assert written, "no response written to stdin"
    payload = _read_frame(io.BytesIO(written))
    assert payload["type"] == "tool_result"
    assert payload["id"] == "abc"
    assert payload["content"] == {"ok": True}
    assert responses == [{"name": "echo", "arguments": {"text": "hi"}}]
