"""Agent catalog and access resolution.

Access is derived entirely from Databricks-native state - there is no local
permissions database to drift out of sync:

    what an agent is   -> a serving endpoint whose task is agent/v1/responses
    its friendly name  -> endpoint tags (display_name, blurb)
    who may use it     -> the endpoint's own ACL (CAN_QUERY / CAN_MANAGE)
    an "agent set"     -> a workspace group named in those ACLs

Listing needs the app identity because reading an ACL requires CAN_MANAGE,
which end users do not have. That is safe: listing only decides what is *shown*.
Invocation always uses the user's own token, so Databricks remains the authority
on what can actually be run.
"""
from __future__ import annotations

import os
import re
import time

import app_agents
import custom_agents
from dbx import AGENT_TASK_PREFIX, PORTAL_TAG, QUERYABLE, DbxError, app_token, call

ADMIN_GROUP = "admins"

# How each agent type is described to someone who has never opened Databricks.
# Keyed by the endpoint's task, with the Agent Bricks name prefix as a hint for
# the two builders that share the Responses schema.
KINDS = {
    "supervisor": ("Multi-agent", "Coordinates several tools and agents to answer."),
    "knowledge": ("Document expert", "Answers questions from a set of documents."),
    "agent": ("Agent", "A custom-built agent."),
    "model": ("Chat model", "A general-purpose chat model."),
}


def identity(user_tok: str) -> dict:
    me = call("GET", "/api/2.0/preview/scim/v2/Me", user_tok)
    groups = [g.get("display") for g in me.get("groups", []) if g.get("display")]
    return {
        "user_name": me.get("userName", ""),
        "display_name": me.get("displayName") or me.get("userName", ""),
        "groups": groups,
        "is_admin": ADMIN_GROUP in groups,
    }


def _prettify(name: str) -> str:
    """`mas-0062ab3f-endpoint` -> `Agent 0062ab3f`, so the UI is never ugly."""
    stem = re.sub(r"-endpoint$", "", name)
    m = re.match(r"^mas-([0-9a-f]{6,})$", stem)
    if m:
        return "Agent " + m.group(1)[:8]
    return stem.replace("-", " ").replace("_", " ").title()


def _tags(ep: dict) -> dict:
    return {t.get("key"): t.get("value", "") for t in (ep.get("tags") or []) if t.get("key")}


def _kind(ep: dict) -> str:
    """Best-effort agent type, for labelling only - never for routing.

    Routing is done from `task` by `adapters`. This is cosmetic, so a wrong
    guess costs a slightly-off badge and nothing more.
    """
    task = ep.get("task") or ""
    name = ep.get("name") or ""
    if task.startswith(AGENT_TASK_PREFIX):
        if name.startswith("mas-"):
            return "supervisor"
        if name.startswith("ka-"):
            return "knowledge"
        return "agent"
    return "model"


# Agent Bricks keeps the name and description the builder typed, keyed by
# endpoint. Note the version: these live under /api/2.1, not /api/2.0.
BRICKS_APIS = (
    ("/api/2.1/supervisor-agents", "supervisor_agents"),
    ("/api/2.1/knowledge-assistants", "knowledge_assistants"),
)


_meta_cache: dict = {}
META_TTL = 60


def bricks_meta(tok: str, workspace_id: str = "", fallback_tok: str = "") -> dict:
    """endpoint_name -> {display_name, description, agent_id} straight from Agent Bricks.

    Saves an admin from retyping a name Databricks already has, so a freshly
    built agent shows up in the portal already looking presentable. Best effort:
    if these APIs are unavailable the catalog just falls back to tags and then
    to a prettified endpoint name.

    Seen live (2026-10-11): an assistant built straight in Databricks ("DataBeat Media
    Planner") showed in a deployed portal as "Agent 1cab65e2". The user's Apps token has
    no supervisor-agents / knowledge-assistants scope, so the names were never read.
    A refused read is retried with `fallback_tok` (the portal's identity, which the
    deployer shares every assistant with). Names are presentation, not access: only
    endpoints the caller can already see are ever shown. Cached for a minute.
    """
    key = (tok[-16:], fallback_tok[-16:], workspace_id)
    hit = _meta_cache.get(key)
    if hit and time.time() - hit[0] < META_TTL:
        return hit[1]
    out: dict = {}
    headers = {"Accept": "application/json"}
    if workspace_id:
        headers["X-Databricks-Workspace-Id"] = workspace_id
    for path, field in BRICKS_APIS:
        data = None
        for t in dict.fromkeys(x for x in (tok, fallback_tok) if x):
            try:
                data = call("GET", path, t, headers=headers, quiet=True)
                break
            except DbxError:
                continue
        for a in (data or {}).get(field) or []:
            ep = a.get("endpoint_name")
            if ep:
                out[ep] = {
                    "display_name": a.get("display_name") or "",
                    "description": a.get("description") or "",
                    "agent_id": str(a.get("supervisor_agent_id") or a.get("knowledge_assistant_id") or a.get("id") or ""),
                }
    _meta_cache[key] = (time.time(), out)
    return out


