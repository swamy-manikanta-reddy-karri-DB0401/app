"""Portal Deployer: puts the Agent Portal into client workspaces.

A Databricks App in our own workspace. Only members of DEPLOYER_GROUP (default
`admins`) may use it; membership is read with the signed-in person's own token,
so Databricks decides. The deployer stores nothing itself:
- clients and their secrets are GitHub Environments (ghub.py),
- versions are GitHub Releases, deploys/checks are GitHub Actions runs,
- what happened is the deployment record in Delta (registry.py).

The actor recorded for every action is the signed-in person from their token,
never a request field. Secrets are accepted, passed to GitHub encrypted, and
never logged or returned.
"""
from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import Body, FastAPI, Header, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import clients  # noqa: E402
import ghub  # noqa: E402
import home  # noqa: E402
import readiness  # noqa: E402
import secure  # noqa: E402
from common import Api, DeployError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("deployer")
HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
GROUP = os.environ.get("DEPLOYER_GROUP") or "admins"


def _build() -> str:
    """Which UI build this server ships (it changes with every UI build). An
    open page compares it with its own and asks for a reload when they differ:
    a page left open across an update ran old buttons (seen live: an old Remove
    forgot two clients without cleaning their workspaces)."""
    import hashlib

    try:
        with open(os.path.join(WEB, "index.html"), "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()[:12]
    except OSError:
        return ""


BUILD = _build()

# Off in Databricks Apps unless DEPLOYER_API_DOCS=on: an enterprise scan reports open API docs.
_DOCS = os.environ.get("DEPLOYER_API_DOCS", "").lower() == "on" or not os.environ.get("DATABRICKS_CLIENT_ID")
app = FastAPI(title="Portal Deployer", docs_url="/api/docs" if _DOCS else None, redoc_url=None,
              openapi_url="/api/openapi.json" if _DOCS else None)
_who: dict = {}


@app.middleware("http")
async def access_log(request: Request, call_next):
    t0 = time.time()
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        log.info("%s %s user=%s -> %d (%d ms)", request.method, request.url.path,
                 request.headers.get("x-forwarded-email", "local"), resp.status_code, (time.time() - t0) * 1000)
    return resp


_HTTPS = bool(os.environ.get("DATABRICKS_CLIENT_ID"))
_SECURITY_HEADERS = secure.headers(WEB, _HTTPS) if os.path.isdir(WEB) else {}
_RATE = secure.RateLimit(secure.rate_from_env())


@app.middleware("http")
async def _security(request: Request, call_next):
    """Same protection as the portal (secure.py): headers, cross-site, size and rate checks."""
    path = request.url.path
    if path.startswith("/api/"):
        who = request.headers.get("x-forwarded-email") or ""
        why = secure.cross_site(request.method, request.headers) or secure.too_big(request.headers)
        status = 403 if why.startswith("sent") else 413
        if not why and _RATE.over(who):
            why, status = "too many requests from you in the last minute; wait a moment", 429
        if why:
            log.warning("%s %s user=%s refused: %s", request.method, path, who or "local", why)
            resp = secure.refuse(status, why)
        else:
            resp = await call_next(request)
        resp.headers.setdefault("Cache-Control", "no-store")
    else:
        resp = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        if not (k == "Content-Security-Policy" and path.startswith("/api/docs")):
            resp.headers[k] = v
    return resp


@app.exception_handler(DeployError)
async def _deploy_error(_request: Request, exc: DeployError):
    if exc.status >= 500:
        log.warning("failed: %s", exc)
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


def me(forwarded: str | None) -> dict:
    """Who is asking, and whether they may use the deployer. Cached briefly per token."""
    tok = home.user_token(forwarded)
    hit = _who.get(tok)
    if hit and hit[0] > time.time():
        return hit[1]
    d = Api(home.home_host(), tok).call("GET", "/api/2.0/preview/scim/v2/Me")
    groups = {g.get("display") for g in d.get("groups") or []}
    who = {"user_name": d.get("userName", ""), "display_name": d.get("displayName") or d.get("userName", ""),
           "allowed": GROUP in groups, "group": GROUP}
    if len(_who) > 500:
        _who.clear()
    _who[tok] = (time.time() + 300, who)
    return who


def allowed(forwarded: str | None) -> dict:
    who = me(forwarded)
    if not who["allowed"]:
        raise DeployError("Only members of the %s group can use the deployer." % GROUP, 403)
    return who


@app.get("/api/session")
def session(x_forwarded_access_token: str = Header(None)):
    who = me(x_forwarded_access_token)
    return {**who, "mode": home.mode(), "repo": ghub.repo(), "actions_url": ghub.actions_url(), "build": BUILD}


@app.get("/api/version")
def version():
    """The UI build this server ships; open pages poll it to offer a reload."""
    return {"build": BUILD}


@app.get("/api/setup")
def setup_checks(fresh: bool = False, x_forwarded_access_token: str = Header(None)):
    """What the deployer depends on, checked read-only, each with its fix."""
    allowed(x_forwarded_access_token)
    return readiness.checks(fresh)


@app.get("/api/clients")
def list_clients(x_forwarded_access_token: str = Header(None)):
    allowed(x_forwarded_access_token)
    return clients.list_all()


@app.get("/api/clients/{env}")
def get_client(env: str, x_forwarded_access_token: str = Header(None)):
    allowed(x_forwarded_access_token)
    return clients.detail(env)


@app.post("/api/clients/test")
def test_client(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    allowed(x_forwarded_access_token)
    return clients.test(payload)


@app.post("/api/clients")
def add_client(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who = allowed(x_forwarded_access_token)
    out = clients.create(payload)
    log.info("client %s added by %s", out["id"], who["user_name"])
    return out


@app.patch("/api/clients/{env}")
def edit_client(env: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who = allowed(x_forwarded_access_token)
    out = clients.update(env, payload)
    log.info("client %s changed by %s%s", env, who["user_name"], " (secret replaced)" if payload.get("secret") else "")
    return out


@app.delete("/api/clients/{env}")
def delete_client(env: str, only_here: bool = False, x_forwarded_access_token: str = Header(None)):
    """Forget the client here only; their workspace is left as it is. Must be
    asked for by name (`only_here=true`): a page from before "Remove
    everywhere" sent a plain DELETE and silently skipped the clean-up."""
    who = allowed(x_forwarded_access_token)
    if not only_here:
        raise DeployError("This page is out of date. Reload it (Ctrl+Shift+R), then remove the client again.", 409)
    clients.remove(env)
    log.info("client %s removed (deployer only) by %s", env, who["user_name"])
    return {"ok": True}


@app.post("/api/clients/{env}/uninstall")
def uninstall_client(env: str, payload: dict = Body(default={}), x_forwarded_access_token: str = Header(None)):
    """Take the portal out of their workspace, then forget the client (follow it with /api/deploys/{id})."""
    who = allowed(x_forwarded_access_token)
    out = clients.uninstall(env, who["user_name"], bool(payload.get("history")))
    log.info("client %s: removal started by %s%s", env, who["user_name"], " (with history)" if payload.get("history") else "")
    return out


@app.post("/api/clients/{env}/deploy")
def deploy(env: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who = allowed(x_forwarded_access_token)
    return clients.start(env, (payload.get("version") or "").strip(), "deploy", who["user_name"])


@app.post("/api/clients/{env}/rollback")
def rollback(env: str, payload: dict = Body(default={}), x_forwarded_access_token: str = Header(None)):
    who = allowed(x_forwarded_access_token)
    return clients.start(env, (payload.get("version") or "").strip(), "rollback", who["user_name"])


@app.post("/api/gam/test")
def gam_test(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Check a Google Ad Manager key and list the networks it can see (nothing is saved)."""
    import gamconn

    allowed(x_forwarded_access_token)
    return gamconn.test(payload.get("key_json") or "")


@app.put("/api/clients/{env}/gam")
def gam_save(env: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who = allowed(x_forwarded_access_token)
    out = clients.save_gam(env, payload.get("key_json") or "", payload.get("network") or "")
    log.info("client %s: GAM connection saved by %s (network %s%s)", env, who["user_name"], out["network"],
             ", new key" if payload.get("key_json") else "")
    return out


@app.delete("/api/clients/{env}/gam")
def gam_remove(env: str, x_forwarded_access_token: str = Header(None)):
    who = allowed(x_forwarded_access_token)
    log.info("client %s: GAM connection removed by %s", env, who["user_name"])
    return clients.remove_gam(env)


@app.post("/api/rollouts")
def start_rollout(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Deploy one version to many clients: a canary first, then the rest in a wave."""
    who = allowed(x_forwarded_access_token)
    try:
        parallel = int(payload.get("parallel") or 5)
    except (TypeError, ValueError):
        parallel = 0
    out = clients.rollout((payload.get("version") or "").strip(), payload.get("clients") or [],
                          (payload.get("canary") or "").strip(), parallel, who["user_name"])
    log.info("rollout %s of %s to %d clients by %s", out.get("rollout_id"), payload.get("version"),
             len(out.get("started") or []), who["user_name"])
    return out


@app.post("/api/clients/{env}/check")
def check(env: str, x_forwarded_access_token: str = Header(None)):
    who = allowed(x_forwarded_access_token)
    return clients.check_now(env, who["user_name"])


@app.get("/api/clients/{env}/check/{check_id}")
def check_result(env: str, check_id: str, x_forwarded_access_token: str = Header(None)):
    allowed(x_forwarded_access_token)
    return clients.check_result(env, check_id)


@app.get("/api/deploys/{deploy_id}")
def deploy_status(deploy_id: str, x_forwarded_access_token: str = Header(None)):
    allowed(x_forwarded_access_token)
    return clients.status(deploy_id)


@app.get("/api/mcps")
def mcp_catalog(ref: str = "", x_forwarded_access_token: str = Header(None)):
    """The MCP catalog's versions and the tools in one of them (newest by default)."""
    allowed(x_forwarded_access_token)
    return clients.mcp_catalog(ref)


@app.get("/api/releases")
def releases(x_forwarded_access_token: str = Header(None)):
    allowed(x_forwarded_access_token)
    return clients.releases()


@app.get("/api/health")
def health():
    return {"ok": True, "mode": home.mode()}


if os.path.isdir(WEB):
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
