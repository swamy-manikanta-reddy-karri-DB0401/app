"""Offline tests for the Portal Deployer. No workspace, no GitHub: every call
is faked. Run: python deployer/test_deployer.py
"""
from __future__ import annotations

import base64
import shutil
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clients  # noqa: E402
import common  # noqa: E402
import deploy  # noqa: E402
import ghub  # noqa: E402
import registry  # noqa: E402
import workspace as ws  # noqa: E402
from common import DeployError  # noqa: E402

CASES = []
common.time.sleep = lambda s: None  # every wait loop runs instantly


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def raises(fn, *a, contains="", **kw):
    try:
        fn(*a, **kw)
    except DeployError as exc:
        assert contains.lower() in str(exc).lower(), str(exc)
        return exc
    raise AssertionError("expected DeployError")


class FakeApi:
    """A workspace: apps, deployments, permissions and SQL, with scripted failures."""

    def __init__(self, live: str = "", exists: bool = True, fail_deploy=None):
        self.host, self.token = "https://x", "TOKEN"
        self.calls = []
        self.app = None
        if exists:
            self.app = {"name": "agent-portal", "url": "https://agent-portal-1.apps", "service_principal_client_id": "sp-1",
                        "compute_status": {"state": "ACTIVE"}, "app_status": {"state": "RUNNING"},
                        "effective_user_api_scopes": ["a"],
                        "active_deployment": {"source_code_path": ws.release_path("cid", "agent-portal", live) if live else "",
                                              "status": {"state": "SUCCEEDED"}}}
        self.fail_deploy = fail_deploy or set()  # versions whose deployment fails
        self.fail = {}

    def call(self, method, path, *, json=None, params=None, quiet_404=False):
        self.calls.append((method, path, json))
        for (m, frag), exc in self.fail.items():
            if m == method and frag in path:
                raise exc
        if path == "/api/2.0/apps/agent-portal" and method == "GET":
            return self.app
        if path == "/api/2.0/apps" and method == "POST":
            self.app = {"name": json["name"], "url": "https://new.apps", "service_principal_client_id": "sp-2",
                        "compute_status": {"state": "ACTIVE"}, "app_status": {"state": "RUNNING"},
                        "effective_user_api_scopes": json["user_api_scopes"], "active_deployment": {}}
            return self.app
        if path.endswith("/deployments") and method == "POST":
            v = ws.version_of(json["source_code_path"])
            if v in self.fail_deploy:
                return {"deployment_id": "d-" + v, "status": {"state": "FAILED", "message": "boom in " + v}}
            self.app["active_deployment"] = {"source_code_path": json["source_code_path"], "status": {"state": "SUCCEEDED"}}
            return {"deployment_id": "d-" + v, "status": {"state": "SUCCEEDED"}}
        if "/deployments/" in path:
            v = path.rsplit("d-", 1)[-1]
            return {"status": {"state": "FAILED" if v in self.fail_deploy else "SUCCEEDED", "message": "boom in " + v}}
        if path == "/api/2.0/sql/warehouses":
            return {"warehouses": [{"id": "0123456789abcdef", "state": "RUNNING", "name": "w"}]}
        if path == "/api/2.0/sql/statements":
            return {"statement_id": "s1", "status": {"state": "SUCCEEDED"}}
        return {}

    def made(self, method, frag=""):
        return [c for c in self.calls if c[0] == method and frag in c[1]]


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes  # path -> (status, json)

    def get(self, url, **kw):
        for path, (status, body) in self.routes.items():
            if url.endswith(path):
                return _Resp(status, body)
        return _Resp(404, {})


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def healthy_http(version):
    return FakeHttp({"/api/health": (200, {"ok": True, "version": version}),
                     "/api/admin/agent-health?days=1": (200, {"questions": 10, "failed": 1, "kinds": [], "agents": []}),
                     "/api/admin/logs?days=1&limit=50": (200, {"lines": [{"at": "t", "message": "x"}]})})


CFG = {"client": "client-acme", "version": "v1.1.0", "action": "deploy", "deploy_id": "11111111-1111-1111-1111-111111111111",
       "actor": "me@x.io", "run_url": "https://gh/run/1", "host": "https://x", "client_id": "cid", "secret": "s",
       "app": "agent-portal", "log_table": "main.portal.portal_logs", "warehouse": "", "users_group": "users"}


def run_deploy(api, cfg=None, http_for=None):
    """Run deploy.run with staging/upload/registry faked. Returns (code, rows, health_rows)."""
    rows, health_rows = [], []
    saved = (ws.stage, ws.upload, registry.safe, ws.check)
    ws.stage = lambda *a, **k: None
    ws.upload = lambda *a, **k: None
    registry.safe = lambda fn, *a, **k: health_rows.append(a) if fn is registry.record_health else None
    real_check = saved[3]
    ws.check = lambda api_, app, expect="", http=None: real_check(api_, app, expect,
                                                                  http=(http_for or healthy_http)(ws.version_of(api_.app["active_deployment"]["source_code_path"])))
    try:
        c = dict(cfg or CFG)
        rec = deploy.Recorder(c, write=lambda **kw: rows.append(kw))
        code = deploy.run(c, rec, api, ["a"], tempfile.gettempdir())
    finally:
        ws.stage, ws.upload, registry.safe, ws.check = saved
    return code, rows, health_rows


# --- inputs ------------------------------------------------------------------

@case("deploy inputs are validated before anything runs")
def _():
    env = {"CLIENT": "client-acme", "VERSION": "v1.2.3", "DEPLOY_ID": CFG["deploy_id"], "DATABRICKS_HOST": "h",
           "DATABRICKS_CLIENT_ID": "c", "DATABRICKS_CLIENT_SECRET": "s"}
    assert deploy.config(env)["app"] == "agent-portal"
    assert deploy.config({**env, "USERS_GROUP": "none"})["users_group"] == ""
    raises(deploy.config, {**env, "CLIENT": "acme; rm -rf /"}, contains="unknown client")
    raises(deploy.config, {**env, "VERSION": "main"}, contains="not a release")
    raises(deploy.config, {**env, "DEPLOY_ID": "x'"}, contains="deploy id")
    raises(deploy.config, {**env, "APP_NAME": "Bad_Name"}, contains="app_name")
    raises(deploy.config, {**env, "DATABRICKS_CLIENT_SECRET": ""}, contains="missing")


