"""Developer mode's try-out sandbox: the AI's code is tried in seconds while it is written, without ever running in
the portal and without reaching anything real.

Why it exists: the code writer (code_agent.py) never runs AI-written code inside the portal (that process holds the
portal's identity, people's tokens in flight and the secret scope, and Python cannot fence code off inside one
process; Mia runs its coding AI with `sandbox: "danger-full-access"` for that reason). Without somewhere to run it,
every try was a deployment (about 40 s for a new version, 143 s for a new app, measured 2026-11-04), and mistakes in
the logic (a crash on "25,000", 30 instead of 0.30, merged spreadsheet cells) were found only after the developer
had read and approved the code.

What it is: one Databricks App, `agent-portal-sandbox`, whose code is fixed and written here (never by AI):
- **Its identity is granted nothing**: no tables, folders, secrets or apps, and it is created with no user API
  scopes, so no one's sign-in is forwarded to it. Even code that broke out of everything below would find nothing to
  use.
- **Each try runs in its own process** started with a clean environment (no Databricks variables), inside its own
  empty **network namespace** (`unshare(CLONE_NEWUSER | CLONE_NEWNET)` before the code starts: the kernel has no
  network for it; if that is ever refused, the try is not run at all), with a time limit (CPU and wall clock),
  memory, file-size and open-file limits, and its own temporary folder (deleted after). The portal's static checks
  (no open/eval/getattr/subprocess/ctypes/...) run before anything is sent, as another fence.
- **The app's own credentials are gone before the server starts**: `launch.py` drops DATABRICKS_CLIENT_ID/SECRET and
  replaces itself with the server (`execve`), so no process a try could look at holds them.
- Learnt live (2026-11-04, adversarial probes sent straight to the sandbox, bypassing the static checks): the first
  version only replaced `socket` inside the try, and a raw `_socket` connect still reached the internet; and the
  server, started by Databricks with DATABRICKS_CLIENT_SECRET in its environment, had it readable at
  /proc/<parent>/environ (tries run as uid 65534, the same as the server). Both are closed as above; a never-ending
  loop (stopped at 10 s) and a 3 GB allocation (MemoryError) were already stopped. The app's identity has no groups
  and only Databricks' default entitlements (workspace-access, databricks-sql-access), with nothing granted to it.
- **Tools are stand-ins**: `agent/tools.py` in the sandbox has the same functions as the real one, but each answers
  only what the check says it should (`tools.fake("read_file", workbook_bytes)`); anything not faked says so. So a
  check builds its own small sample (an ad book with merged cells) and tries the logic on it.
- The AI writes the checks too (`tests/checks.py`, `check_*` functions with plain asserts), shipped with the project
  so the developer owns them. A failed check goes straight back to the AI as a problem, like a failed static check.

What it is not: a test against real data. The tests after deployment (the developer's token, the real tools and
files) still decide whether it works. It costs one of the workspace's 100 apps and its compute while running, so it is
set up only when a developer asks, and the portal stops it after IDLE_STOP of no use.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import mcps
from dbx import DbxError, app_token, call, http

log = logging.getLogger("portal.sandbox")

NAME = "agent-portal-sandbox"
MARK = "Portal sandbox:"
IDLE_STOP = 30 * 60
RUN_TIMEOUT = 90
_last_used = {"at": 0.0, "tok": ""}
_setting_up = {"at": 0.0}

SERVER_PY = r'''"""The portal's try-out sandbox (sandbox.py in the portal wrote this file; AI never does).

POST /run {files: {"agent/logic.py": ..., "tests/checks.py": ...}} runs every check_* in tests/checks.py against the
agent's own code, each try in its own process: a clean environment, time, memory, file and process limits, its own
temporary folder (deleted after), no network, and stand-in tools. This app's identity is granted nothing.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from fastapi import Body, FastAPI, HTTPException

HERE = os.path.dirname(os.path.abspath(__file__))
PATH_RE = re.compile(r"^(agent/[a-z][a-z0-9_]{0,40}\.py|tests/checks\.py)$")
FIXED = {"agent/tools.py", "agent/__init__.py"}
MAX_BYTES = 600_000
WALL = 40
SLOTS = threading.Semaphore(3)
app = FastAPI(title="Portal sandbox")


