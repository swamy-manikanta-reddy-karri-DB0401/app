"""Offline checks of Developer mode's "Write an agent's code with AI" (code_agent.py): the project it writes into
passes its own checks, what the checks refuse, the write-and-fix loop with a scripted model, fixed files that
cannot be changed or swapped, approval by fingerprint, the agent's own abilities really registering, and
Developer-only routes."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import types
import zipfile

import code_agent as ca
import designer
import designer_guard
from dbx import DbxError

ca.BACKGROUND = False
designer_guard.settings = lambda: dict(designer_guard.DEFAULTS)
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


SETUP = ca.clean_setup({"name": "Plan Maker", "framework": "mlflow", "model": "databricks-claude-sonnet-5-5",
                        "task": "Works out the margin of a media plan from its net cost and hard cost.",
                        "write_folders": ["/Volumes/c/s/out/plans"]})

LOGIC_OK = '''"""Margins."""
try:
    from agent import tools  # noqa: F401
except ImportError:
    import tools  # noqa: F401

INSTRUCTIONS = "Ask for the net and hard cost if they are missing."


def margin(net: float, hard_cost: float = 0.0) -> dict:
    """Use when someone asks for a plan's margin. net: the plan's net cost; hard_cost: its hard cost."""
    if net <= 0:
        raise ValueError("The net cost must be more than 0.")
    return {"margin": round((net - hard_cost) / net, 4)}


def _helper(x: int) -> int:
    return x
'''


def reply(files: dict, tests=None, summary="Works out margins.") -> str:
    out = "".join("=== FILE %s ===\n%s\n=== END FILE ===\n" % (p, t) for p, t in files.items())
    out += "=== TESTS ===\n%s\n=== SUMMARY ===\n%s\n" % (json.dumps(tests or [{"question": "Margin for 80000 net, 5000 hard cost?",
                                                                               "expect": "Says 93.75%"}]), summary)
    return out


def scripted(*replies):
    seen = []
    it = iter(replies)

    def ask(role, use, notes, messages, tools, tok, max_tokens=3000):
        seen.append(messages)
        return {"content": next(it)}
    designer._ask = ask
    return seen


USE = {"chat": "m-chat", "code": "m-code", "judge": "m-judge"}


@case("the project the AI writes into passes its own checks, in both styles")
def _():
    for fw in ("mlflow", "langgraph"):
        setup = dict(SETUP, framework=fw)
        r = ca.check(setup, ca.base(setup, "T", urls={}))
        assert r["problems"] == [], (fw, r["problems"])
        assert r["report"]["files"] == ["agent/agent.py", "agent/logic.py"]


@case("refused: eval, os.system, subprocess, getattr, dunders, async or untyped abilities, undeclared websites, keys")
def _():
    base = ca.base(SETUP, "T", urls={})

    def problems(logic):
        return " | ".join(ca.check(SETUP, {**base, "agent/logic.py": logic})["problems"])
    assert "eval() is not allowed" in problems("x = eval('1')\n")
    assert ".system is not allowed" in problems("import os\nos.system('ls')\n")
    assert "only os.environ" in problems("import os\nP = os.path.join('a', 'b')\n")
    assert "subprocess may not be imported" in problems("import subprocess\n")
    assert "getattr() is not allowed" in problems("def f(a: str) -> str:\n    '''Use it.'''\n    return getattr(a, 'x')\n")
    assert ".__class__ is not allowed" in problems("y = ().__class__\n")
    assert "plain (not async)" in problems("async def f(a: str) -> str:\n    '''Use it.'''\n    return a\n")
    assert "needs a docstring" in problems("def f(a: str) -> str:\n    return a\n")
    assert "needs a type" in problems("def f(a) -> str:\n    '''Use it.'''\n    return a\n")
    assert "api.evil.com" in problems("URL = 'https://api.evil.com/x'\n")
    assert "httpx" in problems("import httpx\n")
    fake = "dapi" + "0123456789abcdef" * 2
    assert "looks like a password" in problems("K = '%s'\n" % fake)
    assert "is not valid Python" in problems("def (:\n")
    assert "may be written" in " ".join(ca.check(SETUP, {**base, "secrets.txt": "x"})["problems"])
    # declared websites are fine, and the admins' allowed list still applies
    web = dict(SETUP, websites=["api.example.com"])
    assert ca.check(web, {**base, "agent/logic.py": "import httpx\nU = 'https://api.example.com/v1'\n"})["problems"] == []
    saved = designer_guard.settings
    designer_guard.settings = lambda: dict(designer_guard.DEFAULTS, allowed_hosts=["*.ourco.com"], allow_changes_data=False)
    try:
        p = " | ".join(ca.check(web, base)["problems"])
        assert "only allow these websites" in p and "may not save files" in p, p
    finally:
        designer_guard.settings = saved


@case("write: whole file first, then edits for what the checks found; tests and summary are read")
def _():
    bad = LOGIC_OK.replace("    return {\"margin\"", "    eval('1')\n    return {\"margin\"")
    seen = scripted(reply({"agent/logic.py": bad}),
                    "=== EDIT agent/logic.py ===\n<<<<<<< SEARCH\n    eval('1')\n=======\n    pass\n>>>>>>> REPLACE\n=== END EDIT ===\n")
    out = ca.write(SETUP, "T", dict(USE), [], urls={})
    assert out["problems"] == [], out["problems"]
    assert "eval" not in out["code"]["agent/logic.py"] and out["tests"][0]["expect"] == "Says 93.75%"
    assert out["report"]["abilities"] == [{"name": "margin", "about": "Use when someone asks for a plan's margin. net: the plan's net cost; hard_cost: its hard cost.", "inputs": ["net", "hard_cost"]}]
    assert "eval() is not allowed" in seen[1][-1]["content"]          # the model was told exactly what failed
    first = json.loads(seen[0][1]["content"])["project"]
    assert "tools.save_file" in first["tools_py_offers"] and first["what_it_can_use"]["write_folders"] == ["/Volumes/c/s/out/plans"]
    assert "agent/tools.py" not in first["files_you_may_write"]       # the model only sees what it may write


@case("fixed files: a change by the model is put back and it is told; the browser can never send them")
def _():
    base = ca.base(SETUP, "T", urls={})
    seen = scripted(reply({"agent/logic.py": LOGIC_OK, "agent/tools.py": "# mine now\n"}),
                    "=== SUMMARY ===\nok")
    out = ca.write(SETUP, "T", dict(USE), [], urls={})
    assert "agent/tools.py" not in out["code"] and "is fixed and was put back" in seen[1][-1]["content"]
    files = ca.assemble(SETUP, {**out["code"], "agent/tools.py": "# swapped\n", "app.py": "# swapped\n"}, "T", urls={})
    assert files["agent/tools.py"] == base["agent/tools.py"] and files["app.py"] == base["app.py"]


@case("approval: deploy refuses code that changed after approval, and sends exactly the approved project")
def _():
    import code_upload
    code = {"agent/logic.py": LOGIC_OK, "agent/agent.py": ca.base(SETUP, "T", urls={})["agent/agent.py"]}
    fp = ca.fingerprint(SETUP, code)
    sent = []
    saved = (code_upload.start, ca.base)
    code_upload.start = lambda blob, name, desc, app, who, tok: sent.append((blob, name, app)) or {"job": "j", "app_name": "agent-plan-maker", "new": True, "files": 9}
    ca.base = lambda setup, tok, urls=None: saved[1](setup, tok, urls={})
    try:
        try:
            ca.deploy(SETUP, {**code, "agent/logic.py": LOGIC_OK + "\nX = 1\n"}, [], fp, "", "", {"user_name": "d"}, "T")
            raise AssertionError("deployed unapproved code")
        except DbxError as exc:
            assert exc.status == 409
        # editing a test does not need a new approval
        ca.deploy(SETUP, code, [{"question": "Margin for 100 net?", "expect": "Says 100%"}], fp, "Plan Maker", "", {"user_name": "d"}, "T")
        z = zipfile.ZipFile(io.BytesIO(sent[0][0]))
        names = {n.split("/", 1)[1]: n for n in z.namelist()}
        assert z.read(names["agent/logic.py"]).decode() == LOGIC_OK
        assert json.loads(z.read(names["tests/conversations.json"]))[0]["expect"] == "Says 100%"
        assert "openai==" in z.read(names["requirements.txt"]).decode()
    finally:
        code_upload.start, ca.base = saved


@case("the agent's own abilities really register: typed, documented public functions, with INSTRUCTIONS")
def _():
    files = ca.assemble(SETUP, {"agent/logic.py": LOGIC_OK}, "T", urls={})
    sdk = types.ModuleType("databricks.sdk")
    sdk.WorkspaceClient = object
    sys.modules.setdefault("databricks", types.ModuleType("databricks"))
    sys.modules["databricks.sdk"] = sdk
    with tempfile.TemporaryDirectory() as d:
        for path, text in files.items():
            os.makedirs(os.path.join(d, os.path.dirname(path)), exist_ok=True)
            with open(os.path.join(d, path), "w", encoding="utf-8") as fh:
                fh.write(text)
        sys.path.insert(0, d)
        try:
            from agent import tools
            ab = {a["name"]: a for a in tools.logic_abilities()}
            assert set(ab) == {"margin"}, ab.keys()  # helpers (_helper) and imports are not abilities
            assert ab["margin"]["schema"] == {"type": "object", "properties": {"net": {"type": "number"}, "hard_cost": {"type": "number"}}, "required": ["net"]}
            assert json.loads(ab["margin"]["run"]({"net": 80000, "hard_cost": 5000, "junk": 1})) == {"margin": 0.9375}
            assert "Ask for the net and hard cost" in tools.system_prompt()
            try:
                tools.save_file("/Volumes/c/s/elsewhere/x.xlsx", b"x")
                raise AssertionError("saved outside the declared folder")
            except PermissionError:
                pass
        finally:
            sys.path.remove(d)
            for m in [m for m in sys.modules if m == "agent" or m.startswith("agent.")]:
                sys.modules.pop(m)


@case("setup is checked; only Developer mode may write, change, deploy, test or download")
def _():
    for bad in ({"framework": "mlflow", "task": "x" * 30}, {"name": "A b", "task": "short"},
                {"name": "A b", "task": "x" * 30, "write_folders": ["/tmp/x"]}, {"name": "A b", "task": "x" * 30, "framework": "java"}):
        try:
            ca.clean_setup(bad)
            raise AssertionError("accepted %s" % bad)
        except DbxError as exc:
            assert exc.status == 400
    from fastapi.testclient import TestClient

    import app as portal
    portal.user_token = lambda f: "T"
    portal.access.identity = lambda tok: {"user_name": "dev@example.com", "is_admin": True, "groups": ["admins"]}
    c = TestClient(portal.app, raise_server_exceptions=False)
    body = {"setup": {"name": "Plan Maker", "model": "", "source": "", "task": "Works out the margin of a media plan."}}
    for path in ("write", "change", "deploy", "test", "zip"):
        assert c.post("/api/admin/code-agents/ai/" + path, json=body).status_code == 403, path
    assert c.get("/api/admin/code-agents/ai/jobs/x").status_code == 403


if __name__ == "__main__":
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  " + name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            print("  FAIL  " + name + "\n        " + type(exc).__name__ + ": " + str(exc)[:400])
            traceback.print_exc(limit=3)
    print("--- %d passed, %d failed ---" % (len(CASES) - failed, failed))
    sys.exit(1 if failed else 0)
