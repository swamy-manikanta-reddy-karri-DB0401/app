"""Databricks access layer.

Two identities are used, deliberately:

* the **user** identity (OBO) - every agent invocation goes out under the
  signed-in user's own token, so Unity Catalog and the serving-endpoint ACL are
  enforced by Databricks itself. A bug in this app cannot widen access.
* the **app** identity (service principal) - used only to *read* endpoint ACLs
  and manage groups, i.e. the metadata plane. It never invokes an agent for a
  user.

Locally there is no Apps runtime, so both fall back to the developer's CLI
token; `auth_mode()` reports which world we are in and the UI shows it.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from typing import Any

import httpx

log = logging.getLogger("portal.dbx")

PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "azure-prem")

# Anything under agent/* is an agent, whatever its schema - that prefix is what
# distinguishes a built agent from a raw model. An endpoint tagged
# portal=true is published too, which is how an admin exposes a plain chat
# model (or a task type invented after this code was written) as an agent.
AGENT_TASK_PREFIX = "agent/"
PORTAL_TAG = "portal"
QUERYABLE = {"CAN_QUERY", "CAN_MANAGE"}

_cli_token: tuple[str, float] | None = None
_sp_token: tuple[str, float] | None = None

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def http() -> httpx.Client:
    """The one HTTP client every Databricks call goes through.

    `httpx.request(...)` builds a brand-new client for every call, and building
    one loads the TLS certificate bundle. Measured: about 250 ms of the portal's
    own CPU per call, which capped a whole process at roughly 8 upstream calls a
    second however many threads it had. One shared client reuses connections and
    costs about 1 ms, so the portal stops being the bottleneck. The client is
    safe to share between threads.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    limits=httpx.Limits(max_connections=400, max_keepalive_connections=100, keepalive_expiry=30),
                    timeout=120,
                )
    return _client


class DbxError(RuntimeError):
    """Carries an upstream status code so routes can mirror it."""

    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


def in_apps() -> bool:
    return bool(os.environ.get("DATABRICKS_CLIENT_ID"))


def auth_mode() -> str:
    return "databricks-apps" if in_apps() else "local-dev"


def host() -> str:
    h = os.environ.get("DATABRICKS_HOST")
    if not h:
        h = _cli(["auth", "env", "--profile", PROFILE]).get("env", {}).get("DATABRICKS_HOST", "")
    if not h:
        raise DbxError("DATABRICKS_HOST is not set and could not be read from the CLI profile")
    h = h.strip().rstrip("/")
    # The Apps runtime supplies a bare hostname; the CLI profile supplies a full
    # URL. Normalise, or httpx rejects the request for a missing scheme.
    if not h.startswith(("http://", "https://")):
        h = "https://" + h
    return h


