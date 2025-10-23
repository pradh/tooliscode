# wasi_server.py
from __future__ import annotations

import base64
import secrets
import errno
import json
import os
import shutil
import stat
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, Callable

import wasmtime  # pip install wasmtime

ToolCallback = Callable[[str, str, dict[str, object]], dict[str, object]]


def _trace(msg: str) -> None:
    flag = os.environ.get("WASI_SERVER_TRACE")
    if flag and flag not in {"0", "false", "False"}:
        print(f"[wasi_server] {msg}", file=sys.stderr, flush=True)


def _lp_write(stdin_stream: Any, obj: dict) -> None:
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    header = f"{len(payload)}\n".encode("utf-8")
    _trace(f"[host] lp_write header={len(header)} payload={len(payload)}")
    stdin_stream.write(header)
    stdin_stream.write(payload)


def _lp_read(stdout_stream: Any, max_bytes: int = 2_000_000) -> dict:
    header = bytearray()
    while True:
        chunk = stdout_stream.read(1)
        if not chunk:
            raise EOFError("guest stdout closed")
        header += chunk
        if header.endswith(b"\n"):
            break
        if len(header) > 64:
            raise ValueError("invalid frame header")
    length = int(header.strip() or b"0")
    if length < 0 or length > max_bytes:
        raise ValueError(f"frame too large: {length}")
    data = bytearray()
    while len(data) < length:
        chunk = stdout_stream.read(length - len(data))
        if not chunk:
            raise EOFError("guest stdout closed mid-frame")
        data += chunk
    _trace(f"[host] lp_read framesz={length} bytes={len(data)}")
    return json.loads(data.decode("utf-8"))


def _wait_for_fifo_fd(path: str, flags: int, blocking: bool, deadline: Optional[float]) -> int:
    while True:
        try:
            fd = os.open(path, flags)
            break
        except OSError as exc:
            if exc.errno in (errno.ENXIO, errno.ENOENT):
                if deadline is not None and time.time() > deadline:
                    raise TimeoutError(f"timeout opening FIFO: {path}") from exc
                time.sleep(0.01)
                continue
            raise
    os.set_blocking(fd, blocking)
    return fd


class _FifoWriter:
    def __init__(self, path: str, deadline: Optional[float]) -> None:
        flags = os.O_WRONLY | os.O_NONBLOCK
        self._fd = _wait_for_fifo_fd(path, flags, True, deadline)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        sent = 0
        while sent < len(view):
            n = os.write(self._fd, view[sent:])
            if n <= 0:
                raise RuntimeError("FIFO write failed")
            sent += n
            _trace(f"[host] fifo write chunk={n} total={sent}/{len(view)}")

    def close(self) -> None:
        os.close(self._fd)


class _FifoReader:
    def __init__(self, path: str, blocking: bool, deadline: Optional[float]) -> None:
        flags = os.O_RDONLY | (0 if blocking else os.O_NONBLOCK)
        self._fd = _wait_for_fifo_fd(path, flags, blocking, deadline)
        self._blocking = blocking

    def read(self, n: int = -1) -> bytes:
        if self._blocking:
            size = n if n and n > 0 else 1
            return os.read(self._fd, size)
        remaining = n if n and n > 0 else None
        chunks: List[bytes] = []
        while True:
            size = remaining if remaining is not None else 65536
            try:
                chunk = os.read(self._fd, size)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
            if remaining is not None:
                remaining -= len(chunk)
                if remaining <= 0:
                    break
        return b"".join(chunks)

    def close(self) -> None:
        os.close(self._fd)


@dataclass
class ExecResult:
    ok: bool
    stdout: str
    stderr: str
    wall_ms: int
    error: Optional[str] = None


