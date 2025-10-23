"""Simple demo that drives ``tooliscode.wasi_service.WasiService``."""

from __future__ import annotations

import os
from pathlib import Path

from tooliscode import NOP_CALLBACK
from tooliscode.host import ExecResult, WasiService

ROOT = Path(os.environ.get("TOOLISCODE_ROOT", "/tmp/tooliscode")).resolve()
PYTHON_WASM = os.environ.get("PYTHON_WASM", "/opt/wasm/python.wasm")


def _print_result(label: str, result: ExecResult) -> None:
    """Pretty-print the outcome of ``exec_cell`` for the example."""
    print(f"{label}: ok={result.ok} wall={result.wall_ms}ms")
    if result.stdout.strip():
        print("stdout:")
        print(result.stdout.strip())
    if result.stderr.strip():
        print("stderr:")
        print(result.stderr.strip())
    if result.error:
        print(f"error: {result.error}")
    print("-" * 40)


def main() -> None:
    # Ensure the python.wasm path is available to the guest.
    os.environ.setdefault("PYTHON_WASM", PYTHON_WASM)

    service = WasiService(root=str(ROOT))
    sessions: list[str] = []
    try:
        alpha = service.create_session(NOP_CALLBACK)
        beta = service.create_session(NOP_CALLBACK)
        sessions.extend([alpha, beta])
        print(f"Created sessions alpha={alpha} beta={beta}")

        _print_result(
            "alpha: init",
            service.exec_cell(alpha, 'x = 41\nprint("x =", x)'),
        )
        _print_result(
            "alpha: mutate",
            service.exec_cell(alpha, 'x += 1\nprint("x now =", x)'),
        )

        _print_result(
            "beta: globals",
            service.exec_cell(beta, 'print("x exists?", "x" in globals())'),
        )
        _print_result(
            "beta: assign",
            service.exec_cell(beta, 'x = 5\nprint("set x =", x)'),
        )

        _print_result(
            "alpha: file",
            service.exec_cell(alpha, 'open("alpha.txt","w").write("hello alpha"); print("ok")')
        )

        _print_result(
            "alpha: can see file",
            service.exec_cell(alpha, 'import os; print(os.path.exists("alpha.txt"))')
        )

        _print_result(
            "beta: cannot see file",
            service.exec_cell(beta, 'import os; print(os.path.exists("alpha.txt"))')
        )

        service.reset(alpha)
        _print_result(
            "alpha: after reset",
            service.exec_cell(alpha, 'print("x still defined?", "x" in globals())'),
        )
    finally:
        for sid in sessions:
            service.close(sid)


if __name__ == "__main__":
    main()