def _cli(args: list[str]) -> dict:
    proc = subprocess.run(
        ["databricks", *args, "-o", "json"], capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise DbxError(f"databricks CLI failed: {proc.stderr.strip()[:300]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def _cli_user_token() -> str:
    """Developer's own token, used for both roles in local-dev mode.

    A PAT in DATABRICKS_TOKEN skips the CLI subprocess entirely - useful when
    the `databricks` binary isn't on this process's PATH, or OAuth login isn't
    available (e.g. workspace SSO only offers a provider you don't have).
    """
    env_tok = os.environ.get("DATABRICKS_TOKEN")
    if env_tok:
        return env_tok
    global _cli_token
    if _cli_token and _cli_token[1] > time.time() + 60:
        return _cli_token[0]
    data = _cli(["auth", "token", "--profile", PROFILE])
    tok = data.get("access_token")
    if not tok:
        raise DbxError("could not obtain a CLI token; run `databricks auth login`")
    # The CLI hands back its own cached token, which may be minutes from expiry,
    # so trust what it says rather than assuming a fresh half hour.
    try:
        life = min(1800.0, float(data.get("expires_in") or 1800))
    except (TypeError, ValueError):
        life = 1800.0
    _cli_token = (tok, time.time() + life)
    return tok


def app_token() -> str:
    """Service-principal token (metadata plane only)."""
    if not in_apps():
        return _cli_user_token()
    global _sp_token
    if _sp_token and _sp_token[1] > time.time() + 60:
        return _sp_token[0]
    resp = http().post(
        f"{host()}/oidc/v1/token",
        data={"grant_type": "client_credentials", "scope": "all-apis"},
        auth=(os.environ["DATABRICKS_CLIENT_ID"], os.environ["DATABRICKS_CLIENT_SECRET"]),
        timeout=30,
    )
    if resp.status_code != 200:
        raise DbxError(f"service-principal token request failed: {resp.text[:200]}")
    payload = resp.json()
    _sp_token = (payload["access_token"], time.time() + payload.get("expires_in", 3600))
    return _sp_token[0]


def user_token(forwarded: str | None) -> str:
    """OBO token for the signed-in user.

    In Apps the runtime supplies it as X-Forwarded-Access-Token. Its absence in
    Apps is a misconfiguration (missing user_api_scopes) and must fail closed
    rather than silently falling back to the app identity - that fallback would
    turn every user into the service principal.
    """
    if forwarded:
        return forwarded
    if in_apps():
        raise DbxError(
            "no X-Forwarded-Access-Token from the Apps runtime. Apply user_api_scopes "
            "with `databricks apps update <app> --json @update-scopes.json`.",
            403,
        )
    return _cli_user_token()


def _stale_cli_token(token: str, resp: httpx.Response) -> str:
    """Local dev only: a fresh CLI token if Databricks rejected the cached one.

    Seen on this workspace: the CLI rotates its OAuth tokens on refresh and the
    previous access token stops working before its stated expiry, so a cached
    token can turn into "403 Invalid Token" mid-session and the whole portal
    shows that error. When that happens, drop the cache, ask the CLI again and
    hand back the new token for one retry. Returns "" (no retry) in Apps, for
    any other refusal, or when the CLI hands back the same token.
    """
    global _cli_token
    if in_apps() or os.environ.get("DATABRICKS_TOKEN"):
        return ""
    if resp.status_code not in (401, 403) or "invalid token" not in (resp.text or "").lower():
        return ""
    if not _cli_token or _cli_token[0] != token:
        return ""
    _cli_token = None
    try:
        fresh = _cli_user_token()
    except DbxError:
        return ""
    if fresh == token:
        return ""
    log.info("CLI token was rejected as invalid; refreshed it and retried")
    return fresh


def call(method: str, path: str, token: str, **kw) -> Any:
    # Merge rather than overwrite: some APIs need extra headers (the Agent
    # Bricks endpoints want X-Databricks-Workspace-Id).
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(kw.pop("headers", None) or {})
    # For best-effort calls whose failure is expected (and handled by the
    # caller): logged quietly so they do not fill the problem log.
    quiet = kw.pop("quiet", False)
    try:
        resp = http().request(method, f"{host()}{path}", headers=headers, timeout=120, **kw)
        fresh = _stale_cli_token(token, resp)
        if fresh:
            headers["Authorization"] = f"Bearer {fresh}"
            resp = http().request(method, f"{host()}{path}", headers=headers, timeout=120, **kw)
    except httpx.RequestError as exc:
        # Seen (2026-11-04): the network dropped mid-run and the page got a bare 500. A connection
        # failure is said in words and mapped to 503, like every other Databricks refusal.
        log.log(logging.DEBUG if quiet else logging.WARNING, "%s %s -> could not connect: %s", method, path, type(exc).__name__)
        raise DbxError("Could not reach Databricks just now (%s). Check the connection and try again." % type(exc).__name__, 503)
    if resp.status_code >= 400:
        # Logged here as well as in the route handler, because callers often
        # swallow this error (name lookups, "why" labels) and it would vanish.
        log.log(logging.DEBUG if quiet else logging.WARNING, "%s %s -> %s: %s", method, path, resp.status_code, resp.text[:300])
        raise DbxError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}", resp.status_code)
    return resp.json() if resp.content else {}
