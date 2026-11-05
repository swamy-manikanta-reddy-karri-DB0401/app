"""Agent Portal - a friendly front door to Databricks agents.

Two audiences, one workspace:

* an end user signs in and sees only the agents they hold CAN_QUERY on, and
  chats with them;
* a workspace admin grants that access from the UI, using Databricks groups as
  reusable "agent sets".

Nothing is stored here. Every read and write lands on Databricks-native state,
so the portal can be deleted and rebuilt without losing configuration.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import access
import agent_export
import app_agents
import attachments
import audit
import builder
import chat
import code_agent
import code_upload
import genie
import knowledge
import chats
import connections
import custom_agents
import safety
import sandbox
import dashboard
import designer
import designer_files
import designer_guard
import designer_models
import events
import files as filestore
import signer
import toolpolicy
import forge
import llm
import logbuf
import logsink
import mcps
import memory
import secure
import tooltest
from dbx import DbxError, app_token, auth_mode, in_apps, user_token

HERE = os.path.dirname(os.path.abspath(__file__))
# Built by `npm run build` in ui/ (Next.js static export) and copied here.
# Databricks Apps runs one command, so there is no Node server: the export is
# plain files and FastAPI serves them.
WEB = os.path.join(HERE, "web")

# The interactive API docs describe every route; an enterprise scan reports them as exposed, so in
# Databricks Apps they are off unless PORTAL_API_DOCS=on. Local dev keeps them.
_DOCS = os.environ.get("PORTAL_API_DOCS", "").lower() == "on" or not in_apps()
app = FastAPI(title="Mediamint Agent Builder", docs_url="/api/docs" if _DOCS else None,
              redoc_url=None, openapi_url="/api/openapi.json" if _DOCS else None)


# stdout/stderr from a Databricks App land in the app's Logs tab (and
# `databricks apps logs <app>`), so plain logging is all that is needed. Tokens
# and request bodies are never logged.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("portal")
log.addHandler(logbuf.buffer)  # in-memory fallback for the admin panel's Logs view
if logsink.enabled():
    log.addHandler(logsink.handler)  # durable copy of problems, in a Delta table
elif logsink.TABLE:
    log.warning("PORTAL_LOG_TABLE=%r is not catalog.schema.table; logs stay in memory only", logsink.TABLE)


def _caller(request: Request) -> str:
    """Who is asking, from the headers the Apps proxy adds. Not a credential."""
    h = request.headers
    return h.get("x-forwarded-email") or h.get("x-forwarded-preferred-username") or "unknown"


def _ctx(request: Request, status: int) -> dict:
    """Structured fields, so the Delta table can be filtered by user or path."""
    return {"extra": {"actor": _caller(request), "method": request.method, "path": request.url.path, "status": status}}


@app.on_event("startup")
async def _more_threads():
    """Room for many people to wait on an assistant at once.

    Every route here is a plain `def`, which Starlette runs in a pool of 40 worker
    threads, and a chat holds one for as long as the assistant takes (seconds to
    minutes). With the default, the 41st simultaneous chat waits for a free thread,
    and so does everything else, including opening the page. The threads only wait
    on the network, so many more are cheap. Tune with PORTAL_THREADS.
    """
    import anyio.to_thread

    try:
        n = max(40, int(os.environ.get("PORTAL_THREADS", "400")))
    except ValueError:
        n = 400
    anyio.to_thread.current_default_thread_limiter().total_tokens = n


_CHANGES_ACCESS = ("/api/admin/grant", "/api/admin/group", "/api/admin/meta", "/api/admin/custom-agents", "/api/admin/app-agents",
                   "/api/approvals", "/api/admin/builder", "/api/admin/designer/build", "/api/admin/designer/tools/create")


@app.middleware("http")
async def _access_log(request: Request, call_next):
    # The Logs panel polls its own route; logging that would fill the view with itself.
    if not request.url.path.startswith("/api/") or request.url.path in ("/api/health", "/api/admin/logs"):
        return await call_next(request)
    start = time.monotonic()
    # Routes that do something worth recording fill this in (events.note).
    rec = events.begin()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("%s %s user=%s -> unhandled error", request.method, request.url.path, _caller(request), **_ctx(request, 500))
        events.finish(rec, 500, int((time.monotonic() - start) * 1000), _caller(request))
        raise
    ms = int((time.monotonic() - start) * 1000)
    if request.method in secure.UNSAFE and response.status_code < 400 and request.url.path.startswith(_CHANGES_ACCESS):
        # Who may see what just changed: the next request reads it from Databricks again (cache.py).
        access.VISIBLE.clear()
        if request.url.path.startswith(("/api/admin/group", "/api/admin/grant")):
            access.IDENTITY.clear()
    events.finish(rec, response.status_code, ms, _caller(request))
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    log.log(level, "%s %s user=%s -> %s (%d ms)", request.method, request.url.path, _caller(request), response.status_code, ms,
            **_ctx(request, response.status_code))
    return response


_HTTPS = in_apps()
_SECURITY_HEADERS = secure.headers(WEB, _HTTPS)
_RATE = secure.RateLimit(secure.rate_from_env())


@app.middleware("http")
async def _security(request: Request, call_next):
    """Security headers on every response; cross-site, size and rate checks on the API (secure.py).
    Registered after the access log, so it runs first and a refused request never reaches a route."""
    path = request.url.path
    if path.startswith("/api/"):
        why = secure.cross_site(request.method, request.headers) or secure.too_big(request.headers)
        status = 403 if why.startswith("sent") else 413
        if not why and _RATE.over(_caller(request) if _caller(request) != "unknown" else ""):
            why, status = "too many requests from you in the last minute; wait a moment", 429
        if why:
            log.warning("%s %s user=%s refused: %s", request.method, path, _caller(request), why, **_ctx(request, status))
            response = secure.refuse(status, why)
        else:
            response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
    else:
        response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        if k == "Content-Security-Policy" and path.startswith("/api/docs"):
            continue  # the docs page loads its viewer from a CDN; only offered when switched on
        response.headers[k] = v
    return response


@app.exception_handler(DbxError)
async def _dbx_error(request: Request, exc: DbxError):
    # The reason behind a failed Databricks call, e.g. a user without WRITE
    # VOLUME on an upload. The access log above carries the same path and user.
    log.warning("databricks call failed: %s %s user=%s status=%s: %s",
                request.method, request.url.path, _caller(request), exc.status, str(exc)[:500],
                **_ctx(request, exc.status))
    events.failed(str(exc), exc.status)
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    # Our own refusals ("You do not have access to that agent"): put the reason on
    # the activity record, then answer exactly as FastAPI would have.
    events.failed(str(exc.detail), exc.status_code)
    return await http_exception_handler(request, exc)


def _who(forwarded):
    """Resolve the caller. Fails closed if the OBO token is missing in Apps."""
    tok = user_token(forwarded)
    who = access.identity(tok)
    events.note(actor=who.get("user_name"))
    return who, tok


def _require_admin(forwarded):
    who, tok = _who(forwarded)
    if not who["is_admin"]:
        raise HTTPException(403, "Access administration is limited to workspace admins.")
    return who, tok


@app.get("/api/session")
def session(x_forwarded_access_token: str = Header(None)):
    who, _ = _who(x_forwarded_access_token)
    events.note(action="opened_portal")
    return {**who, "auth_mode": auth_mode(), "chat_history": chats.enabled(), "version": VERSION, "brand": BRAND}


@app.get("/api/agents")
def agents(x_forwarded_access_token: str = Header(None)):
    who, tok = _who(x_forwarded_access_token)
    return {"agents": access.visible_agents(who, app_token(), tok)}


def _allowed_agent(who: dict, endpoint: str, user_tok: str = "") -> dict:
    """The agent record, or 403. Needed because the task drives the wire format."""
    for a in access.visible_agents(who, app_token(), user_tok):
        if a["name"] == endpoint:
            return a
    raise HTTPException(403, "You do not have access to that agent.")


@app.post("/api/chat")
def send(
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    endpoint = (payload.get("endpoint") or "").strip()
    history = payload.get("history") or []
    uploads = [p for p in (payload.get("files") or []) if isinstance(p, str) and p]
    if not endpoint:
        raise HTTPException(400, "endpoint is required")

    who, tok = _who(x_forwarded_access_token)
    # Recorded for agent health: which assistant, how long, did it work. Never
    # the question or the answer.
    events.note(action="asked", target=endpoint, detail={"files": len(uploads)})
    # Confirm the agent is one this user may see before spending a call on it.
    # The invocation below still runs under the user's own token, so this check
    # is a courtesy for clearer errors, not the security boundary.
    agent = _allowed_agent(who, endpoint, tok)
    # `known`: the assistant exists and this person may use it, so the outcome
    # says something about the assistant. A refused or unknown name only says
    # something about the request, and stays out of per-assistant reliability.
    events.note(label=agent.get("display_name"), detail={"files": len(uploads), "known": True})
    output_dir = filestore.output_dir_path(agent["output_volume"]) if agent.get("output_volume") else ""
    if agent.get("custom_id"):
        out = custom_agents.ask(custom_agents.resolve(endpoint, who), history, tok, who)
    else:
        out = chat.ask(_transport(agent), agent["task"], history, tok, files=uploads, output_dir=output_dir,
                       ask_person=_policy(agent, tok))
    # Safety flags (an override attempt, a key removed, an answer held back) go on the audit record; never the text.
    flags = out.pop("safety", None)
    return _answered(out, endpoint, who, tok, payload, history, background, len(uploads),
                     extra={"safety": flags[:10]} if flags else None)


def _transport(agent: dict) -> str:
    """Where a question goes: the serving endpoint's name, or a custom-code agent's app address."""
    return app_agents.target(agent) if agent.get("app_name") else agent["name"]


def _policy(agent: dict, tok: str):
    """Which tool calls of this assistant wait for the person (see toolpolicy)."""
    mode = toolpolicy.mode_of(agent.get("tool_approval") or "")
    return lambda request: toolpolicy.needs_person(request, mode, tok)


DOWNLOAD_TTL = 30 * 24 * 3600


def _sign_files(files: list, endpoint: str, user: str) -> list:
    """Each file an agent named gets a permit for this person and assistant, so it can be
    downloaded without an admin first declaring the agent's folder (see /api/download)."""
    out = []
    for f in files or []:
        path = str(f.get("path") or "") if isinstance(f, dict) else ""
        if path.startswith("/Volumes/") and ".." not in path.split("/") and "//" not in path:
            f = dict(f, sig=signer.permit({"e": endpoint, "p": f["path"], "u": user}, DOWNLOAD_TTL))
        out.append(f)
    return out


def _answered(out: dict, endpoint: str, who: dict, tok: str, payload: dict, history: list, background, uploads: int = 0,
              extra: dict | None = None) -> dict:
    """What every chat answer goes through: health record, signed downloads, a paused tool
    call turned into a decision for the person, and the saved conversation."""
    broken = events.tool_failure(out.get("reply") or "")
    events.note(
        detail={"files": uploads, "known": True, "tools": (out.get("tools") or [])[:10], "files_back": len(out.get("attachments") or []),
                **(extra or {})},
        **({"status": "tool_error", "error": broken} if broken else {}),
    )
    out["attachments"] = _sign_files(out.get("attachments") or [], endpoint, who["user_name"])
    pause = out.pop("pause", None)
    if pause:
        # The browser holds the paused answer (the portal keeps nothing); signed for this person
        # and assistant so it cannot be swapped for another. What they decide comes back to
        # /api/chat/decide. Not saved yet: the exchange is not finished.
        state = {"e": endpoint, "u": who["user_name"], "pause": pause}
        items = (custom_agents.approval_items(pause) if custom_agents.is_key(endpoint)
                 else [toolpolicy.describe(a, tok) for a in pause["approvals"] if a.get("id") in pause["asked"]])
        out["approval"] = {"state": state, "sig": signer.mac(state), "items": items}
        events.note(detail={**(extra or {}), "waiting_for": [a.get("name") for a in pause["approvals"] if a.get("id") in pause["asked"]]})
        return out

    # Saved after the reply has gone out, and only for a successful exchange, so
    # history never slows a chat and never holds a failed attempt.
    cid = (payload.get("conversation_id") or "").strip()
    if chats.enabled() and chats.valid_id(cid) and history and history[-1].get("role") == "user":
        names = [n for n in (payload.get("file_names") or []) if isinstance(n, str)][:20]
        background.add_task(
            _save_chat, who["user_name"], cid, endpoint, len(history) - 1,
            str(history[-1].get("content") or ""), names, out,
        )
    return out


