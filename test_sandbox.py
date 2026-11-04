"""Offline checks of the try-out sandbox (sandbox.py): its own runner and server run here for real (the same code the
sandbox app runs), so a failing check names the agent's line, the network is off, the sandbox's own credentials never
reach the tried code, stand-in tools answer only what a check says, a missing library is "not tried" rather than a
failure, and the code writer sends what failed back to the model."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import types

import sandbox

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def server():
    """The sandbox app's server.py, loaded here with its files beside it (process limits are Linux-only)."""
    d = tempfile.mkdtemp(prefix="sbx-")
    for path, text in sandbox.FILES:
        with open(os.path.join(d, path), "w", encoding="utf-8") as f:
            f.write(text)
    mod = types.ModuleType("sandbox_server")
    mod.__file__ = os.path.join(d, "server.py")
    exec(compile(sandbox.SERVER_PY, mod.__file__, "exec"), mod.__dict__)
    if os.name == "nt":
        mod._limits = None  # preexec_fn is POSIX only; on the Linux app the limits apply
    return mod


SRV = server()


def run(files: dict) -> dict:
    return SRV.run({"files": files})


LOGIC = '''"""Margins."""
try:
    from agent import tools
except ImportError:
    import tools


def margin(net_cost: str, hard_cost: str) -> dict:
    """Use for a plan's margin."""
    net = float(net_cost)
    hard = float(hard_cost)
    m = (net - hard) / net
    return {"margin": round(m, 2), "needs_approval": m < 30}


def first_sheet(path: str) -> list:
    """Use to list the sheet names of a workbook."""
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(tools.read_file(path)), data_only=True)
    return [ws.title for ws in wb.worksheets]
'''

CHECKS = '''from agent import logic, tools


def check_commas():
    r = logic.margin("25,000", "15,000")
    assert r["margin"] == 0.4, r


def check_approval_threshold():
    r = logic.margin("20000", "8000")
    assert r["needs_approval"] is False, r


def check_plain():
    assert logic.margin("100", "50")["margin"] == 0.5
'''


@case("a failing check names the agent's own line; passing ones pass; it is quick")
def _():
    out = run({"agent/logic.py": LOGIC, "tests/checks.py": CHECKS})
    by = {r["name"]: r for r in out["results"]}
    assert out["ok"] is False and by["check_plain"]["ok"] is True, out
    assert "could not convert string to float: '25,000'" in by["check_commas"]["error"], by["check_commas"]
    assert by["check_commas"]["where"].startswith("agent/logic.py line 10 (net = float(net_cost))"), by["check_commas"]["where"]
    assert by["check_approval_threshold"]["error"].startswith("Check failed:") and "'needs_approval': True" in by["check_approval_threshold"]["error"]
    assert out["seconds"] < 20


@case("the network is off, the sandbox's own credentials are not there, and nothing outside the try folder is left")
def _():
    os.environ["DATABRICKS_CLIENT_SECRET"] = "would-be-secret"
    try:
        checks = '''import os
import socket


def check_network():
    socket.create_connection(("example.com", 443), timeout=2)


def check_environment():
    assert "DATABRICKS_CLIENT_SECRET" not in os.environ, "the sandbox's secret reached the tried code"
    assert not any(k.startswith("DATABRICKS") for k in os.environ), sorted(os.environ)
'''
        out = run({"agent/logic.py": "X = 1\n", "tests/checks.py": checks})
        by = {r["name"]: r for r in out["results"]}
        assert "no network" in by["check_network"]["error"], by["check_network"]
        assert by["check_environment"]["ok"] is True, by["check_environment"]
    finally:
        os.environ.pop("DATABRICKS_CLIENT_SECRET", None)