def _shape(ep: dict, meta: dict | None = None) -> dict:
    tags = _tags(ep)
    kind = _kind(ep)
    label, hint = KINDS.get(kind, KINDS["agent"])
    # Precedence: an admin's explicit tag wins, then what the builder typed in
    # Databricks, then a tidied-up endpoint name.
    built = (meta or {}).get(ep.get("name")) or {}
    # Opt-in per agent, because only its author knows whether it reads a file.
    accepts = (tags.get("accepts") or "").strip()
    return {
        "name": ep.get("name"),
        "id": ep.get("id"),
        "task": ep.get("task") or "",
        "kind": kind,
        "kind_label": label,
        "kind_hint": hint,
        "display_name": (
            tags.get("display_name") or built.get("display_name") or _prettify(ep.get("name", ""))
        ),
        "blurb": tags.get("blurb") or built.get("description") or ep.get("description") or "",
        "ready": (ep.get("state") or {}).get("ready") == "READY",
        "state": (ep.get("state") or {}).get("ready", "UNKNOWN"),
        # Where uploads go, and what the agent will accept. Both come from tags
        # so the whole capability declaration stays Databricks-native.
        # Agent Bricks agents keep their governing permission list at the
        # agent level, not on the serving endpoint. Synced in by sync_agents.py
        # because the app cannot discover it (no grantable scope).
        "agent_id": (tags.get("agent_id") or "").strip() or (built.get("agent_id") if kind == "supervisor" else ""),
        "upload_volume": tags.get("upload_volume") or "",
        "output_volume": tags.get("output_volume") or "",
        # Which of its tool calls wait for the person: changes (default) | all | auto. See toolpolicy.
        "tool_approval": (tags.get("tool_approval") or "").strip().lower(),
        "accepts": [a.strip().lower() for a in accepts.split(",") if a.strip()],
        "supports_files": bool(tags.get("upload_volume")),
    }


def _portal_token() -> str:
    """The portal's own token for reading names, or "" if it cannot be had."""
    try:
        return app_token()
    except Exception:  # noqa: BLE001 - names are best effort
        return ""


def all_agents(tok: str, meta_tok: str = "") -> list:
    """Every agent endpoint the caller can see, newest first.

    Includes anything served under agent/*, plus endpoints an admin has
    explicitly published with the portal tag - so a raw chat model, or an agent
    type that postdates this code, can still be offered.

    `meta_tok` reads the Agent Bricks display names. It defaults to `tok`, but
    the caller should pass the *user's* token: the app's service principal is
    not granted the Agent Bricks APIs, so with the app identity the names fall
    back to a prettified endpoint id. Names are presentation, not access, so
    reading them as the user is safe.
    """
    data = call("GET", "/api/2.0/serving-endpoints", tok)
    eps = []
    for e in data.get("endpoints", []):
        task = e.get("task") or ""
        published = _tags(e).get(PORTAL_TAG, "").lower() in ("true", "1", "yes")
        if task.startswith(AGENT_TASK_PREFIX) or published:
            eps.append(e)
    eps.sort(key=lambda e: e.get("creation_timestamp") or 0, reverse=True)
    meta = bricks_meta(meta_tok or tok, os.environ.get("DATABRICKS_WORKSPACE_ID", ""), _portal_token())
    return [_shape(e, meta) for e in eps]


ENDPOINT_PERMS = "/api/2.0/permissions/serving-endpoints/"
AGENT_PERMS = "/api/2.0/permissions/supervisor-agents/"

# The agent-level list has no CAN_VIEW.
AGENT_LEVELS = {"CAN_QUERY", "CAN_MANAGE"}


