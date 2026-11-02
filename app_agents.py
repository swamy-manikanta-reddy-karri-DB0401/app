"""Custom-code agents: an agent someone wrote in code and runs as its own Databricks App.

Supervisor Agents, Knowledge Assistants and Genie are served from serving endpoints, which
carry tags (name, files) and a permission list. An agent written in code (LangGraph, the
Agent Bricks runtime, a FastAPI app of its own) runs as a Databricks App instead: it has a
URL and a permission list (CAN_USE / CAN_MANAGE) but **no tags**, so there is nowhere in
Databricks to say "show this in the portal, under this name". That one fact is kept in a
small Delta table beside the portal's other tables (`portal_app_agents`, or
PORTAL_APP_AGENTS_TABLE): which app, the friendly name, the address it answers on, and the
optional file folders. Everything else stays Databricks-native:

    who may use it  -> the app's own permission list (CAN_USE or CAN_MANAGE), read as the
                       portal, which is why registering gives the portal CAN_MANAGE on it
    is it running   -> the app's compute and status, read each time
    the answer      -> the app itself, called with the **person's own token**

Invocation never falls back to the portal's identity (the security model forbids it). If
the Apps proxy refuses the person's token, the reply says so and links to the app, where
the person signs in themselves.

Agent apps do not share one contract. Seen in one workspace (2026-11-02): an Agent Bricks
custom agent (`databricks_agentkit.DurableAgentServer`) takes a message list, Databricks'
agent templates take Responses `input` on `/invocations`, and a hand-written FastAPI agent
takes `{"message", "history"}` on `/api/chat` and answers `{"reply", "tool_calls"}`. So the
address is configurable (default `/invocations`) and the request shape is probed by
`chat.ask` exactly as for serving endpoints, then remembered.
"""
from __future__ import annotations

import os
import re
import threading
import time

import store
from dbx import DbxError, app_token, call, in_apps
from logsink import TABLE as _LOG_TABLE

PREFIX = "app:"
APP_PERMS = "/api/2.0/permissions/apps/"
USABLE = {"CAN_USE", "CAN_MANAGE"}
APP_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
PATH = re.compile(r"^/[A-Za-z0-9_\-./]{0,200}$")
DEFAULT_PATH = "/invocations"
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_VOLUME = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(/[A-Za-z0-9_ .-]+)*$")
FIELDS = ("display_name", "blurb", "path", "upload_volume", "accepts", "output_volume")
LIMITS = {"display_name": 120, "blurb": 600, "path": 200, "upload_volume": 300, "accepts": 120, "output_volume": 600}
TTL = 30

KIND = ("custom", "Custom agent", "Built in code and running as its own app.")


def _table() -> str:
    explicit = os.environ.get("PORTAL_APP_AGENTS_TABLE", "").strip()
    if explicit:
        return explicit
    parts = _LOG_TABLE.split(".")
    return ".".join(parts[:-1] + ["portal_app_agents"]) if len(parts) == 3 else ""


TABLE = _table()
QUALIFIED = ".".join("`" + p + "`" for p in TABLE.split(".")) if _NAME_RE.match(TABLE) else ""

OFF = ("Custom-code agents need the portal's own tables, which are not set up here. "
       "Set PORTAL_LOG_TABLE (catalog.schema.table) in app.yaml, or ask whoever deploys the portal.")


def enabled() -> bool:
    return bool(QUALIFIED)


def key(app_name: str) -> str:
    return PREFIX + app_name


def is_key(name: str) -> bool:
    return (name or "").startswith(PREFIX)


def app_of(name: str) -> str:
    return name[len(PREFIX):] if is_key(name) else ""


_ready = False
_lock = threading.Lock()
_rows: tuple[float, list] | None = None
_apps: dict = {}


def ensure_table(runner=None) -> None:
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        store.ensure_table(
            QUALIFIED,
            "CREATE TABLE IF NOT EXISTS " + QUALIFIED + " ("
            "app_name STRING, display_name STRING, blurb STRING, path STRING, upload_volume STRING, "
            "accepts STRING, output_volume STRING, added_by STRING, added_at TIMESTAMP)",
            runner=runner,
        )
        _ready = True