def _limits():
    import ctypes
    import resource

    # Its own empty network namespace: no network at all for the try (a raw socket gets "Network is unreachable").
    # Fail closed: a try is never run with the network there.
    if ctypes.CDLL(None, use_errno=True).unshare(0x10000000 | 0x40000000) != 0:
        os._exit(97)
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024 ** 2, 20 * 1024 ** 2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    os.setsid()


VERSION = "@@VERSION@@"


@app.get("/")
def health():
    return {"ok": True, "sandbox": True, "version": VERSION, "credentials": "DATABRICKS_CLIENT_SECRET" in os.environ}


@app.post("/run")
def run(body: dict = Body(...)):
    files = body.get("files") or {}
    if not isinstance(files, dict) or not files:
        raise HTTPException(400, "No files.")
    total = 0
    for path, text in files.items():
        if not isinstance(path, str) or not PATH_RE.match(path) or path in FIXED or not isinstance(text, str):
            raise HTTPException(400, "Only agent/*.py and tests/checks.py can be tried: %s" % str(path)[:80])
        total += len(text)
    if total > MAX_BYTES or "tests/checks.py" not in files:
        raise HTTPException(400, "Send tests/checks.py, and less than %d characters." % MAX_BYTES)
    if not SLOTS.acquire(timeout=30):
        raise HTTPException(429, "The sandbox is busy. Try again in a moment.")
    d = tempfile.mkdtemp(prefix="try-")
    t0 = time.time()
    try:
        os.makedirs(os.path.join(d, "agent"))
        os.makedirs(os.path.join(d, "tests"))
        for name in ("agent/__init__.py", "tests/__init__.py"):
            open(os.path.join(d, name), "w").close()
        shutil.copy(os.path.join(HERE, "fake_tools.py"), os.path.join(d, "agent", "tools.py"))
        for path, text in files.items():
            with open(os.path.join(d, path), "w", encoding="utf-8") as f:
                f.write(text)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": d, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
               "LANG": "C.UTF-8"}
        try:
            p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "runner.py"), d], cwd=d, env=env, capture_output=True,
                               text=True, timeout=WALL, preexec_fn=_limits)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "It ran for more than %d seconds and was stopped (a loop that never ends?)." % WALL,
                    "results": [], "seconds": round(time.time() - t0, 1)}
        if p.returncode == 97:
            return {"ok": False, "error": "The sandbox could not cut off the network, so nothing was run.", "results": [],
                    "seconds": round(time.time() - t0, 1)}
        out = p.stdout.rsplit("@@RESULT@@", 1)
        if len(out) < 2:
            tail = (p.stderr or p.stdout or "")[-1500:]
            why = "It used too much memory or time and was stopped." if p.returncode in (-9, -24, 137, 152) else "It stopped before reporting."
            return {"ok": False, "error": why + " " + tail, "results": [], "seconds": round(time.time() - t0, 1)}
        res = json.loads(out[1])
        res["seconds"] = round(time.time() - t0, 1)
        return res
    finally:
        shutil.rmtree(d, ignore_errors=True)
        SLOTS.release()
'''

RUNNER_PY = r'''"""Runs every check_* in tests/checks.py, in this process only, with the network off. Prints one result line."""
import io
import json
import os
import signal
import socket
import sys
import time
import traceback
from contextlib import redirect_stdout

ROOT = sys.argv[1]
sys.path.insert(0, ROOT)


def _no_network(*a, **k):
    raise OSError("The sandbox has no network. Websites and tool apps are tried after it is deployed.")


class _NoSocket(socket.socket):
    def __init__(self, *a, **k):
        _no_network()


socket.socket = _NoSocket
socket.create_connection = _no_network
socket.getaddrinfo = _no_network


def _where(tb) -> str:
    """The last line of the agent's own code (or the check) the error went through."""
    for fr in reversed(traceback.extract_tb(tb)):
        rel = os.path.relpath(fr.filename, ROOT).replace(os.sep, "/")
        if rel.startswith(("agent/", "tests/")) and rel != "agent/tools.py":
            return "%s line %d (%s)" % (rel, fr.lineno, (fr.line or "").strip()[:140])
    return ""


def _timeout(*a):
    raise TimeoutError("This check took more than 10 seconds.")