def perm_paths(agent: dict) -> list:
    """Every permission list attached to this agent, the deciding one first.

    An Agent Bricks agent has two lists, and they do NOT mean the same thing.
    Measured with a purpose-made non-admin identity:

        granted on the serving endpoint only -> invocation returns 200
        granted on the Agent Bricks list only -> invocation returns 403

    So the **serving endpoint** list is what decides who may use an agent, and
    it is therefore what the portal reads and what a grant is written to. The
    Agent Bricks list (which its build screen edits) governs the agent as an
    editable object, not the right to call it. Neither list propagates to the
    other - verified by polling.

    A revoke still clears both: leaving someone on the Agent Bricks list would
    let them keep editing an agent they were supposedly removed from, and it
    makes the two screens disagree.
    """
    if agent.get("app_name"):
        # A custom-code agent is an app: its own list is the only one (see app_agents).
        return [APP_PERMS + agent["app_name"]]
    paths = []
    if agent.get("id"):
        paths.append(ENDPOINT_PERMS + agent["id"])
    aid = (agent.get("agent_id") or "").strip()
    if aid:
        paths.append(AGENT_PERMS + aid)
    return paths


def acl(agent, app_tok: str) -> list:
    """Read the governing ACL. Accepts a shaped agent, or a bare endpoint id."""
    if isinstance(agent, str):
        agent = {"id": agent}
    last = None
    for path in perm_paths(agent):
        try:
            return call("GET", path, app_tok).get("access_control_list", [])
        except DbxError as exc:
            last = exc
    raise last or DbxError("no permission list available for this agent", 404)


def _grants(entries: list) -> list:
    """Flatten an ACL into {principal, kind, level} rows for the admin UI."""
    rows = []
    for e in entries:
        if e.get("user_name"):
            principal, kind = e["user_name"], "user"
        elif e.get("group_name"):
            principal, kind = e["group_name"], "group"
        elif e.get("service_principal_name"):
            principal = e.get("display_name") or e["service_principal_name"]
            kind = "service_principal"
        else:
            continue
        for p in e.get("all_permissions", []):
            rows.append({
                "principal": principal,
                "kind": kind,
                "level": p.get("permission_level"),
                "inherited": bool(p.get("inherited")),
            })
    return rows


def _can_query(entries: list, who: dict):
    """The reason this caller may query, or None. Explanation only, not a gate.

    Matches service principals too - automation is a legitimate portal caller,
    and an ACL records those under a different field entirely.
    """
    groups = set(who.get("groups") or [])
    me = who.get("user_name") or ""
    best = None
    for e in entries:
        levels = {p.get("permission_level") for p in e.get("all_permissions", [])}
        if not levels & QUERYABLE:
            continue
        if me and (e.get("user_name") == me or e.get("service_principal_name") == me):
            return "granted to you directly"
        if e.get("group_name") in groups:
            best = "via group " + e["group_name"]
    return best


def _can_manage(entries: list, who: dict) -> bool:
    """Does THIS user hold CAN_MANAGE on this endpoint, in their own right?

    The portal reads and writes ACLs with the app's service principal, which
    holds CAN_MANAGE on every published agent. Without this check, anyone the
    portal calls an admin could edit the guest list of an agent Databricks
    would refuse them - the app's privilege would leak to its users. Verified
    the hard way: that escalation was real before this existed.
    """
    groups = set(who.get("groups") or [])
    for e in entries:
        levels = {p.get("permission_level") for p in e.get("all_permissions", [])}
        if "CAN_MANAGE" not in levels:
            continue
        if e.get("user_name") == who.get("user_name"):
            return True
        if e.get("group_name") in groups:
            return True
    return False


def may_manage(agent, who: dict, app_tok: str) -> bool:
    """Authorisation gate for every ACL/tag write the admin console offers."""
    try:
        return _can_manage(acl(agent, app_tok), who)
    except DbxError:
        return False


def visible_agents(who: dict, app_tok: str, user_tok: str = "") -> list:
    """Agents this user may use, each annotated with why.

    The list is taken under the **user's own token**, because Databricks already
    filters `GET /serving-endpoints` per caller - verified with a non-admin
    holding one grant, who saw exactly that one agent. So visibility is decided
    by Databricks, not computed here, which is what makes it genuinely
    on-behalf-of the user. It also means an agent shows up even if the portal
    itself was never added to that agent's guest list.

    The guest list is then read with the app identity purely to explain *why*
    access exists ("via group finance"). If that read fails the agent is still
    shown - Databricks already said this person may use it, and an unexplained
    entry is better than hiding something they can legitimately open.
    """
    listing_tok = user_tok or app_tok
    out = []
    for agent in all_agents(listing_tok, user_tok):
        # Databricks already decided this caller may see the agent. Do not
        # second-guess it: re-deriving the answer from the guest list only
        # invents ways to hide something the user is entitled to (it silently
        # dropped service-principal callers, for one). The guest list is read
        # solely to explain *why*, and any failure to explain is cosmetic.
        reason = ""
        try:
            reason = _can_query(acl(agent, app_tok), who) or ""
        except DbxError:
            pass
        out.append(dict(agent, access_reason=reason or "granted in Databricks"))
    return out + app_agents.visible(who) + custom_agents.visible(who)