@case("the client form: names, addresses, ids and tables are checked; secrets required only when adding")
def _():
    good = {"slug": "acme", "name": "Acme", "host": "adb-1.2.azuredatabricks.net", "client_id": "8959a7fc-afea-4b8e-b7c4-5c3be8474df2",
            "secret": "s3cret", "log_table": "main.portal.portal_logs", "warehouse_id": "", "users_group": "users"}
    out = clients.clean(good, new=True)
    assert out["env"] == "client-acme" and out["DATABRICKS_HOST"] == "https://adb-1.2.azuredatabricks.net"
    assert out["APP_NAME"] == "agent-portal" and out[clients.SECRET] == "s3cret"
    raises(clients.clean, {**good, "slug": "Acme Corp"}, new=True, contains="short name")
    raises(clients.clean, {**good, "host": "https://evil.com/path?x"}, new=True, contains="address")
    raises(clients.clean, {**good, "client_id": "nope"}, new=True, contains="application id")
    raises(clients.clean, {**good, "log_table": "main.portal"}, new=True, contains="catalog.schema.table")
    raises(clients.clean, {**good, "warehouse_id": "abc"}, new=True, contains="warehouse")
    raises(clients.clean, {**good, "secret": ""}, new=True, contains="secret")
    assert clients.SECRET not in clients.clean({"name": "Acme 2"}, new=False)


def tiny_png(w=2, h=2) -> str:
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(bytes([0]) + bytes([255, 0, 0, 255]) * w for _ in range(h))
    png = (bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


@case("branding: name, colour and logo are checked; only PNG data URLs within size and 512 px pass")
def _():
    out = clients.clean_brand({"brand_name": "Acme: AI \"Assist\" #1", "brand_color": "#1A73E8", "brand_logo": tiny_png()})
    assert out["BRAND_COLOR"] == "#1a73e8" and out["BRAND_NAME"].startswith("Acme")
    assert clients.clean_brand({"brand_logo": ""}) == {"BRAND_LOGO": ""}  # empty removes it
    assert clients.clean_brand({}) == {}
    raises(clients.clean_brand, {"brand_color": "blue"}, contains="colour")
    raises(clients.clean_brand, {"brand_name": "x" * 41}, contains="too long")
    raises(clients.clean_brand, {"brand_logo": "data:image/svg+xml;base64,PHN2Zz4="}, contains="PNG")
    raises(clients.clean_brand, {"brand_logo": "data:image/png;base64,aGVsbG8gd29ybGQgdGhpcyBpcyBub3QgYSBwbmc="}, contains="not a valid PNG")
    raises(clients.clean_brand, {"brand_logo": tiny_png(600, 2)}, contains="512")
    raises(clients.clean_brand, {"brand_logo": "data:image/png;base64," + "A" * 50000}, contains="too large")


@case("branding reaches app.yaml safely quoted, and the logo is written as branding/logo.png")
def _():
    text = "command: [a]\nenv:\n  - name: X\n    value: y\n"
    out = ws.client_yaml(text, "", "", 'Acme: AI "Assist" #1', "#1a73e8")
    import yaml

    env = {e["name"]: e["value"] for e in yaml.safe_load(out)["env"]}
    assert env == {"PORTAL_BRAND_NAME": 'Acme: AI "Assist" #1', "PORTAL_BRAND_COLOR": "#1a73e8"}, env
    assert "PORTAL_BRAND_COLOR" not in ws.client_yaml(text, "", "", "", "red")
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "repo")
        os.makedirs(repo)
        with open(os.path.join(repo, "app.yaml"), "w") as f:
            f.write(text)
        git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=repo, check=True, capture_output=True)
        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "r")
        git("tag", "v1.0.0")
        out = ws.stage(repo, "v1.0.0", os.path.join(d, "out"), "", "", {"name": "Acme", "color": "#1a73e8", "logo": tiny_png()})
        with open(os.path.join(out, "branding", "logo.png"), "rb") as f:
            assert f.read(4) == bytes([0x89, 0x50, 0x4E, 0x47])
    env = {"CLIENT": "client-acme", "VERSION": "v1.2.3", "DEPLOY_ID": CFG["deploy_id"], "DATABRICKS_HOST": "h",
           "DATABRICKS_CLIENT_ID": "c", "DATABRICKS_CLIENT_SECRET": "s"}
    raises(deploy.config, {**env, "BRAND_COLOR": "red"}, contains="colour")
    raises(deploy.config, {**env, "BRAND_LOGO": "data:image/gif;base64,R0lG"}, contains="PNG")


@case("the connection check lists the client's real catalogs for chat history, without Databricks' own")
def _():
    class Api:
        def __init__(self, host, token):
            pass

        def call(self, method, path, params=None, quiet_404=False):
            if path.endswith("/Me"):
                return {"displayName": "agent-portal-deployer", "groups": [{"display": "admins"}]}
            if path == "/api/2.1/unity-catalog/catalogs":
                if (params or {}).get("page_token"):
                    return {"catalogs": [{"name": "Zeta"}]}
                return {"catalogs": [{"name": "workspace"}, {"name": "system"}, {"name": "__databricks_internal"},
                                     {"name": "mcp-test"}, {"name": "samples"}], "next_page_token": "p2"}
            if path == "/api/2.0/sql/warehouses":
                return {"warehouses": [{"id": "x"}]}
            return {}
    saved = clients.m2m_token
    clients.m2m_token = lambda h, c, s: "T"
    try:
        out = clients.test({"host": "https://adb-1.2.azuredatabricks.net", "client_id": "abc-1234-5678", "secret": "s"}, http_api=Api)
    finally:
        clients.m2m_token = saved
    assert out["ok"] and out["catalogs"] == ["mcp-test", "workspace", "Zeta"], out["catalogs"]


# --- staging -----------------------------------------------------------------

@case("app.yaml: only the env block changes, per client")
def _():
    text = "command:\n  - uvicorn\n\n# comment\nenv:\n  - name: PORTAL_LOG_TABLE\n    value: old.a.b\n\nuser_api_scopes:\n  - x\n"
    out = ws.client_yaml(text, "main.p.portal_logs", "0123456789abcdef")
    assert "old.a.b" not in out and "value: main.p.portal_logs" in out and "PORTAL_LOG_WAREHOUSE" in out
    assert out.startswith("command:\n  - uvicorn") and "user_api_scopes:\n  - x" in out
    assert "env: []" in ws.client_yaml(text, "", "")


