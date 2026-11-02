"""Deploy (or roll back) one release of the Agent Portal to one client.

Run by `.github/workflows/deploy.yml` inside the client's GitHub Environment,
so the client's service principal secret is only ever an environment variable
of this process. Steps, each written to the deployment record as it happens:

  1. sign in to the client's workspace as its service principal
  2. make sure the app exists and its compute is running
  3. access: open the app to the client's users group, let the app use a SQL
     warehouse, create the history schema and grant the app on it
  4. upload the release to its own folder (the folder name is the version)
  5. set the user API scopes on the app record
  6. deploy, then restart if the scopes changed (they only apply after a restart)
  7. verify: Databricks says it is running, and the portal answers with the
     expected version

If anything from step 4 on fails and another version was live before, that
version is put back automatically (its folder is still there) and verified, and
both attempts are recorded. Access problems (step 3) are warnings, never a
failed deploy: the portal runs without history, it just cannot save chats.

Inputs (environment): CLIENT, VERSION, ACTION (deploy|rollback), DEPLOY_ID,
ACTOR, RUN_URL; the client's DATABRICKS_HOST, DATABRICKS_CLIENT_ID,
DATABRICKS_CLIENT_SECRET, APP_NAME, PORTAL_LOG_TABLE, WAREHOUSE_ID, USERS_GROUP;
and HOME_* / REGISTRY_SCHEMA for the record (see home.py, registry.py).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import registry  # noqa: E402
import workspace as ws  # noqa: E402
from common import APP_RE, CLIENT_RE, COLOR_RE, ID_RE, VERSION_RE, Api, DeployError, check_logo, m2m_token  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _allow(raw: str):
    raw = raw.strip()
    if raw in ("", "*", "all"):
        return None
    if raw.lower() == "none":
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def config(env: dict) -> dict:
    c = {
        "client": env.get("CLIENT", ""),
        "version": env.get("VERSION", ""),
        "action": env.get("ACTION") or "deploy",
        "deploy_id": env.get("DEPLOY_ID") or str(uuid.uuid4()),
        "actor": env.get("ACTOR", ""),
        "run_url": env.get("RUN_URL", ""),
        "host": env.get("DATABRICKS_HOST", ""),
        "client_id": env.get("DATABRICKS_CLIENT_ID", ""),
        "secret": env.get("DATABRICKS_CLIENT_SECRET", ""),
        "app": env.get("APP_NAME") or "agent-portal",
        "log_table": env.get("PORTAL_LOG_TABLE", ""),
        "warehouse": env.get("WAREHOUSE_ID", ""),
        "users_group": "" if (env.get("USERS_GROUP") or "users").lower() in ("-", "none") else (env.get("USERS_GROUP") or "users"),
        # Share the client's assistants with the portal on each deploy unless switched off.
        "share_agents": (env.get("SHARE_AGENTS") or "all").strip().lower() != "none",
        # The MCP catalog: which repo, which version ("" = newest tag), and which
        # MCPs this client gets ("" or "*" = all, "none" = none, else a list).
        "mcp_repo": (env.get("MCP_REPO") or "swamy-manikanta-reddy-karri-DB0401/mcps").strip(),
        "mcp_ref": (env.get("MCP_CATALOG") or "").strip(),
        "mcp_allow": _allow(env.get("MCP_ALLOW") or ""),
        "mcp_token": env.get("MCP_REPO_TOKEN") or "",
        # Secrets for tools, put in the client's own secret scope on each deploy
        # (empty = removed there). Never logged.
        "tool_secrets": {"gam-key": env.get("GAM_KEY_JSON") or "", "gam-network": (env.get("GAM_NETWORK_CODE") or "").strip()},
        "brand": {"name": (env.get("BRAND_NAME") or "").strip()[:40], "color": (env.get("BRAND_COLOR") or "").strip(),
                  "logo": (env.get("BRAND_LOGO") or "").strip()},
    }
    # Inputs reach this script as environment variables, never pasted into the
    # workflow's shell, and are checked before use.
    if not CLIENT_RE.match(c["client"]):
        raise DeployError("Unknown client %r." % c["client"])
    if c["action"] == "uninstall":
        c["version"] = "-"
        c["purge_history"] = (env.get("PURGE") or "").strip().lower() == "history"
    elif not VERSION_RE.match(c["version"]):
        raise DeployError("Not a release version: %r" % c["version"])
    if c["action"] not in ("deploy", "rollback", "uninstall"):
        raise DeployError("Unknown action %r." % c["action"])
    if not ID_RE.match(c["deploy_id"]):
        raise DeployError("Bad deploy id.")
    if not APP_RE.match(c["app"]):
        raise DeployError("APP_NAME %r is not allowed." % c["app"])
    if c["brand"]["color"] and not COLOR_RE.match(c["brand"]["color"]):
        raise DeployError("BRAND_COLOR %r is not a colour like #1A73E8." % c["brand"]["color"])
    if c["brand"]["logo"]:
        check_logo(c["brand"]["logo"])  # refuse a bad logo before touching the client's app
    if not (c["host"] and c["client_id"] and c["secret"]):
        raise DeployError("This client is missing its workspace address, service principal id or secret. "
                          "Open it in the deployer and fill in its settings.")
    return c


class Recorder:
    """Writes each step to the deployment record and to the job log."""

    def __init__(self, c: dict, write=None):
        self.c = c
        self.write = write or (lambda **kw: registry.safe(registry.record, **kw))
        self.from_version = ""
        self.warnings: list[str] = []
        self.failed_from = False

    def __call__(self, status: str, step: str, message: str = "", **detail):
        print("[%s] %s%s" % (status, step, (": " + message) if message else ""), flush=True)
        self.write(deploy_id=self.c["deploy_id"], client=self.c["client"], action=self.c["action"],
                   version=self.c["version"], status=status, step=step, message=message,
                   from_version=self.from_version, actor=self.c["actor"], run_url=self.c["run_url"],
                   detail={"warnings": self.warnings, **detail})

    def say(self, step: str):
        self("running", step)


def verify(api: Api, app: str, version: str, timeout: float = 300, **kw) -> dict:
    """Wait for the app to come up on `version`. Raises if it does not."""
    last = {}

    def ok():
        nonlocal last
        last = ws.check(api, app, expect=version)
        if last["status"] in ("healthy", "unverified"):
            return last
        if last["status"] == "degraded" and last.get("version") == version:
            return last  # up on the right version; failing questions are not the release's fault
        return None

    got = ws.wait(ok, timeout, every=15, **kw)
    if not got:
        raise DeployError("The portal did not come up on %s: %s" % (version, last.get("summary", "no answer")), 502)
    return got


def ship(api: Api, c: dict, rec: Recorder, scopes: list[str], workdir: str, **kw) -> dict:
    """Steps 2-7 for c['version']. Returns the health result. Sets rec.from_version."""
    rec.say("Getting the app ready")
    app, created = ws.ensure_app(api, c["app"], scopes, rec.say)
    rec.from_version = ws.version_of((app.get("active_deployment") or {}).get("source_code_path", ""))
    rec.say("Setting up access")
    try:
        warehouse = ws.pick_warehouse(api, c["warehouse"])
    except DeployError as exc:
        warehouse = ""
        rec.warnings.append("Could not find a SQL warehouse: %s" % exc)
    rec.warnings += ws.grant_access(api, app, c["log_table"], warehouse, c["users_group"], rec.say, c.get("share_agents", True))
    if c.get("tool_secrets") is not None:
        rec.warnings += ws.sync_tool_secrets(api, app, c["tool_secrets"], rec.say)
    path = ws.release_path(c["client_id"], c["app"], c["version"])
    ws.stage(REPO, c["version"], os.path.join(workdir, "release"), c["log_table"], warehouse, c.get("brand"))
    if c.get("mcp_repo"):
        ship_mcps(c, rec, os.path.join(workdir, "release"))
    ws.upload(api, os.path.join(workdir, "release"), path, rec.say)
    rec.failed_from = True  # the live app changes from here, so a failure puts the old version back
    changed = ws.set_scopes(api, c["app"], app, scopes, rec.say)
    ws.deploy(api, c["app"], path, rec.say, **kw)
    if changed and not created:
        ws.restart(api, c["app"], rec.say, **kw)
    rec.say("Checking the portal is up")
    return verify(api, c["app"], c["version"], **kw)


def _gh(token: str):
    import httpx

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token

    def get(path):
        # A moved or renamed repository answers 301 to its new id (seen live 2026-11-02: the
        # catalog moved and the deploy said its version "was not found"). Same host, so the
        # token is kept across the redirect.
        r = httpx.get("https://api.github.com" + path, headers=headers, timeout=30, follow_redirects=True)
        if r.status_code >= 400:
            raise DeployError("GitHub refused %s (HTTP %d)." % (path.split("?")[0], r.status_code), 502)
        return r.json()
    return get


def ship_mcps(c: dict, rec: Recorder, release: str, gh_get=None, download=None) -> None:
    """Put this client's MCPs, at its pinned catalog version, inside the release:
    the portal then lists and installs only these, from its own files, with no
    GitHub access needed from the client's workspace."""
    import httpx

    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", c["mcp_repo"]):
        raise DeployError("MCP_REPO must be owner/name.")
    gh_get = gh_get or _gh(c.get("mcp_token", ""))
    ref, sha = ws.mcp_ref(c["mcp_repo"], c["mcp_ref"], gh_get)
    if download is None:
        headers = {"Authorization": "Bearer " + c["mcp_token"]} if c.get("mcp_token") else {}
        r = httpx.get("https://codeload.github.com/%s/tar.gz/%s" % (c["mcp_repo"], sha), headers=headers,
                      follow_redirects=True, timeout=120)
        if r.status_code >= 400:
            raise DeployError("Could not download the MCP catalog %s (HTTP %d)." % (ref, r.status_code), 502)
        blob = r.content
    else:
        blob = download(sha)
    shipped = ws.stage_mcps(blob, c["mcp_allow"], release, {"repo": c["mcp_repo"], "ref": ref, "sha": sha})
    missing = sorted(set(c["mcp_allow"] or []) - set(shipped))
    if missing:
        rec.warnings.append("Not in MCP catalog %s, so not included: %s." % (ref, ", ".join(missing)))
    rec.mcps = {"ref": ref, "sha": sha[:12], "mcps": shipped}
    rec.say("Including %d tool%s from the MCP catalog %s (%s)" % (len(shipped), "" if len(shipped) == 1 else "s", ref, sha[:7]))