def _p(name: str, value) -> dict:
    return {"name": name, "type": "STRING", "value": str(value or "")}


def registered(fresh: bool = False) -> list:
    """Every registered app agent: [{app_name, display_name, ...}]. Cached briefly; [] when off."""
    global _rows
    if not enabled():
        return []
    if not fresh and _rows and time.time() - _rows[0] < TTL:
        return _rows[1]
    ensure_table()
    cols = ("app_name",) + FIELDS + ("added_by",)
    data = store.run("SELECT " + ", ".join(cols) + " FROM " + QUALIFIED + " ORDER BY added_at DESC")
    seen, out = set(), []
    for r in data:
        row = dict(zip(cols, [v or "" for v in r]))
        if row["app_name"] and row["app_name"] not in seen:
            seen.add(row["app_name"])
            out.append(row)
    _rows = (time.time(), out)
    return out


def find(name: str) -> dict | None:
    app = app_of(name) or name
    return next((r for r in registered() if r["app_name"] == app), None)


def clean(values: dict) -> dict:
    """The settings an admin may give, checked. Raises DbxError(400) with a plain reason."""
    out = {}
    for f in FIELDS:
        if f not in values:
            continue
        v = str(values.get(f) or "").strip()[: LIMITS[f]]
        if f == "path":
            v = v or DEFAULT_PATH
            if not PATH.match(v) or ".." in v:
                raise DbxError("The address must start with / and use letters, numbers, dashes and slashes, "
                               "for example /invocations.", 400)
        if f == "upload_volume" and v and not _VOLUME.match(v):
            raise DbxError("The upload folder must look like catalog.schema.folder.", 400)
        if f == "output_volume" and v and not all(_VOLUME.match(x.strip()) for x in v.split(",") if x.strip()):
            raise DbxError("Results folders must look like catalog.schema.folder, separated by commas.", 400)
        if f == "accepts" and v:
            v = ", ".join(x.strip().lstrip(".").lower() for x in v.split(",") if re.match(r"^\s*\.?[A-Za-z0-9]{1,10}\s*$", x))
        out[f] = v
    return out


def save(app_name: str, values: dict, who: str) -> dict:
    """Register (or change) one app agent. Values are the full set; missing ones are kept."""
    if not enabled():
        raise DbxError(OFF, 503)
    if not APP_NAME.match(app_name or ""):
        raise DbxError("That is not an app name.", 400)
    old = find(app_name) or {}
    row = {f: old.get(f, "") for f in FIELDS}
    row.update(clean(values))
    row["path"] = row.get("path") or DEFAULT_PATH
    if not row.get("display_name"):
        raise DbxError("Give it a name people will recognise.", 400)
    ensure_table()
    params = [_p("a", app_name), _p("by", who)] + [_p(f, row[f]) for f in FIELDS]
    store.run("DELETE FROM " + QUALIFIED + " WHERE app_name = :a", [_p("a", app_name)])
    store.run(
        "INSERT INTO " + QUALIFIED + " (app_name, " + ", ".join(FIELDS) + ", added_by, added_at) VALUES (:a, "
        + ", ".join(":" + f for f in FIELDS) + ", :by, current_timestamp())",
        params,
    )
    _forget()
    return dict(row, app_name=app_name)


def remove(app_name: str) -> None:
    """Stop offering it in the portal. The app itself, and its permissions, are left alone."""
    if not enabled():
        raise DbxError(OFF, 503)
    ensure_table()
    store.run("DELETE FROM " + QUALIFIED + " WHERE app_name = :a", [_p("a", app_name)])
    _forget()


def _forget() -> None:
    global _rows
    _rows = None
    _apps.clear()


# ----------------------------------------------------------------------- Databricks side


def _scope_refused(exc: DbxError) -> bool:
    return exc.status in (401, 403) and "scope" in str(exc).lower()


