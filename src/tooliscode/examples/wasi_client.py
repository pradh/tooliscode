# example_wasi_server.py
from __future__ import annotations
import os, textwrap
from tooliscode.wasi_server import WasiPythonServer

ROOT = "/tmp/tooliscode"
PY_WASM = os.environ.get("PYTHON_WASM", "/opt/wasm/python.wasm")

# 1) Prepare session dirs and write the shim if it's not present
def ensure_session_dir(sid: str):
    sdir = os.path.join(ROOT, sid)
    os.makedirs(sdir, exist_ok=True)

def main():
    # Ensure session dirs + shims exist
    ensure_session_dir("alpha")
    ensure_session_dir("beta")

    # Make sure python.wasm is resolvable for the per-session runners
    os.environ.setdefault("PYTHON_WASM", PY_WASM)

    srv = WasiPythonServer(root=ROOT)

    # 2) Basic statefulness: run multiple cells in session "alpha"
    print("== alpha: define x and increment across turns ==")
    r1 = srv.exec_cell("alpha", 'x = 41; print("x =", x)')
    r2 = srv.exec_cell("alpha", 'x += 1; print("x now =", x)')
    print(r1.stdout.strip())
    print(r2.stdout.strip())

    # 3) Isolation: "beta" has its own state, no shared globals with "alpha"
    print("\n== beta: fresh namespace, x should be undefined until set ==")
    r3 = srv.exec_cell("beta", 'print("x exists?", "x" in globals())')
    r4 = srv.exec_cell("beta", 'x = 5; print("set x =", x)')
    print(r3.stdout.strip())
    print(r4.stdout.strip())

    # 4) File I/O scoped to session dir
    print("\n== alpha: write a file inside alpha dir ==")
    r5 = srv.exec_cell("alpha", 'open("alpha.txt","w").write("hello alpha"); print("ok")')
    print(r5.stdout.strip())
    print("alpha file present?", os.path.exists(os.path.join(ROOT, "alpha", "alpha.txt")))
    print("beta can’t see it from Python (different preopen):")
    r6 = srv.exec_cell("beta", 'import os; print(os.path.exists("alpha.txt"))')
    print(r6.stdout.strip())

    # 5) Timeout demo (busy loop). Expect exit_code 124 and error message.
    print("\n== alpha: timeout after 500 ms ==")
    r7 = srv.exec_cell("alpha", "while True: pass", timeout_ms=500)
    print("ok:", r7.ok, "error:", r7.error, "wall_ms:", r7.wall_ms)

    # 6) Reset session "alpha"
    print("\n== alpha: reset and verify x is gone ==")
    srv.reset("alpha")
    r8 = srv.exec_cell("alpha", 'print("x in globals?", "x" in globals())')
    print(r8.stdout.strip())

    # 7) Clean up
    srv.close("alpha")
    srv.close("beta")
    # srv.close_all()  # or close everything

if __name__ == "__main__":
    main()