def roll_back(api: Api, c: dict, rec: Recorder, reason: str, **kw) -> int:
    """Put rec.from_version back after a failed deploy. Returns the exit code."""
    prev = rec.from_version
    rec("running", "Putting %s back" % prev, reason)
    back = {**c, "deploy_id": str(uuid.uuid4()), "action": "auto_rollback", "version": prev}
    brec = Recorder(back, rec.write)
    brec.from_version = c["version"]
    try:
        brec.say("Putting %s back after %s failed" % (prev, c["version"]))
        ws.deploy(api, c["app"], ws.release_path(c["client_id"], c["app"], prev), brec.say, **kw)
        h = verify(api, c["app"], prev, **kw)
    except DeployError as exc:
        brec("failed", "Could not put %s back" % prev, str(exc))
        rec("failed", "Failed, and putting %s back also failed" % prev, "%s Then: %s" % (reason, exc))
        return 1
    brec("succeeded", "Back on %s" % prev, h.get("summary", ""), health=h)
    registry.safe(registry.record_health, str(uuid.uuid4()), c["client"], h, c["actor"], c["run_url"])
    rec("rolled_back", "Rolled back to %s" % prev, reason, rolled_back_to=prev, rollback_id=back["deploy_id"])
    return 1


def run(c: dict, rec: Recorder, api: Api, scopes: list[str], workdir: str, **kw) -> int:
    try:
        h = ship(api, c, rec, scopes, workdir, **kw)
    except DeployError as exc:
        if rec.failed_from and rec.from_version and rec.from_version != c["version"]:
            return roll_back(api, c, rec, str(exc), **kw)
        rec("failed", "Failed", str(exc))
        return 1
    registry.safe(registry.record_health, str(uuid.uuid4()), c["client"], h, c["actor"], c["run_url"])
    note = " (%d warning%s)" % (len(rec.warnings), "" if len(rec.warnings) == 1 else "s") if rec.warnings else ""
    rec("succeeded", "Live on %s%s" % (c["version"], note), h.get("summary", ""), health=h, url=h.get("url", ""),
        mcp_catalog=getattr(rec, "mcps", None))
    return 0