@app.post("/api/chat/decide")
def chat_decide(
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    """The person approved or declined the tool calls an answer was waiting on; carry on.

    The state is the one /api/chat signed for this person and assistant. The tool calls
    still run under the person's own token, so Databricks decides what they may do."""
    endpoint = (payload.get("endpoint") or "").strip()
    history = payload.get("history") or []
    approval = payload.get("approval") if isinstance(payload.get("approval"), dict) else {}
    state = approval.get("state") if isinstance(approval.get("state"), dict) else {}
    who, tok = _who(x_forwarded_access_token)
    if not signer.check(state, approval.get("sig") or "") or state.get("e") != endpoint or state.get("u") != who["user_name"]:
        raise HTTPException(400, "That decision is out of date. Ask the assistant again.")
    decisions = {str(k): bool(v) for k, v in (payload.get("decisions") or {}).items()}
    pause = state["pause"]
    named = {a.get("id"): a.get("name") for a in pause.get("approvals") or []}
    events.note(action="asked", target=endpoint)
    agent = _allowed_agent(who, endpoint, tok)
    yes = [named[i] for i in pause.get("asked") or [] if decisions.get(i)]
    no = [named[i] for i in pause.get("asked") or [] if not decisions.get(i)]
    # The person's decision is the audit record, under their own identity; never the arguments.
    events.note(label=agent.get("display_name"), detail={"known": True, "approved": yes, "declined": no})
    if agent.get("custom_id"):
        out = custom_agents.resume(custom_agents.resolve(endpoint, who), pause, decisions, tok, who)
    else:
        out = chat.resume(_transport(agent), pause, decisions, tok, ask_person=_policy(agent, tok))
    return _answered(out, endpoint, who, tok, payload, history, background, extra={"approved": yes, "declined": no})


def _save_chat(user: str, cid: str, endpoint: str, idx: int, question: str, names: list, out: dict):
    try:
        chats.save(user, cid, endpoint, idx, [
            {"role": "user", "text": question, "meta": {"files": names}},
            {"role": "assistant", "text": out.get("reply") or "", "meta": {
                "tools": out.get("tools") or [],
                "citations": out.get("citations") or [],
                "attachments": out.get("attachments") or [],
            }},
        ])
    except Exception as exc:  # a failed save must never surface as a failed chat
        log.warning("could not save conversation %s for %s: %s", cid, user, str(exc)[:300])


@app.get("/api/chats")
def chat_list(endpoint: str = "", x_forwarded_access_token: str = Header(None)):
    """The signed-in person's saved conversations, newest first."""
    who, _ = _who(x_forwarded_access_token)
    if not chats.enabled():
        return {"enabled": False, "chats": []}
    return {"enabled": True, "chats": chats.list_chats(who["user_name"], endpoint.strip())}


@app.get("/api/chats/{cid}")
def chat_open(cid: str, x_forwarded_access_token: str = Header(None)):
    who, _ = _who(x_forwarded_access_token)
    if not chats.enabled():
        raise HTTPException(404, "conversation history is not turned on")
    messages = chats.get_chat(who["user_name"], cid)
    if not messages:
        raise HTTPException(404, "no such conversation")
    for m in messages:  # fresh download permits for files in a reopened conversation
        if m.get("attachments") and m.get("endpoint"):
            m["attachments"] = _sign_files(m["attachments"], m["endpoint"], who["user_name"])
    return {"messages": messages}


@app.delete("/api/chats/{cid}")
def chat_delete(cid: str, x_forwarded_access_token: str = Header(None)):
    who, _ = _who(x_forwarded_access_token)
    events.note(action="deleted_conversation")
    if chats.enabled():
        chats.delete_chat(who["user_name"], cid)
    return {"deleted": cid}


@app.post("/api/upload")
async def upload(
    endpoint: str = Form(...),
    file: UploadFile = File(...),
    x_forwarded_access_token: str = Header(None),
):
    """Put a file where a file-driven agent can read it, as the signed-in user."""
    who, tok = _who(x_forwarded_access_token)
    name = file.filename or "upload"
    events.note(action="uploaded", target=endpoint.strip(), detail={"name": name})
    agent = _allowed_agent(who, endpoint.strip(), tok)
    events.note(label=agent.get("display_name"))
    if not agent.get("upload_volume"):
        raise HTTPException(400, "This agent does not accept file uploads.")

    accepts = agent.get("accepts") or []
    if accepts and not any(name.lower().endswith("." + a.lstrip(".")) for a in accepts):
        raise HTTPException(
            400, "This agent accepts only: " + ", ".join("." + a.lstrip(".") for a in accepts)
        )

    blob = await file.read()
    events.note(detail={"name": name, "bytes": len(blob)})
    return filestore.upload(agent["upload_volume"], name, blob, tok)


@app.get("/api/download")
def download(
    endpoint: str,
    path: str,
    sig: str = "",
    x_forwarded_access_token: str = Header(None),
):
    """Fetch a file an agent generated, as the signed-in user.

    Allowed when the file is one this agent named to this person in a reply (`sig`, a permit
    the chat route signed), or inside the folders an admin declared for it (`output_volume`).
    Either way it is read with the person's own token, so Unity Catalog decides."""
    who, tok = _who(x_forwarded_access_token)
    events.note(action="downloaded", target=endpoint.strip(), detail={"name": path.rsplit("/", 1)[-1]})
    agent = _allowed_agent(who, endpoint.strip(), tok)
    events.note(label=agent.get("display_name"))
    p = signer.read(sig) if sig else None
    named = bool(p) and p.get("e") == endpoint.strip() and p.get("p") == path and p.get("u") == who["user_name"]
    if not named:
        if not agent.get("output_volume"):
            raise HTTPException(400, "This agent does not produce downloadable files.")
        if not filestore.in_volume(path, agent["output_volume"]):
            raise HTTPException(403, "That file is not in this agent's output location.")

    blob = filestore.download(path, tok)
    name = path.rsplit("/", 1)[-1] or "download"
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="' + name.replace('"', "") + '"'},
    )


@app.get("/api/admin/overview")
def overview(x_forwarded_access_token: str = Header(None)):
    who, user_tok = _require_admin(x_forwarded_access_token)
    tok = app_token()
    return {"agents": access.admin_agents(tok, user_tok, who), **access.principals(tok)}


