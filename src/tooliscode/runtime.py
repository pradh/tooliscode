"""Runtime helpers for tool function invocation inside the WASI guest."""

from __future__ import annotations

import sys, json, io, contextlib, traceback, os, time, threading, uuid, select

from typing import Any, Dict

__all__ = ["ToolCallError", "tool_call"]
G = {"__name__": "__main__"}  # persistent globals


DEFAULT_REQ_NAME = "tool_req.pipe"
DEFAULT_RESP_NAME = "tool_res.pipe"

SIDEDIR = os.path.dirname(__file__)          # <— this is the session dir
SIDELOG = os.path.join(SIDEDIR, "_shim.log")

sys.path.append(os.path.dirname(__file__))
os.chdir(SIDEDIR)


def _sidelog(msg):
    try:
        with open(SIDELOG, "a", buffering=1) as f:  # line-buffered
            f.write(f"[{time.time():.3f}] {msg}\n")
    except Exception:
        pass

_sidelog(f"shim boot: starting up {sys.stdin.isatty()} and {sys.stdin.buffer.raw}")


class ToolCallError(RuntimeError):
    """Raised when tool_call cannot complete."""

_IO_LOCK = threading.Lock()


def tool_call(function_name: str, args: dict, *, timeout: float = 30.0) -> Dict[str, Any]:
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

    def __init__(self) -> None:
        self._req_path = DEFAULT_REQ_NAME
        self._resp_path = DEFAULT_RESP_NAME

    def invoke(self, function_name: str, args: dict, *, timeout: float) -> Dict[str, Any]:
        request_id = uuid.uuid4().hex
        payload = {
            "type": "function_call",
            "id": request_id,
            "name": function_name,
            "arguments": args,
        }
        message = json.dumps(payload, ensure_ascii=False) + "\n"
        with _IO_LOCK:
            self._write_request(message)
            return self._read_response(request_id, timeout)

    def _write_request(self, message: str) -> None:
        try:
            with open(self._req_path, "w", encoding="utf-8", buffering=1) as pipe:
                pipe.write(message)
                pipe.flush()
        except OSError as exc:
            raise ToolCallError(f"Failed to write to request pipe {self._req_path}: {exc}") from exc

    def _read_response(self, request_id: str, timeout: float) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout if timeout and timeout > 0 else None
        try:
            with open(self._resp_path, "r", encoding="utf-8", buffering=1) as pipe:
                while True:
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ToolCallError(f"Timed out waiting for tool response for id {request_id}")
                        ready, _, _ = select.select([pipe.fileno()], [], [], remaining)
                        if not ready:
                            raise ToolCallError(f"Timed out waiting for tool response for id {request_id}")
                    line = pipe.readline()
                    if not line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if message.get("id") != request_id:
                        continue
                    return message
        except OSError as exc:
            raise ToolCallError(f"Failed to read response pipe {self._resp_path}: {exc}") from exc


def _lp_read():
    fin = sys.stdin.buffer
    header = bytearray()
    while True:
        ch = fin.read(1)
        if not ch:
            return None  # EOF before header
        if ch == b"\n":
            break
        header += ch
    n = int(header.decode("ascii"))
    _sidelog(f"_lp_read header={n}")
    if not n:
        return None
    data = bytearray()
    while len(data) < n:
        chunk = sys.stdin.buffer.read(n - len(data))
        _sidelog(f"_lp_read payload_len={len(data)}")
        if not chunk:
            raise EOFError("stdin closed mid-frame")
        data.extend(chunk)
    return json.loads(data)


def _lp_write(obj):
    b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    _sidelog(f"_lp_write payload_len={len(b)}")
    fout = sys.stdout.buffer
    fout.write(str(len(b)).encode("ascii") + b"\n")
    fout.write(b)
    fout.flush()


def run_cell(src: str):
    _sidelog(f"run_cell start len={len(src)}")
    out, err = io.StringIO(), io.StringIO()
    ok, eobj = True, None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(src, "<cell>", "exec"), G, G)
    except SystemExit as e:
        ok, eobj = False, {"type": "SystemExit", "msg": str(e)}
    except Exception as e:
        ok, eobj = False, {"type": type(e).__name__, "msg": str(e), "trace": traceback.format_exc()}
    _sidelog(f"run_cell done ok={ok} out={len(out.getvalue())} err={len(err.getvalue())}")
    return {"ok": ok, "stdout": out.getvalue(), "stderr": err.getvalue(), "error": eobj}


if __name__ == "__main__":
    try:
        while True:
            try:
                _sidelog("Waiting on read")
                req = _lp_read()
            except Exception as e:
                _sidelog(f"_lp_read error: {type(e).__name__}: {e}")
                _lp_write({"ok": False, "error": {"type": type(e).__name__, "msg": str(e), "trace": traceback.format_exc()}})
                break
            if not req:
                _sidelog("EOF on stdin")
                break
            t = req.get("type")
            _sidelog(f"recv type={t}")
            try:
                if t == "exec":
                    _lp_write(run_cell(req.get("code", "")))
                elif t == "reset":
                    G.clear(); G["__name__"] = "__main__"
                    _lp_write({"ok": True})
                elif t == "exit":
                    _lp_write({"ok": True}); break
                else:
                    _lp_write({"ok": False, "error": {"msg": f"unknown type: {t}"}})
            except BrokenPipeError as e:
                _sidelog(f"BrokenPipe during _lp_write: {e}")
                break
            except Exception as e:
                _sidelog(f"_lp_write error: {type(e).__name__}: {e}")
                break
    finally:
        _sidelog("main loop exit")