@case("a release is staged from its git tag: portal files only, plus VERSION and the client's app.yaml")
def _():
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "repo")
        os.makedirs(os.path.join(repo, "web"))
        os.makedirs(os.path.join(repo, "deployer"))
        files = {"app.py": "x", "app.yaml": "command: [a]\nenv:\n  - name: X\n    value: y\n", "test_app.py": "t",
                 "web/index.html": "<html>", "deployer/app.py": "secret stuff", "README.md": "r"}
        for p, body in files.items():
            with open(os.path.join(repo, p), "w") as f:
                f.write(body)
        git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=repo, check=True,
                                        capture_output=True)
        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "r")
        git("tag", "v1.0.0")
        out = ws.stage(repo, "v1.0.0", os.path.join(d, "out"), "main.p.portal_logs", "")
        got = sorted(os.path.relpath(os.path.join(r, f), out).replace("\\", "/") for r, _, fs in os.walk(out) for f in fs)
        assert got == ["VERSION", "app.py", "app.yaml", "web/index.html"], got
        assert open(os.path.join(out, "VERSION")).read().strip() == "v1.0.0"
        assert "main.p.portal_logs" in open(os.path.join(out, "app.yaml")).read()
        raises(ws.stage, repo, "v9.9.9", os.path.join(d, "out2"), "", "", contains="no release")


# --- health ------------------------------------------------------------------

@case("health: healthy, wrong version, down, unreachable, and sign-in refused are told apart")
def _():
    api = FakeApi(live="v1.0.0")
    h = ws.check(api, "agent-portal", "v1.0.0", http=healthy_http("v1.0.0"))
    assert h["status"] == "healthy" and h["questions"] == 10 and h["api_errors"] == 1, h
    assert ws.check(api, "agent-portal", "v2.0.0", http=healthy_http("v1.0.0"))["status"] == "degraded"
    assert ws.check(api, "agent-portal", "", http=FakeHttp({"/api/health": (302, {})}))["status"] == "unverified"
    assert ws.check(api, "agent-portal", "", http=FakeHttp({"/api/health": (502, {})}))["status"] == "down"
    api.app["app_status"] = {"state": "CRASHED", "message": "exit 1"}
    h = ws.check(api, "agent-portal", "", http=healthy_http("v1.0.0"))
    assert h["status"] == "down" and "exit 1" in h["summary"]
    assert ws.check(FakeApi(exists=False), "agent-portal")["status"] == "down"


@case("health: a high failure rate is degraded; missing admin rights only skip the error counts")
def _():
    api = FakeApi(live="v1.0.0")
    bad = FakeHttp({"/api/health": (200, {"ok": True, "version": "v1.0.0"}),
                    "/api/admin/agent-health?days=1": (200, {"questions": 8, "failed": 4, "kinds": [{"label": "Timeout", "count": 4}],
                                                            "agents": [{"label": "A", "failed": 4, "questions": 8, "failure_rate": 0.5}]})})
    h = ws.check(api, "agent-portal", "", http=bad)
    assert h["status"] == "degraded" and h["failing_assistants"][0]["label"] == "A"
    no_admin = FakeHttp({"/api/health": (200, {"ok": True}), "/api/admin/agent-health?days=1": (403, {}),
                         "/api/admin/logs?days=1&limit=50": (403, {})})
    h = ws.check(api, "agent-portal", "", http=no_admin)
    assert h["status"] == "healthy" and "admin" in h["errors_note"]


# --- deploy ------------------------------------------------------------------

@case("a deploy records each step and ends live on the new version, with a health record")
def _():
    api = FakeApi(live="v1.0.0")
    code, rows, health = run_deploy(api)
    assert code == 0, rows[-1]
    assert rows[-1]["status"] == "succeeded" and rows[-1]["step"].startswith("Live on v1.1.0")
    assert all(r["from_version"] in ("", "v1.0.0") for r in rows) and rows[-1]["from_version"] == "v1.0.0"
    assert ws.version_of(api.app["active_deployment"]["source_code_path"]) == "v1.1.0"
    assert len(health) == 1
    # access was set up: users group on the app, warehouse for the app's identity, three SQL statements
    assert api.made("PATCH", "/permissions/apps/agent-portal") and api.made("PATCH", "/permissions/warehouses/")
    assert len(api.made("POST", "/sql/statements")) == 3
    # scopes changed (["a"] -> ["a"] is unchanged): no restart
    assert not api.made("POST", "/stop")


@case("a failed deploy puts the previous version back automatically and records both")
def _():
    api = FakeApi(live="v1.0.0", fail_deploy={"v1.1.0"})
    code, rows, health = run_deploy(api)
    assert code == 1
    assert ws.version_of(api.app["active_deployment"]["source_code_path"]) == "v1.0.0"
    final = rows[-1]
    assert final["status"] == "rolled_back" and "boom in v1.1.0" in final["message"], final
    back = [r for r in rows if r["action"] == "auto_rollback"]
    assert back and back[-1]["status"] == "succeeded" and back[-1]["version"] == "v1.0.0"
    assert back[-1]["deploy_id"] != CFG["deploy_id"]  # its own record, so "current version" reads v1.0.0
    assert len(health) == 1


@case("no automatic rollback on a first install, or when nothing live had changed yet")
def _():
    code, rows, _ = run_deploy(FakeApi(exists=False, fail_deploy={"v1.1.0"}))
    assert code == 1 and rows[-1]["status"] == "failed" and not [r for r in rows if r["action"] == "auto_rollback"]
    api = FakeApi(live="v1.0.0")
    saved, saved_up = ws.stage, ws.upload
    try:
        def broken(*a, **k):
            raise DeployError("There is no release v1.1.0 in the repository.", 404)
        ws.upload = lambda *a, **k: None
        rows2 = []
        ws.stage = broken
        rec = deploy.Recorder(dict(CFG), write=lambda **kw: rows2.append(kw))
        code = deploy.run(dict(CFG), rec, api, ["a"], tempfile.gettempdir())
    finally:
        ws.stage, ws.upload = saved, saved_up
    assert code == 1 and rows2[-1]["status"] == "failed" and not api.made("POST", "/deployments")