def _assign_fifo_path(wasi: wasmtime.WasiConfig, name: str, path: str) -> Optional[int]:
    """
    Assign a FIFO-backed stdio path. wasmtime immediately opens the file during
    assignment, so we temporarily open the FIFO in O_RDWR to satisfy both ends
    and keep that placeholder open until the host attaches real streams.
    """
    peer_fd: Optional[int] = None
    try:
        peer_fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno not in (errno.ENXIO, errno.ENOENT):
            raise
        peer_fd = None

    try:
        setattr(wasi, f"{name}_file", path)
        return peer_fd
    except Exception:
        if peer_fd is not None:
            try:
                os.close(peer_fd)
            except OSError:
                pass
        raise


class _Session:
    """
    One long-lived CPython-WASI instance running `guest.py` inside /tmp/tooliscode/<sid>.
    Maintains persistent Python globals via the shim loop.
    """

    def __init__(self, sid: str, callback: ToolCallback, root: str = "/tmp/tooliscode") -> None:
        self.sid = sid
        self.callback = callback
        self.sdir = os.path.abspath(os.path.join(root, sid))
        _trace(f"[session {sid}] init start (root={self.sdir})")
        if not os.path.isdir(self.sdir):
            raise FileNotFoundError(f"missing session dir: {self.sdir}")

        cfg = wasmtime.Config()
        if hasattr(cfg, "interruptable"):
            try:
                cfg.interruptable = True
            except TypeError:
                cfg.interruptable(True)
        self.engine = wasmtime.Engine(cfg)

        wasm_path = self._python_wasm_path()
        _trace(f"[session {sid}] loading python.wasm from {wasm_path}")
        self.module = wasmtime.Module.from_file(self.engine, wasm_path)
        self.store = wasmtime.Store(self.engine)

        self._ih = None
        if hasattr(self.store, "interrupt_handle"):
            try:
                self._ih = self.store.interrupt_handle()
            except Exception:
                self._ih = None
        if self._ih:
            _trace(f"[session {sid}] interrupt handle acquired")
        else:
            _trace(f"[session {sid}] interrupt handle unavailable")

        self.linker = wasmtime.Linker(self.engine)
        self.linker.define_wasi()

        wasi = wasmtime.WasiConfig()

        session_alias = os.environ.get("WASI_SESSION_GUEST")
        self._session_guest_path = self._preopen_dir(wasi, self.sdir, session_alias)

        self._ensure_guest_files()

        wasi.argv = ["python", "-u", f"{self._session_guest_path}/guest.py"]

        env_map = self._setup_python_runtime(wasi, wasm_path)
        py_path = env_map.setdefault("PYTHONPATH", [])
        if self._session_guest_path not in py_path:
            py_path.insert(0, self._session_guest_path)
        if env_map:
            self._configure_wasi_env(wasi, env_map)
        _trace(f"[session {sid}] env_map = {env_map}")

        self._stdin = None
        self._stdout = None
        self._stderr = None
        self._fifo_paths: Optional[Dict[str, str]] = None
        self._fifo_placeholders: Dict[str, int] = {}
        self._use_pipe = hasattr(wasmtime, "Pipe")

        if self._use_pipe:
            _trace(f"[session {sid}] using wasmtime.Pipe for stdio")
            self._stdin = wasmtime.Pipe()
            self._stdout = wasmtime.Pipe()
            self._stderr = wasmtime.Pipe()
            for name, stream in (("stdin", self._stdin), ("stdout", self._stdout), ("stderr", self._stderr)):
                setattr(wasi, f"{name}_file", stream)
        else:
            _trace(f"[session {sid}] falling back to FIFO stdio")
            self._fifo_paths = self._prepare_fifos()
            for name, path in self._fifo_paths.items():
                fd = _assign_fifo_path(wasi, name, path)
                if fd is not None:
                    self._fifo_placeholders[name] = fd

        self.store.set_wasi(wasi)

        if not self._use_pipe:
            self._attach_fifo_streams()
        else:
            _trace(f"[session {sid}] pipe streams ready")

        inst = self.linker.instantiate(self.store, self.module)

        def _run_guest() -> None:
            try:
                inst.exports(self.store)["_start"](self.store)
            except BaseException as exc:
                self._guest_error = exc
                print(exc)
                _trace(f"[session {self.sid}] guest terminated with {exc!r}")

        self._guest_error: Optional[BaseException] = None
        self._guest_thread = threading.Thread(
            target=_run_guest, name=f"wasi-session-{sid}", daemon=True
        )
        _trace(f"[session {sid}] About to start guest")
        self._guest_thread.start()

        self._lock = threading.Lock()
        _trace(f"[session {sid}] init complete")

    def exec_cell(self, code: str, timeout_ms: int = 8000) -> ExecResult:
        with self._lock:
            timer = None
            timed_out = False
            if timeout_ms and timeout_ms > 0 and self._ih is not None:
                def _on_timeout() -> None:
                    nonlocal timed_out
                    timed_out = True
                    self._ih.interrupt()

                timer = threading.Timer(timeout_ms / 1000.0, _on_timeout)
                timer.daemon = True
                timer.start()

            start = time.perf_counter_ns()
            err_text = None
            _trace(f"[session {self.sid}] exec start timeout={timeout_ms}")
            try:
                self._ensure_guest_running()
                _trace(f"[session {self.sid}] guest is running")
                _lp_write(self._stdin, {"type": "exec_request", "code": code})
                _trace(f"[session {self.sid}] wrote to it, now reading")
                resp = self._wait_for_exec_result()
            except wasmtime.Trap:
                err_text = f"Timeout after {timeout_ms} ms" if timed_out else "Trap"
                resp = {"ok": False, "stdout": "", "stderr": "", "error": {"msg": err_text}}
                _trace(f"[session {self.sid}] exec trap err={err_text}")
            except Exception as exc:
                err_text = str(exc)
                resp = {"ok": False, "stdout": "", "stderr": "", "error": {"msg": err_text}}
                _trace(f"[session {self.sid}] exec exception {err_text}")
            finally:
                if timer:
                    timer.cancel()

            wall = (time.perf_counter_ns() - start) // 1_000_000
            raw_stderr = self._drain_stderr().decode("utf-8", "replace")
            stderr = (resp.get("stderr") or "") + raw_stderr
            _trace(
                f"[session {self.sid}] exec done ok={resp.get('ok')} wall={wall}ms "
                f"stdout_len={len(resp.get('stdout') or '')} stderr_len={len(stderr)}"
            )
            _trace(resp)

            return ExecResult(
                ok=bool(resp.get("ok")),
                stdout=resp.get("stdout") or "",
                stderr=stderr,
                wall_ms=int(wall),
                error=(resp.get("error") or {}).get("msg") if resp.get("error") else err_text,
            )

    def _wait_for_exec_result(self) -> Dict[str, Any]:
        while True:
            resp = _lp_read(self._stdout)
            if not isinstance(resp, dict):
                continue
            msg_type = resp.get("type")
            if msg_type == "tool_request":
                self._handle_tool_request(resp)
                continue
            if msg_type is None:
                resp["type"] = "exec_result"
            return resp

    def _handle_tool_request(self, message: dict) -> None:
        request_id = message.get("id")
        if not request_id:
            _trace(f"[session {self.sid}] tool request missing id")
            return
        name = message.get("name") or ""
        arguments = message.get("arguments") or {}
        try:
            result = self.callback(name, request_id, arguments)
        except Exception as exc:  # pragma: no cover - defensive
            _trace(f"[session {self.sid}] tool callback error: {exc}")
            response = {
                "type": "tool_result",
                "id": request_id,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }
        else:
            assert isinstance(result, dict)
            response = result.copy()
            response.setdefault("type", "tool_result")
            response.setdefault("id", request_id)

        _lp_write(self._stdin, response)

    def reset(self) -> None:
        with self._lock:
            _trace(f"[session {self.sid}] reset request")
            self._ensure_guest_running()
            _lp_write(self._stdin, {"type": "reset"})
            try:
                _lp_read(self._stdout)
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            try:
                _trace(f"[session {self.sid}] close request")
                self._ensure_guest_running()
                _lp_write(self._stdin, {"type": "exit"})
                _lp_read(self._stdout)
            except Exception:
                pass
            finally:
                self._cleanup_stdio()

    def _python_wasm_path(self) -> str:
        path = os.environ.get("PYTHON_WASM", "/opt/wasm/python.wasm")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"python.wasm not found at {path}")
        return path

    def _ensure_guest_files(self) -> None:
        for f in ["guest.py", "guest_helpers.py"]:
            dst_path = os.path.join(self.sdir, f)
            if not os.path.isfile(dst_path):
                src_path = os.path.join(os.path.dirname(__file__), f)
                shutil.copyfile(src_path, dst_path)

    def _prepare_fifos(self) -> Dict[str, str]:
        mapping = {
            "stdin": os.path.join(self.sdir, "_stdin.fifo"),
            "stdout": os.path.join(self.sdir, "_stdout.fifo"),
            "stderr": os.path.join(self.sdir, "_stderr.fifo"),
        }
        for path in mapping.values():
            if os.path.exists(path):
                if not stat.S_ISFIFO(os.stat(path).st_mode):
                    raise RuntimeError(f"{path} exists but is not a FIFO")
            else:
                os.mkfifo(path, 0o600)
        return mapping

    def _attach_fifo_streams(self) -> None:
        assert self._fifo_paths is not None
        wait_seconds = float(os.environ.get("WASI_FIFO_WAIT_SECONDS", "5") or "0")
        deadline = time.time() + max(wait_seconds, 0.5) if wait_seconds > 0 else None
        try:
            self._stdin = _FifoWriter(self._fifo_paths["stdin"], deadline)
            self._stdout = _FifoReader(self._fifo_paths["stdout"], True, deadline)
            self._stderr = _FifoReader(self._fifo_paths["stderr"], False, deadline)
            _trace(f"[session {self.sid}] fifo streams attached")
        except Exception:
            self._cleanup_stdio()
            raise
        finally:
            self._release_fifo_placeholders()

    def _release_fifo_placeholders(self) -> None:
        for fd in list(self._fifo_placeholders.values()):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fifo_placeholders.clear()

    def _ensure_guest_running(self) -> None:
        if self._guest_thread is None:
            raise RuntimeError("guest not started")
        if self._guest_thread.is_alive():
            return
        if self._guest_error:
            raise RuntimeError("guest process terminated") from self._guest_error
        raise RuntimeError("guest process exited unexpectedly")

    def _drain_stderr(self) -> bytes:
        if self._stderr is None:
            return b""
        try:
            data = self._stderr.read()
            return data or b""
        except TypeError:
            chunks = []
            while True:
                chunk = self._stderr.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if len(chunk) < 65536:
                    break
            return b"".join(chunks)

    def _configure_wasi_env(self, wasi: wasmtime.WasiConfig, env_map: Dict[str, List[str]]) -> None:
        entries: Dict[str, str] = {}
        for key, values in env_map.items():
            if not values:
                continue
            if key == "PYTHONPATH":
                seen = []
                for value in values:
                    if value not in seen:
                        seen.append(value)
                entries[key] = ":".join(seen)
            else:
                entries[key] = values[-1]

        if not entries:
            return

        setter = getattr(wasi, "set_env", None)
        if callable(setter):
            try:
                for key, value in entries.items():
                    setter(key, value)
                return
            except TypeError:
                for key, value in entries.items():
                    setter(f"{key}={value}")
                return

        existing: List[Iterable]
        try:
            existing = list(getattr(wasi, "env"))
        except (AttributeError, TypeError):
            existing = []
        merged = existing + [(key, value) for key, value in entries.items() if key not in existing]
        try:
            setattr(wasi, "env", merged)
        except AttributeError as exc:
            raise AttributeError("WasiConfig cannot assign environment variables") from exc

    def _setup_python_runtime(self, wasi: wasmtime.WasiConfig, wasm_path: str) -> Dict[str, List[str]]:
        env: Dict[str, List[str]] = {}
        host_home = os.environ.get("PYTHON_WASM_HOME")
        if not host_home or not os.path.isdir(host_home):
            _trace(f"[session {self.sid}] python home not found at {host_home}")
            return env

        guest_home_req = os.environ.get("PYTHON_WASM_HOME_GUEST") or "/python_home"
        guest_home = self._preopen_dir(wasi, host_home, guest_home_req)
        env["PYTHONHOME"] = [guest_home]

        lib_dir = os.path.join(host_home, "lib")
        search: List[str] = []
        if os.path.isdir(lib_dir):
            guest_lib = f"{guest_home}/lib"
            search.append(guest_lib)
            for entry in sorted(os.listdir(lib_dir)):
                host_entry = os.path.join(lib_dir, entry)
                guest_entry = f"{guest_lib}/{entry}"
                if entry.endswith(".zip") and os.path.isfile(host_entry):
                    search.append(guest_entry)
                elif os.path.isdir(host_entry):
                    search.append(guest_entry)
        else:
            search.append(guest_home)

        deduped: List[str] = []
        for value in search:
            if value not in deduped:
                deduped.append(value)
        env.setdefault("PYTHONPATH", []).extend(deduped)
        _trace(f"[session {self.sid}] resolved python env {env}")
        return env

    def _preopen_dir(self, wasi: wasmtime.WasiConfig, host_path: str, guest_path: Optional[str]) -> str:
        if not os.path.isdir(host_path):
            raise FileNotFoundError(f"preopen dir missing: {host_path}")
        alias = guest_path or host_path
        preopen = getattr(wasi, "preopen_dir", None)
        if not callable(preopen):
            raise AttributeError("WasiConfig has no preopen_dir")
        try:
            preopen(host_path, alias)
        except TypeError:
            preopen(host_path)
            alias = host_path
        return alias

    def _cleanup_stdio(self) -> None:
        for stream in (self._stdin, self._stdout, self._stderr):
            if stream is None:
                continue
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except OSError:
                    pass
        if self._fifo_paths:
            for path in self._fifo_paths.values():
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        self._release_fifo_placeholders()