@case("stand-in tools: a check says what they answer (a real workbook with merged cells); unfaked ones say so")
def _():
    import openpyxl

    checks = '''import io
import openpyxl
from agent import logic, tools


def _book():
    wb = openpyxl.Workbook()
    wb.active.title = "Display"
    wb.active.merge_cells("A1:C1")
    wb.create_sheet("Video")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def check_sheets():
    tools.fake("read_file", _book())
    assert logic.first_sheet("/Volumes/c/s/v/ad book.xlsx") == ["Display", "Video"]


def check_not_faked():
    logic.first_sheet("/Volumes/c/s/v/x.xlsx")


def check_saved():
    tools.save_file("/Volumes/sandbox/try/out/plan.xlsx", b"x")
    assert "/Volumes/sandbox/try/out/plan.xlsx" in tools.SAVED
'''
    assert openpyxl  # the sandbox has it too (sandbox.REQUIREMENTS)
    out = run({"agent/logic.py": LOGIC, "tests/checks.py": checks})
    by = {r["name"]: r for r in out["results"]}
    assert by["check_sheets"]["ok"] is True, by["check_sheets"]
    assert "cannot reach anything in the sandbox" in by["check_not_faked"]["error"] and "tools.fake('read_file'" in by["check_not_faked"]["error"]
    assert by["check_saved"]["ok"] is True, by["check_saved"]


@case("a library the sandbox lacks is 'not tried', not a failure; bad requests are refused")
def _():
    out = run({"agent/logic.py": "X = 1\n", "tests/checks.py": "def check_x():\n    import not_a_real_library_xyz\n"})
    assert out["results"][0]["ok"] is None and "not_a_real_library_xyz" in out["results"][0]["error"], out
    assert out["ok"] is True
    from fastapi import HTTPException
    for files in ({"agent/logic.py": "x"}, {"agent/tools.py": "x", "tests/checks.py": ""}, {"../evil.py": "", "tests/checks.py": ""}):
        try:
            run(files)
            raise AssertionError("accepted %s" % list(files))
        except HTTPException as exc:
            assert exc.status_code == 400


@case("the code writer: sandbox failures go back to the model as problems, and it fixes them")
def _():
    import code_agent as ca
    import designer
    import designer_guard
    import test_code_agent as t

    designer_guard.settings = lambda: dict(designer_guard.DEFAULTS)
    good_checks = "from agent import logic\n\n\ndef check_margin():\n    assert logic.margin(80000.0, 5000.0)['margin'] == 0.9375\n"
    first = t.reply({"agent/logic.py": t.LOGIC_OK, "tests/checks.py": good_checks.replace("0.9375", "0.5")})
    seen = t.scripted(first, "=== EDIT tests/checks.py ===\n<<<<<<< SEARCH\n== 0.5\n=======\n== 0.9375\n>>>>>>> REPLACE\n=== END EDIT ===\n")
    tries = []

    def try_code(code):
        tries.append(code)
        return run(code)
    rounds = []
    out = ca.write(t.SETUP, "T", dict(t.USE), [], urls={}, rounds=rounds, try_code=try_code)
    assert [r["kind"] for r in rounds] == ["write", "try", "fix", "try"], [r["kind"] for r in rounds]
    assert "check_margin failed in the sandbox" in rounds[1]["problems"][0] and rounds[3]["problems"] == []
    assert "check_margin failed in the sandbox" in seen[1][-1]["content"]  # told to the model as a problem
    assert out["problems"] == [] and out["tried"]["ok"] is True and "tests/checks.py" in out["code"]
    assert set(tries[0]) == {"agent/logic.py", "agent/agent.py", "tests/checks.py"}  # only the agent's own files are sent
    # not ready / not set up: nothing to try, not a problem
    assert ca.try_problems({"skipped": "Not set up yet."}) == []
    assert designer  # (scripted through it)


