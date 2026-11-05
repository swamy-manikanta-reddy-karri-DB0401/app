"""Offline checks of Developer mode's "Upload an agent's code" (code_upload.py): what a project ZIP may hold, that
keys never get in, that only apps uploaded here are replaced, that the app gets the portal's user scopes, and that
only Developer mode may upload."""
from __future__ import annotations

import io
import sys
import time
import zipfile

import code_upload as cu
from dbx import DbxError

cu.POLL = 0.05

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def zip_of(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for path, text in files.items():
            z.writestr(path, text)
    return buf.getvalue()


GOOD = {"my-agent/app.yaml": "command: [uvicorn, app:app]\n", "my-agent/app.py": "print('hi')\n",
        "my-agent/agent/config.json": "{}", "my-agent/.git/HEAD": "ref", "my-agent/agent/__pycache__/x.pyc": "x"}


def refused(files: dict, words: str):
    try:
        cu.read_project(zip_of(files))
    except DbxError as exc:
        assert exc.status == 400 and words in str(exc), str(exc)
        return
    raise AssertionError("not refused: %s" % words)


@case("a project: the one top folder is taken off, caches and .git are skipped")
def _():
    out = dict(cu.read_project(zip_of(GOOD)))
    assert set(out) == {"app.yaml", "app.py", "agent/config.json"}, out.keys()
    flat = dict(cu.read_project(zip_of({"app.yaml": "x", "app.py": "y"})))
    assert set(flat) == {"app.yaml", "app.py"}


@case("refused: not a ZIP, no app.yaml, unsafe paths, binaries, key files, keys inside code")
def _():
    try:
        cu.read_project(b"not a zip")
        raise AssertionError("accepted")
    except DbxError as exc:
        assert "not a ZIP" in str(exc)
    refused({"a/app.py": "x"}, "app.yaml")
    refused({"app.yaml": "x", "../evil.py": "x"}, "unsafe path")
    refused({"app.yaml": "x", "lib.so": "x"}, "source and text files")
    refused({"app.yaml": "x", ".env": "A=1"}, "looks like a key")
    refused({"app.yaml": "x", "keys/server.pem": "x"}, "looks like a key")
    fake = "dapi" + "0123456789abcdef" * 2  # built at run time so the repo holds no token-shaped text
    refused({"app.yaml": "x", "app.py": "TOKEN = '%s'" % fake}, "password or key")
    refused({"app.yaml": "x", "sa.json": '{"type": "service_account", "private_key": "%s"}' % ("k" * 40)}, "key")
    # ordinary code that says "token = ..." is fine (the generic pattern is left out on purpose)
    cu.read_project(zip_of({"app.yaml": "x", "app.py": "token = request.headers.get('x-forwarded-access-token')"}))


@case("refused: too many files, a file over 2 MB")
def _():
    refused({"app.yaml": "x", **{"f%d.py" % i: "x" for i in range(cu.MAX_FILES + 1)}}, "too big")
    refused({"app.yaml": "x", "big.txt": "x" * (cu.MAX_FILE + 1)}, "larger than")


class FakeWorkspace:
    def __init__(self, apps):
        self.apps, self.saved, self.installed = apps, [], []
        self.deployed = "2099-01-01T00:00:00Z"   # the new version is live

    def act(self, method, path, tok, **kw):
        name = path.rsplit("/", 1)[-1]
        if name in self.apps:
            return self.apps[name], "you"
        raise DbxError("not found", 404)


def with_fakes(apps):
    import app_agents
    import mcps

    fw = FakeWorkspace(apps)
    saved = (mcps.act, mcps.install, mcps._apps, app_agents.save, app_agents.share_with_portal)
    mcps.act = fw.act
    mcps.install = lambda entry, who, tok, files=None: fw.installed.append((entry, files)) or True
    mcps._apps = lambda tok: ({e["app_name"]: {"name": e["app_name"], "app_status": {"state": "RUNNING"},
                                               "compute_status": {"state": "ACTIVE"}, "active_deployment": {
                                               "status": {"state": "SUCCEEDED"}, "create_time": fw.deployed}}
                               for e, _ in fw.installed}, "you")
    app_agents.save = lambda name, values, who: fw.saved.append((name, values, who)) or {}
    app_agents.share_with_portal = lambda name, tok: ""

    def restore():
        mcps.act, mcps.install, mcps._apps, app_agents.save, app_agents.share_with_portal = saved
    return fw, restore


def wait(job, who):
    for _ in range(100):
        j = cu.job(job, who)
        if j["state"] != "deploying":
            return j
        time.sleep(0.05)
    raise AssertionError("still deploying")


@case("deploy: a new app gets the portal's user scopes and its own folder, then is offered as an assistant")
def _():
    fw, restore = with_fakes({})
    who = {"user_name": "dev@example.com"}
    try:
        out = cu.start(zip_of(GOOD), "Media Plan Builder", "Builds plans.", "", who, "T")
        assert out["app_name"] == "agent-media-plan-builder" and out["new"] and out["files"] == 3
        j = wait(out["job"], who)
        assert j["state"] == "done", j
        entry, files = fw.installed[0]
        assert entry["description"].startswith(cu.MARK) and entry["source_root"] == cu.ROOT
        assert "serving.serving-endpoints" in entry["user_api_scopes"], entry["user_api_scopes"]  # local: update-scopes.json
        assert dict(files)["app.yaml"].startswith(b"command")
        assert fw.saved[0][0] == "agent-media-plan-builder" and fw.saved[0][1]["path"] == "/invocations"
        try:
            cu.job(out["job"], {"user_name": "someone@example.com"})
            raise AssertionError("another person read the job")
        except DbxError as exc:
            assert exc.status == 404
    finally:
        restore()


@case("never replaces an app someone made by hand; replaces one uploaded here; checks the app name")
def _():
    fw, restore = with_fakes({"hand-made": {"name": "hand-made", "description": "Our app"},
                              "agent-mine": {"name": "agent-mine", "description": cu.MARK + " earlier"}})
    who = {"user_name": "dev@example.com"}
    try:
        try:
            cu.start(zip_of(GOOD), "Thing", "", "hand-made", who, "T")
            raise AssertionError("replaced a hand-made app")
        except DbxError as exc:
            assert exc.status == 409
        # a new version: not done while the app still serves the old deployment
        fw.deployed = "2000-01-01T00:00:00Z"
        out = cu.start(zip_of(GOOD), "Thing", "", "agent-mine", who, "T")
        assert out["new"] is False
        time.sleep(0.3)
        assert cu.job(out["job"], who)["state"] == "deploying"
        fw.deployed = "2099-01-01T00:00:00Z"
        assert wait(out["job"], who)["state"] == "done"
        for bad in ("Bad Name", "x", "1abc"):
            try:
                cu.start(zip_of(GOOD), "Thing", "", bad, who, "T")
                raise AssertionError("accepted " + bad)
            except DbxError as exc:
                assert exc.status == 400
    finally:
        restore()


@case("a deployment Databricks refused is a failure, even when the app runs (its old code)")
def _():
    import mcps
    fw, restore = with_fakes({"agent-mine": {"name": "agent-mine", "description": cu.MARK + " earlier"}})
    real_install = mcps.install

    def refused(entry, who, tok, files=None):
        fw.installed.append((entry, files))
        mcps._progress[entry["app_name"]] = {"phase": "failed", "message": "Cannot deploy: it has no requirements.txt", "at": time.time()}
        return True
    mcps.install = refused
    try:
        out = cu.start(zip_of(GOOD), "Thing", "", "agent-mine", {"user_name": "dev@example.com"}, "T")
        j = wait(out["job"], {"user_name": "dev@example.com"})
        assert j["state"] == "failed" and "no requirements.txt" in j["step"], j
    finally:
        mcps.install = real_install
        mcps._progress.pop("agent-mine", None)
        restore()


@case("a new agent asks for exactly the portal's own scopes, so the person's token can always be passed on")
def _():
    real = cu.portal_scopes
    try:
        cu.portal_scopes = lambda: ["sql", "model-serving", "genie"]
        assert cu._scopes() == ["sql", "model-serving", "genie"]
        assert cu.scope_gap({"user_api_scopes": ["sql", "files.files", "dashboards.genie"]}) == ["dashboards.genie", "files.files"]
        assert cu.scope_gap({"user_api_scopes": ["sql"]}) == []
        cu.portal_scopes = lambda: None  # outside Apps (local dev): the repo's list
        assert "serving.serving-endpoints" in cu._scopes() and cu.scope_gap({"user_api_scopes": ["x"]}) == []
    finally:
        cu.portal_scopes = real


@case("only Developer mode may upload or read a job")
def _():
    import designer_guard
    from fastapi.testclient import TestClient

    import app as portal

    portal.user_token = lambda f: "T"
    portal.access.identity = lambda tok: {"user_name": "dev@example.com", "is_admin": True, "groups": ["admins"]}
    c = TestClient(portal.app, raise_server_exceptions=False)
    saved = designer_guard.settings
    fw, restore = with_fakes({})
    try:
        designer_guard.settings = lambda: dict(designer_guard.DEFAULTS)
        form = {"display_name": "Media Plan Builder"}
        r = c.post("/api/admin/code-agents/upload", files={"file": ("p.zip", zip_of(GOOD))}, data=form)
        assert r.status_code == 403, r.text
        assert c.get("/api/admin/code-agents/jobs/x").status_code == 403
        designer_guard.settings = lambda: dict(designer_guard.DEFAULTS, developer_group="admins")
        r = c.post("/api/admin/code-agents/upload", files={"file": ("p.zip", zip_of(GOOD))}, data=form)
        assert r.status_code == 200, r.text
        assert wait(r.json()["job"], {"user_name": "dev@example.com"})["state"] == "done"
        r = c.post("/api/admin/code-agents/upload", files={"file": ("p.zip", b"nope")}, data=form)
        assert r.status_code == 400
    finally:
        designer_guard.settings = saved
        restore()


if __name__ == "__main__":
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  " + name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL  " + name + "\n        " + type(exc).__name__ + ": " + str(exc)[:300])
    print("--- %d passed, %d failed ---" % (len(CASES) - failed, failed))
    sys.exit(1 if failed else 0)