@app.post("/api/admin/grant")
def grant(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    endpoint_id = (payload.get("endpoint_id") or "").strip()
    kind = (payload.get("kind") or "").strip()
    principal = (payload.get("principal") or "").strip()
    if not (endpoint_id and kind and principal):
        raise HTTPException(400, "endpoint_id, kind and principal are required")
    # level omitted or null means revoke
    level = payload.get("level")
    if level not in (None, "", "CAN_QUERY", "CAN_VIEW", "CAN_MANAGE"):
        raise HTTPException(400, "unsupported permission level")

    tok = app_token()
    # Resolve the whole agent: an Agent Bricks agent is governed by its own
    # permission list, not the serving endpoint's, and only the shaped record
    # knows which.
    if app_agents.is_key(endpoint_id):
        row = app_agents.find(endpoint_id)
        target = app_agents.shape(row, None) if row else None
    else:
        target = next((a for a in access.all_agents(tok) if a["id"] == endpoint_id), None)
    if not target:
        target = {"id": endpoint_id}
    events.note(
        action="granted" if level else "revoked",
        target=target.get("name") or endpoint_id,
        label=target.get("display_name"),
        detail={"principal": principal, "kind": kind, "level": level or ""},
    )
    # The portal must not lend the app's CAN_MANAGE to its users.
    if not access.may_manage(target, who, tok):
        raise HTTPException(
            403,
            "You need Manage permission on this agent in Databricks to change who can use "
            "it. Ask its owner to grant you CAN_MANAGE.",
        )
    return access.set_grant(target, kind, principal, level or None, tok)


@app.post("/api/admin/group")
def new_group(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    name = (payload.get("name") or "").strip()
    events.note(action="created_team", target=name, label=name)
    if not name:
        raise HTTPException(400, "name is required")
    # Also grants the group permission to open the portal, so setting up access
    # never needs a terminal.
    return access.create_group(name, app_token(), os.environ.get("DATABRICKS_APP_NAME", "agent-portal"))


@app.post("/api/admin/group-members")
def group_members(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    group_id = (payload.get("group_id") or "").strip()
    users = payload.get("users") or []
    events.note(action="changed_team", target=group_id, label=(payload.get("name") or group_id), detail={"members": len(users)})
    if not group_id:
        raise HTTPException(400, "group_id is required")
    return access.set_group_members(group_id, [u for u in users if u], app_token())


@app.post("/api/admin/meta")
def meta(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    values = {k: payload[k] for k in access.META_TAGS if k in payload}
    events.note(action="changed_settings", target=name, detail={"fields": sorted(values)})
    if not values:
        raise HTTPException(400, "nothing to update")
    tok = app_token()
    if app_agents.is_key(name):
        return _app_agent_meta(who, name, values)
    target = next((a for a in access.all_agents(tok) if a["name"] == name), None)
    if not target:
        raise HTTPException(404, "no such agent")
    events.note(label=target.get("display_name"))
    if not access.may_manage(target, who, tok):
        raise HTTPException(
            403,
            "You need Manage permission on this agent in Databricks to change how it "
            "appears. Ask its owner to grant you CAN_MANAGE.",
        )
    return access.set_meta(name, values, tok)


def _app_agent_meta(who: dict, name: str, values: dict) -> dict:
    """Settings of a custom-code agent live in the portal's table, not in tags."""
    row = app_agents.find(name)
    if not row:
        raise HTTPException(404, "no such agent")
    events.note(label=row.get("display_name"))
    if not app_agents.may_manage(row["app_name"], who):
        raise HTTPException(403, "You need Can Manage on this app in Databricks to change it. Ask its owner.")
    changes = {k: values[k] for k in app_agents.FIELDS if k in values}
    if not changes:
        raise HTTPException(400, "Only the name, description, address and file folders of a custom agent can be changed here.")
    return {"agent": app_agents.save(row["app_name"], changes, who["user_name"])}


# --------------------------------------------------------------- custom-code agents (apps)


@app.get("/api/admin/app-agents")
def app_agent_list(x_forwarded_access_token: str = Header(None)):
    """The custom-code agents registered in the portal, with whether each is running."""
    _require_admin(x_forwarded_access_token)
    if not app_agents.enabled():
        return {"enabled": False, "reason": app_agents.OFF, "agents": []}
    rows = app_agents.registered(fresh=True)
    return {"enabled": True, "agents": [app_agents.shape(r, app_agents.app_info(r["app_name"])) for r in rows]}


@app.get("/api/admin/app-agents/apps")
def app_agent_apps(x_forwarded_access_token: str = Header(None)):
    """Apps in this workspace an admin could offer as an agent."""
    _, tok = _require_admin(x_forwarded_access_token)
    return {"apps": app_agents.candidates(tok)}


@app.get("/api/admin/app-agents/check")
def app_agent_check(name: str, x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    return app_agents.check(name.strip(), tok)


@app.post("/api/admin/app-agents/try")
def app_agent_try(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Send one message to the app as the admin, to see that the portal and the app understand
    each other before anyone else is given it. A failure is a result, not an error."""
    _, tok = _require_admin(x_forwarded_access_token)
    name = (payload.get("app_name") or "").strip()
    message = (payload.get("message") or "").strip()[:2000] or "Hello. In one sentence, what can you help with?"
    path = app_agents.clean({"path": payload.get("path") or ""})["path"]
    info = app_agents.check(name, tok)
    if not info["running"]:
        return {"ok": False, "message": info["message"]}
    try:
        gap = code_upload.scope_gap(app_agents._as("GET", "/api/2.0/apps/" + name, tok))
    except DbxError:
        gap = []
    if gap:
        # Databricks will not pass the person's sign-in to an app that asks for more than the portal holds.
        return {"ok": False, "message": "This agent asks for permissions this portal's sign-in does not have (%s), so "
                "Databricks will not pass your sign-in on and the agent cannot answer. An admin can add them to the "
                "portal's user authorization (update-scopes.json) and restart the portal." % ", ".join(gap)}
    url = info["url"].rstrip("/") + path
    started = time.monotonic()
    try:
        out = chat.ask(url, "app", [{"role": "user", "content": message}], tok)
    except DbxError as exc:
        return {"ok": False, "message": str(exc)[:600]}
    return {"ok": True, "reply": (out.get("reply") or "")[:4000], "tools": (out.get("tools") or [])[:10],
            "seconds": round(time.monotonic() - started, 1)}


def _app_agent_access(app_name: str, share: list) -> list:
    """Give the chosen teams and people Can use on the app. Returns plain warnings."""
    warnings = []
    target = {"app_name": app_name, "id": app_agents.key(app_name)}
    for s in share or []:
        kind = (s or {}).get("kind")
        principal = str((s or {}).get("principal") or "").strip()
        if kind not in ("user", "group") or not principal:
            continue
        try:
            access.set_grant(target, kind, principal, "CAN_QUERY", app_token())
        except DbxError as exc:
            warnings.append("Could not give %s access: %s" % (principal, str(exc)[:160]))
    return warnings


@app.post("/api/admin/app-agents")
def app_agent_add(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Offer an app as an agent: share it with the portal, check this admin manages it, record
    it, and give the chosen people access. The app itself is not changed otherwise."""
    who, tok = _require_admin(x_forwarded_access_token)
    name = (payload.get("app_name") or "").strip()
    events.note(action="created_assistant", target=app_agents.key(name), label=payload.get("display_name"),
                detail={"type": "Custom agent"})
    if not app_agents.enabled():
        raise HTTPException(503, app_agents.OFF)
    info = app_agents.check(name, tok)
    if not info["found"]:
        raise HTTPException(404, info["message"])
    problem = app_agents.share_with_portal(name, tok)
    if problem:
        raise HTTPException(403, problem)
    if not app_agents.may_manage(name, who):
        raise HTTPException(403, "You need Can Manage on the app %s in Databricks to offer it here. Ask its owner." % name)
    values = {k: payload.get(k) for k in app_agents.FIELDS if k in payload}
    row = app_agents.save(name, values, who["user_name"])
    warnings = _app_agent_access(name, payload.get("share") or [])
    if not info["running"]:
        warnings.append(info["message"])
    return {"agent": app_agents.shape(row, app_agents.app_info(name)), "warnings": warnings}


@app.put("/api/admin/app-agents/{name}")
def app_agent_edit(name: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    events.note(action="edited_assistant", target=app_agents.key(name), label=payload.get("display_name"),
                detail={"type": "Custom agent"})
    values = {k: payload.get(k) for k in app_agents.FIELDS if k in payload}
    return _app_agent_meta(who, app_agents.key(name), values)


@app.delete("/api/admin/app-agents/{name}")
def app_agent_remove(name: str, x_forwarded_access_token: str = Header(None)):
    """Stop offering it here. The app and who may use it in Databricks are left as they are."""
    who, _ = _require_admin(x_forwarded_access_token)
    row = app_agents.find(name)
    events.note(action="deleted_assistant", target=app_agents.key(name), label=(row or {}).get("display_name"),
                detail={"type": "Custom agent"})
    if not row:
        raise HTTPException(404, "no such agent")
    # A deleted or never-shared app can still be taken off the list; otherwise only by a manager.
    if app_agents.app_info(name) is not None and not app_agents.may_manage(name, who):
        raise HTTPException(403, "You need Can Manage on this app in Databricks to remove it. Ask its owner.")
    app_agents.remove(name)
    return {"removed": name}


# --------------------------------------------------------------- custom agents (run in the portal)


def _custom_or_404(agent_id: str) -> dict:
    if not custom_agents.enabled():
        raise HTTPException(503, custom_agents.OFF)
    if not re.match(r"^[0-9a-f]{32}$", agent_id or ""):
        raise HTTPException(404, "There is no such custom agent.")
    return custom_agents.summary(agent_id)


def _custom_detail(agent_id: str, tok: str = "") -> dict:
    out = _custom_or_404(agent_id)
    last = custom_agents.latest(agent_id)
    out["spec"] = dict(last["spec"], policy=custom_agents.policy_of(last["spec"]))
    out["meta"] = last["meta"]
    test = last["meta"].get("test") or {}
    out["test"] = custom_agents.test_run(agent_id, test["id"]) if test.get("id") else None
    cur = custom_agents.live(agent_id)
    out["live_spec"] = cur["spec"] if cur and cur["version"] != last["version"] else None
    out["key"] = custom_agents.key(agent_id, last["version"])
    try:
        out["usage"] = custom_agents.usage_summary(cur or last, tok)
    except DbxError:
        out["usage"] = None  # the page says usage could not be read; nothing else depends on it
    return out


def _prepare_tools(spec: dict, tok: str) -> list:
    """Let the portal call the chosen tool apps when a person's own sign-in is refused by them (as the admin)."""
    warnings = []
    for t in spec["tools"]:
        try:
            tooltest._let_portal_use(t["app"], tok)
        except DbxError as exc:
            warnings.append("The portal could not be given access to %s: %s" % (t["name"], str(exc)[:120]))
    return warnings


@app.get("/api/admin/custom-agents")
def custom_list(x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    if not custom_agents.enabled():
        return {"enabled": False, "reason": custom_agents.OFF, "agents": []}
    return {"enabled": True, "agents": [custom_agents.summary(a) for a in custom_agents.all_agents(fresh=True)]}


@app.get("/api/admin/custom-agents/options")
def custom_options(x_forwarded_access_token: str = Header(None)):
    """What a custom agent can be given: models this admin may use, running tools, data assistants
    and other assistants. Each list says why it is empty instead of failing the page."""
    who, tok = _require_admin(x_forwarded_access_token)
    out: dict = {"notes": {}}
    out["models"] = [m for m in designer_models.catalog(tok)]
    out["residency"] = safety.residency(tok)
    try:
        tools = []
        for e in mcps.listing(tok)["mcps"]:
            running = e["state"] == "running"
            tools.append({"app": e["app_name"], "name": e["name"], "description": e.get("description") or "",
                          "running": running, "starting": e["state"] == "deploying",
                          "needs": [] if running else mcps.missing_secrets(e, tok),
                          "note": e.get("problem") or e.get("state_note") or ""})
        for e in mcps.installed(tok):
            tools.append({"app": e["name"], "name": e["name"], "description": e["description"],
                          "running": e["state"] == "running", "starting": e["state"] == "deploying",
                          "needs": [], "note": e.get("note") or ""})
        out["tools"] = tools
    except DbxError as exc:
        out["tools"], out["notes"]["tools"] = [], "The tools could not be listed: " + str(exc)[:160]
    try:
        out["data"] = genie.list_spaces(tok)
    except DbxError as exc:
        out["data"], out["notes"]["data"] = [], "The data assistants could not be listed: " + str(exc)[:160]
    out["assistants"] = [{"key": a["name"], "name": a["display_name"], "kind": a.get("kind_label") or ""}
                         for a in access.visible_agents(who, app_token(), tok) if not a.get("preview")]
    return out


@app.post("/api/admin/custom-agents/runbook")
async def custom_runbook(file: UploadFile = File(...), x_forwarded_access_token: str = Header(None)):
    """Read a runbook file (PDF, Word, PowerPoint, text) into text the builder can check and edit.
    Nothing is stored here: the text goes back to the page and is saved with the agent's version."""
    _require_admin(x_forwarded_access_token)
    name = (file.filename or "runbook").strip()[:120]
    events.note(action="attached_file", label=name, detail={"type": "Custom agent runbook"})
    blob = await file.read()
    if len(blob) > attachments.MAX_PARSE:
        raise HTTPException(400, "That file is too large for a runbook (25 MB at most).")
    ext = attachments.kind_of(name)
    if ext not in ("pdf", "docx", "pptx", "txt", "md", "html", "htm", "json", "yaml", "yml", "xml"):
        raise HTTPException(400, "A runbook can be a PDF, Word, PowerPoint or text file.")
    if attachments.looks_like_key_file(blob):
        raise HTTPException(400, "That file looks like a key or password, so it is not read. Keys are connected separately.")
    text, _ = custom_agents.text_of(ext, blob)
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "No text could be read from that file (a scanned PDF has none). Paste the text instead.")
    text, removed = attachments.redact(text[: custom_agents.RUNBOOK_MAX])
    return {"name": name, "text": text, "chars": len(text), "removed_keys": removed,
            "searched": len(text) > custom_agents.RUNBOOK_INLINE}


@app.post("/api/admin/custom-agents/suggest-tests")
def custom_suggest_tests(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Test conversations written by the chosen model from the agent as it stands (saved or not)."""
    _, tok = _require_admin(x_forwarded_access_token)
    return custom_agents.suggest_tests(payload.get("spec") or {}, tok, payload.get("models"))


@app.post("/api/admin/custom-agents/draft")
def custom_draft(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """A first draft (name, description, instructions, rules, tests) written from the runbook."""
    _, tok = _require_admin(x_forwarded_access_token)
    return custom_agents.draft_from_runbook(str(payload.get("text") or ""), tok, payload.get("models"))


@app.post("/api/admin/custom-agents/tools/start")
def custom_tool_start(background: BackgroundTasks, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Get a tool ready from the custom agent wizard: install or start it, or say which keys it needs first."""
    who, tok = _require_admin(x_forwarded_access_token)
    name = str(payload.get("app") or "").strip()
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,62}$", name):
        raise HTTPException(400, "That is not a tool.")
    events.note(action="changed_settings", target=name, label=name, detail={"fields": ["started tool"]})
    entry = next((e for e in mcps.catalog()[0] if e["app_name"] == name), None)
    if entry:
        missing = mcps.missing_secrets(entry, tok)
        if missing:
            return {"starting": False, "needs": [{
                "name": m, "label": m, "kind": "key_file",
                "what_for": "%s needs this to work. It is kept in the workspace's secret store and never shown to an AI model." % entry["name"],
                "how_to_get": "Whoever manages that service (for Google, the service account's JSON key) can give it to you.",
            } for m in missing]}
        for e in mcps.prepare([entry["slug"]], tok):
            background.add_task(mcps.install_quietly, e, who, tok)
        return {"starting": True}
    # A tool made outside the catalog (for example by the assistant designer): switch its app on.
    builder.act("POST", "/api/2.0/apps/%s/start" % name, tok, quiet=True)
    return {"starting": True}


def _safety_view(agent_id: str, tok: str) -> dict:
    rec = custom_agents.latest(agent_id)
    test = (rec["meta"].get("test") or {})
    safety_results = []
    if test.get("id"):
        try:
            safety_results = [r for r in custom_agents.test_run(agent_id, test["id"])["results"] if r.get("safety")]
        except DbxError:
            safety_results = []
    cur = custom_agents.live(agent_id) or rec
    price, note = safety.usd_per_dbu(rec["spec"]["model"], tok)
    return {"guardrails": custom_agents.guardrails(rec["spec"]["model"], tok),
            "policy": custom_agents.policy_of(rec["spec"]),
            "live_policy": custom_agents.policy_of(cur["spec"]) if cur["version"] != rec["version"] else None,
            "price": {"usd_per_dbu": price, "note": note},
            "residency": safety.residency(tok),
            "ask_before_changes": rec["spec"].get("ask_before_changes", True),
            "tests": {"total": len(safety_results), "good": sum(1 for r in safety_results if r["verdict"] == "pass" or r.get("accepted")),
                      "run": bool(test.get("id")), "full": test.get("full", True)}}


@app.get("/api/admin/custom-agents/{agent_id}/safety")
def custom_safety(agent_id: str, x_forwarded_access_token: str = Header(None)):
    """Everything that keeps this agent safe, for the safety card: Databricks' guardrails on its model, the
    portal's own layer, the optional reviewer and the last safety test results."""
    _, tok = _require_admin(x_forwarded_access_token)
    _custom_or_404(agent_id)
    return _safety_view(agent_id, tok)


@app.get("/api/approvals/{agent_id}/safety")
def approval_safety(agent_id: str, x_forwarded_access_token: str = Header(None)):
    """The same card for the approvers it was sent to (read only)."""
    who, tok = _who(x_forwarded_access_token)
    if not any(i["id"] == agent_id for i in custom_agents.waiting_for(who)):
        raise HTTPException(403, "You were not asked to approve this agent, or it has already been decided.")
    out = _safety_view(agent_id, tok)
    out["guardrails"]["can_change"] = False
    return out


@app.post("/api/admin/custom-agents/guardrails")
def custom_guardrails(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Switch Databricks' AI Gateway safety filter (and personal-data handling) on a model endpoint.
    As the admin; it affects everyone in the workspace who uses that model, which the page says first."""
    _, tok = _require_admin(x_forwarded_access_token)
    model = str(payload.get("model") or "").strip()
    if not custom_agents.MODEL_RE.match(model):
        raise HTTPException(400, "That is not a model.")
    events.note(action="changed_settings", target=model, label=model, detail={"fields": ["model guardrails"]})
    try:
        return custom_agents.set_guardrails(model, bool(payload.get("safety", True)), str(payload.get("pii") or "NONE").upper(), tok)
    except DbxError as exc:
        raise HTTPException(exc.status if exc.status < 500 else 502,
                            "Databricks did not allow the change: " + str(exc)[:300])


@app.put("/api/admin/custom-agents/{agent_id}/usage-limits")
def custom_usage_limits(agent_id: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Requests, tokens, cost and questions per person: how much it may be used, so no new test or approval."""
    _require_admin(x_forwarded_access_token)
    s = _custom_or_404(agent_id)
    events.note(action="changed_settings", target=custom_agents.key(agent_id), label=s["name"], detail={"fields": ["usage limits"]})
    custom_agents.set_usage_limits(agent_id, payload.get("usage") or {})
    return _custom_detail(agent_id)


@app.get("/api/admin/safety-policies")
def safety_policies(x_forwarded_access_token: str = Header(None)):
    """The two starting points and the policies admins saved to reuse."""
    _require_admin(x_forwarded_access_token)
    try:
        mine, note = safety.saved(), ""
    except DbxError as exc:
        mine, note = [], "Saved policies could not be read: " + str(exc)[:160]
    return {"presets": [{"id": k, "name": v["name"], "settings": v} for k, v in safety.PRESETS.items()],
            "saved": mine, "can_save": safety.enabled(), "note": note}


@app.post("/api/admin/safety-policies")
def safety_policy_save(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    out = safety.save(payload.get("settings") or {}, who.get("user_name") or "", str(payload.get("id") or ""))
    events.note(action="changed_settings", target="safety-policy:" + out["id"], label=out["name"], detail={"fields": ["safety policy"]})
    return out


@app.delete("/api/admin/safety-policies/{policy_id}")
def safety_policy_delete(policy_id: str, x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    safety.remove(policy_id)
    events.note(action="changed_settings", target="safety-policy:" + policy_id, label="safety policy", detail={"fields": ["deleted"]})
    return {"ok": True}


@app.post("/api/admin/custom-agents/{agent_id}/restore")
def custom_restore(agent_id: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    s = _custom_or_404(agent_id)
    version = int(payload.get("version") or 0)
    events.note(action="restored_version", target=custom_agents.key(agent_id), label=s["name"], detail={"version": version})
    custom_agents.restore(agent_id, version, who)
    return _custom_detail(agent_id)


@app.get("/api/admin/custom-agents/{agent_id}")
def custom_get(agent_id: str, x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    return _custom_detail(agent_id, tok)


@app.post("/api/admin/custom-agents")
def custom_create(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, tok = _require_admin(x_forwarded_access_token)
    spec = custom_agents.clean(payload)
    events.note(action="created_assistant", label=spec["name"], detail={"type": "Custom agent"})
    if not custom_agents.enabled():
        raise HTTPException(503, custom_agents.OFF)
    problems = custom_agents.verify(spec, tok, who)
    if problems:
        raise HTTPException(400, " ".join(problems))
    warnings = _prepare_tools(spec, tok)
    rec = custom_agents.create(spec, who)
    events.note(target=custom_agents.key(rec["agent_id"]))
    custom_agents.start_reading(rec, tok)
    return dict(_custom_detail(rec["agent_id"]), warnings=warnings)


@app.put("/api/admin/custom-agents/{agent_id}")
def custom_save(agent_id: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, tok = _require_admin(x_forwarded_access_token)
    _custom_or_404(agent_id)
    spec = custom_agents.clean(payload)
    events.note(action="edited_assistant", target=custom_agents.key(agent_id), label=spec["name"], detail={"type": "Custom agent"})
    problems = custom_agents.verify(spec, tok, who, own_key=custom_agents.key(agent_id))
    if problems:
        raise HTTPException(400, " ".join(problems))
    warnings = _prepare_tools(spec, tok)
    rec, what = custom_agents.save(agent_id, spec, who)
    custom_agents.start_reading(rec, tok)
    return dict(_custom_detail(agent_id), warnings=warnings, what=what)


@app.put("/api/admin/custom-agents/{agent_id}/audience")
def custom_audience(agent_id: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    s = _custom_or_404(agent_id)
    aud = payload.get("audience") or []
    events.note(action="changed_settings", target=custom_agents.key(agent_id), label=s["name"], detail={"fields": ["audience"]})
    custom_agents.set_audience(agent_id, aud)
    return _custom_detail(agent_id)


@app.get("/api/admin/custom-agents/{agent_id}/tests/estimate")
def custom_test_estimate(agent_id: str, x_forwarded_access_token: str = Header(None)):
    """Roughly what a quick and a full test run will cost, shown before either is started."""
    _require_admin(x_forwarded_access_token)
    _custom_or_404(agent_id)
    rec = custom_agents.latest(agent_id)
    _, tok = _require_admin(x_forwarded_access_token)
    return {"quick": custom_agents.estimate(rec, False), "full": custom_agents.estimate(rec, True),
            "judge": custom_agents.suggested_judge(rec["spec"]["model"], tok)}


@app.post("/api/admin/custom-agents/{agent_id}/tests")
def custom_test(agent_id: str, payload: dict = Body(default={}), x_forwarded_access_token: str = Header(None)):
    who, tok = _require_admin(x_forwarded_access_token)
    _custom_or_404(agent_id)
    rec = custom_agents.latest(agent_id)
    if rec["status"] != "draft":
        raise HTTPException(409, "Only a draft is tested. Change the agent to make a new draft.")
    if (rec["meta"].get("docs") or {}).get("state") == "reading":
        raise HTTPException(409, "The documents are still being read. Try again when that is done.")
    if len(rec["spec"]["tests"]) < custom_agents.MIN_TESTS:
        raise HTTPException(400, "Add at least %d test questions first." % custom_agents.MIN_TESTS)
    judge = str((payload or {}).get("judge") or "").strip()
    if judge:
        # The builder chose who checks the answers; it must be a model they may use (Databricks decides).
        if not custom_agents.MODEL_RE.match(judge) or judge not in {m["name"] for m in designer_models.catalog(tok)}:
            raise HTTPException(400, "That checker model is not one you can use. Choose another.")
        if judge == rec["spec"]["model"]:
            raise HTTPException(400, "Choose a different model to check the answers than the one the agent uses.")
    failed_only = bool((payload or {}).get("failed_only"))
    if failed_only:
        t = rec["meta"].get("test") or {}
        if not t.get("id") or t.get("checksum") != custom_agents.checksum(rec["spec"]):
            raise HTTPException(409, "The agent changed after the last test, so run the whole test again.")
        if t.get("passed"):
            raise HTTPException(409, "Nothing failed in the last test.")
    return {"job": custom_agents.start_tests(rec, tok, who, full=(payload or {}).get("full", True) is not False, judge=judge,
                                             failed_only=failed_only)}


@app.get("/api/admin/custom-agents/jobs/{job_id}")
def custom_job(job_id: str, x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    j = custom_agents.job(job_id, who)
    if j["state"] == "done" and not j.get("noted"):
        r = j.get("result") or {}
        custom_agents._jobs[job_id]["noted"] = True
        name = custom_agents.latest(j["agent_id"])["spec"]["name"]
        events.note(action="tested_assistant", target=custom_agents.key(j["agent_id"]), label=name,
                    detail={"total": r.get("total"), "good": r.get("good"), "passed": r.get("passed")})
    return j


@app.post("/api/admin/custom-agents/{agent_id}/tests/{test_id}/accept")
def custom_accept(agent_id: str, test_id: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    _custom_or_404(agent_id)
    return custom_agents.accept(agent_id, test_id, int(payload.get("index", -1)), str(payload.get("note") or ""), who)


@app.post("/api/admin/custom-agents/{agent_id}/submit")
def custom_submit(agent_id: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    s = _custom_or_404(agent_id)
    approvers = [str(a) for a in (payload.get("approvers") or []) if a]
    events.note(action="sent_for_approval", target=custom_agents.key(agent_id), label=s["name"], detail={"approvers": approvers[:10]})
    try:
        people = {u["name"] for u in access.principals(app_token())["users"]}
    except DbxError:
        people = set()  # the list could not be read; names are still checked when someone tries to approve
    custom_agents.submit(agent_id, approvers, str(payload.get("note") or ""), who, people)
    return _custom_detail(agent_id)


@app.post("/api/admin/custom-agents/{agent_id}/retire")
def custom_retire(agent_id: str, x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    s = _custom_or_404(agent_id)
    events.note(action="retired_assistant", target=custom_agents.key(agent_id), label=s["name"])
    custom_agents.retire(agent_id, who)
    return _custom_detail(agent_id)


@app.delete("/api/admin/custom-agents/{agent_id}")
def custom_delete(agent_id: str, x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    s = _custom_or_404(agent_id)
    events.note(action="deleted_assistant", target=custom_agents.key(agent_id), label=s["name"], detail={"type": "Custom agent"})
    custom_agents.delete(agent_id)
    return {"deleted": agent_id}


@app.get("/api/custom-agents/memory")
def my_memory(key: str, x_forwarded_access_token: str = Header(None)):
    """What a custom agent remembers about the caller (never anyone else: the user comes from their token)."""
    who, _ = _who(x_forwarded_access_token)
    rec = custom_agents.resolve(key, who)
    if not (rec["spec"].get("memory") or {}).get("on"):
        return {"on": False, "notes": []}
    return {"on": True, "notes": memory.notes(rec["agent_id"], who.get("user_name") or "")}


@app.delete("/api/custom-agents/memory")
def my_memory_delete(key: str, note: str = "", x_forwarded_access_token: str = Header(None)):
    """Forget one note (`note` = its id) or all of them (no `note`)."""
    who, _ = _who(x_forwarded_access_token)
    rec = custom_agents.resolve(key, who)
    if note and not re.match(r"^[0-9a-f]{32}$", note):
        raise HTTPException(400, "That is not a note.")
    n = memory.forget(rec["agent_id"], who.get("user_name") or "", note_id=note, text="" if note else "*")
    events.note(action="forgot_memory", target=custom_agents.key(rec["agent_id"]), label=rec["spec"]["name"], detail={"notes": n})
    return {"forgotten": n}


@app.get("/api/approvals")
def approvals(x_forwarded_access_token: str = Header(None)):
    """Agents waiting for this person's approval. Anyone may be chosen as an approver, not only admins."""
    who, _ = _who(x_forwarded_access_token)
    if not custom_agents.enabled():
        return {"items": []}
    return {"items": custom_agents.waiting_for(who)}


@app.get("/api/approvals/{agent_id}")
def approval_detail(agent_id: str, x_forwarded_access_token: str = Header(None)):
    who, _ = _who(x_forwarded_access_token)
    if not any(i["id"] == agent_id for i in custom_agents.waiting_for(who)):
        raise HTTPException(403, "You were not asked to approve this agent, or it has already been decided.")
    return _custom_detail(agent_id)


@app.post("/api/approvals/{agent_id}")
def approval_decide(agent_id: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _who(x_forwarded_access_token)
    approve = bool(payload.get("approve"))
    s = _custom_or_404(agent_id)
    events.note(action="approved_release" if approve else "sent_back", target=custom_agents.key(agent_id), label=s["name"],
                detail={"version": s["latest"]["version"]})
    custom_agents.decide(agent_id, approve, str(payload.get("note") or ""), who)
    return _custom_detail(agent_id) if who.get("is_admin") else {"decided": True, "approved": approve}


@app.get("/api/admin/builder/types")
def builder_types(x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    return {"types": builder.tool_types()}


@app.get("/api/admin/builder/sources")
def builder_sources(
    kind: str,
    catalog: str = "",
    schema: str = "",
    x_forwarded_access_token: str = Header(None),
):
    _, tok = _require_admin(x_forwarded_access_token)
    return builder.sources(kind, tok, catalog, schema)


def _folder_access(tools: list, volumes: dict, tok: str) -> list:
    """Bring the folder access of the assistant's running catalog tools up to date,
    before the assistant is handed back so the first chat cannot beat it. Never
    fails the save: what could not be granted comes back as warnings."""
    if not (volumes["read"] or volumes["write"]):
        return []
    try:
        ready = mcps.users_of_volumes(tools)
        return mcps.grant_volumes(ready, volumes, tok) if ready else []
    except DbxError as exc:
        return ["Could not give the tools access to the folders: " + str(exc)[:160]]


@app.get("/api/admin/builder/mcps")
def mcps_list(refresh: bool = False, x_forwarded_access_token: str = Header(None)):
    """The tool catalog from git, with what is already deployed as a Databricks App."""
    _, tok = _require_admin(x_forwarded_access_token)
    return mcps.listing(tok, refresh)


@app.post("/api/admin/builder/mcps/prepare")
def mcps_prepare(
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    """Start getting ticked tools ready. The wizard waits for them (it watches the
    list above) and only then creates the assistant."""
    who, tok = _require_admin(x_forwarded_access_token)
    slugs = [str(s) for s in (payload.get("slugs") or []) if s]
    if not slugs or len(slugs) > 20:
        raise HTTPException(400, "choose between 1 and 20 tools")
    started = mcps.prepare(slugs, tok)
    for entry in started:
        background.add_task(mcps.install_quietly, entry, who, tok)
    return {"preparing": [e["name"] for e in started]}


@app.get("/api/admin/builder/principals")
def builder_principals(x_forwarded_access_token: str = Header(None)):
    """The teams and people that can be given access: the same lists the Manage
    access screen offers."""
    _require_admin(x_forwarded_access_token)
    return access.principals(app_token())


@app.get("/api/admin/builder/agents")
def builder_list(x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    return builder.list_agents(tok)


@app.get("/api/admin/builder/agents/{agent_id}")
def builder_get(agent_id: str, x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    return builder.get_agent(agent_id, tok)


@app.post("/api/admin/builder/agents")
def builder_create(
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    who, tok = _require_admin(x_forwarded_access_token)
    return _create_supervisor(builder.clean_spec(payload), who, tok, background)


def _create_supervisor(spec: dict, who: dict, tok: str, background: BackgroundTasks, via: str = "") -> dict:
    """Create a Supervisor Agent from a validated spec. Shared by the wizard and the
    assistant designer, so both follow the same rules."""
    events.note(action="created_assistant", label=spec.get("display_name"))
    # Catalog tools whose app is not running yet are installed after the agent
    # exists and added to it when ready; the rest are attached now.
    spec["tools"], pending = mcps.plan(spec["tools"], tok, derive=True)
    try:
        out = builder.create_agent(spec, who, tok)
    except DbxError as exc:
        # Databricks says an app we meant to attach is not there. Whatever made us
        # think it was is wrong, so install those instead and try once more.
        if exc.status != 404 or "does not exist" not in str(exc):
            raise
        spec["tools"], more = mcps.defer(spec["tools"])
        if not more:
            raise
        pending += more
        out = builder.create_agent(spec, who, tok)
    deploying = [p["entry"]["name"] for p in pending]
    events.note(action="created_assistant", label=spec.get("display_name"), detail={"type": "Combines tools", "tools": len(spec.get("tools") or []), "deploying": deploying, **({"via": via} if via else {})})
    events.note(target=out.get("endpoint_name") or out.get("agent_id"))
    volumes = mcps.volumes_for(spec)
    if pending:
        background.add_task(mcps.deploy_and_attach, out["agent_id"], pending, who, tok, volumes)
    # Tools that are already running only need their folder access brought up to date.
    out["warnings"] = list(out.get("warnings") or []) + _folder_access(spec["tools"], volumes, tok)
    out["deploying"] = deploying
    # The serving endpoint appears a few minutes later; tag and share it then.
    background.add_task(builder.finish_provisioning, out["agent_id"], out["endpoint_name"], spec, who)
    return out


@app.put("/api/admin/builder/agents/{agent_id}")
def builder_update(
    agent_id: str,
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    who, tok = _require_admin(x_forwarded_access_token)
    spec = builder.clean_spec(payload)
    spec["tools"], pending = mcps.plan(spec["tools"], tok)
    deploying = [p["entry"]["name"] for p in pending]
    events.note(action="edited_assistant", target=agent_id, label=spec.get("display_name"), detail={"type": "Combines tools", "deploying": deploying})
    out = builder.update_agent(agent_id, spec, who, tok)
    # Folder access follows the assistant's current folders, for tools attached earlier too.
    # (spec["tools"] holds the tools to attach now; ones already on the assistant are
    # recognised by their app name.)
    volumes = mcps.volumes_for(spec)
    if pending:
        background.add_task(mcps.deploy_and_attach, agent_id, pending, who, tok, volumes)
    out["warnings"] = list(out.get("warnings") or []) + _folder_access(mcps.as_catalog_tools(spec["tools"]), volumes, tok)
    out["deploying"] = deploying
    return out


@app.delete("/api/admin/builder/agents/{agent_id}")
def builder_delete(agent_id: str, x_forwarded_access_token: str = Header(None)):
    who, tok = _require_admin(x_forwarded_access_token)
    events.note(action="deleted_assistant", target=agent_id, detail={"type": "Combines tools"})
    return builder.delete_agent(agent_id, who, tok)


@app.get("/api/admin/builder/genie")
def genie_list(x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    return {"spaces": genie.list_spaces(tok)}


@app.get("/api/admin/builder/genie/{space_id}")
def genie_get(space_id: str, x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    return genie.get_space(space_id, tok)


@app.post("/api/admin/builder/genie")
def genie_create(
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    who, tok = _require_admin(x_forwarded_access_token)
    return _create_genie(genie.clean(payload), who, tok, background)


def _create_genie(gspec: dict, who: dict, tok: str, background: BackgroundTasks, via: str = "") -> dict:
    events.note(action="created_assistant", label=gspec.get("title"), detail={"type": "Answers from data", **({"via": via} if via else {})})
    out = genie.create(gspec, who, tok)
    events.note(target=out.get("space_id"))
    chat_ = out.pop("_chat", None)
    if chat_:
        # Same follow-up as any supervisor: tag and share its endpoint once it exists.
        background.add_task(builder.finish_provisioning, chat_["agent_id"], chat_["endpoint_name"], chat_["spec"], who)
        out["endpoint_name"] = chat_["endpoint_name"]
    return out


@app.put("/api/admin/builder/genie/{space_id}")
def genie_update(space_id: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    gspec = genie.clean(payload)
    events.note(action="edited_assistant", target=space_id, label=gspec.get("title"), detail={"type": "Answers from data"})
    return genie.update_space(space_id, gspec, tok)


@app.delete("/api/admin/builder/genie/{space_id}")
def genie_delete(space_id: str, x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    events.note(action="deleted_assistant", target=space_id, detail={"type": "Answers from data"})
    return genie.delete_space(space_id, tok)


def _names(tok: str) -> dict:
    """Endpoint name -> the friendly name people know it by (cosmetic only)."""
    try:
        names = {a["name"]: a["display_name"] for a in access.all_agents(tok)}
    except DbxError:
        names = {}
    try:
        names.update({app_agents.key(r["app_name"]): r["display_name"] for r in app_agents.registered()})
    except DbxError:
        pass
    try:
        for agent_id, vs in custom_agents.all_agents().items():
            names[custom_agents.key(agent_id)] = vs[-1]["spec"]["name"]
            for r in vs:
                names[custom_agents.key(agent_id, r["version"])] = "%s (version %d)" % (r["spec"]["name"], r["version"])
    except DbxError:
        pass
    return names


@app.get("/api/dashboard/me")
def dashboard_me(days: int = 30, x_forwarded_access_token: str = Header(None)):
    """The signed-in person's own activity. The name comes from their token, never
    from the request, so nobody can ask for someone else's."""
    who, _ = _who(x_forwarded_access_token)
    return dashboard.activity(who["user_name"], days, _names(app_token()))


@app.get("/api/dashboard/me/insights")
def dashboard_me_insights(days: int = 30, x_forwarded_access_token: str = Header(None)):
    """When the signed-in person uses the assistants, and their files. Own data only:
    the name comes from their token, never from the request."""
    who, _ = _who(x_forwarded_access_token)
    return dashboard.insights(who["user_name"], days, _names(app_token()))


@app.get("/api/admin/dashboard/overview")
def dashboard_overview(days: int = 30, x_forwarded_access_token: str = Header(None)):
    """Adoption across the portal: active and new people, use per assistant. Counts only."""
    _require_admin(x_forwarded_access_token)
    return dashboard.org(days, _names(app_token()))


@app.get("/api/admin/dashboard/people")
def dashboard_people(days: int = 30, x_forwarded_access_token: str = Header(None)):
    _require_admin(x_forwarded_access_token)
    return dashboard.everyone(days)


@app.get("/api/admin/dashboard/person")
def dashboard_person(user: str, days: int = 30, x_forwarded_access_token: str = Header(None)):
    """Counts for one person, for admins. Never their questions or chat titles."""
    _require_admin(x_forwarded_access_token)
    if not user.strip() or len(user) > 200:
        raise HTTPException(400, "user is required")
    return dashboard.activity(user.strip(), days, _names(app_token()))


@app.get("/api/admin/dashboard/costs")
def dashboard_costs(days: int = 30, x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    return dashboard.costs(days, tok)


@app.get("/api/admin/builder/knowledge")
def knowledge_list(x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    return {"assistants": knowledge.list_assistants(tok)}


@app.get("/api/admin/builder/knowledge/{ka_id}")
def knowledge_get(ka_id: str, x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    return knowledge.get_assistant(ka_id, tok)


@app.post("/api/admin/builder/knowledge")
def knowledge_create(
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    who, tok = _require_admin(x_forwarded_access_token)
    return _create_knowledge(knowledge.clean(payload), who, tok, background)


def _create_knowledge(spec: dict, who: dict, tok: str, background: BackgroundTasks, via: str = "") -> dict:
    events.note(action="created_assistant", label=spec.get("display_name"), detail={"type": "Answers from documents", "folders": len(spec.get("sources") or []), **({"via": via} if via else {})})
    out = knowledge.create(spec, who, tok)
    events.note(target=out.get("ka_id"))
    # Reading the documents takes minutes; tag and share the endpoint when it exists.
    background.add_task(knowledge.finish, out["ka_id"], spec, who)
    return out


@app.put("/api/admin/builder/knowledge/{ka_id}")
def knowledge_update(ka_id: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    # Editing needs only the text fields; documents are changed in Databricks.
    spec = {
        "display_name": (payload.get("display_name") or "").strip(),
        "description": (payload.get("description") or "").strip(),
        "instructions": (payload.get("instructions") or "").strip(),
    }
    events.note(action="edited_assistant", target=ka_id, label=spec["display_name"] or None, detail={"type": "Answers from documents"})
    if not spec["display_name"] or not spec["description"]:
        raise HTTPException(400, "a name and a description are required")
    return knowledge.update_assistant(ka_id, spec, tok)


@app.delete("/api/admin/builder/knowledge/{ka_id}")
def knowledge_delete(ka_id: str, x_forwarded_access_token: str = Header(None)):
    _, tok = _require_admin(x_forwarded_access_token)
    events.note(action="deleted_assistant", target=ka_id, detail={"type": "Answers from documents"})
    return knowledge.delete_assistant(ka_id, tok)


@app.get("/api/admin/designer/status")
def designer_status(x_forwarded_access_token: str = Header(None)):
    """Is the designer on, and which AI models can this admin choose from (read under their own token)."""
    who, tok = _require_admin(x_forwarded_access_token)
    out = designer.status(tok)
    if not designer_guard.may_use(who):
        out.update(ready=False, reason="Your admins have limited the assistant designer to the team %s." % designer_guard.settings()["group"])
    return out


@app.post("/api/admin/designer/files")
async def designer_attach(
    file: UploadFile = File(...),
    folder: str = Form(...),
    on_conflict: str = Form("ask"),
    x_forwarded_access_token: str = Header(None),
):
    """Attach a file while designing: saved to the folder the admin chose, as the admin
    (so Databricks decides where they may save). Key files are refused (use Connect)."""
    _, tok = _require_designer(x_forwarded_access_token)
    blob = await file.read()
    name = file.filename or "file"
    out = attachments.upload(folder, name, blob, tok, on_conflict if on_conflict in ("ask", "replace", "keep_both") else "ask")
    events.note(action="attached_file", label=out["name"], detail={"bytes": out["bytes"], "kind": out["kind"]})
    return out


@app.delete("/api/admin/designer/files")
def designer_detach(path: str, x_forwarded_access_token: str = Header(None)):
    """Remove a file attached a moment ago (before it was sent), as the admin."""
    _, tok = _require_designer(x_forwarded_access_token)
    attachments.remove(path, tok)
    return {"ok": True}


@app.get("/api/admin/designer/connections")
def designer_connections(names: str = "", x_forwarded_access_token: str = Header(None)):
    """Which of these connections are set. Names only; a value is never returned."""
    _, tok = _require_designer(x_forwarded_access_token)
    return {"connections": connections.status([n.strip() for n in names.split(",") if n.strip()][:20], tok)}


@app.put("/api/admin/designer/connections/{name}")
def designer_connect(name: str, payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Save a credential a tool needs into the workspace's secret store. The value goes
    from this request to Databricks and nowhere else: never logged, never returned,
    never shown to an AI model."""
    _, tok = _require_designer(x_forwarded_access_token)
    out = connections.save(name, payload.get("value") or "", str(payload.get("kind") or ""), tok, bool(payload.get("replace")))
    events.note(action="saved_connection", label=out["name"], detail={"replaced": bool(payload.get("replace"))})
    return out


@app.post("/api/admin/designer/turn")
def designer_turn(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """One exchange of the interview. The browser holds the conversation and the
    draft and sends both each time; nothing is kept here, and what was typed is
    never logged."""
    who, tok = _require_designer(x_forwarded_access_token)
    designer_guard.count_turn(who)
    out = designer.turn(payload.get("messages") or [], payload.get("draft") or {}, tok, payload.get("models"),
                        owner=who.get("user_name") or "")
    if out.get("refused"):
        # Recorded as a fact (off topic / against a rule), never what was typed.
        events.note(action="designer_refused", target="designer", label="Assistant designer", detail={"why": out["refused"]})
    return out


def _require_designer(forwarded):
    """An admin in the team the designer rules allow (designer_guard.py)."""
    who, tok = _require_admin(forwarded)
    if not designer_guard.may_use(who):
        raise HTTPException(403, "Your admins have limited the assistant designer to the team %s." % designer_guard.settings()["group"])
    return who, tok


def _require_developer(forwarded):
    """Developer mode (designer_guard.may_develop)."""
    who, tok = _require_admin(forwarded)
    if not designer_guard.may_develop(who):
        raise HTTPException(403, "Developer mode is off for you. An admin can turn it on for a team in Rules for the designer.")
    return who, tok


@app.post("/api/admin/custom-agents/{agent_id}/export")
def custom_export(agent_id: str, payload: dict = Body(default={}), x_forwarded_access_token: str = Header(None)):
    """A custom agent as a code project the developer owns (agent_export.py): MLflow ResponsesAgent or LangGraph.
    Built from the latest version's settings; nothing secret is in it."""
    who, tok = _require_developer(x_forwarded_access_token)
    _custom_or_404(agent_id)
    framework = str((payload or {}).get("framework") or "mlflow")
    if framework not in agent_export.FRAMEWORKS:
        raise HTTPException(400, "Choose mlflow or langgraph.")
    rec = custom_agents.latest(agent_id)
    urls = {}
    if rec["spec"].get("tools"):
        try:
            apps, _ = mcps._apps(tok)
            urls = {n: a.get("url") for n, a in apps.items() if a.get("url")}
        except DbxError as exc:
            log.warning("export: could not read the tool apps' addresses: %s", exc)
    data, name = agent_export.build(rec, framework, urls)
    events.note(action="exported_agent", target=custom_agents.key(agent_id), label=rec["spec"]["name"],
                detail={"framework": framework, "version": rec["version"]})
    return Response(data, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="%s"' % name})


@app.post("/api/admin/code-agents/upload")
async def code_agent_upload(
    file: UploadFile = File(...),
    display_name: str = Form(...),
    description: str = Form(""),
    app_name: str = Form(""),
    x_forwarded_access_token: str = Header(None),
):
    """Developer mode: deploy an uploaded project as a Databricks App and offer it as an assistant (code_upload.py).
    The portal checks the files and never runs them."""
    who, tok = _require_developer(x_forwarded_access_token)
    blob = await file.read()
    if len(blob) > code_upload.MAX_BYTES * 2:
        raise HTTPException(413, "The ZIP is too big.")
    out = code_upload.start(blob, display_name, description, app_name.strip(), who, tok)
    events.note(action="uploaded_agent", target="app:" + out["app_name"], label=display_name,
                detail={"files": out["files"], "new": out["new"]})
    return out


def _code_setup(payload: dict, tok: str) -> dict:
    setup = code_agent.clean_setup(payload.get("setup") or {})
    if setup["model"]:
        offered = {m["name"] for m in designer_models.catalog(tok)}
        if offered and setup["model"] not in offered:
            raise HTTPException(400, "That model is not one you can use. Choose another.")
    return setup


@app.post("/api/admin/code-agents/ai/write")
def code_agent_write(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Developer mode: the code model writes an agent's code from the developer's words (code_agent.py). Runs in
    the background; nothing is deployed or run. What they type is checked like the designer's chat."""
    who, tok = _require_developer(x_forwarded_access_token)
    setup = _code_setup(payload, tok)
    designer_guard.count_turn(who)
    _tool_words(setup["task"], "What should the agent you are writing code for do?", payload, tok)
    j = code_agent.start_write(setup, who, tok, payload.get("models"))
    return {"job": j["id"]}


@app.post("/api/admin/code-agents/ai/change")
def code_agent_change(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """A change asked in words, or failed tests to fix: edits to the code, shown again before anything is deployed."""
    who, tok = _require_developer(x_forwarded_access_token)
    setup = _code_setup(payload, tok)
    request = " ".join(str(payload.get("request") or "").split())[:1500]
    failures = [f for f in payload.get("failures") or [] if isinstance(f, dict)][:6]
    brief_before = str(payload.get("brief_before") or "")[:6000]
    brief_changed = bool(brief_before.strip()) and brief_before.strip() != setup["task"].strip()
    if not request and not failures and not brief_changed:
        raise HTTPException(400, "Say what should change, or which tests failed.")
    designer_guard.count_turn(who)
    if request:
        _tool_words(request, "What should change in the code of the agent %s?" % setup["name"], payload, tok)
    if brief_changed:
        _tool_words(setup["task"], "What should the agent you are writing code for do?", payload, tok)
    code = {k: str(v) for k, v in (payload.get("code") or {}).items() if isinstance(k, str)}
    j = code_agent.start_write(setup, who, tok, payload.get("models"), current=code_agent.editable(code), request=request,
                               failures=failures, brief_before=brief_before if brief_changed else "")
    return {"job": j["id"]}


@app.post("/api/admin/code-agents/ai/check")
def code_agent_check(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """The checks on the code as the developer edits it by hand (no model, nothing run): problems with their
    lines, what it can reach, the changes and the fingerprint to approve."""
    _, tok = _require_developer(x_forwarded_access_token)
    setup = _code_setup(payload, tok)
    code = code_agent.editable({k: str(v) for k, v in (payload.get("code") or {}).items() if isinstance(k, str)})
    prev = payload.get("previous")
    prev = code_agent.editable({k: str(v) for k, v in prev.items() if isinstance(k, str)}) if isinstance(prev, dict) else None
    return code_agent.check_code(setup, code, tok, prev)


@app.get("/api/admin/code-agents/sandbox")
def code_agent_sandbox(x_forwarded_access_token: str = Header(None)):
    """Is the try-out sandbox there and on (sandbox.py)?"""
    _, tok = _require_developer(x_forwarded_access_token)
    return sandbox.status(tok)


@app.post("/api/admin/code-agents/sandbox")
def code_agent_sandbox_setup(x_forwarded_access_token: str = Header(None)):
    """Set the sandbox up (once; it uses one of the workspace's apps) or switch it on."""
    who, tok = _require_developer(x_forwarded_access_token)
    out = sandbox.setup(who, tok)
    events.note(action="sandbox", target="app:" + sandbox.NAME, label="Try-out sandbox", detail={"state": out["state"]})
    return out


@app.post("/api/admin/code-agents/ai/try")
def code_agent_try(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Try the code as it is now (a hand edit) in the sandbox: the static checks first, then tests/checks.py."""
    _, tok = _require_developer(x_forwarded_access_token)
    setup = _code_setup(payload, tok)
    code = code_agent.editable({k: str(v) for k, v in (payload.get("code") or {}).items() if isinstance(k, str)})
    res = code_agent.check_code(setup, code, tok)
    if res["problems"]:
        return {"skipped": "Fix the problems the checks found first; then it can be tried.", "problems": res["problems"]}
    if code_agent.CHECKS not in code:
        return {"skipped": "There is no tests/checks.py to try."}
    return sandbox.run(code, tok)


@app.post("/api/admin/code-agents/ai/jobs/{job_id}/stop")
def code_agent_stop(job_id: str, x_forwarded_access_token: str = Header(None)):
    """Stop the writing (to change the brief); the model's stream is closed, so it stops spending tokens."""
    who, _ = _require_developer(x_forwarded_access_token)
    code_agent.stop(job_id, who)
    return {"ok": True}


@app.get("/api/admin/code-agents/ai/jobs/{job_id}")
def code_agent_job(job_id: str, x_forwarded_access_token: str = Header(None)):
    who, _ = _require_developer(x_forwarded_access_token)
    return code_agent.job(job_id, who)


@app.post("/api/admin/code-agents/ai/deploy")
def code_agent_deploy(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Deploy exactly the approved code (its fingerprint) as an app and offer it as an assistant; progress is the
    upload's own job (/api/admin/code-agents/jobs/{id})."""
    who, tok = _require_developer(x_forwarded_access_token)
    setup = _code_setup(payload, tok)
    code = code_agent.editable({k: str(v) for k, v in (payload.get("code") or {}).items() if isinstance(k, str)})
    out = code_agent.deploy(setup, code, payload.get("tests") or [], str(payload.get("approved") or ""),
                            str(payload.get("display_name") or ""), str(payload.get("app_name") or "").strip(), who, tok,
                            payload.get("models"))
    events.note(action="uploaded_agent", target="app:" + out["app_name"], label=setup["name"],
                detail={"files": out["files"], "new": out["new"], "via": "ai", "framework": setup["framework"]})
    return out


@app.post("/api/admin/code-agents/ai/test")
def code_agent_test(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Ask the deployed agent its tests as the developer and mark each answer with another model (background)."""
    who, tok = _require_developer(x_forwarded_access_token)
    tests = code_agent.clean_tests(payload.get("tests"))
    if not tests:
        raise HTTPException(400, "Add at least one test question with what a good answer does.")
    name = str(payload.get("app_name") or "")
    if not code_upload.APP_RE.match(name):
        raise HTTPException(400, "Which app?")
    j = code_agent.start_tests(name, tests, who, tok, payload.get("models"))
    return {"job": j["id"]}


@app.post("/api/admin/code-agents/ai/zip")
def code_agent_zip(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """The whole project as a ZIP, so the developer owns the code."""
    who, tok = _require_developer(x_forwarded_access_token)
    setup = _code_setup(payload, tok)
    code = code_agent.editable({k: str(v) for k, v in (payload.get("code") or {}).items() if isinstance(k, str)})
    files = code_agent.assemble(setup, code, tok, code_agent.clean_tests(payload.get("tests")))
    root = agent_export._slug(setup["name"]) + "-" + setup["framework"]
    events.note(action="exported_agent", target="code:" + root, label=setup["name"], detail={"framework": setup["framework"], "via": "ai"})
    return Response(agent_export.zip_of(files, root), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="%s.zip"' % root})


@app.get("/api/admin/code-agents/jobs/{job_id}")
def code_agent_job(job_id: str, x_forwarded_access_token: str = Header(None)):
    who, _ = _require_developer(x_forwarded_access_token)
    return code_upload.job(job_id, who)


@app.get("/api/admin/developer")
def developer_status(x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    return {"on": designer_guard.may_develop(who), "team": designer_guard.settings().get("developer_group") or ""}


@app.get("/api/admin/designer/tool-code")
def designer_tool_code(slug: str, x_forwarded_access_token: str = Header(None)):
    """The code of a tool the designer made, for a developer to edit (read from the workspace, never run here)."""
    _, tok = _require_developer(x_forwarded_access_token)
    item = forge.item_from_workspace(_tool_slug({"slug": slug}), tok)
    return {"slug": item["slug"], "name": item["name"], "code": item["code"], "abilities": item["abilities"],
            "fingerprint": item["fingerprint"]}


@app.post("/api/admin/designer/tool-code/check")
def designer_tool_code_check(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """A developer's edited code, checked as if new: problems, the diff against what is deployed, and the fingerprint
    to approve. Nothing is deployed here; the apply route does that with this exact fingerprint."""
    _, tok = _require_developer(x_forwarded_access_token)
    slug = _tool_slug(payload)
    current = forge.item_from_workspace(slug, tok)
    code = str(payload.get("code") or "")
    if len(code) > forge.MAX_CODE:
        raise HTTPException(400, "The code is too long.")
    new = forge.clean_item({**current, "code": code}) or current
    return {"problems": new["problems"], "diff": forge.code_diff(current["code"], code), "fingerprint": new["fingerprint"],
            "same": code.strip() == current["code"].strip()}


@app.get("/api/admin/designer/rules")
def designer_rules_get(x_forwarded_access_token: str = Header(None)):
    """What the designer may be used for and may build: set by admins, applied to every message and build."""
    _require_admin(x_forwarded_access_token)
    return {"rules": designer_guard.settings(), "can_save": safety.enabled()}


@app.put("/api/admin/designer/rules")
def designer_rules_put(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    who, _ = _require_admin(x_forwarded_access_token)
    out = designer_guard.save(payload.get("rules") or {}, who.get("user_name") or "")
    events.note(action="changed_settings", target="designer", label="Designer rules", detail={"fields": ["designer rules"]})
    return {"rules": out, "can_save": True}


@app.post("/api/admin/designer/draft-files")
def designer_draft_files(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """The side panel's Plan, Files and Changes: the draft as readable files, marked against the previous
    turn's draft. Made from what the browser sends; nothing is stored, no model is asked, nothing is run."""
    who, _ = _require_designer(x_forwarded_access_token)
    admin = who.get("user_name") or ""
    cur = designer_files.files(designer.clean_draft(payload.get("draft") or {}), admin)
    prev = designer_files.files(designer.clean_draft(payload["previous"]), admin) if isinstance(payload.get("previous"), dict) else []
    return {"files": designer_files.with_changes(cur, prev)}


@app.post("/api/admin/designer/draft-files/zip")
def designer_draft_zip(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """All of the draft's files in one ZIP, to keep or share."""
    who, _ = _require_designer(x_forwarded_access_token)
    d = designer.clean_draft(payload.get("draft") or {})
    body = designer_files.zip_of(designer_files.files(d, who.get("user_name") or ""), d.get("display_name") or "assistant")
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in (d.get("display_name") or "assistant")).strip("-") or "assistant"
    return Response(body, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="%s.zip"' % name})


@app.get("/api/admin/designer/jobs/{job_id}")
def designer_job(job_id: str, x_forwarded_access_token: str = Header(None)):
    """How a new tool being written in the background is going; when done, the tool itself."""
    who, _ = _require_designer(x_forwarded_access_token)
    return designer.job(job_id, who.get("user_name") or "")


@app.post("/api/admin/designer/build")
def designer_build(
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    """Approve: build the draft with the same code the wizards use. The draft came
    through the browser, so it is checked again here, whatever the model said."""
    who, tok = _require_designer(x_forwarded_access_token)
    if not designer.enabled():
        raise HTTPException(404, "The assistant designer is switched off.")
    draft = designer.clean_draft(payload.get("draft"))
    # Tools the designer proposed must already have been created (see tools/create).
    problems = (designer.check(draft) or designer.verify(draft, tok, created=True)
                or designer_guard.review_plan(draft, designer_guard.settings(), designer_models.resolve(payload.get("models"), tok)["chat"], tok))
    if problems:
        raise HTTPException(400, "It is not ready to build yet: " + " ".join(problems[:3]))
    kind = draft["kind"]
    if kind == "genie":
        out = _create_genie(genie.clean(designer.genie_payload(draft)), who, tok, background, via="designer")
    elif kind == "knowledge":
        out = _create_knowledge(knowledge.clean(designer.knowledge_payload(draft)), who, tok, background, via="designer")
    else:
        out = _create_supervisor(builder.clean_spec(designer.supervisor_payload(draft)), who, tok, background, via="designer")
    return {**out, "kind": kind, "name": draft["display_name"]}


@app.post("/api/admin/designer/tools/create")
def designer_tools_create(
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    """Create the new tools a draft proposes, after the admin has read them.

    `approved` must be the fingerprints of exactly the tools being created, as the
    admin saw them. The draft came through the browser, so everything is checked
    again here; functions are created now (as the admin), apps are installed in
    the background and watched through tools/status.
    """
    who, tok = _require_designer(x_forwarded_access_token)
    if not designer.enabled():
        raise HTTPException(404, "The assistant designer is switched off.")
    draft = designer.clean_draft(payload.get("draft"))
    news = draft.get("new_tools") or []
    if not news:
        raise HTTPException(400, "There are no new tools to create.")
    problems = designer.check(draft) or designer.verify(draft, tok)
    if problems:
        raise HTTPException(400, "A new tool is not ready: " + " ".join(problems[:3]))
    if {str(x) for x in payload.get("approved") or []} != {n["fingerprint"] for n in news}:
        raise HTTPException(400, "Read and approve every new tool as it is shown now. It changed since you approved it.")
    events.note(action="created_tool", label=", ".join(forge.key_of(n) for n in news),
                detail={"via": "designer", "tools": [{"kind": n["kind"], "key": forge.key_of(n), "fingerprint": n["fingerprint"][:12]} for n in news]})
    results: list = []
    for n in news:  # functions first and in the request, so a refusal stops everything before an app is started
        if n["kind"] != "uc_function":
            continue
        try:
            results.append({"key": n["name"], "kind": "uc_function", **forge.create_function(n, draft.get("warehouse_id", ""), tok)})
        except DbxError as exc:
            made = [r["key"] for r in results]
            results.append({"key": n["name"], "kind": "uc_function", "created": False, "error": str(exc)[:300]})
            return JSONResponse({"results": results, "error": str(exc)[:300] + (
                " Already created: %s." % ", ".join(made) if made else "")}, status_code=400)
    today = time.strftime("%Y-%m-%d")
    for n in news:
        if n["kind"] == "mcp":
            mcps._progress[forge.app_name(n["slug"])] = {"phase": "copying", "message": "Getting it ready.", "at": time.time()}
            background.add_task(forge.install_app, n, who, tok, today)
            results.append({"key": n["slug"], "kind": "mcp", "app_name": forge.app_name(n["slug"]), "started": True})
    return {"results": results}


@app.get("/api/admin/designer/tools/status")
def designer_tools_status(apps: str = "", x_forwarded_access_token: str = Header(None)):
    """Where each new tool's app is up to: not started, getting ready, running or failed."""
    _, tok = _require_designer(x_forwarded_access_token)
    names = [a for a in apps.split(",") if re.match(r"^mcp-[a-z0-9][a-z0-9-]{0,60}$", a)][:5]
    return {"apps": forge.app_states(names, tok) if names else {}}


def _tool_slug(payload: dict) -> str:
    slug = str(payload.get("slug") or "").strip()
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,60}$", slug):
        raise HTTPException(400, "That is not a tool id.")
    return slug


@app.post("/api/admin/designer/tool-test/plan")
def designer_tooltest_plan(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Write the calls to try a new tool with, from its real input shapes and the files that are
    really in its folders. Reads only; nothing is called yet."""
    _, tok = _require_designer(x_forwarded_access_token)
    if not designer.enabled():
        raise HTTPException(404, "The assistant designer is switched off.")
    slug = _tool_slug(payload)
    item = forge.item_from_workspace(slug, tok)
    client = tooltest.connect(slug, tok)
    tools = client.tools()
    facts = tooltest.facts_for(item, tok)
    facts["answers"] = tooltest.discover(client, tools)  # real sheets, rows and ids for realistic calls
    return tooltest.plan(item, tools, facts, tok, payload.get("models"))


@app.post("/api/admin/designer/tool-test/run")
def designer_tooltest_run(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Call one ability of a new tool, as an assistant would, and say what came back. Only a read-only
    ability can be run: the tool's own declaration is checked here, not taken from the request."""
    _, tok = _require_designer(x_forwarded_access_token)
    slug = _tool_slug(payload)
    forge.item_from_workspace(slug, tok)  # a tool the designer made, and nobody else's
    test = payload.get("test") if isinstance(payload.get("test"), dict) else {}
    client = tooltest.connect(slug, tok)
    tool = next((t for t in client.tools() if t.get("name") == test.get("ability")), None)
    if not tool:
        raise HTTPException(400, "That tool has no such ability.")
    if not tooltest._readonly(tool):
        raise HTTPException(400, "That ability saves or changes something, so it is not run automatically.")
    args = test.get("arguments") if isinstance(test.get("arguments"), dict) else {}
    if len(json.dumps(args)) > 2000:
        raise HTTPException(400, "That input is too large.")
    return tooltest.run_one(client, {"ability": tool["name"], "arguments": args, "expect": str(test.get("expect") or "")[:300],
                                     "expect_error": bool(test.get("expect_error"))}, tok, payload.get("models"))


@app.post("/api/admin/designer/tool-test/fix")
def designer_tooltest_fix(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Have the code model repair a new tool after a failed test. Proposes only: the result (the new code,
    a diff, what changed, a fingerprint) is shown to the admin and nothing is deployed."""
    who, tok = _require_designer(x_forwarded_access_token)
    slug = _tool_slug(payload)
    item = forge.item_from_workspace(slug, tok)
    failures = [f for f in payload.get("failures") or [] if isinstance(f, dict)][:6]
    request = " ".join(str(payload.get("request") or "").split())[:1500]
    if not failures and not request:
        raise HTTPException(400, "Say what failed, or what you want changed.")
    if request:
        designer_guard.count_turn(who)
        _tool_words(request, "What should change in the tool %s?" % item["name"], payload, tok)
    out = tooltest.propose_fix(item, failures, tok, payload.get("models"), request=request)
    return {**out, "slug": slug, "name": item["name"]}


def _tool_words(words: str, question: str, payload: dict, tok: str) -> None:
    """What an admin types about a tool goes through the same check as the designer's chat (designer_guard.py)."""
    seen = designer_guard.check_message(words, {"question": question}, designer_guard.settings(),
                                        designer_models.resolve(payload.get("models"), tok)["chat"], tok)
    if not seen["ok"]:
        events.note(action="designer_refused", target="designer", label="Assistant designer", detail={"why": seen["flag"]})
        raise HTTPException(400, seen["reply"])


@app.post("/api/admin/designer/tool-test/plan-one")
def designer_tooltest_plan_one(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """One test the admin describes in their own words ("try the Imaginate sheet with a 50,000 budget"), written
    as a real call from the tool's input shapes. Nothing is run here; the run route runs it (read-only only)."""
    who, tok = _require_designer(x_forwarded_access_token)
    designer_guard.count_turn(who)
    slug = _tool_slug(payload)
    asks = " ".join(str(payload.get("asks") or "").split())[:600]
    if len(asks) < 3:
        raise HTTPException(400, "Describe the test you want.")
    item = forge.item_from_workspace(slug, tok)
    _tool_words(asks, "Which test should be tried on the tool %s?" % item["name"], payload, tok)
    client = tooltest.connect(slug, tok)
    tools = client.tools()
    out = tooltest.plan(item, tools, tooltest.facts_for(item, tok), tok, payload.get("models"), asks=asks)
    out["tests"] = out.get("tests", [])[:1]
    return out


@app.post("/api/admin/designer/tool-test/apply")
def designer_tooltest_apply(
    background: BackgroundTasks,
    payload: dict = Body(...),
    x_forwarded_access_token: str = Header(None),
):
    """Deploy a repair the admin has read. The code comes back from the browser, so it is checked again
    as if it were new, and `approved` must be the fingerprint of exactly this code on this tool."""
    who, tok = _require_designer(x_forwarded_access_token)
    slug = _tool_slug(payload)
    current = forge.item_from_workspace(slug, tok)
    code = str(payload.get("code") or "")
    new = forge.clean_item({**current, "code": code}) or current
    if new["problems"]:
        raise HTTPException(400, "The repaired code did not pass the checks: " + " ".join(new["problems"][:2]))
    if code.strip() == current["code"].strip():
        raise HTTPException(400, "There is nothing to change.")
    if str(payload.get("approved") or "") != new["fingerprint"]:
        raise HTTPException(400, "Read and approve the repair as it is shown now. It changed since you approved it.")
    events.note(action="fixed_tool", label=slug, target=forge.app_name(slug),
                detail={"via": "designer", "fingerprint": new["fingerprint"][:12], "what": str(payload.get("what") or "")[:200]})
    # Stamped a few seconds early, because the deployment's own clock is Databricks' and this one is ours.
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
    mcps._progress[forge.app_name(slug)] = {"phase": "copying", "message": "Applying the repair.", "at": time.time()}
    background.add_task(forge.install_app, new, who, tok, time.strftime("%Y-%m-%d"))
    return {"started": True, "app_name": forge.app_name(slug), "since": since}


@app.get("/api/admin/designer/tool-test/ready")
def designer_tooltest_ready(slug: str, since: str, x_forwarded_access_token: str = Header(None)):
    """Has a repair gone live yet? The old version keeps answering until the new one is in."""
    _, tok = _require_designer(x_forwarded_access_token)
    slug = _tool_slug({"slug": slug})
    if not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", since or ""):
        raise HTTPException(400, "since must be a time like 2026-10-08T12:00:00Z")
    return tooltest.deployed_since(slug, since, tok)


@app.post("/api/admin/designer/test-plan")
def designer_test_plan(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    _, tok = _require_designer(x_forwarded_access_token)
    return {"tests": designer.plan_tests(payload.get("draft") or {}, tok, payload.get("models"))}


@app.get("/api/admin/designer/assistant-state")
def designer_assistant_state(endpoint: str, x_forwarded_access_token: str = Header(None)):
    """Is the new assistant up yet? It takes a few minutes, and only appears here
    once it has been shared with the admin."""
    who, tok = _require_designer(x_forwarded_access_token)
    for a in access.visible_agents(who, app_token(), tok):
        if a["name"] == endpoint:
            return {"state": "ready" if a["ready"] else "starting"}
    return {"state": "waiting"}


@app.post("/api/admin/designer/test-run")
def designer_test_run(payload: dict = Body(...), x_forwarded_access_token: str = Header(None)):
    """Ask the new assistant one test question as the admin and have a second model
    mark the answer. A failure to answer is a result, not an error."""
    who, tok = _require_designer(x_forwarded_access_token)
    endpoint = (payload.get("endpoint") or "").strip()
    question = (payload.get("question") or "").strip()[:500]
    expect = (payload.get("expect") or "").strip()[:500]
    if not endpoint or not question:
        raise HTTPException(400, "an endpoint and a question are required")
    agent = _allowed_agent(who, endpoint, tok)
    if not agent["ready"]:
        raise HTTPException(409, "The assistant is still starting.")
    out_dir = filestore.output_dir_path(agent["output_volume"]) if agent.get("output_volume") else ""
    try:
        out = chat.ask(_transport(agent), agent["task"], [{"role": "user", "content": question}], tok, output_dir=out_dir)
    except DbxError as exc:
        return {"verdict": "fail", "reason": "It did not answer: " + str(exc)[:200], "answer": ""}
    broken = events.tool_failure(out.get("reply") or "")
    if broken:
        return {"verdict": "fail", "reason": "A tool it used failed: " + str(broken)[:200], "answer": (out.get("reply") or "")[:1500]}
    try:
        graded = designer.grade(question, expect, out.get("reply") or "", out.get("tools") or [], tok, payload.get("models"))
    except DbxError as exc:
        graded = {"verdict": "ungraded", "reason": "The checker could not run: " + str(exc)[:160]}
    return {**graded, "answer": (out.get("reply") or "")[:1500], "tools": (out.get("tools") or [])[:10]}


@app.get("/api/admin/cost")
def llm_cost(days: int = 30, x_forwarded_access_token: str = Header(None)):
    """Actual model-serving spend, read from the workspace billing tables."""
    _who_admin, tok = _require_admin(x_forwarded_access_token)
    return llm.spend(tok, days)


@app.get("/api/admin/logs")
def recent_logs(
    days: int = 7, limit: int = 200, x_forwarded_access_token: str = Header(None)
):
    """Recent problems - failed API calls and their reasons.

    Read from the Delta table when PORTAL_LOG_TABLE is set, otherwise from this
    process's memory. Falls back to memory, with a note, if the table cannot be
    read, so a permissions slip cannot blank the panel.
    """
    _require_admin(x_forwarded_access_token)
    limit = 20 if limit < 20 else (500 if limit > 500 else limit)
    if logsink.enabled():
        try:
            return {"stored": True, "note": "", "lines": logsink.recent(days, limit)}
        except Exception as exc:
            note = "Could not read the log table, showing this session only. " + str(exc)[:200]
            return {"stored": False, "note": note, "lines": logbuf.buffer.recent(logging.WARNING, limit)}
    return {"stored": False, "note": "", "lines": logbuf.buffer.recent(logging.WARNING, limit)}


@app.get("/api/admin/audit")
def activity(
    days: int = 7,
    cats: str = "access",
    x_forwarded_access_token: str = Header(None),
):
    """Recent activity, from Databricks' own audit records."""
    _who_admin, user_tok = _require_admin(x_forwarded_access_token)
    wanted = [c.strip() for c in (cats or "").split(",") if c.strip()]
    return audit.entries(
        user_tok,
        app_token(),
        days=days,
        cats=wanted,
        workspace_id=os.environ.get("DATABRICKS_WORKSPACE_ID", "")
        or str(access.identity(user_tok).get("workspace_id") or ""),
    )


@app.get("/api/admin/activity")
def portal_activity(
    days: int = 7,
    category: str = "",
    status: str = "",
    actor: str = "",
    x_forwarded_access_token: str = Header(None),
):
    """What people did in the portal: actions, never what anyone typed."""
    _require_admin(x_forwarded_access_token)
    return events.activity(days, category.strip(), status.strip(), actor.strip())


@app.get("/api/admin/agent-health")
def agent_health(days: int = 7, x_forwarded_access_token: str = Header(None)):
    """Each assistant's failure rate, response time and recent problems."""
    _require_admin(x_forwarded_access_token)
    return events.health(days)


def _version() -> str:
    """The release this copy runs, from the VERSION file the deployer writes
    into each release (empty when run from a checkout)."""
    try:
        with open(os.path.join(HERE, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()[:40]
    except OSError:
        return ""


VERSION = _version()

# A client's branding, set by the deployer: PORTAL_BRAND_NAME and
# PORTAL_BRAND_COLOR in app.yaml, and the logo as branding/logo.png in the
# release (always a PNG: the deployer converts uploads, so no SVG with script
# is ever served from the portal's own origin). All optional; unbranded = the
# default name and teal. The UI turns the colour into a palette (lib/brand.ts).
LOGO = os.path.join(HERE, "branding", "logo.png")


def _brand() -> dict:
    color = (os.environ.get("PORTAL_BRAND_COLOR") or "").strip()
    logo = ""
    if os.path.isfile(LOGO):
        logo = "/api/brand/logo?v=%d" % int(os.path.getmtime(LOGO))
    return {
        "name": (os.environ.get("PORTAL_BRAND_NAME") or "").strip()[:40],
        "color": color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "",
        "logo": logo,
    }


BRAND = _brand()


@app.get("/api/brand/logo")
def brand_logo():
    if not BRAND["logo"]:
        raise HTTPException(404, "No logo")
    with open(LOGO, "rb") as f:
        body = f.read()
    return Response(body, media_type="image/png", headers={
        "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


_shared: dict = {}


def _assistants_shared() -> int | None:
    """How many assistants the portal's own identity can see, i.e. have been
    shared with it. Zero means an empty portal for everyone, which the
    deployer's health check reports. Cached a minute; None if unknown."""
    hit = _shared.get("n")
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        n = len(access.all_agents(app_token()))
    except Exception:  # noqa: BLE001 - health must answer even if Databricks does not
        n = None
    _shared["n"] = (time.time() + 60, n)
    return n


@app.get("/api/health")
def health():
    out = {"ok": True, "auth_mode": auth_mode(), "version": VERSION}
    n = _assistants_shared()
    if n is not None:
        out["assistants"] = n
    return out


class _Web(StaticFiles):
    """The built UI. Its pages (index.html, 404.html, index.txt) name the hashed script files of
    one release, and were sent with no caching rule, so browsers kept an old page after a deploy
    (seen live 2026-11-02: a new Build option missing until a hard refresh). Pages are re-checked
    every time (cheap: an unchanged page is a 304); the hashed files under _next/ never change
    under one name, so they are kept for a year."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.replace("\\", "/").startswith("_next/static/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp


# Mounted last on purpose: every /api route above is registered first and so
# still wins, while everything else falls through to the built UI.
app.mount("/", _Web(directory=WEB, html=True), name="web")