@case("hardening: the launcher drops the app's credentials; each try gets its own empty network, or is not run")
def _():
    ns = {"__name__": "launch"}
    exec(compile(sandbox.LAUNCH_PY, "launch.py", "exec"), ns)
    env = ns["clean_env"]({"DATABRICKS_CLIENT_SECRET": "s", "DATABRICKS_CLIENT_ID": "i", "DATABRICKS_HOST": "h", "PATH": "/bin"})
    assert env == {"DATABRICKS_HOST": "h", "PATH": "/bin"}, env
    assert "python\n  - launch.py" in sandbox.APP_YAML  # started through the launcher, never straight into the server
    assert "unshare(0x10000000 | 0x40000000) != 0" in sandbox.SERVER_PY and "os._exit(97)" in sandbox.SERVER_PY
    assert "could not cut off the network, so nothing was run" in sandbox.SERVER_PY
    served = dict(sandbox.FILES)
    assert 'VERSION = "%s"' % sandbox.VERSION in served["server.py"] and "launch.py" in served


@case("status: none, ready, stopped, and someone else's app of that name is never used")
def _():
    import mcps
    from dbx import DbxError

    real = mcps.act
    apps = {}

    def act(method, path, tok, **kw):
        if method == "GET":
            if sandbox.NAME not in apps:
                raise DbxError("does not exist", 404)
            return apps[sandbox.NAME], "you"
        return {}, "you"
    mcps.act = act
    try:
        sandbox._setting_up["at"] = 0
        assert sandbox.status("T")["state"] == "none"
        apps[sandbox.NAME] = {"description": "Our own thing", "compute_status": {"state": "ACTIVE"}}
        assert sandbox.status("T")["state"] == "taken"
        try:
            sandbox.setup({"user_name": "d"}, "T")
            raise AssertionError("used someone else's app")
        except DbxError as exc:
            assert exc.status == 409
        apps[sandbox.NAME] = {"description": sandbox.MARK + " x", "compute_status": {"state": "STOPPED"}, "app_status": {"state": "UNAVAILABLE"}}
        assert sandbox.status("T")["state"] == "stopped"
        apps[sandbox.NAME] = {"description": sandbox.MARK + " x", "url": "https://sbx", "compute_status": {"state": "ACTIVE"},
                              "app_status": {"state": "RUNNING"}}
        sandbox._versions["https://sbx"] = (__import__("time").time(), "1")
        assert sandbox.status("T")["state"] == "outdated"  # an older sandbox is redeployed when set up
        sandbox._versions["https://sbx"] = (__import__("time").time(), sandbox.VERSION)
        assert sandbox.status("T")["state"] == "ready"
        apps[sandbox.NAME] = {"description": sandbox.MARK + " x", "compute_status": {"state": "STOPPED"}, "app_status": {"state": "UNAVAILABLE"}}
        assert sandbox.run({"agent/logic.py": "", "tests/checks.py": ""}, "T")["skipped"].startswith("Switched off")
        assert sandbox._entry()["user_api_scopes"] == []  # nobody's sign-in is forwarded to it
        # deleted by hand: Databricks lists it as DELETING for a while; it reads as "none" and is never recreated by itself
        apps[sandbox.NAME] = {"description": sandbox.MARK + " x", "compute_status": {"state": "DELETING"}, "app_status": {"state": "RUNNING"}}
        assert sandbox.status("T")["state"] == "none" and "removed" in sandbox.status("T")["note"]
    finally:
        mcps.act = real


@case("only Developer mode may see, set up or use the sandbox")
def _():
    from fastapi.testclient import TestClient

    import app as portal
    import designer_guard

    designer_guard.settings = lambda: dict(designer_guard.DEFAULTS)
    portal.user_token = lambda f: "T"
    portal.access.identity = lambda tok: {"user_name": "dev@example.com", "is_admin": True, "groups": ["admins"]}
    c = TestClient(portal.app, raise_server_exceptions=False)
    assert c.get("/api/admin/code-agents/sandbox").status_code == 403
    assert c.post("/api/admin/code-agents/sandbox").status_code == 403
    assert c.post("/api/admin/code-agents/ai/try", json={"setup": {}, "code": {}}).status_code == 403


if __name__ == "__main__":
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  " + name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            print("  FAIL  " + name + "\n        " + type(exc).__name__ + ": " + str(exc)[:500])
            traceback.print_exc(limit=4)
    print("--- %d passed, %d failed ---" % (len(CASES) - failed, failed))
    sys.exit(1 if failed else 0)