def remove_everything(api: Api, c: dict, rec: Recorder) -> int:
    """The client is being removed: take the portal out of their workspace.
    Succeeds only when nothing was refused, since the deployer then forgets
    the client (and its secret) for good."""
    import uninstall

    try:
        done, problems = uninstall.run(api, c, rec.say, history=c.get("purge_history", False))
    except DeployError as exc:
        rec("failed", "Could not remove the portal", str(exc))
        return 1
    rec.warnings += problems
    if problems:
        rec("failed", "Not everything could be removed", "; ".join(problems)[:900], removed=done)
        return 1
    rec("succeeded", "Removed from their workspace", "Removed " + (", ".join(done) if done else "nothing (it was already gone)") + ".",
        removed=done)
    return 0


def main() -> int:
    try:
        c = config(dict(os.environ))
    except DeployError as exc:
        print("::error::%s" % exc)
        return 2
    rec = Recorder(c)
    rec.say("Signing in to the client's workspace")
    try:
        api = Api(c["host"], m2m_token(c["host"], c["client_id"], c["secret"]))
        scopes = ws.scopes_from(REPO)
    except DeployError as exc:
        rec("failed", "Could not sign in", str(exc))
        return 1
    if c["action"] == "uninstall":
        return remove_everything(api, c, rec)
    with tempfile.TemporaryDirectory() as workdir:
        code = run(c, rec, api, scopes, workdir)
    if code:
        print("::error::The deploy did not go through. See the deployer for details.")
    return code


if __name__ == "__main__":
    sys.exit(main())