@case("if putting the old version back also fails, the record says so plainly")
def _():
    code, rows, _ = run_deploy(FakeApi(live="v1.0.0", fail_deploy={"v1.1.0", "v1.0.0"}))
    assert code == 1 and rows[-1]["status"] == "failed" and "also failed" in rows[-1]["step"], rows[-1]


@case("access refusals are warnings, not failures; scopes are only written when they changed")
def _():
    api = FakeApi(live="v1.0.0")
    api.fail[("PATCH", "/permissions/warehouses/")] = DeployError("no", 403)
    saved = ws.sql

    def no_sql(*a, **k):
        raise DeployError("PERMISSION_DENIED", 403)
    ws.sql = no_sql
    try:
        code, rows, _ = run_deploy(api, cfg=CFG)
    finally:
        ws.sql = saved
    assert code == 0 and "warning" in rows[-1]["step"], rows[-1]["step"]
    assert len(rows[-1]["detail"]["warnings"]) == 4  # warehouse + 3 statements
    api2 = FakeApi(live="v1.0.0")
    # Databricks' effective list differs from the request (defaults added, sql.* folded): no restart for that.
    api2.app["user_api_scopes"] = ["a", "sql.warehouses"]
    api2.app["effective_user_api_scopes"] = ["a", "iam.current-user:read", "sql"]
    assert not ws.set_scopes(api2, "agent-portal", api2.app, ["sql.warehouses", "a"], lambda s: None)
    changed = ws.set_scopes(api2, "agent-portal", api2.app, ["a", "b"], lambda s: None)
    assert changed and api2.made("PATCH", "/api/2.0/apps/agent-portal")
    assert not ws.set_scopes(api2, "agent-portal", {"user_api_scopes": ["a"]}, ["a"], lambda s: None)


@case("a new app is created with the portal's scopes, and its compute is started if stopped")
def _():
    api = FakeApi(exists=False)
    app, created = ws.ensure_app(api, "agent-portal", ["x"], lambda s: None)
    assert created and api.made("POST", "/api/2.0/apps")[0][2]["user_api_scopes"] == ["x"]
    api = FakeApi(live="v1.0.0")
    states = iter(["STOPPED", "STARTING", "ACTIVE"])
    orig = api.call

    def call(method, path, **kw):
        if method == "GET" and path == "/api/2.0/apps/agent-portal":
            api.app["compute_status"] = {"state": next(states, "ACTIVE")}
        return orig(method, path, **kw)
    api.call = call
    ws.wait_active(api, "agent-portal", lambda s: None)
    assert api.made("POST", "/start")
    raises(ws.ensure_app, FakeApi(), "Bad_Name", [], lambda s: None, contains="not allowed")


@case("deploy shares the client's assistants with the portal; ones it may not share are named in a warning")
def _():
    api = FakeApi(live="v1.0.0")
    eps = [{"id": "e1", "name": "mas-1", "task": "agent/v1/responses", "tags": [{"key": "agent_id", "value": "a1"}, {"key": "display_name", "value": "Sales"}]},
           {"id": "e2", "name": "ka-2", "task": "agent/v1/responses", "tags": [{"key": "display_name", "value": "Policies"}]},
           {"id": "e3", "name": "gpt", "task": "llm/v1/chat", "tags": []},
           {"id": "e4", "name": "tagged", "task": "llm/v1/chat", "tags": [{"key": "portal", "value": "true"}]}]
    orig = api.call

    def call(method, path, **kw):
        if path == "/api/2.0/serving-endpoints":
            return {"endpoints": eps}
        if method == "PATCH" and path.endswith("/e2"):
            raise DeployError("no Manage permission", 403)
        return orig(method, path, **kw)
    api.call = call
    n, warns = ws.share_agents(api, api.app, lambda s: None)
    patched = [c[1] for c in api.calls if c[0] == "PATCH"]
    assert n == 2 and "/api/2.0/permissions/serving-endpoints/e1" in patched and "/api/2.0/permissions/supervisor-agents/a1" in patched
    assert not any(p.endswith("/e3") for p in patched)  # a plain chat model is not an assistant
    assert len(warns) == 1 and "Policies" in warns[0] and "sp-1" in warns[0], warns


@case("health: a portal with no assistants shared is degraded, with the identity to share them with")
def _():
    api = FakeApi(live="v1.0.0")
    empty = FakeHttp({"/api/health": (200, {"ok": True, "version": "v1.0.0", "assistants": 0})})
    h = ws.check(api, "agent-portal", "v1.0.0", http=empty)
    assert h["status"] == "degraded" and "no assistants" in h["summary"] and "sp-1" in h["summary"], h
    some = FakeHttp({"/api/health": (200, {"ok": True, "version": "v1.0.0", "assistants": 3})})
    assert ws.check(api, "agent-portal", "v1.0.0", http=some)["status"] == "healthy"


