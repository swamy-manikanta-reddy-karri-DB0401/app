"""Take the portal out of a client's workspace, when the client is removed from
the deployer. Runs in the deploy job (action `uninstall`), as the client's own
service principal.

Removed: the portal app (its own service principal and everyone's access to it
go with it) and its release folders; and, only when asked, the portal's chat
history, activity and problem tables, and its list of custom-code agents
(`portal_app_agents`; kept otherwise, so a reinstall offers them again).

Kept, on purpose: the client's assistants, and the tools (MCP apps) the portal
installed, with their keys in the `agent-portal` secret scope and their code in
`/Workspace/Shared/agent-portal-mcps/`. Assistants call those tools, so removing
them would break assistants that keep working without the portal. The
installer's service principal is the client admin's to delete.

Each step is best effort and idempotent: something already gone is fine, a
refusal is reported, and a run can simply be repeated.
"""
from __future__ import annotations

from common import TABLE_RE, Api, DeployError
import workspace as ws

HISTORY_TABLES = ("portal_logs", "portal_chats", "portal_events", "portal_app_agents")


def _gone(exc: DeployError) -> bool:
    s = str(exc)
    return exc.status == 404 or "RESOURCE_DOES_NOT_EXIST" in s or "does not exist" in s or "doesn't exist" in s


def run(api: Api, c: dict, say, history: bool = False) -> tuple[list[str], list[str]]:
    """Returns (what was removed, problems), as plain sentences."""
    done: list[str] = []
    problems: list[str] = []

    def attempt(what: str, method: str, path: str, **kw) -> None:
        try:
            api.call(method, path, **kw)
            done.append(what)
        except DeployError as exc:
            if not _gone(exc):
                problems.append("Could not remove %s: %s" % (what, exc))

    say("Removing the portal app %s" % c["app"])
    attempt("the portal app %s" % c["app"], "DELETE", "/api/2.0/apps/" + c["app"])

    say("Removing the portal's files")
    attempt("the portal's release folders", "POST", "/api/2.0/workspace/delete",
            json={"path": "/Workspace/Users/%s/agent-portal/%s" % (c["client_id"], c["app"]), "recursive": True})

    if history and c.get("log_table"):
        if not TABLE_RE.match(c["log_table"]):
            problems.append("The history table name %r is not valid; nothing was dropped." % c["log_table"])
        else:
            say("Deleting the chat history and logs")
            schema = c["log_table"].rsplit(".", 1)[0]
            quoted = ".".join("`%s`" % part for part in schema.split("."))  # names may hold dashes
            try:
                warehouse = ws.pick_warehouse(api, c.get("warehouse") or "")
                for t in HISTORY_TABLES:
                    ws.sql(api, warehouse, "DROP TABLE IF EXISTS %s.`%s`" % (quoted, t))
                done.append("the chat history and logs in %s" % schema)
            except DeployError as exc:
                problems.append("Could not delete the chat history in %s: %s" % (schema, exc))
    return done, problems
