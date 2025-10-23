from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest
from pydantic import BaseModel

from tooliscode import runtime as runtime_mod
from tooliscode.runtime import ToolCallError, tool_call
from tooliscode.wasi_service import _ToolRequestWorker

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.append(str(SRC))


class EchoArgs(BaseModel):
    text: str


def test_tool_call_round_trip(tmp_path, monkeypatch):
    req_pipe = tmp_path / "tool_req.pipe"
    resp_pipe = tmp_path / "tool_res.pipe"
    os.mkfifo(req_pipe)
    os.mkfifo(resp_pipe)

    monkeypatch.chdir(tmp_path)

    ack = threading.Event()

    def responder():
        with open(req_pipe, "r", encoding="utf-8") as req_fh:
            line = req_fh.readline()
            payload = json.loads(line)
            ack.set()
            response = {
                "type": "tool_result",
                "id": payload["id"],
                "content": {"echo": payload["arguments"]["text"]},
            }
            with open(resp_pipe, "w", encoding="utf-8", buffering=1) as resp_fh:
                resp_fh.write(json.dumps(response))
                resp_fh.write("\n")
                resp_fh.flush()

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()

    result = tool_call("alpha", "echo", EchoArgs(text="hi"), timeout=5.0)

    assert ack.is_set(), "responder thread did not receive request"
    assert result["type"] == "tool_result"
    assert result["content"]["echo"] == "hi"
    thread.join(timeout=1)
    runtime_mod._session_runtimes.clear()


def test_tool_call_missing_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ToolCallError):
        tool_call("alpha", "noop", EchoArgs(text="hi"), timeout=0.1)
    runtime_mod._session_runtimes.clear()


def test_tool_request_worker_dispatch(tmp_path):
    req_pipe = tmp_path / "tool_req.pipe"
    resp_pipe = tmp_path / "tool_res.pipe"
    os.mkfifo(req_pipe)
    os.mkfifo(resp_pipe)

    responses = {}

    def callback(name: str, request_id: str, arguments: dict[str, object]) -> dict[str, object]:
        responses[request_id] = {"name": name, "arguments": arguments}
        return {
            "type": "tool_result",
            "id": request_id,
            "content": {"ok": True, "args": arguments},
        }

    worker = _ToolRequestWorker("alpha", callback, str(req_pipe), str(resp_pipe))

    received: dict[str, object] = {}

    def reader() -> None:
        with open(resp_pipe, "r", encoding="utf-8", buffering=1) as resp_fh:
            payload = resp_fh.readline().strip()
            if payload:
                received.update(json.loads(payload))

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    message = {
        "type": "function_call",
        "id": "req-1",
        "name": "echo",
        "arguments": {"text": "hi"},
    }
    with open(req_pipe, "w", encoding="utf-8", buffering=1) as req_fh:
        req_fh.write(json.dumps(message))
        req_fh.write("\n")
        req_fh.flush()

    reader_thread.join(timeout=1)
    worker.stop()

    assert "req-1" in responses
    assert responses["req-1"]["name"] == "echo"
    assert responses["req-1"]["arguments"] == {"text": "hi"}
    assert received["type"] == "tool_result"
    assert received["id"] == "req-1"
    assert received["content"] == {"ok": True, "args": {"text": "hi"}}