out = {"ok": True, "results": [], "error": "", "missing": []}
try:
    from tests import checks
    from agent import tools
except ModuleNotFoundError as exc:
    out.update(ok=False, error="It needs %s, which the sandbox does not have." % exc.name, missing=[exc.name])
    checks = None
except BaseException as exc:  # noqa: BLE001 - reported, never raised
    out.update(ok=False, error="The code could not be loaded: %s: %s %s" % (type(exc).__name__, str(exc)[:300], _where(exc.__traceback__)))
    checks = None
if checks is not None:
    names = sorted(n for n in dir(checks) if n.startswith("check_") and callable(getattr(checks, n)))
    if not names:
        out.update(ok=False, error="tests/checks.py has no check_ functions.")
    for name in names[:40]:
        tools.FAKES.clear()
        tools.SAVED.clear()
        t0 = time.time()
        printed = io.StringIO()
        r = {"name": name, "ok": True, "error": "", "where": "", "printed": ""}
        try:
            if hasattr(signal, "alarm"):
                signal.signal(signal.SIGALRM, _timeout)
                signal.alarm(10)
            with redirect_stdout(printed):
                getattr(checks, name)()
        except ModuleNotFoundError as exc:
            r.update(ok=None, error="Not tried: it needs %s, which the sandbox does not have." % exc.name)
        except AssertionError as exc:
            r.update(ok=False, error="Check failed: " + (str(exc)[:500] or "assert was false"), where=_where(exc.__traceback__))
        except BaseException as exc:  # noqa: BLE001
            r.update(ok=False, error="%s: %s" % (type(exc).__name__, str(exc)[:500]), where=_where(exc.__traceback__))
        finally:
            if hasattr(signal, "alarm"):
                signal.alarm(0)
        r["printed"] = printed.getvalue()[-800:]
        r["seconds"] = round(time.time() - t0, 2)
        out["results"].append(r)
        if r["ok"] is False:
            out["ok"] = False
print("@@RESULT@@" + json.dumps(out, default=str))
'''

FAKE_TOOLS_PY = r'''"""agent/tools.py inside the try-out sandbox: the same functions as the real one, but nothing real is reachable.
A check says what a tool answers: tools.fake("read_file", workbook_bytes) or tools.fake("ask_data", lambda space, q: {...}).
What save_file was given is kept in tools.SAVED, so a check can open it.
"""
import json

CONFIG = {"name": "sandbox", "description": "", "model": "", "instructions": "", "runbook": "", "rules": [], "documents": [],
          "tools": [], "tool_urls": {}, "data": [], "assistants": [], "limits": {"steps": 10, "reply_tokens": 4000},
          "write_folders": ["/Volumes/sandbox/try/out"], "websites": []}
FAKES = {}
SAVED = {}


def fake(name, answer):
    """In a check: what a tool returns here (a value, or a function given the same inputs)."""
    FAKES[name] = answer


def _ask(name, *args):
    if name not in FAKES:
        raise RuntimeError("tools.%s cannot reach anything in the sandbox. In the check, say what it returns: "
                           "tools.fake(%r, ...)" % (name, name))
    a = FAKES[name]
    return a(*args) if callable(a) else a


def search_documents(query):
    return _ask("search_documents", query)


def ask_data(space_id, question):
    return _ask("ask_data", space_id, question)


def call_mcp(app, name, args, read_only):
    return _ask("call_mcp", app, name, args, read_only)


def ask_assistant(endpoint, question):
    return _ask("ask_assistant", endpoint, question)


def read_file(path):
    return _ask("read_file", path)


def save_file(path, data):
    if "save_file" in FAKES:
        return _ask("save_file", path, data)
    folders = [f.rstrip("/") + "/" for f in CONFIG.get("write_folders") or []]
    if ".." in path.split("/") or not any(path.startswith(f) for f in folders):
        raise PermissionError("This agent may only save into: %s" % (", ".join(folders) or "no folder"))
    if path in SAVED:
        raise FileExistsError("%s already exists; pick a new name." % path)
    SAVED[path] = data
    return path


def mcp_tools():
    return []


def mcp_tools_of(app):
    return ()