def _as(method: str, path: str, tok: str, **kw):
    """As the admin first; as the portal only when the admin's Apps token lacks the scope
    (Apps user tokens have no `apps` scope, seen live). Databricks still decides."""
    try:
        return call(method, path, tok, quiet=True, **kw)
    except DbxError as exc:
        if not (in_apps() and _scope_refused(exc)):
            raise
    return call(method, path, app_token(), quiet=True, **kw)


def _state(app: dict) -> tuple[bool, str]:
    compute = ((app.get("compute_status") or {}).get("state") or "").upper()
    status = ((app.get("app_status") or {}).get("state") or "").upper()
    if compute and compute != "ACTIVE":
        return False, "stopped" if compute == "STOPPED" else compute.lower().replace("_", " ")
    if status and status not in ("RUNNING",):
        return False, status.lower().replace("_", " ")
    return bool(app.get("url")), "running" if app.get("url") else "not started"


def app_info(app_name: str, tok: str = "") -> dict | None:
    """The app as the portal sees it, or None when it is gone or not shared with the portal."""
    hit = _apps.get(app_name)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        app = call("GET", "/api/2.0/apps/" + app_name, tok or app_token(), quiet=True)
    except DbxError:
        app = None
    _apps[app_name] = (time.time(), app)
    return app


def acl(app_name: str) -> list:
    return call("GET", APP_PERMS + app_name, app_token(), quiet=True).get("access_control_list", [])


def may_use(entries: list, who: dict) -> str:
    """Why this person may use the app ("" if not). Inherited grants count: Databricks honours them."""
    groups = set(who.get("groups") or [])
    me = who.get("user_name") or ""
    best = ""
    for e in entries:
        levels = {p.get("permission_level") for p in e.get("all_permissions", [])}
        if not levels & USABLE:
            continue
        if me and (e.get("user_name") == me or e.get("service_principal_name") == me):
            return "granted to you directly"
        if e.get("group_name") in groups:
            best = "via group " + e["group_name"]
    return best


def may_manage(app_name: str, who: dict) -> bool:
    groups = set(who.get("groups") or [])
    try:
        entries = acl(app_name)
    except DbxError:
        return False
    for e in entries:
        if "CAN_MANAGE" not in {p.get("permission_level") for p in e.get("all_permissions", [])}:
            continue
        if e.get("user_name") == who.get("user_name") or e.get("group_name") in groups:
            return True
    return False


def shape(row: dict, app: dict | None) -> dict:
    """The same record a serving-endpoint agent has, so every page treats it alike."""
    ready, state = _state(app or {}) if app else (False, "not shared with the portal")
    kind, label, hint = KIND
    accepts = row.get("accepts") or ""
    url = ((app or {}).get("url") or "").rstrip("/")
    return {
        "name": key(row["app_name"]),
        "id": key(row["app_name"]),
        "app_name": row["app_name"],
        "task": "app",
        "kind": kind,
        "kind_label": label,
        "kind_hint": hint,
        "display_name": row.get("display_name") or row["app_name"],
        "blurb": row.get("blurb") or (app or {}).get("description") or "",
        "ready": ready,
        "state": state.upper() if ready else state,
        "app_url": url,
        "path": row.get("path") or DEFAULT_PATH,
        "agent_id": "",
        "upload_volume": row.get("upload_volume") or "",
        "output_volume": row.get("output_volume") or "",
        "tool_approval": "",
        "accepts": [a.strip() for a in accepts.split(",") if a.strip()],
        "supports_files": bool(row.get("upload_volume")),
    }


def target(agent: dict) -> str:
    """Where to send a question: the app's URL plus its address."""
    if not agent.get("app_url"):
        raise DbxError("%s is not running right now. Ask an admin to start it." % agent.get("display_name"), 503)
    return agent["app_url"] + (agent.get("path") or DEFAULT_PATH)


def _rows_safe() -> list:
    try:
        return registered()
    except DbxError:
        return []


