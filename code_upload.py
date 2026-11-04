"""Developer mode: start an agent from code. A developer uploads a project (a ZIP, for example one exported from the
portal and changed); the portal checks it, deploys it as a Databricks App and offers it as an assistant.

The portal never runs the uploaded code. It is copied to the workspace (`/Workspace/Shared/agent-portal-agents/<app>`)
and Databricks runs it as its own app, with the same installer the tool apps use (`mcps.install`: create or switch
on, wait for compute, deploy, retry Databricks' transient failures). The app is created with the portal's own user
API scopes (update-scopes.json), on the app before it first starts, because Databricks mints people's tokens from the
scopes an app booted with; so the person's own sign-in reaches the agent. Once running it is registered like any
custom-code agent (app_agents.py): access comes from the app's own permission list, set on the Access page.

Checked before anything is created: only text source files (no binaries, no .env or key files), safe relative paths,
at most MAX_FILES files and MAX_BYTES in all, an app.yaml at the top, and no credential in any file (the specific
key formats from attachments.KEY_PATTERNS; the generic "token = ..." pattern is left out because ordinary code
matches it). An app is only replaced if it was uploaded here before (its description starts with MARK), never an app
someone made by hand. Each agent is one app, and a workspace allows 100 apps (fixed), which the screen says.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
import zipfile

import app_agents
import attachments
import mcps
from dbx import DbxError

log = logging.getLogger("portal.code_upload")

MARK = "Portal agent (uploaded):"
ROOT = "/Workspace/Shared/agent-portal-agents"
MAX_FILES = 400
MAX_BYTES = 20 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
TEXT = {".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".csv", ".html", ".css", ".js", ".ts",
        ".sql", ".jinja", ".j2", ".lock", ".sh", ".env.example", ".gitignore", ""}
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".ipynb_checkpoints", ".pytest_cache", ".mypy_cache"}
REFUSE_NAMES = re.compile(r"(^|/)(\.env|.*\.pem|.*\.p12|.*\.pfx|.*\.key|id_rsa.*|.*credentials.*\.json|.*service[-_]?account.*\.json)$", re.I)
CODE_KEYS = attachments.KEY_PATTERNS[:-1]   # the specific formats; not the generic "token = ..." that code matches
APP_RE = re.compile(r"^[a-z][a-z0-9-]{1,29}$")
POLL = 10   # seconds between looks at the app while it deploys
_jobs: dict = {}


def app_name_for(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return ("agent-" + slug)[:30].rstrip("-")


def read_project(blob: bytes) -> list:
    """[(relative path, bytes)] from the ZIP, checked. Raises DbxError(400) with the reason in plain words."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise DbxError("That is not a ZIP file.", 400)
    names = [i for i in z.infolist() if not i.is_dir()]
    paths = [i.filename.replace("\\", "/") for i in names]
    # One top folder around everything (as an export has) is taken off.
    tops = {p.split("/", 1)[0] for p in paths}
    strip = (tops.pop() + "/") if len(tops) == 1 and all("/" in p for p in paths) else ""
    out, total = [], 0
    for info, raw in zip(names, paths):
        path = raw[len(strip):]
        parts = path.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]) or parts[-1] in (".DS_Store",):
            continue
        if path.startswith("/") or ".." in parts or ":" in path or not path:
            raise DbxError("The ZIP has an unsafe path (%s)." % raw[:80], 400)
        if REFUSE_NAMES.search(path):
            raise DbxError("%s looks like a key or a settings file with secrets. Keep keys in the secret scope, never in code." % path, 400)
        ext = os.path.splitext(parts[-1])[1].lower()
        if ext not in TEXT and parts[-1] not in ("Procfile", "LICENSE", "Makefile", "Dockerfile"):
            raise DbxError("Only source and text files can be uploaded; %s is not one." % path, 400)
        if info.file_size > MAX_FILE:
            raise DbxError("%s is larger than %d MB." % (path, MAX_FILE // 1024 // 1024), 400)
        total += info.file_size
        if total > MAX_BYTES or len(out) >= MAX_FILES:
            raise DbxError("The project is too big (at most %d files and %d MB)." % (MAX_FILES, MAX_BYTES // 1024 // 1024), 400)
        data = z.read(info)
        text = data.decode("utf-8", "ignore")
        if any(p.search(text) for p in CODE_KEYS) or (ext == ".json" and attachments.looks_like_key_file(data)):
            raise DbxError("%s contains something that looks like a password or key. Remove it: keys belong in the secret "
                           "scope, never in code." % path, 400)
        out.append((path, data))
    if not any(p in ("app.yaml", "app.yml") for p, _ in out):
        raise DbxError("The project needs an app.yaml at the top, saying how Databricks starts it (an exported project has one).", 400)
    return out


def _scopes() -> list:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "update-scopes.json"), encoding="utf-8") as fh:
            return list(json.load(fh).get("user_api_scopes") or [])
    except (OSError, ValueError):
        return []


def owned(app: dict | None) -> bool:
    return app is None or str(app.get("description") or "").startswith(MARK)


def start(blob: bytes, display_name: str, description: str, app_name: str, who: dict, tok: str) -> dict:
    """Check the project and start deploying it in the background. Returns {job, app_name}."""
    files = read_project(blob)
    display_name = " ".join(str(display_name or "").split())[:80]
    if len(display_name) < 2:
        raise DbxError("Give it a name people will recognise.", 400)
    app_name = app_name or app_name_for(display_name)
    if not APP_RE.match(app_name):
        raise DbxError("The app name must be 2 to 30 lowercase letters, numbers and dashes, starting with a letter.", 400)
    try:
        existing, _ = mcps.act("GET", "/api/2.0/apps/" + app_name, tok, quiet=True)
    except DbxError as exc:
        if exc.status != 404:
            raise
        existing = None
    if not owned(existing):
        raise DbxError("An app called %s already exists and was not uploaded here, so it is not replaced. Choose another name." % app_name, 409)
    entry = {"slug": app_name, "app_name": app_name, "name": display_name, "problem": "", "needs": {"secrets": [], "volumes": []},
             "description": (MARK + " " + (description or display_name))[:500], "source_root": ROOT, "user_api_scopes": _scopes()}
    job = "%s-%d" % (app_name, int(time.time()))
    _jobs[job] = {"owner": who.get("user_name"), "app_name": app_name, "state": "deploying", "step": "Copying the files.",
                  "new": existing is None, "started": time.time()}
    threading.Thread(target=_run, args=(job, entry, files, display_name, description, who, tok), daemon=True).start()
    return {"job": job, "app_name": app_name, "new": existing is None, "files": len(files)}


def _run(job: str, entry: dict, files: list, display_name: str, description: str, who: dict, tok: str) -> None:
    name = entry["app_name"]
    # "Running" is not enough: an app that already exists keeps answering from its old version (and reports RUNNING)
    # while the new one goes in, and a new app's compute is ACTIVE before its first deployment has finished (seen
    # live: the tests ran 30 s before the app had started). So the active deployment must have succeeded and be
    # newer than this upload (as tooltest.deployed_since does for repairs; ISO times in one format compare as text).
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
    try:
        mcps.install(entry, who, tok, files=files)
        deadline, retries = time.time() + 20 * 60, 0
        while True:
            apps, _ = mcps._apps(tok)
            state, line = mcps._state(apps.get(name), mcps._progress.get(name))
            _jobs[job]["step"] = line or "Deploying."
            if state == "running" and _live_since(apps.get(name) or {}, since):
                break
            if state == "failed":
                if retries < mcps.DEPLOY_RETRIES and mcps.transient(line):
                    retries += 1
                    mcps.redeploy(entry, tok)
                    time.sleep(20)
                    continue
                raise DbxError(line or "the deployment failed", 502)
            if time.time() > deadline:
                raise DbxError("it was not running after 20 minutes", 504)
            time.sleep(POLL)
        _jobs[job]["step"] = "Adding it as an assistant."
        app_agents.save(name, {"display_name": display_name, "description": description or display_name, "path": "/invocations"},
                        who.get("user_name") or "")
        note = app_agents.share_with_portal(name, tok)
        _jobs[job].update(state="done", step="Running and offered as an assistant.", note=note)
    except Exception as exc:  # noqa: BLE001 - shown on the screen
        log.warning("uploaded agent %s: %s", name, str(exc)[:300])
        _jobs[job].update(state="failed", step=str(exc)[:300])


def _live_since(app: dict, since: str) -> bool:
    act = app.get("active_deployment") or {}
    return (not app.get("pending_deployment") and (act.get("status") or {}).get("state") == "SUCCEEDED"
            and str(act.get("create_time") or "") >= since)


def job(job_id: str, who: dict) -> dict:
    j = _jobs.get(job_id)
    if not j or j["owner"] != who.get("user_name"):
        raise DbxError("There is no such upload.", 404)
    return {k: v for k, v in j.items() if k != "owner"}