def client():
    raise RuntimeError("There is no Databricks connection in the sandbox.")


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""
'''

VERSION = "2"   # raise when SERVER_PY, RUNNER_PY, FAKE_TOOLS_PY or LAUNCH_PY change: the portal then redeploys it

LAUNCH_PY = r'''"""Starts the sandbox server without the app's own credentials (sandbox.py in the portal wrote this file).

Databricks starts an app with DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET in its environment. The sandbox never
calls Databricks, so they are dropped and this process replaces itself with the server (execve): no process a try
could look at (/proc/<pid>/environ) holds them.
"""
import os
import sys

DROP = ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_TOKEN")


def clean_env(environ):
    return {k: v for k, v in environ.items() if k not in DROP}


if __name__ == "__main__":
    port = os.environ.get("DATABRICKS_APP_PORT") or "8000"
    os.execve(sys.executable, [sys.executable, "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", port],
              clean_env(os.environ))
'''

APP_YAML = "command:\n  - python\n  - launch.py\n"
# What tried code may import (as in the agent's own project, without network libraries).
REQUIREMENTS = "fastapi==0.142.2\nuvicorn==0.54.0\nopenpyxl==3.1.5\npyyaml==6.0.3\n"
FILES = [("server.py", SERVER_PY.replace("@@VERSION@@", VERSION)), ("runner.py", RUNNER_PY), ("fake_tools.py", FAKE_TOOLS_PY),
         ("launch.py", LAUNCH_PY), ("app.yaml", APP_YAML), ("requirements.txt", REQUIREMENTS)]


def _entry() -> dict:
    return {"slug": NAME, "app_name": NAME, "name": "Try-out sandbox", "problem": "", "needs": {"secrets": [], "volumes": []},
            "description": MARK + " runs the code writer's checks in seconds, with nothing real reachable. Code by the portal.",
            "source_root": "/Workspace/Shared/agent-portal-agents", "user_api_scopes": []}


def _app(tok: str) -> dict | None:
    try:
        app, _ = mcps.act("GET", "/api/2.0/apps/" + NAME, tok, quiet=True)
        return app
    except DbxError as exc:
        if exc.status == 404 or "does not exist" in str(exc).lower():
            return None
        raise


def status(tok: str) -> dict:
    """{state: none | setting_up | starting | ready | outdated | stopped | failed | taken, note, url}."""
    try:
        app = _app(tok)
    except DbxError as exc:
        return {"state": "failed", "note": "The sandbox could not be checked: " + str(exc)[:160], "url": ""}
    if app is None:
        if time.time() - _setting_up["at"] < 15 * 60:
            return {"state": "setting_up", "note": "Setting it up (a few minutes, once).", "url": ""}
        return {"state": "none", "note": "Not set up yet. It uses 1 of the workspace's 100 apps.", "url": ""}
    if not str(app.get("description") or "").startswith(MARK):
        return {"state": "taken", "note": "An app called %s exists that the portal did not make, so it is not used." % NAME, "url": ""}
    state, line = mcps._state(app, mcps._progress.get(NAME))
    url = app.get("url") or ""
    if state == "not_deployed" and time.time() - _setting_up["at"] >= 15 * 60:
        # Deleted (Databricks lists a deleted app as DELETING for a few minutes). It is never set up again by itself:
        # a developer chooses to, with the button.
        return {"state": "none", "note": "It was removed. Set it up again when you want it (it uses 1 of the workspace's 100 apps).", "url": ""}
    if state == "running":
        if time.time() - _setting_up["at"] < 15 * 60 and _setting_up.get("updating"):
            return {"state": "starting", "note": "Updating it.", "url": url}
        if _version(url, tok) != VERSION:
            return {"state": "outdated", "note": "A newer sandbox comes with this portal; it is updated when set up.", "url": url}
        return {"state": "ready", "note": "Ready: checks run in seconds.", "url": url}
    if state == "stopped":
        return {"state": "stopped", "note": "Switched off to save cost. It starts in about a minute.", "url": url}
    if state == "failed":
        return {"state": "failed", "note": line or "It did not start.", "url": url}
    return {"state": "starting", "note": line or "Starting.", "url": url}


_versions: dict = {}


def _version(url: str, tok: str) -> str:
    """The sandbox's version (its health answer), read at most once a minute."""
    hit = _versions.get(url)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    v = ""
    try:
        r = http().get(url.rstrip("/") + "/", headers={"Authorization": "Bearer " + tok}, timeout=15, follow_redirects=False)
        if r.status_code == 200:
            v = str((r.json() or {}).get("version") or "1")
    except Exception:  # noqa: BLE001 - unknown is treated as current, so a hiccup never redeploys it
        v = VERSION
    _versions[url] = (time.time(), v or VERSION)
    return v or VERSION


def setup(who: dict, tok: str) -> dict:
    """Create it (once) or switch it on. The app is created with no user API scopes and nothing is granted to its
    identity; every admin may use it (developers are admins)."""
    st = status(tok)
    if st["state"] == "taken":
        raise DbxError(st["note"], 409)
    if st["state"] in ("ready", "starting", "setting_up"):
        return st
    if st["state"] == "stopped":
        mcps.act("POST", "/api/2.0/apps/%s/start" % NAME, tok)
        return status(tok)
    _setting_up["at"] = time.time()
    _setting_up["updating"] = st["state"] == "outdated"
    _versions.clear()

    def run():
        try:
            mcps.install(_entry(), who, tok, files=[(p, t.encode("utf-8")) for p, t in FILES])
            try:
                mcps.act("PATCH", "/api/2.0/permissions/apps/" + NAME, tok,
                         json={"access_control_list": [{"group_name": "admins", "permission_level": "CAN_USE"}]})
            except DbxError as exc:
                log.warning("sandbox: could not let admins use it: %s", str(exc)[:160])
        except Exception as exc:  # noqa: BLE001 - shown as the status
            log.warning("sandbox setup failed: %s", str(exc)[:300])
        finally:
            _setting_up["at"] = 0.0 if _setting_up.get("updating") else time.time()
            _setting_up["updating"] = False
            _versions.clear()
    threading.Thread(target=run, name="sandbox-setup", daemon=True).start()
    return {"state": "setting_up", "note": "Setting it up (a few minutes, once).", "url": ""}


def run(files: dict, tok: str) -> dict:
    """Try the agent's own code: every check_* in tests/checks.py. {ok, results, error, seconds} or {skipped: why}."""
    st = status(tok)
    if st["state"] != "ready":
        return {"skipped": st["note"]}
    _last_used.update(at=time.time(), tok=tok)
    _watch()
    send = {p: t for p, t in files.items() if p.startswith("agent/") and p.endswith(".py") and p not in ("agent/tools.py", "agent/__init__.py")}
    if "tests/checks.py" in files:
        send["tests/checks.py"] = files["tests/checks.py"]
    last = None
    for who, t in (("you", tok), ("the portal", None)):
        try:
            t = t or app_token()
            r = http().post(st["url"].rstrip("/") + "/run", headers={"Authorization": "Bearer " + t}, json={"files": send},
                            timeout=RUN_TIMEOUT, follow_redirects=False)
        except Exception as exc:  # noqa: BLE001
            last = "The sandbox could not be reached: %s" % str(exc)[:160]
            continue
        if r.status_code in (301, 302, 303, 307, 401, 403):
            last = "The sandbox refused the sign-in (%s, as %s)." % (r.status_code, who)
            continue
        if r.status_code >= 400:
            return {"skipped": "The sandbox answered %s: %s" % (r.status_code, r.text[:200])}
        try:
            return r.json()
        except ValueError:
            return {"skipped": "The sandbox answered in a way the portal could not read."}
    return {"skipped": last or "The sandbox could not be reached."}


_watching = {"on": False}


def _watch() -> None:
    """Stop the sandbox after IDLE_STOP without a try (its compute costs while it runs)."""
    if _watching["on"]:
        return
    _watching["on"] = True

    def loop():
        while True:
            time.sleep(300)
            if _last_used["at"] and time.time() - _last_used["at"] > IDLE_STOP:
                try:
                    # as the developer who used it last (they may manage it; the portal's identity may not)
                    mcps.act("POST", "/api/2.0/apps/%s/stop" % NAME, _last_used["tok"], quiet=True)
                    log.info("sandbox stopped after %d minutes without use", IDLE_STOP // 60)
                except DbxError as exc:
                    log.info("sandbox could not be stopped: %s", str(exc)[:120])
                _last_used["at"] = 0.0
    threading.Thread(target=loop, name="sandbox-idle", daemon=True).start()