def visible(who: dict) -> list:
    """The registered app agents this person may use, from each app's own permission list.

    Unlike serving endpoints there is no per-person listing to lean on (Apps user tokens
    have no `apps` scope), so the list is read as the portal and matched to the person.
    When it cannot be read the agent is **not** shown: nothing here can confirm access."""
    out = []
    for row in _rows_safe():
        try:
            reason = may_use(acl(row["app_name"]), who)
        except DbxError:
            continue
        if reason:
            out.append(dict(shape(row, app_info(row["app_name"])), access_reason=reason))
    return out


def for_admin(who: dict) -> list:
    """Every registered app agent, with its grants, for the Access page."""
    import access  # the Access page's helpers; imported here to avoid a cycle

    out = []
    for row in _rows_safe():
        agent = shape(row, app_info(row["app_name"]))
        try:
            entries = acl(row["app_name"])
            out.append(dict(agent, grants=access._grants(entries), acl_error=None, manageable=True,
                            you_can_manage=_manages(entries, who)))
        except DbxError:
            out.append(dict(agent, grants=[], manageable=False, you_can_manage=False, acl_error=(
                "This app has not been shared with the portal. In Databricks, open the app, choose "
                "Permissions and give the portal's service principal Can Manage.")))
    return out


def _manages(entries: list, who: dict) -> bool:
    groups = set(who.get("groups") or [])
    for e in entries:
        if "CAN_MANAGE" in {p.get("permission_level") for p in e.get("all_permissions", [])}:
            if e.get("user_name") == who.get("user_name") or e.get("group_name") in groups:
                return True
    return False


# ----------------------------------------------------------------------- admin helpers


def candidates(tok: str) -> list:
    """Apps an admin could register: everything except the portal itself, newest first."""
    me = os.environ.get("DATABRICKS_APP_NAME", "")
    data = _as("GET", "/api/2.0/apps", tok)
    taken = {r["app_name"] for r in _rows_safe()}
    out = []
    for a in (data or {}).get("apps") or []:
        name = a.get("name") or ""
        if not name or name == me:
            continue
        ready, state = _state(a)
        out.append({"name": name, "description": (a.get("description") or "")[:300], "running": ready, "state": state,
                    "url": a.get("url") or "", "registered": name in taken, "updated": a.get("update_time") or ""})
    out.sort(key=lambda a: a["updated"], reverse=True)
    return out


def check(app_name: str, tok: str) -> dict:
    """Is it there and running, and can the portal see it? Words, never a raw error."""
    if not APP_NAME.match(app_name or ""):
        raise DbxError("An app name uses lowercase letters, numbers and dashes.", 400)
    try:
        app = _as("GET", "/api/2.0/apps/" + app_name, tok)
    except DbxError as exc:
        if exc.status == 404 or "does not exist" in str(exc).lower():
            return {"found": False, "running": False, "message": "There is no app called %s in this workspace." % app_name}
        return {"found": False, "running": False, "message": "The app could not be checked: " + str(exc)[:200]}
    ready, state = _state(app)
    msg = ("It is running." if ready else
           "It exists but is %s. Start it in Databricks (Compute > Apps), then people can use it here." % state)
    return {"found": True, "running": ready, "state": state, "url": app.get("url") or "",
            "description": app.get("description") or "", "message": msg}


def share_with_portal(app_name: str, tok: str) -> str:
    """Give the portal Can Manage on the app, so it can read who may use it and manage that.
    Returns "" or a plain explanation of what the admin must do instead."""
    sp = os.environ.get("DATABRICKS_CLIENT_ID") or ""
    if not sp:
        return ""  # local dev: the portal is the developer
    body = {"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_MANAGE"}]}
    try:
        _as("PATCH", APP_PERMS + app_name, tok, json=body)
        _apps.pop(app_name, None)
        return ""
    except DbxError as exc:
        return ("The portal could not be given access to this app (%s). In Databricks, open the app, choose "
                "Permissions and give the service principal %s Can Manage." % (str(exc)[:120], sp))