class WasiService:
    """
    Manages one host process with multiple CPython-WASI sessions.
    Each session is isolated to /tmp/tooliscode/<sid>.
    """

    def __init__(self, root: str = "/tmp/tooliscode") -> None:
        self.root = os.path.abspath(root)
        self._sessions: Dict[str, _Session] = {}
        self._lock = threading.RLock()
        os.makedirs(self.root, exist_ok=True)

    def create_session(self, callback: ToolCallback) -> str:
        sid = base64.b64encode(secrets.token_bytes(12)).decode("ascii")
        _trace(f"[server] creating session {sid}")
        assert sid not in self._sessions
        with self._lock:
            session_dir = os.path.join(self.root, sid)
            os.makedirs(session_dir, exist_ok=True)
            self._sessions[sid] = _Session(sid, callback, self.root)
        return sid

    def exec_cell(self, sid: str, code: str, timeout_ms: int = 8000) -> ExecResult:
        _trace(f"[server] exec_cell sid={sid}")
        return self._sessions[sid].exec_cell(code, timeout_ms=timeout_ms)

    def reset(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if session:
                session.reset()

    def close(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.pop(sid, None)
            if session:
                _trace(f"[server] closing session {sid}")
                session.close()

    def close_all(self) -> None:
        with self._lock:
            for sid, session in list(self._sessions.items()):
                try:
                    session.close()
                finally:
                    self._sessions.pop(sid, None)
