from __future__ import annotations

import io
import sys
import types


def _install_wasmtime_stub() -> None:
    if "wasmtime" in sys.modules:
        return

    module = types.ModuleType("wasmtime")

    class Trap(Exception):
        pass

    class Pipe:
        def __init__(self) -> None:
            self._buffer = io.BytesIO()
            self._closed = False

        def write(self, data: bytes) -> int:
            if self._closed:
                raise ValueError("I/O operation on closed pipe")
            pos = self._buffer.tell()
            self._buffer.seek(0, io.SEEK_END)
            written = self._buffer.write(data)
            self._buffer.seek(pos)
            return written

        def read(self, n: int = -1) -> bytes:
            if self._closed:
                return b""
            return self._buffer.read(n)

        def close(self) -> None:
            self._closed = True

        def flush(self) -> None:
            pass

    class Config:
        def __init__(self) -> None:
            self._interruptable = False

        @property
        def interruptable(self) -> bool:
            return self._interruptable

        @interruptable.setter
        def interruptable(self, value: bool) -> None:
            self._interruptable = bool(value)

    class Engine:
        def __init__(self, config: Config | None = None) -> None:
            self.config = config

    class Store:
        def __init__(self, engine: Engine) -> None:
            self.engine = engine

        def set_wasi(self, _: object) -> None:
            pass

        def interrupt_handle(self):
            class Handle:
                def interrupt(self) -> None:
                    pass

            return Handle()

    class Module:
        @staticmethod
        def from_file(engine: Engine, path: str) -> object:
            return object()

    class Linker:
        def __init__(self, engine: Engine) -> None:
            self.engine = engine

        def define_wasi(self) -> None:
            pass

        def instantiate(self, store: Store, module: object):
            class Instance:
                @staticmethod
                def exports(_store: Store) -> dict[str, object]:
                    return {"_start": lambda _store: None}

            return Instance()

    class WasiConfig:
        pass

    module.Trap = Trap
    module.Pipe = Pipe
    module.Config = Config
    module.Engine = Engine
    module.Store = Store
    module.Module = Module
    module.Linker = Linker
    module.WasiConfig = WasiConfig

    sys.modules["wasmtime"] = module


_install_wasmtime_stub()