def catalog_tar(files: dict) -> bytes:
    """A gzipped tarball shaped like GitHub's: everything under one top folder."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            data = body if isinstance(body, bytes) else body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


SAMPLE = {
    "mcps-abc/README.md": "catalog readme",
    "mcps-abc/_template/mcp.yaml": "name: Template",
    "mcps-abc/report/mcp.yaml": "name: Report",
    "mcps-abc/report/app.yaml": "command: [python, server.py]",
    "mcps-abc/report/standalones/template.pptx": bytes(range(256)),
    "mcps-abc/report/tests/test_x.py": "skip me",
    "mcps-abc/report/__pycache__/x.pyc": "skip me",
    "mcps-abc/weather/mcp.yaml": "name: Weather",
    "mcps-abc/weather/app.yaml": "command: [python, s.py]",
    "mcps-abc/../escape.txt": "outside",
}


@case("MCP catalog: only the client's MCPs ship (binaries intact); template, tests and caches never; nothing escapes")
def _():
    with tempfile.TemporaryDirectory() as d:
        got = ws.stage_mcps(catalog_tar(SAMPLE), ["report"], d, {"ref": "v1.0.0", "sha": "a" * 40})
        files = sorted(os.path.relpath(os.path.join(r, f), d).replace("\\", "/") for r, _, fs in os.walk(d) for f in fs)
        assert got == ["report"]
        assert files == ["mcp_catalog/CATALOG.json", "mcp_catalog/report/app.yaml", "mcp_catalog/report/mcp.yaml",
                         "mcp_catalog/report/standalones/template.pptx"], files
        with open(os.path.join(d, "mcp_catalog", "report", "standalones", "template.pptx"), "rb") as f:
            assert f.read() == bytes(range(256))
        assert not os.path.exists(os.path.join(os.path.dirname(d), "escape.txt"))
    with tempfile.TemporaryDirectory() as d:
        assert ws.stage_mcps(catalog_tar(SAMPLE), None, d, {}) == ["report", "weather"]  # None = all
    with tempfile.TemporaryDirectory() as d:
        assert ws.stage_mcps(catalog_tar(SAMPLE), [], d, {}) == []  # [] = none


@case("MCP catalog version: empty means the newest version tag (else main), always pinned to a commit")
def _():
    sha = "b" * 40

    def gh(path):
        if "/tags" in path:
            return [{"name": "v1.2.0"}, {"name": "v1.10.0"}, {"name": "v1.9.0-beta"}, {"name": "nightly"}]
        return {"sha": sha} if "/commits/" in path else {}
    assert ws.mcp_ref("o/mcps", "", gh) == ("v1.10.0", sha)
    assert ws.mcp_ref("o/mcps", "v1.2.0", gh) == ("v1.2.0", sha)
    assert ws.mcp_ref("o/mcps", "", lambda p: [] if "/tags" in p else {"sha": sha}) == ("main", sha)
    raises(ws.mcp_ref, "o/mcps", "../etc", gh, contains="not valid")
    raises(ws.mcp_ref, "o/mcps", "v9.9.9", lambda p: {} if "/commits/" in p else [], contains="not found")


@case("a deploy ships the pinned catalog, records it, and warns about MCPs the catalog does not have")
def _():
    c = {**CFG, "mcp_repo": "o/mcps", "mcp_ref": "", "mcp_allow": ["report", "gone-tool"], "mcp_token": ""}
    rows = []
    rec = deploy.Recorder(c, write=lambda **kw: rows.append(kw))
    gh = lambda p: [{"name": "v1.0.0"}] if "/tags" in p else {"sha": "c" * 40}  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        deploy.ship_mcps(c, rec, d, gh_get=gh, download=lambda sha: catalog_tar(SAMPLE))
        assert os.path.isdir(os.path.join(d, "mcp_catalog", "report"))
    assert rec.mcps == {"ref": "v1.0.0", "sha": "c" * 12, "mcps": ["report"]}
    assert any("gone-tool" in w for w in rec.warnings), rec.warnings
    assert deploy._allow("") is None and deploy._allow("*") is None and deploy._allow("none") == [] \
        and deploy._allow("a, b") == ["a", "b"]


@case("upload clears the release folder first, then imports every file over the API (no CLI, so no all-apis needed)")
def _():
    import base64 as b64

    src = tempfile.mkdtemp()
    os.makedirs(os.path.join(src, "web", "_next"))
    open(os.path.join(src, "app.py"), "w").write("print(1)")
    open(os.path.join(src, "web", "_next", "a.js"), "w").write("x")
    api = FakeApi()
    api.fail[("POST", "/workspace/delete")] = DeployError("Path (/x) doesn't exist.", 404)
    root = "/Workspace/Users/sp/agent-portal/agent-portal/v1.4.0"
    ws.upload(api, src, root, lambda s: None)
    calls = [(c[1].rsplit("/", 1)[-1], (c[2] or {}).get("path")) for c in api.calls]
    assert calls[0] == ("delete", root), calls  # cleared first; a missing folder is fine
    assert ("mkdirs", root + "/web/_next") in calls
    imports = {c[2]["path"]: c[2] for c in api.calls if c[1].endswith("/workspace/import")}
    assert set(imports) == {root + "/app.py", root + "/web/_next/a.js"}, imports
    assert imports[root + "/app.py"]["format"] == "AUTO" and b64.b64decode(imports[root + "/app.py"]["content"]) == b"print(1)"
    shutil.rmtree(src)


@case("sign-in asks for all-apis, and falls back to the installer's own scopes when the secret has only those")
def _():
    asked = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, str(body)

        def json(self):
            return self._b
    saved = common.httpx.post

    def post(url, data=None, auth=None, timeout=None):
        asked.append(data["scope"])
        if data["scope"] == "all-apis":
            return R(403, {"error_description": "Scopes 'all-apis' are not assigned to the client x"})
        return R(200, {"access_token": "T"})
    common.httpx.post = post
    try:
        assert common.m2m_token("https://x.cloud.databricks.com", "id", "s") == "T"
    finally:
        common.httpx.post = saved
    assert asked == ["all-apis", " ".join(common.SCOPES)] and "access-management" in common.SCOPES, asked


@case("tool secrets: put in the client's agent-portal scope with the portal allowed to use them; removed ones deleted")
def _():
    api = FakeApi()
    store = {"scopes": set(), "keys": {"gam-network": "old"}}
    orig = api.call

    def call(method, path, **kw):
        body = kw.get("json") or {}
        if path == "/api/2.0/secrets/scopes/list":
            return {"scopes": [{"name": n} for n in store["scopes"]]}
        if path == "/api/2.0/secrets/scopes/create":
            store["scopes"].add(body["scope"])
        if path == "/api/2.0/secrets/list":
            return {"secrets": [{"key": k} for k in store["keys"]]}
        if path == "/api/2.0/secrets/put":
            store["keys"][body["key"]] = body["string_value"]
        if path == "/api/2.0/secrets/delete":
            store["keys"].pop(body["key"], None)
        return orig(method, path, **kw)
    api.call = call
    said = []
    w = ws.sync_tool_secrets(api, api.app, {"gam-key": "{json}", "gam-network": "123"}, said.append)
    assert not w and store["scopes"] == {"agent-portal"} and store["keys"] == {"gam-key": "{json}", "gam-network": "123"}
    acl = api.made("POST", "/secrets/acls/put")[0][2]
    assert acl == {"scope": "agent-portal", "principal": "sp-1", "permission": "MANAGE"}, acl
    assert "{json}" not in " ".join(said)  # values are never written to the record
    # disconnected: both removed, the scope left alone
    ws.sync_tool_secrets(api, api.app, {"gam-key": "", "gam-network": ""}, said.append)
    assert store["keys"] == {} and store["scopes"] == {"agent-portal"}
    # nothing connected and no scope: nothing is created
    api2 = FakeApi()
    assert ws.sync_tool_secrets(api2, api2.app, {"gam-key": ""}, said.append) == [] and not api2.made("POST")


@case("a GAM connection is saved only for a key that sees the chosen network; the key goes in as a secret")
def _():
    import gamconn

    saved = (ghub.clients, ghub.set_secret, ghub.set_vars, ghub.secret_set, gamconn.test)
    got = {}
    ghub.clients = lambda: {"client-acme": {}}
    ghub.set_secret = lambda env, name, value: got.setdefault("secret", (name, value))
    ghub.set_vars = lambda env, vals: got.setdefault("vars", vals)
    ghub.secret_set = lambda env, name: ""
    gamconn.test = lambda text: {"account": "sa@x.iam", "networks": [{"code": "111"}]}
    try:
        raises(clients.save_gam, "client-acme", "{k}", "222", contains="cannot see network 222")
        assert "secret" not in got
        raises(clients.save_gam, "client-acme", "", "111", contains="Upload")
        raises(clients.save_gam, "client-acme", "{k}", "abc", contains="Choose")
        clients.save_gam("client-acme", "{k}", "111")
        assert got["secret"] == ("GAM_KEY_JSON", "{k}") and got["vars"] == {"GAM_NETWORK_CODE": "111", "GAM_ACCOUNT": "sa@x.iam"}
    finally:
        ghub.clients, ghub.set_secret, ghub.set_vars, ghub.secret_set, gamconn.test = saved


@case("a GAM key file is checked before Google is asked: JSON, a service account, the fields it needs")
def _():
    import gamconn

    raises(gamconn.parse_key, "nope", contains="not valid JSON")
    raises(gamconn.parse_key, '{"type": "authorized_user"}', contains="not a service account")
    raises(gamconn.parse_key, '{"type": "service_account", "client_email": "a"}', contains="missing private_key")


@case("uninstall: removes only the portal app and its files; assistants, tools and tool keys stay; history only if asked")
def _():
    import uninstall

    api = FakeApi()
    c = {"app": "agent-portal", "client_id": "cid", "log_table": "cat.sch.portal_logs", "warehouse": ""}
    done, problems = uninstall.run(api, c, lambda s: None)
    assert [x[1] for x in api.calls if x[0] == "DELETE"] == ["/api/2.0/apps/agent-portal"], api.calls
    assert [x[2]["path"] for x in api.calls if x[1] == "/api/2.0/workspace/delete"] == ["/Workspace/Users/cid/agent-portal/agent-portal"]
    touched = " ".join(x[1] for x in api.calls)
    assert "secrets" not in touched and "agent-portal-mcps" not in str(api.calls) and "serving-endpoints" not in touched
    assert not problems and not api.made("POST", "/sql/statements")  # history kept unless asked
    api.calls.clear()
    uninstall.run(api, c, lambda s: None, history=True)
    drops = [x[2]["statement"] for x in api.calls if x[1] == "/api/2.0/sql/statements"]
    assert drops == ["DROP TABLE IF EXISTS `cat`.`sch`.`%s`" % t for t in ("portal_logs", "portal_chats", "portal_events", "portal_app_agents")], drops


@case("uninstall: a refusal fails the job, so the deployer keeps the client to retry")
def _():
    api = FakeApi()
    api.fail[("DELETE", "/api/2.0/apps/agent-portal")] = DeployError("no permission", 403)
    rows = []
    c = deploy.config({"CLIENT": "client-acme", "ACTION": "uninstall", "VERSION": "-", "DEPLOY_ID": "11111111-1111-1111-1111-111111111111",
                       "DATABRICKS_HOST": "https://x.cloud.databricks.com", "DATABRICKS_CLIENT_ID": "cid", "DATABRICKS_CLIENT_SECRET": "s"})
    rec = deploy.Recorder(c, write=lambda **kw: rows.append(kw))
    assert deploy.remove_everything(api, c, rec) == 1
    assert rows[-1]["status"] == "failed" and "no permission" in rows[-1]["message"], rows[-1]
    api.fail.clear()
    rows.clear()
    assert deploy.remove_everything(api, c, rec) == 0 and rows[-1]["status"] == "succeeded"


@case("removing a client: started as a job, deploys refused meanwhile, forgotten only when the job succeeded")
def _():
    vars_ = {"DATABRICKS_HOST": "https://x", "DATABRICKS_CLIENT_ID": "cid"}
    log = []
    saved = (ghub.clients, ghub.variables, ghub.secret_set, ghub.set_vars, ghub.dispatch, ghub.remove, registry.record,
             registry.latest_deploys, registry.steps, ghub.find_run)
    ghub.clients = lambda: {"client-acme": {}} if "gone" not in log else {}
    ghub.variables = lambda env: dict(vars_)
    ghub.secret_set = lambda env, name: "2026-10-01"
    ghub.set_vars = lambda env, vals: vars_.update(vals) or [vars_.pop(k) for k, v in vals.items() if not v]
    ghub.dispatch = lambda wf, inputs: log.append(("dispatch", inputs["action"], inputs["purge"]))
    ghub.remove = lambda env: log.append("gone")
    registry.record = lambda *a, **k: None
    registry.latest_deploys = lambda *a, **k: []
    ghub.find_run = lambda *a: None
    try:
        out = clients.uninstall("client-acme", "me", history=True)
        assert ("dispatch", "uninstall", "history") in log and vars_["REMOVING"] == out["deploy_id"]
        raises(clients.start, "client-acme", "v1.0.0", "deploy", "me", contains="being removed")
        # the job failed: kept, deploys allowed again
        registry.steps = lambda d: [{"client": "client-acme", "action": "uninstall", "status": "failed", "version": "-", "at": ""}]
        clients.status(out["deploy_id"])
        assert "REMOVING" not in vars_ and "gone" not in log
        # again, and it succeeded: forgotten
        out = clients.uninstall("client-acme", "me")
        registry.steps = lambda d: [{"client": "client-acme", "action": "uninstall", "status": "succeeded", "version": "-", "at": ""}]
        clients.status(out["deploy_id"])
        assert "gone" in log
    finally:
        (ghub.clients, ghub.variables, ghub.secret_set, ghub.set_vars, ghub.dispatch, ghub.remove, registry.record,
         registry.latest_deploys, registry.steps, ghub.find_run) = saved


# --- GitHub --------------------------------------------------------------------

@case("secrets are sealed with the environment's public key (only the private key opens them)")
def _():
    from nacl.public import PrivateKey, SealedBox

    k = PrivateKey.generate()
    sealed = ghub.seal(base64.b64encode(bytes(k.public_key)).decode(), "top secret")
    assert "top secret" not in sealed
    assert SealedBox(k).decrypt(base64.b64decode(sealed)) == b"top secret"


@case("environment variables: new ones created, changed ones updated, emptied ones deleted, same ones untouched")
def _():
    calls = []
    saved = ghub.call
    ghub.call = lambda method, path, **kw: calls.append((method, path.rsplit("/", 1)[-1], kw.get("json")))
    try:
        ghub.set_vars("client-acme", {"A": "1", "B": "2", "C": "", "D": "same"}, have={"B": "old", "C": "x", "D": "same"})
    finally:
        ghub.call = saved
    assert ("POST", "variables", {"name": "A", "value": "1"}) in calls
    assert ("PATCH", "B", {"name": "B", "value": "2"}) in calls
    assert ("DELETE", "C", None) in calls
    assert len(calls) == 3
    raises(ghub.set_vars, "acme", {"A": "1"}, have={}, contains="unknown client")


@case("GitHub reads are conditional: an unchanged answer (304) is served from memory; any change forgets them")
def _():
    class R:
        def __init__(self, status, body=None, etag=""):
            self.status_code, self._b, self.headers = status, body, ({"etag": etag} if etag else {})
            self.content = b"x" if body is not None else b""
            self.text = ""

        def json(self):
            return self._b
    sent = []

    class Http:
        def __init__(self, replies):
            self.replies = replies

        def request(self, method, url, json=None, params=None, headers=None):
            sent.append((method, dict(headers or {})))
            return self.replies.pop(0)
    saved = ghub.token
    ghub.token = lambda: "T"
    ghub._etags.clear()
    try:
        first = ghub.call("GET", "/repos/x/environments", http=Http([R(200, {"n": 1}, etag='"a"')]))
        again = ghub.call("GET", "/repos/x/environments", http=Http([R(304)]))
        assert first == again == {"n": 1} and sent[1][1].get("If-None-Match") == '"a"'
        ghub.call("POST", "/repos/x/environments/e/variables", json={}, http=Http([R(201, {})]))
        assert not ghub._etags  # a write forgets every remembered answer
    finally:
        ghub.token = saved
        ghub._etags.clear()


# --- the deployer's decisions --------------------------------------------------

class World:
    """GitHub + registry for clients.py, in memory."""

    def __init__(self, deploys=None, releases=("v1.0.0", "v1.1.0")):
        self.deploys = list(deploys or [])
        self.rows = []
        self.dispatched = []
        self.saved = {}
        self.releases = [{"version": v} for v in releases]
        self.run = None

    def __enter__(self):
        fakes = {
            (ghub, "clients"): lambda: ["client-acme"],
            (ghub, "variables"): lambda env: {"DATABRICKS_HOST": "https://x", "DATABRICKS_CLIENT_ID": "c"},
            (ghub, "secret_set"): lambda env, name: "2026-10-01",
            (ghub, "releases"): lambda: self.releases,
            (ghub, "dispatch"): lambda wf, inputs: self.dispatched.append((wf, inputs)),
            (ghub, "find_run"): lambda wf, marker: self.run,
            (registry, "latest_deploys"): lambda client="", limit=200: self.deploys,
            (registry, "record"): lambda *a, **kw: self.rows.append((a, kw)),
            (registry, "safe"): lambda fn, *a, **kw: self.rows.append((a, kw)),
            (registry, "steps"): lambda deploy_id: [dict(r) for r in self.deploys if r["deploy_id"] == deploy_id],
            (registry, "last_good"): lambda client, other_than="": next(
                (d["version"] for d in self.deploys if d["status"] == "succeeded" and d["version"] != other_than), ""),
        }
        for (mod, name), fn in fakes.items():
            self.saved[(mod, name)] = getattr(mod, name)
            setattr(mod, name, fn)
        return self

    def __exit__(self, *a):
        for (mod, name), fn in self.saved.items():
            setattr(mod, name, fn)


def row(version, status, deploy_id="d1", at="2026-10-06T10:00:00Z", action="deploy"):
    return {"deploy_id": deploy_id, "client": "client-acme", "version": version, "status": status, "at": at,
            "started": at, "action": action, "from_version": "", "actor": "a", "run_url": ""}


@case("deploy requests are recorded before GitHub is asked, by the signed-in person")
def _():
    with World([row("v1.0.0", "succeeded")]) as w:
        out = clients.start("client-acme", "v1.1.0", "deploy", "me@x.io")
    (args, kw), = w.rows
    assert kw["status"] == "requested" and kw["actor"] == "me@x.io" and kw["from_version"] == "v1.0.0"
    wf, inputs = w.dispatched[0]
    assert wf == "deploy.yml" and inputs["deploy_id"] == out["deploy_id"] and inputs["version"] == "v1.1.0"


@case("deploy refuses unknown versions, a second deploy in flight, and incomplete clients")
def _():
    with World() as w:
        raises(clients.start, "client-acme", "v9.0.0", "deploy", "me", contains="not a published release")
        raises(clients.start, "client-zzz", "v1.0.0", "deploy", "me", contains="no client")
        assert not w.dispatched
    now = clients._now().isoformat()
    with World([row("v1.1.0", "running", at=now)]) as w:
        raises(clients.start, "client-acme", "v1.0.0", "deploy", "me", contains="already being deployed")
    with World() as w:
        ghub.secret_set = lambda env, name: ""
        raises(clients.start, "client-acme", "v1.0.0", "deploy", "me", contains="incomplete")


@case("rollback with no version picks the last good one before the current version")
def _():
    deploys = [row("v1.1.0", "succeeded", "d2", "2026-10-06T11:00:00Z"), row("v1.0.0", "succeeded", "d1")]
    with World(deploys) as w:
        out = clients.start("client-acme", "", "rollback", "me")
    assert out["version"] == "v1.0.0" and w.dispatched[0][1]["action"] == "rollback"
    with World([row("v1.0.0", "succeeded")]) as w:
        raises(clients.start, "client-acme", "", "rollback", "me", contains="no earlier version")


@case("a GitHub job that ended (or never started) without finishing its record is recorded as failed")
def _():
    with World([row("v1.1.0", "running")]) as w:
        w.run = {"status": "completed", "conclusion": "cancelled", "url": "u"}
        w.deploys[0]["deploy_id"] = CFG["deploy_id"]
        clients.status(CFG["deploy_id"])
        (_, kw), = w.rows
        assert kw["status"] == "failed" and "cancelled" in kw["message"]
    with World([row("v1.1.0", "requested", at="2020-01-01T00:00:00Z")]) as w:
        w.deploys[0]["deploy_id"] = CFG["deploy_id"]
        clients.status(CFG["deploy_id"])
        assert "did not start" in w.rows[0][1]["message"]
    with World([row("v1.1.0", "running", at=clients._now().isoformat())]) as w:
        w.deploys[0]["deploy_id"] = CFG["deploy_id"]
        clients.status(CFG["deploy_id"])
        assert not w.rows  # still within the grace period and no run yet: leave it


@case("rollout: canary first, everyone recorded before GitHub is asked, ineligible clients skipped with reasons")
def _():
    import json as _json

    now = clients._now().isoformat()
    with World([row("v1.1.0", "succeeded", "d-on", now), row("v1.0.0", "running", "d-busy", now)]) as w:
        w.deploys[0]["client"] = "client-on"
        w.deploys[1]["client"] = "client-busy"
        envs = ["client-on", "client-busy", "client-half", "client-a", "client-b", "client-c"]
        ghub.clients = lambda: envs
        ghub.variables = lambda env: {} if env == "client-half" else {"DATABRICKS_HOST": "https://x", "DATABRICKS_CLIENT_ID": "c"}
        out = clients.rollout("v1.1.0", envs + ["client-ghost"], "client-b", 5, "me@x.io")
        reasons = {s["client"]: s["reason"] for s in out["skipped"]}
        assert reasons == {"client-on": "Already on v1.1.0.", "client-busy": "A deploy is already running.",
                           "client-half": "Settings are incomplete.", "client-ghost": "No such client."}, reasons
        assert out["started"] == ["client-b", "client-a", "client-c"] and out["canary"] == "client-b"
        wf, inputs = w.dispatched[0]
        assert wf == "rollout.yml" and _json.loads(inputs["canary"])["client"] == "client-b"
        assert [x["client"] for x in _json.loads(inputs["rest"])] == ["client-a", "client-c"] and inputs["parallel"] == "5"
        recorded = [kw for _, kw in w.rows]
        assert len(recorded) == 3 and all(r["status"] == "requested" and r["actor"] == "me@x.io" for r in recorded)
        assert {r["detail"]["wave"] for r in recorded} == {"canary", "rest"}
        # a canary that is not eligible falls back to the first eligible client
        assert clients.rollout("v1.1.0", ["client-a", "client-c"], "client-on", 3, "me")["canary"] == "client-a"
        raises(clients.rollout, "v1.1.0", ["client-a"], "", 7, "me", contains="at a time")
        raises(clients.rollout, "v9.9.9", ["client-a"], "", 5, "me", contains="not a published release")


@case("rollout: a client whose turn never came is recorded as stopped once the rollout ends")
def _():
    r = row("v1.1.0", "requested", at="2020-01-01T00:00:00Z")
    r["deploy_id"] = CFG["deploy_id"]
    r["detail"] = {"rollout_id": "r-1", "wave": "rest"}
    with World([r]) as w:
        asked = []
        ghub.find_run = lambda wf, marker: asked.append((wf, marker)) or {"status": "completed", "conclusion": "failure", "url": "u"}
        clients.status(CFG["deploy_id"])
        assert asked == [("rollout.yml", "r-1")]
        assert "rollout stopped" in w.rows[0][1]["message"]


@case("the app: only the deployer group gets in, and the actor comes from the token")
def _():
    from fastapi.testclient import TestClient

    import app as deployer_app

    saved = deployer_app.me
    deployer_app.me = lambda tok: {"user_name": "real@x.io", "display_name": "R", "allowed": tok == "ADMIN", "group": "admins"}
    seen = []
    saved_start = clients.start
    clients.start = lambda env, version, action, actor: seen.append(actor) or {"deploy_id": "x", "version": version}
    try:
        c = TestClient(deployer_app.app)
        r = c.post("/api/clients/client-acme/deploy", json={"version": "v1.0.0", "actor": "spoof@x.io"},
                   headers={"X-Forwarded-Access-Token": "NOPE"})
        assert r.status_code == 403 and "admins" in r.json()["detail"]
        r = c.post("/api/clients/client-acme/deploy", json={"version": "v1.0.0", "actor": "spoof@x.io"},
                   headers={"X-Forwarded-Access-Token": "ADMIN"})
        assert r.status_code == 200 and seen == ["real@x.io"]
        # a plain DELETE (a page from before "Remove everywhere") is refused; only an explicit "only here" forgets
        gone = []
        saved_remove = clients.remove
        clients.remove = lambda env: gone.append(env)
        try:
            r = c.delete("/api/clients/client-acme", headers={"X-Forwarded-Access-Token": "ADMIN"})
            assert r.status_code == 409 and "out of date" in r.json()["detail"] and not gone
            r = c.delete("/api/clients/client-acme?only_here=true", headers={"X-Forwarded-Access-Token": "ADMIN"})
            assert r.status_code == 200 and gone == ["client-acme"]
        finally:
            clients.remove = saved_remove
        assert c.get("/api/version").json()["build"] == deployer_app.BUILD
    finally:
        deployer_app.me = saved
        clients.start = saved_start


def main() -> int:
    passed = failed = 0
    for name, fn in CASES:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - test harness
            print("  FAIL  " + name)
            print("        %s: %s" % (type(exc).__name__, str(exc)[:300]))
            failed += 1
        else:
            print("  PASS  " + name)
            passed += 1
    print("--- %d passed, %d failed ---" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