def admin_agents(app_tok: str, user_tok: str = "", who: dict | None = None) -> list:
    out = []
    for agent in all_agents(app_tok, user_tok):
        mine = False
        try:
            entries = acl(agent, app_tok)
            rows = _grants(entries)
            mine = _can_manage(entries, who) if who else False
            err = None
        except DbxError as exc:
            # Agent endpoints do not grant workspace admins implicit CAN_MANAGE,
            # so an agent built by someone else is unmanageable until its owner
            # shares it. Say that, rather than leaking the raw API error.
            rows = []
            if exc.status in (401, 403):
                err = (
                    "This agent has not been shared with the portal yet. Its owner needs to "
                    "give the portal CAN_MANAGE on it before access can be managed here."
                )
            else:
                err = "Could not read this agent's permissions: " + str(exc)
        out.append(
            dict(agent, grants=rows, acl_error=err, manageable=err is None, you_can_manage=mine)
        )
    return out + (app_agents.for_admin(who or {}))


def principals(app_tok: str) -> dict:
    groups = call("GET", "/api/2.0/preview/scim/v2/Groups?count=200", app_tok).get("Resources", [])
    users = call(
        "GET",
        "/api/2.0/preview/scim/v2/Users?count=500&attributes=userName,displayName",
        app_tok,
    ).get("Resources", [])
    return {
        "groups": sorted(
            [
                {
                    "name": g.get("displayName"),
                    "id": g.get("id"),
                    "local": (g.get("meta") or {}).get("resourceType") == "WorkspaceGroup",
                }
                for g in groups
                if g.get("displayName")
            ],
            key=lambda g: g["name"],
        ),
        "users": sorted(
            [
                {"name": u.get("userName"), "display": u.get("displayName") or u.get("userName")}
                for u in users
                if u.get("userName")
            ],
            key=lambda u: u["name"],
        ),
    }


def set_grant(agent, kind: str, principal: str, level, app_tok: str) -> dict:
    """Add or remove one grant across every list that governs this agent.

    A grant is written to the authoritative (agent-level) list. A **revoke is
    applied to all of them**, because either list is enough to keep access -
    clearing only one leaves the person still able to use the agent while the
    console shows them as removed.

    PATCH is additive, so a revoke is expressed as PUT of the surviving entries.
    Service-principal grants are preserved on revoke, or the portal would lock
    itself out of the agent it is managing.
    """
    if isinstance(agent, str):
        agent = {"id": agent}
    key = {"user": "user_name", "group": "group_name"}.get(kind)
    if not key:
        raise DbxError("unsupported principal kind " + repr(kind), 400)

    paths = perm_paths(agent)
    if not paths:
        raise DbxError("this agent has no permission list to change", 404)

    if level and paths[0].startswith(APP_PERMS):
        # An app knows only Can use and Can manage: "may ask it questions" is Can use.
        level = "CAN_MANAGE" if level == "CAN_MANAGE" else "CAN_USE"

    if level:
        # The serving endpoint list is what actually permits calling the agent,
        # so it must succeed. The Agent Bricks list is then mirrored so the two
        # Databricks screens agree about who has access - without it, people
        # added here are invisible on the agent's own permissions screen.
        target = paths[0]
        data = call("PATCH", target, app_tok, json={
            "access_control_list": [{key: principal, "permission_level": level}]
        })
        warning = ""
        for path in paths[1:]:
            lvl = level
            if path.startswith(AGENT_PERMS) and lvl not in AGENT_LEVELS:
                # No CAN_VIEW exists at agent level. Skip rather than silently
                # promote a view-only grant into a usable one.
                continue
            try:
                call("PATCH", path, app_tok, json={
                    "access_control_list": [{key: principal, "permission_level": lvl}]
                })
            except DbxError as exc:
                warning = (
                    "Access was granted, but this agent's Agent Bricks permissions could "
                    "not be updated to match, so that screen will not show "
                    + principal + ". Run sync_agents.py --apply, then retry. (" + str(exc)[:90] + ")"
                )
        out = {"access_control_list": data.get("access_control_list", [])}
        if warning:
            out["warning"] = warning
        return out

    # Revoke: clear the principal from every list.
    result: dict = {}
    errors = []
    for path in paths:
        try:
            entries = call("GET", path, app_tok).get("access_control_list", [])
        except DbxError as exc:
            errors.append(str(exc))
            continue
        keep = []
        for e in entries:
            if e.get(key) == principal:
                continue
            for perm in e.get("all_permissions", []):
                if perm.get("inherited"):
                    continue
                row = {"permission_level": perm["permission_level"]}
                if e.get("user_name"):
                    row["user_name"] = e["user_name"]
                elif e.get("group_name"):
                    row["group_name"] = e["group_name"]
                elif e.get("service_principal_name"):
                    row["service_principal_name"] = e["service_principal_name"]
                else:
                    continue
                keep.append(row)
        try:
            data = call("PUT", path, app_tok, json={"access_control_list": keep})
            if not result:
                result = {"access_control_list": data.get("access_control_list", [])}
        except DbxError as exc:
            errors.append(str(exc))
    if not result:
        raise DbxError("could not revoke: " + "; ".join(errors)[:300], 502)
    return result


