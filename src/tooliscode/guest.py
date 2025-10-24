"""Runtime helpers for tool function invocation inside the WASI guest."""

from __future__ import annotations

import sys, io, contextlib, traceback, os

G = {"__name__": "__main__"}  # persistent globals

sys.path.append(os.path.dirname(__file__))

from guest_helpers import sidelog, lp_read, lp_write, ToolRuntime


def run_cell(src: str):
    sidelog(f"run_cell start len={len(src)}")
    out, err = io.StringIO(), io.StringIO()
    ok, eobj = True, None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exec(compile(src, "<cell>", "exec"), G, G)
    except SystemExit as e:
        ok, eobj = False, {"type": "SystemExit", "msg": str(e)}
    except Exception as e:
        ok, eobj = False, {"type": type(e).__name__, "msg": str(e), "trace": traceback.format_exc()}
    sidelog(f"run_cell done ok={ok} out={len(out.getvalue())} err={len(err.getvalue())}")
    if not ok:
        sidelog(eobj)
    return {"ok": ok, "stdout": out.getvalue(), "stderr": err.getvalue(), "error": eobj}


if __name__ == "__main__":
    try:
        while True:
            try:
                sidelog("Waiting on read")
                req = lp_read()
            except Exception as e:
                sidelog(f"_lp_read error: {type(e).__name__}: {e}")
                lp_write({"ok": False, "error": {"type": type(e).__name__, "msg": str(e), "trace": traceback.format_exc()}})
                break
            if not req:
                sidelog("EOF on stdin")
                break
            t = req.get("type")
            sidelog(f"recv type={t}")
            try:
                if t == "exec_request":
                    result = run_cell(req.get("code", ""))
                    if isinstance(result, dict):
                        result = result.copy()
                        result.setdefault("type", "exec_result")
                    lp_write(result)
                elif t == "tool_result":
                    sidelog("main loop received tool_result unexpectedly")
                elif t == "reset":
                    G.clear(); G["__name__"] = "__main__"
                    lp_write({"ok": True})
                elif t == "exit":
                    ToolRuntime().shutdown()
                    lp_write({"ok": True}); break
                else:
                    lp_write({"ok": False, "error": {"msg": f"unknown type: {t}"}})
            except BrokenPipeError as e:
                sidelog(f"BrokenPipe during _lp_write: {e}")
                break
            except Exception as e:
                sidelog(f"_lp_write error: {type(e).__name__}: {e}")
                break
    finally:
        sidelog("main loop exit")