APP_PERMS = "/api/2.0/permissions/apps/"

# SCIM group writes are admin-only. The portal's service principal is not a
# workspace admin, so in a deployed app these fail with a raw 403 that means
# nothing to an admin reading it. Translate, and say what to do instead.
NOT_ADMIN = (
    "The portal's own identity is not a workspace admin, so it cannot create or edit "
    "groups. Either create the group in Databricks (Settings > Identity and access > "
    "Groups) and then grant it agents here, or make the portal's service principal a "
    "workspace admin if you want to manage groups from this screen."
)


def _group_write(method: str, path: str, app_tok: str, body: dict) -> dict:
    try:
        return call(method, path, app_tok, json=body)
    except DbxError as exc:
        if exc.status == 403 and "only accessible by admins" in str(exc):
            raise DbxError(NOT_ADMIN, 403)
        raise


def grant_app_access(group: str, app_name: str, app_tok: str) -> str:
    """Let a group open the portal at all.

    A Databricks App has its own permission list, separate from any agent's. A
    group that is not on it gets a bare 401 from the Apps proxy before this code
    ever runs - a confusing failure with no explanation in the UI. So creating a
    group here also puts it on the app, and admins never need a terminal.
    """
    try:
        call(
            "PATCH",
            APP_PERMS + app_name,
            app_tok,
            json={"access_control_list": [{"group_name": group, "permission_level": "CAN_USE"}]},
        )
    except DbxError as exc:
        return str(exc)[:160]
    return ""


def create_group(name: str, app_tok: str, app_name: str = "") -> dict:
    g = _group_write(
        "POST", "/api/2.0/preview/scim/v2/Groups", app_tok, {"displayName": name}
    )
    out = {"name": g.get("displayName"), "id": g.get("id")}
    if app_name:
        err = grant_app_access(name, app_name, app_tok)
        out["app_access"] = not err
        if err:
            out["warning"] = (
                "Group created, but it could not be given access to open the portal. "
                "An admin needs to add it to the app's permissions. (" + err + ")"
            )
    return out


def set_group_members(group_id: str, user_names: list, app_tok: str) -> dict:
    """Replace a group's membership - this is how an 'agent set' is assigned."""
    users = call(
        "GET", "/api/2.0/preview/scim/v2/Users?count=500&attributes=userName", app_tok
    ).get("Resources", [])
    by_name = {u.get("userName"): u.get("id") for u in users}
    missing = [n for n in user_names if n not in by_name]
    if missing:
        raise DbxError("unknown users: " + ", ".join(missing), 400)
    members = [{"value": by_name[n]} for n in user_names]
    _group_write(
        "PATCH",
        "/api/2.0/preview/scim/v2/Groups/" + group_id,
        app_tok,
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "members", "value": members}],
        },
    )
    return {"group_id": group_id, "members": user_names}


META_TAGS = ("display_name", "blurb", "upload_volume", "output_volume", "accepts", "agent_id", "tool_approval", PORTAL_TAG)


def set_meta(endpoint_name: str, values: dict, app_tok: str) -> dict:
    """Store an agent's presentation and capabilities in its endpoint tags.

    Tags keep the whole declaration Databricks-native, so the portal still owns
    no state of its own. An empty value deletes the tag rather than storing "".
    """
    add, remove = [], []
    for key in META_TAGS:
        if key not in values:
            continue
        val = (values.get(key) or "").strip()
        if val:
            add.append({"key": key, "value": val})
        else:
            remove.append(key)
    if not add and not remove:
        return {"tags": []}

    body: dict = {}
    if add:
        body["add_tags"] = add
    if remove:
        body["delete_tags"] = remove
    data = call(
        "PATCH", "/api/2.0/serving-endpoints/" + endpoint_name + "/tags", app_tok, json=body
    )
    return {"tags": data.get("tags", [])}
