"""MCP catalog: tools kept in a GitHub repo, deployed as Databricks Apps on demand.

The portal still stores nothing. The repo is the catalog (one folder per MCP, each
with an `mcp.yaml` card and an `app.yaml`); Databricks is the state. An MCP is
"deployed" when a Databricks App with its name exists, and the name is a rule, not
a record: `mcp-<folder>`, unless the card sets `app_name` for an app that already
exists under another name. Delete the portal and nothing is lost.

Why one tarball: `codeload.github.com` serves the whole repo in a single request
with no API rate limit (api.github.com allows 60 an hour per address, which a
shared Apps egress address could hit). The same download is the catalog and, on
deploy, the source, so what is listed is what gets deployed.

Choosing: a person ticks tools in the assistant wizard. On create or save, `plan`
attaches the ones already running and `deploy_and_attach` deploys the rest in the
background, adding each to the assistant when its app is up. Nothing is deployed
from the picker itself.

Folder access: a freshly installed app runs as its own new service principal, which
has no access to anyone's volumes. The card's `needs.volumes` (read / write) says
what the tool uses, and the assistant's Files step says which folders, so once the
app exists `grant_volumes` gives that principal just that access (READ_VOLUME on
the folders people upload to, READ_VOLUME + WRITE_VOLUME on the folder results go to
(a file write is refused without both), plus the
USE_CATALOG / USE_SCHEMA that reaching a volume takes). It runs as the admin, so
Databricks only allows what that admin could grant themselves; a refusal is logged
with the exact grant to make by hand.

Identity: same rule as the builder. The admin's token first; the app's service
identity only when Databricks says the token lacks the scope. If the app identity
creates the app, the admin who asked is made its manager.

Unconfirmed against a live workspace (built from memory of the Apps and Workspace
APIs, not run): that the admin's Apps token lacks a scope and so the app identity
does the work (no `apps` user scope is requested: its name is a guess and a bad
scope name breaks the whole scope update); `format: AUTO` importing plain
`.py`/binary files as workspace files; whether the new app's service principal can
read `/Workspace/Shared/agent-portal-mcps/<folder>` without a grant; and that a
Supervisor Agent may call the deployed app without a further grant. Any failure is
reported as Databricks said it. Do not call this verified.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import tarfile
import threading
import time

import yaml

from builder import AGENTS, _add_tools, _check_id, act, list_tools
from dbx import DbxError, app_token, call, http

log = logging.getLogger("portal.mcps")

REPO = os.environ.get("PORTAL_MCP_REPO", "swamy-manikanta-reddy-karri-DB0401/mcps")
REF = os.environ.get("PORTAL_MCP_REF", "main")
# Only needed for a private repo (a read-only token). Never logged or returned.
TOKEN = os.environ.get("PORTAL_MCP_TOKEN", "")

SOURCE_ROOT = "/Workspace/Shared/agent-portal-mcps"
# The start of the description of an app the assistant designer made, so it can be told apart from
# one made by hand (and from the catalog's), and so a retry may replace its own earlier attempt.
GEN_MARK = "Created with the assistant designer."
# A release made by the Portal Deployer carries this client's MCPs, at its
# pinned catalog version, in `mcp_catalog/` (with CATALOG.json saying which
# version). When it is there it is the catalog: only these MCPs are listed or
# installed, nothing is read from GitHub, and no token is needed. Without it
# (a checkout, older releases) the repo is read as before.
LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_catalog")
# Secrets a tool needs (its card's `needs.secrets`, e.g. gam-key) live in this
# scope in the client's workspace. The Portal Deployer puts them there on each
# deploy (e.g. a client's Google Ad Manager connection). A tool's app gets each
# one as a Databricks app resource of the same name, so Databricks hands the
# value to the tool itself and it never passes through the portal.
SECRET_SCOPE = os.environ.get("PORTAL_SECRET_SCOPE", "agent-portal")
_secrets_seen: tuple[float, set] | None = None


def secrets_present(user_tok: str = "") -> set:
    """Secret names in SECRET_SCOPE (names only, never values). Cached a minute."""
    global _secrets_seen
    if _secrets_seen and _secrets_seen[0] > time.time():
        return _secrets_seen[1]
    names: set = set()
    for tok in ([user_tok] if user_tok else []) + [app_token()]:
        try:
            data = call("GET", "/api/2.0/secrets/list", tok, params={"scope": SECRET_SCOPE}, quiet=True)
            names = {x.get("key", "") for x in data.get("secrets") or []}
            break
        except DbxError:
            continue
    _secrets_seen = (time.time() + 60, names)
    return names


def missing_secrets(entry: dict, user_tok: str = "") -> list:
    want = entry["needs"]["secrets"]
    if not want:
        return []
    have = secrets_present(user_tok)
    return [x for x in want if x not in have]


def _resources(entry: dict) -> list:
    return [{"name": x, "secret": {"scope": SECRET_SCOPE, "key": x, "permission": "READ"}}
            for x in entry["needs"]["secrets"] if SECRET_RE.match(x)]


SECRET_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
CACHE_SECONDS = 300
MAX_ARCHIVE = 60 * 1024 * 1024
# The workspace import API takes the file base64-encoded inside a 10 MB request.
MAX_FILE = 7 * 1024 * 1024
SKIP_DIRS = {"__pycache__", ".git", ".venv", "tests", ".pytest_cache", ".ruff_cache"}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
APP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")

GONE = ("DELETING", "DELETED")  # Databricks keeps a deleted app listed for a while


class Removing(DbxError):
    """The app is still being deleted; it cannot be recreated until it is gone."""


_lock = threading.Lock()
_install_lock = threading.Lock()
_installing: set[str] = set()  # apps being installed right now, so two requests do not both do it
_cache: tuple[float, list] | None = None
# Deploys in flight, kept only to show "copying files" and failures. Databricks
# is the source of truth once the deployment is submitted. Lost on restart, which
# is fine: it only decorates a state Databricks already reports.
_progress: dict[str, dict] = {}


# --- reading the repo --------------------------------------------------------

def _local_archive() -> bytes:
    """The shipped catalog folder packed the way GitHub serves the repo (one top
    folder, then a folder per MCP), so everything downstream reads it unchanged."""
    # Only the MCPs CATALOG.json lists: it is written with the release, so a
    # folder left behind by an earlier upload is never offered (seen live).
    listed = shipped().get("mcps")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for root, dirs, files in os.walk(LOCAL):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, LOCAL).replace(os.sep, "/")
                if "/" not in rel:  # CATALOG.json and other top-level files are not MCPs
                    continue
                if isinstance(listed, list) and rel.split("/", 1)[0] not in listed:
                    continue
                tf.add(full, arcname="catalog/" + rel)
    return buf.getvalue()


def shipped() -> dict:
    """Which catalog version this release carries ({} when it reads the repo)."""
    try:
        with open(os.path.join(LOCAL, "CATALOG.json"), encoding="utf-8") as f:
            import json

            return json.load(f)
    except (OSError, ValueError):
        return {}


def _archive() -> bytes:
    if os.path.isdir(LOCAL):
        return _local_archive()
    headers = {"Accept": "application/x-gzip"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    url = "https://codeload.github.com/%s/tar.gz/%s" % (REPO, REF)
    try:
        resp = http().get(url, headers=headers, follow_redirects=True, timeout=60)
    except Exception as exc:  # network trouble, not a Databricks error
        raise DbxError("Could not reach GitHub: " + str(exc)[:120], 502)
    if resp.status_code == 404:
        raise DbxError(
            "The MCP repository %s (%s) was not found. If it is private, set PORTAL_MCP_TOKEN." % (REPO, REF), 502
        )
    if resp.status_code >= 400:
        raise DbxError("GitHub answered %d when downloading the MCP repository." % resp.status_code, 502)
    if len(resp.content) > MAX_ARCHIVE:
        raise DbxError("The MCP repository is larger than %d MB." % (MAX_ARCHIVE // 1048576), 502)
    return resp.content


def _members(blob: bytes):
    """(folder, path inside the folder, member) for every regular file one level
    down. The archive's top directory is `<repo>-<ref>/`, whatever it is called."""
    tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    for m in tf.getmembers():
        if not m.isfile():
            continue
        parts = m.name.split("/")
        if len(parts) < 3 or any(p in ("", ".", "..") for p in parts):
            continue
        yield tf, parts[1], "/".join(parts[2:]), m


def _tools(raw) -> list:
    out = []
    for t in raw if isinstance(raw, list) else []:
        if isinstance(t, dict) and str(t.get("name") or "").strip():
            out.append({
                "name": str(t["name"]).strip(),
                "description": str(t.get("description") or "").strip(),
                "changes_data": bool(t.get("changes_data")),
            })
    return out[:50]


NOT_CONNECTED = ("This tool is not connected yet: it needs %s, which your platform team sets up in the Portal "
                 "Deployer (the client's connection settings), then deploys the portal again.")


def _needs(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "secrets": [str(s) for s in (raw.get("secrets") or [])][:20],
        "volumes": [v for v in dict.fromkeys(str(v).strip().lower() for v in (raw.get("volumes") or [])) if v in ("read", "write")],
    }


def _entry(slug: str, card: dict, has_app_yaml: bool) -> dict:
    app_name = str(card.get("app_name") or "mcp-" + slug).strip()
    problem = ""
    if not SLUG_RE.match(slug):
        problem = "The folder name must use only lowercase letters, numbers and dashes."
    elif not APP_RE.match(app_name):
        problem = "The app name must use only lowercase letters, numbers and dashes."
    elif not has_app_yaml:
        problem = "The folder has no app.yaml, so it cannot be deployed as a Databricks App."
    return {
        "slug": slug,
        "name": str(card.get("name") or slug.replace("-", " ").capitalize()).strip(),
        "description": " ".join(str(card.get("description") or "").split()),
        "version": str(card.get("version") or ""),
        "owner": str(card.get("owner") or ""),
        "app_name": app_name,
        "tools": _tools(card.get("tools")),
        "needs": _needs(card.get("needs")),
        "problem": problem,
    }


def parse_catalog(blob: bytes) -> list:
    """Every folder with an mcp.yaml. Folders starting with `_` (the template) and
    files that are not valid YAML are left out; an undeployable one is listed with
    the reason so the person who wrote it can see why."""
    cards: dict[str, bytes] = {}
    has_app: set[str] = set()
    for tf, folder, rel, m in _members(blob):
        if folder.startswith((".", "_")):
            continue
        if rel == "mcp.yaml":
            cards[folder] = tf.extractfile(m).read()
        elif rel in ("app.yaml", "app.yml"):
            has_app.add(folder)
    out = []
    for slug in sorted(cards):
        try:
            card = yaml.safe_load(cards[slug]) or {}
            if not isinstance(card, dict):
                raise ValueError("not a mapping")
        except (yaml.YAMLError, ValueError) as exc:
            log.warning("MCP %s: mcp.yaml could not be read (%s)", slug, str(exc)[:80])
            continue
        out.append(_entry(slug, card, slug in has_app))
    names: dict[str, str] = {}
    for e in out:  # two folders may not claim one app
        if e["app_name"] in names and not e["problem"]:
            e["problem"] = "The app name %s is also used by %s." % (e["app_name"], names[e["app_name"]])
        names.setdefault(e["app_name"], e["slug"])
    return out


def catalog(refresh: bool = False) -> tuple[list, str]:
    """(entries, note). Cached for a few minutes; a failed refresh serves the last
    good copy with a note rather than emptying the screen."""
    global _cache
    with _lock:
        if _cache and not refresh and time.time() - _cache[0] < CACHE_SECONDS:
            return _cache[1], ""
        try:
            entries = parse_catalog(_archive())
        except DbxError as exc:
            if _cache:
                return _cache[1], "Showing the last list we had. " + str(exc)
            raise
        _cache = (time.time(), entries)
        return entries, ""


# --- what is deployed ---------------------------------------------------------

def _apps(user_tok: str) -> tuple[dict, str]:
    """Every app the caller can see, by name."""
    apps: dict[str, dict] = {}
    token, by = "", "you"
    for _ in range(20):  # pages
        params = {"page_token": token} if token else None
        data, by = act("GET", "/api/2.0/apps", user_tok, params=params)
        for a in data.get("apps") or []:
            apps[a.get("name") or ""] = a
        token = data.get("next_page_token") or ""
        if not token:
            break
    return apps, by


def _state(app: dict | None, prog: dict | None) -> tuple[str, str]:
    """One of not_deployed | deploying | running | stopped | failed, and a line.

    Databricks' own status is trusted over ours: an app that reports RUNNING is
    ready, even if a newer deployment is pending (the old one still serves). Our
    in-memory progress only speaks while we are copying files, which Databricks
    cannot know about yet.
    """
    now = time.time()
    fresh = lambda ph: bool(prog and prog["phase"] == ph and now - prog["at"] < 900)  # noqa: E731
    if prog and prog["phase"] == "failed" and (not app or not app.get("active_deployment")):
        return "failed", prog["message"]
    if not app:
        if prog and prog["phase"] in ("copying", "submitted") and now - prog["at"] < 900:
            return "deploying", prog["message"]
        return "not_deployed", ""
    app_s = app.get("app_status") or {}
    comp_s = app.get("compute_status") or {}
    pend_s = (app.get("pending_deployment") or {}).get("status") or {}
    pend, comp, run = pend_s.get("state", ""), comp_s.get("state", ""), app_s.get("state", "")

    if fresh("copying"):
        return "deploying", prog["message"]
    if comp in GONE or run in GONE:
        return "not_deployed", ""
    if comp in ("STOPPED", "STOPPING"):
        return "stopped", "It is switched off."
    if run == "RUNNING" and comp != "ERROR":
        return "running", ""
    # Seen live (2026-10-09): Databricks could not copy a new tool's files ("Failed to download source code ...
    # request timed out") and the app list showed that deployment as the app's active one. Nothing here knew
    # that is a failure, so the portal waited 20 minutes for an app that was never going to start. Not while a
    # newer deployment is going in, and not in the first minute after one was submitted again.
    dep = (app.get("active_deployment") or {})
    dep_s = dep.get("status") or {}
    last_id = app.get("last_deployment_id") or ""
    again = bool(prog and prog["phase"] == "submitted" and now - prog["at"] < 60)
    if dep_s.get("state") == "FAILED" and (not last_id or last_id == dep.get("deployment_id", "")):
        if again:
            return "deploying", "Installing it."
        return "failed", (dep_s.get("message") or "The deployment did not finish.")[:240]
    if pend in ("FAILED", "CANCELLED"):
        return "failed", (pend_s.get("message") or "The deployment did not finish.")[:200]
    if pend == "IN_PROGRESS" or comp in ("STARTING", "UPDATING"):
        return "deploying", "Installing it."
    if run in ("CRASHED", "UNAVAILABLE") or comp == "ERROR":
        return "failed", (app_s.get("message") or comp_s.get("message") or "")[:200]
    # Submitted, but Databricks has not shown the deployment yet.
    if prog and prog["phase"] == "submitted" and now - prog["at"] < 120 and not app.get("active_deployment"):
        return "deploying", prog["message"]
    # Seen live (2026-10-09): the app *list* no longer carries app_status or pending_deployment, only
    # last_deployment_id beside active_deployment. A newer deployment than the active one is going in;
    # counted as "running" here, a new tool was tried (and folders granted) before its code was live.
    active = app.get("active_deployment") or {}
    last = app.get("last_deployment_id") or ""
    if not run and last and last != active.get("deployment_id", ""):
        return "deploying", "Installing it."
    if not run and comp == "ACTIVE" and ((active.get("status") or {}).get("state") == "SUCCEEDED"):
        return "running", ""
    if not app.get("active_deployment"):
        return "not_deployed", ""
    # It has a deployment and its compute is on, but the status is one we do not
    # recognise. Treat an active app as ready rather than wait forever, and say
    # what was seen so the rule can be corrected.
    log.info("app %s: unrecognised status app=%r compute=%r pending=%r", app.get("name"), run, comp, pend)
    return ("running", "") if comp == "ACTIVE" else ("deploying", app_s.get("message") or "")


def listing(user_tok: str, refresh: bool = False) -> dict:
    entries, note = catalog(refresh)
    apps, by = _apps(user_tok)
    rows = []
    for e in entries:
        state, line = _state(apps.get(e["app_name"]), _progress.get(e["app_name"]))
        app = apps.get(e["app_name"]) or {}
        missing = missing_secrets(e, user_tok) if not e["problem"] and state != "running" else []
        if missing:
            e = {**e, "problem": NOT_CONNECTED % ", ".join(missing)}
        rows.append({**e, "state": state, "state_note": line, "url": app.get("url") or ""})
    # Name where the list really came from: the shipped catalog when there is one.
    src = shipped()
    return {"repo": src.get("repo") or REPO, "ref": src.get("ref") or REF, "mcps": rows, "note": note, "acted_as": by}


# --- deploying -----------------------------------------------------------------

# --- reading what a tool really does -----------------------------------------

_source_cache: dict[str, tuple[float, dict]] = {}


def parse_source(files: dict) -> dict:
    """What a tool's own files say it does, read as text and **never run**.

    The card (`mcp.yaml`) says it in plain words; the server file says what each ability really
    takes. Parsing the server with `ast` gives every `@mcp.tool` function's parameters and docstring,
    which is how the assistant designer tells whether a tool can do what an admin asked, instead
    of guessing from its name. The folders a designer-made tool was built for are read from its
    `_READ_FOLDERS` / `_WRITE_FOLDERS` constants. A file that does not parse yields no abilities
    rather than an error.
    """
    import ast

    readme = files.get("README.md", b"").decode("utf-8", "replace").strip()[:1500]
    abilities = []
    folders = {"read": [], "write": []}
    try:
        tree = ast.parse(files.get("server.py", b"").decode("utf-8", "replace"))
    except (SyntaxError, ValueError):
        tree = None
    for node in tree.body if tree else []:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = {"_READ_FOLDERS": "read", "_WRITE_FOLDERS": "write"}.get(node.targets[0].id)
            if key:
                try:
                    folders[key] = [str(x) for x in ast.literal_eval(node.value)][:20]
                except (ValueError, SyntaxError):
                    pass
    # An ability is a function registered as a tool: with a `@mcp.tool` decorator (how the catalog's
    # tools are written), or by a call, `mcp.tool(...)(fn)` or `mcp.tool(fn)` (how the designer's are,
    # because it wraps model-written functions it does not edit).
    registered: set = set()
    for node in ast.walk(tree) if tree else []:
        if not isinstance(node, ast.Call):
            continue
        outer = node.func
        if isinstance(outer, ast.Call) and isinstance(outer.func, ast.Attribute) and outer.func.attr == "tool":
            registered |= {a.id for a in node.args if isinstance(a, ast.Name)}
        elif isinstance(outer, ast.Attribute) and outer.attr == "tool":
            registered |= {a.id for a in node.args if isinstance(a, ast.Name)}
    for node in ast.walk(tree) if tree else []:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not (node.name in registered or any("tool" in ast.unparse(d) for d in node.decorator_list)):
            continue
        a = node.args
        pos = a.posonlyargs + a.args
        defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
        params = [
            {"name": arg.arg, "type": ast.unparse(arg.annotation) if arg.annotation else "",
             "default": ast.unparse(d) if d is not None else None}
            for arg, d in zip(pos, defaults) if arg.arg not in ("self", "cls")
        ]
        abilities.append({"name": node.name, "parameters": params,
                          "details": " ".join((ast.get_docstring(node) or "").split())[:700]})
    return {"readme": readme, "abilities": abilities[:50], "folders": folders}


def source_summary(slug: str) -> dict:
    """`parse_source` for a catalog tool, cached for the catalog's lifetime."""
    now = time.time()
    hit = _source_cache.get(slug)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    out = parse_source(dict(_files(_archive(), slug)))
    _source_cache[slug] = (now, out)
    return out


# --- tools already installed in the workspace -----------------------------------------------------
# The catalog lists what is in git. A tool can also be running with no entry there: one the assistant
# designer built earlier (its source is kept in the workspace), or one someone deployed by hand. The
# designer must be able to see these, or it offers to build what is already installed.

def _export(path: str, user_tok: str) -> bytes:
    import base64

    data, _ = act("GET", "/api/2.0/workspace/export", user_tok, params={"path": path, "format": "AUTO"}, quiet=True)
    return base64.b64decode(data.get("content") or "")


def installed(user_tok: str) -> list:
    """Tool apps running in this workspace that are not catalog tools, with where each is up to."""
    apps, _ = _apps(user_tok)
    try:
        in_catalog = {e["app_name"] for e in catalog()[0]}
    except DbxError:
        in_catalog = set()
    try:
        data, _ = act("GET", "/api/2.0/workspace/list", user_tok, params={"path": SOURCE_ROOT}, quiet=True)
        kept = {o["path"].rsplit("/", 1)[-1] for o in data.get("objects") or []}
    except DbxError:
        kept = set()
    out = []
    for name, a in sorted(apps.items()):
        if name in in_catalog or not APP_RE.match(name or ""):
            continue
        slug = name[4:] if name.startswith("mcp-") else ""
        made = (a.get("description") or "").startswith(GEN_MARK)
        if not (name.startswith("mcp-") or made):
            continue  # an ordinary app (the portal itself, a deployer): not a tool
        state, note = _state(a, _progress.get(name))
        out.append({"name": name, "description": " ".join((a.get("description") or "").replace(GEN_MARK, "").split()),
                    "state": state, "note": note, "url": a.get("url") or "", "made_by_designer": made,
                    "source_kept": bool(slug and slug in kept)})
    return out


def installed_detail(name: str, user_tok: str, found: dict | None = None) -> dict:
    """One installed tool in full: its card and what each ability takes, when its source is kept.

    Pass `found` (its entry from `installed`) when the list was just read, so it is not read again."""
    import yaml as _yaml

    if not APP_RE.match(name or ""):
        raise DbxError("That is not a tool name.", 400)
    found = found or {t["name"]: t for t in installed(user_tok)}.get(name)
    if not found:
        raise DbxError("There is no installed tool called %s." % name, 404)
    out = {**found, "card": {}, "abilities": [], "folders": {"read": [], "write": []}, "readme": "", "needs": {"secrets": [], "volumes": []}}
    slug = name[4:] if name.startswith("mcp-") else ""
    if not found["source_kept"]:
        return out
    base = SOURCE_ROOT + "/" + slug
    files = {}
    for f in ("mcp.yaml", "server.py", "README.md"):
        try:
            files[f] = _export(base + "/" + f, user_tok)
        except DbxError:
            pass
    try:
        card = _yaml.safe_load(files.get("mcp.yaml", b"")) or {}
    except _yaml.YAMLError:
        card = {}
    card = card if isinstance(card, dict) else {}
    out["card"] = {"name": str(card.get("name") or ""), "tools": _tools(card.get("tools"))}
    out["needs"] = _needs(card.get("needs"))
    out.update(parse_source(files))
    return out

def find(slug: str) -> dict:
    entries, _ = catalog()
    for e in entries:
        if e["slug"] == slug:
            return e
    raise DbxError("That tool is not in the catalog.", 404)


def _files(blob: bytes, slug: str) -> list:
    """The folder's files as (path inside it, bytes). Never trusts an archive path
    that could leave the folder."""
    out, total = [], 0
    for tf, folder, rel, m in _members(blob):
        if folder != slug or any(p in SKIP_DIRS for p in rel.split("/")) or rel.endswith(".pyc"):
            continue
        if m.size > MAX_FILE:
            raise DbxError("%s is larger than %d MB, which is the most that can be copied." % (rel, MAX_FILE // 1048576), 400)
        total += m.size
        if total > MAX_ARCHIVE:
            raise DbxError("The folder is too large to deploy.", 400)
        out.append((rel, tf.extractfile(m).read()))
    if not any(r in ("app.yaml", "app.yml") for r, _ in out):
        raise DbxError("The folder has no app.yaml.", 400)
    return out


def start(slug: str, who: dict, user_tok: str, entry: dict | None = None) -> dict:
    """Make sure the app exists (and is on), then hand the slow part to run().

    Quick on purpose: it runs inside the request. Copying the files and deploying
    takes a while and happens in the background.
    """
    entry = entry or find(slug)  # a tool made by the assistant designer is not in the catalog
    if entry["problem"]:
        raise DbxError(entry["problem"], 400)
    name = entry["app_name"]

    by = "you"
    app = None
    try:
        app, by = act("GET", "/api/2.0/apps/" + name, user_tok, quiet=True)
    except DbxError as exc:
        if exc.status != 404:
            raise
    if app is not None and ((app.get("compute_status") or {}).get("state") in GONE
                            or (app.get("app_status") or {}).get("state") in GONE):
        raise Removing("The previous copy is still being removed.", 409)
    missing = missing_secrets(entry, user_tok)
    if missing:
        raise DbxError(NOT_CONNECTED % ", ".join(missing), 409)
    if app is None:
        body = {"name": name, "description": entry["description"][:500]}
        if _resources(entry):
            body["resources"] = _resources(entry)
        app, by = act("POST", "/api/2.0/apps", user_tok, json=body)
        if by != "you" and who.get("user_name"):
            # Whoever created it owns it; the admin who asked must still be able to manage it.
            try:
                call("PATCH", "/api/2.0/permissions/apps/" + name, app_token(), json={
                    "access_control_list": [{"user_name": who["user_name"], "permission_level": "CAN_MANAGE"}]})
            except DbxError as exc:
                log.warning("could not make %s manager of app %s: %s", who["user_name"], name, str(exc)[:120])
    elif _resources(entry) and {r.get("name") for r in app.get("resources") or []} != {r["name"] for r in _resources(entry)}:
        # An app made before its secrets were wired: give it them now.
        act("PATCH", "/api/2.0/apps/" + name, user_tok, json={"name": name, "resources": _resources(entry)})
    if app is not None and (app.get("compute_status") or {}).get("state") == "STOPPED":
        try:
            act("POST", "/api/2.0/apps/%s/start" % name, user_tok)
        except DbxError as exc:
            log.warning("could not start app %s: %s", name, str(exc)[:120])

    _progress[name] = {"phase": "copying", "message": "Copying the files.", "at": time.time()}
    return {"entry": entry, "acted_as": by}


ACTIVE_WAIT = 10 * 60


def _ensure_active(name: str, user_tok: str) -> None:
    """Switch the app on if it is off, and wait until its compute is running."""
    deadline = time.time() + ACTIVE_WAIT
    started = False
    last = None
    while True:
        app, _ = act("GET", "/api/2.0/apps/" + name, user_tok, quiet=True)
        comp_s = app.get("compute_status") or {}
        comp = comp_s.get("state", "")
        if comp != last:
            log.info("app %s: compute is %s", name, comp or "(not reported)")
            last = comp
        if comp == "ACTIVE":
            return
        if comp == "STOPPED" and not started:
            act("POST", "/api/2.0/apps/%s/start" % name, user_tok)
            started = True
        elif comp == "ERROR":
            raise DbxError("The app could not be started: " + (comp_s.get("message") or "no reason given")[:160], 502)
        elif comp in GONE:
            raise DbxError("The app was removed while it was being set up.", 409)
        if time.time() > deadline:
            raise DbxError("The app did not start within %d minutes." % (ACTIVE_WAIT // 60), 504)
        time.sleep(5)


def _submit(name: str, base: str, user_tok: str) -> None:
    """Ask Databricks to deploy the app from the files in `base`."""
    for attempt in range(6):
        try:
            act("POST", "/api/2.0/apps/%s/deployments" % name, user_tok,
                json={"source_code_path": base, "mode": "SNAPSHOT"})
            return
        except DbxError as exc:
            # "not in RUNNING state": compute reported on a moment too early.
            if exc.status == 400 and "RUNNING state" in str(exc) and attempt < 5:
                time.sleep(10)
                continue
            raise


DEPLOY_RETRIES = 2
TRANSIENT = re.compile(r"download(?:ing)? source|timed out|timeout|temporar|connection (?:reset|refused)|\b50[234]\b|try again", re.I)


def transient(message: str) -> bool:
    """Does a failed deployment's message look like Databricks having a bad moment (so trying again is
    sensible) and not like something wrong with the tool's own files?"""
    return bool(TRANSIENT.search(message or ""))


def redeploy(entry: dict, user_tok: str) -> None:
    """Submit the same deployment again. The files are already in the workspace, so nothing is copied."""
    name = entry["app_name"]
    _progress[name] = {"phase": "copying", "message": "Trying the installation again.", "at": time.time()}
    _ensure_active(name, user_tok)
    _progress[name] = {"phase": "submitted", "message": "Installing it.", "at": time.time()}
    _submit(name, SOURCE_ROOT + "/" + entry["slug"], user_tok)


def run(entry: dict, user_tok: str, files: list | None = None) -> None:
    """Background: download the source (or take `files` already in hand, for a tool
    that is not in the catalog), copy it into the workspace, deploy it."""
    name, slug = entry["app_name"], entry["slug"]
    base = SOURCE_ROOT + "/" + slug
    try:
        files = files if files is not None else _files(_archive(), slug)
        log.info("MCP %s: copying %d files to %s", slug, len(files), base)
        # Replace the folder wholesale so a file removed from git is removed here too.
        try:
            act("POST", "/api/2.0/workspace/delete", user_tok, json={"path": base, "recursive": True}, quiet=True)
        except DbxError:
            pass  # nothing there yet
        made = set()
        for rel, data in files:
            folder = base + ("/" + rel.rsplit("/", 1)[0] if "/" in rel else "")
            if folder not in made:
                act("POST", "/api/2.0/workspace/mkdirs", user_tok, json={"path": folder})
                made.add(folder)
            act("POST", "/api/2.0/workspace/import", user_tok, json={
                "path": base + "/" + rel,
                "format": "AUTO",
                "overwrite": True,
                "content": base64.b64encode(data).decode("ascii"),
            })
        # Databricks refuses a deployment until the app's compute is on, and a new
        # app takes a minute or two to start.
        _progress[name] = {"phase": "copying", "message": "Starting it.", "at": time.time()}
        _ensure_active(name, user_tok)
        log.info("MCP %s: submitting the deployment to app %s", slug, name)
        _progress[name] = {"phase": "submitted", "message": "Installing it.", "at": time.time()}
        _submit(name, base, user_tok)
        log.info("MCP %s deployed to app %s (%d files)", slug, name, len(files))
    except DbxError as exc:
        _progress[name] = {"phase": "failed", "message": str(exc)[:240], "at": time.time()}
        log.warning("deploying MCP %s failed: %s", slug, str(exc)[:200])
    except Exception as exc:  # a bug here must still surface as a failed card, not a stuck spinner
        _progress[name] = {"phase": "failed", "message": "Unexpected error: " + str(exc)[:160], "at": time.time()}
        log.exception("deploying MCP %s crashed", slug)


# --- choosing tools while building an assistant -------------------------------

def _exists(name: str, user_tok: str) -> bool:
    """Ask for the app itself: the list can lag behind a deletion."""
    try:
        app, _ = act("GET", "/api/2.0/apps/" + name, user_tok, quiet=True)
    except DbxError as exc:
        if exc.status == 404:
            return False
        raise
    return not ({(app.get("compute_status") or {}).get("state"), (app.get("app_status") or {}).get("state")} & set(GONE))


def plan(tools: list, user_tok: str, derive: bool = False) -> tuple[list, list]:
    """Split the chosen tools: (attach now, still to deploy).

    A tool picked from the catalog carries its folder name in `mcp`. Its app name
    is taken from the catalog here, never from what the browser sent, so a request
    cannot make the portal deploy or attach an arbitrary app. One whose app is
    already running is attached straight away; any other needs deploying first.

    `derive`: also treat an `app` tool whose name matches a catalog app as a
    catalog tool when the caller did not flag it (a new assistant has nothing
    attached yet, so that is safe). For an existing assistant only flagged tools
    are planned, so tools it already has are never detached and re-added.
    """
    entries = {e["slug"]: e for e in catalog()[0]} if any(
        t.get("mcp") or (derive and t.get("type") == "app") for t in tools) else {}
    if not entries:
        return tools, []
    by_app = {e["app_name"]: e["slug"] for e in entries.values()}
    apps, _ = _apps(user_tok)
    now, later = [], []
    for t in tools:
        slug = t.get("mcp") or (by_app.get(t.get("ref")) if derive and t.get("type") == "app" else None)
        if not slug:
            now.append(t)
            continue
        e = entries.get(slug)
        if not e:
            raise DbxError("The tool '%s' is no longer in the catalog. Refresh and choose again." % slug, 400)
        if e["problem"]:
            raise DbxError("%s cannot be used yet: %s" % (e["name"], e["problem"]), 400)
        t = {**t, "ref": e["app_name"], "mcp": slug}
        state, _ = _state(apps.get(e["app_name"]), _progress.get(e["app_name"]))
        listed = state
        if state == "running" and not _exists(e["app_name"], user_tok):
            state = "not_deployed"  # still in the list, but gone (just deleted)
        log.info("tool %s: app %s listed as %s, now %s", slug, e["app_name"], listed, state)
        if state == "running":
            now.append(t)
        else:
            later.append({"tool": t, "entry": e, "state": state})
    return now, later


def defer(tools: list) -> tuple[list, list]:
    """Set every catalog tool aside to be installed, whatever we believed about it.

    The last resort when Databricks refuses to attach a tool because its app does
    not exist: whatever made the portal think it was there was wrong.
    """
    entries = catalog()[0]
    by_app = {e["app_name"]: e for e in entries if not e["problem"]}
    now, later = [], []
    for t in tools:
        e = by_app.get(t.get("ref")) if t.get("type") == "app" else None
        if e:
            later.append({"tool": {**t, "mcp": e["slug"]}, "entry": e, "state": "not_deployed"})
        else:
            now.append(t)
    return now, later


def needs_install(state: str) -> bool:
    return state in ("not_deployed", "failed", "stopped")


def install(entry: dict, who: dict, user_tok: str, files: list | None = None) -> bool:
    """Create (or switch on) the app and deploy it. Returns once the deployment is
    submitted, False if another request is already installing the same app.

    Waits for a just-deleted app to finish going away (~5 minutes at most) before
    creating it again. Any failure is recorded for the tool's card and re-raised.
    """
    name = entry["app_name"]
    with _install_lock:
        if name in _installing:
            return False
        _installing.add(name)
    log.info("MCP %s: installing as app %s", entry["slug"], name)
    try:
        for _ in range(30):
            try:
                start(entry["slug"], who, user_tok, entry=entry if files is not None else None)
            except Removing:
                _progress[name] = {"phase": "copying", "message": "Waiting for the old copy to go away.", "at": time.time()}
                time.sleep(10)
                continue
            run(entry, user_tok, files)  # records its own failure
            return True
        raise DbxError("the previous copy was still being removed after 5 minutes", 504)
    except DbxError as exc:
        _progress[name] = {"phase": "failed", "message": str(exc)[:240], "at": time.time()}
        raise
    finally:
        with _install_lock:
            _installing.discard(name)


def prepare(slugs: list, user_tok: str) -> list:
    """Start getting these catalog tools ready; return the entries being installed.

    Used before an assistant is created, so it is only made once every tool it
    needs is available. Ones already running are left alone. The card shows
    "getting ready" at once (before the slow work begins), and any earlier failure
    is cleared so a retry is not met with the old error.
    """
    slugs = [s for s in dict.fromkeys(slugs) if s]
    if not slugs:
        return []
    _, later = plan([{"type": "app", "ref": "x", "description": "", "mcp": s} for s in slugs], user_tok)
    started = []
    for p in later:
        if needs_install(p["state"]):
            _progress[p["entry"]["app_name"]] = {"phase": "copying", "message": "Getting it ready.", "at": time.time()}
            started.append(p["entry"])
    return started


def install_quietly(entry: dict, who: dict, user_tok: str) -> None:
    """Background wrapper: the failure is already recorded on the tool; just log it."""
    try:
        install(entry, who, user_tok)
    except DbxError as exc:
        log.warning("could not get MCP %s ready: %s", entry["slug"], str(exc)[:200])
    except Exception:
        _progress[entry["app_name"]] = {"phase": "failed", "message": "Something went wrong.", "at": time.time()}
        log.exception("getting MCP %s ready crashed", entry["slug"])


def deploy_and_attach(agent_id: str, pending: list, who: dict, user_tok: str, volumes: dict | None = None) -> None:
    """Background: deploy whatever is missing, then add each tool to the assistant.

    The assistant already exists and works without these tools; each one appears
    when its app is running. A failure is logged and shown on the tool's card the
    next time the catalog is opened; saving the assistant again retries it.
    """
    _check_id(agent_id)
    for p in pending:
        e = p["entry"]
        name = e["app_name"]
        try:
            if p["state"] in ("not_deployed", "failed", "stopped"):
                install(e, who, user_tok)  # a no-op if another request is already doing it
            deadline = time.time() + 20 * 60
            while True:
                apps, _ = _apps(user_tok)
                state, line = _state(apps.get(name), _progress.get(name))
                if state == "running":
                    break
                if state == "failed":
                    raise DbxError(line or "the deployment failed", 502)
                if time.time() > deadline:
                    raise DbxError("it was still not running after 20 minutes", 504)
                time.sleep(10)
            if volumes and e["needs"]["volumes"]:
                grant_volumes([e], volumes, user_tok)  # before the first call can reach for a folder
            existing, _ = list_tools(agent_id, user_tok)
            used = {(t.get("tool_id") or (t.get("name") or "").rsplit("/", 1)[-1]) for t in existing}
            _add_tools(agent_id, [p["tool"]], used, user_tok)
            log.info("MCP %s is running and was added to assistant %s", e["slug"], agent_id)
        except DbxError as exc:
            log.warning("could not add MCP %s to assistant %s: %s", e["slug"], agent_id, str(exc)[:200])
        except Exception:  # never leave a background task dying silently
            log.exception("adding MCP %s to assistant %s crashed", e["slug"], agent_id)


# --- giving an installed app access to the assistant's folders ----------------

VOLUME_RE = re.compile(r"^[\w\-]+\.[\w\-]+\.[\w\-]+$")
UC = "/api/2.1/unity-catalog/permissions/"


def volumes_for(spec: dict) -> dict:
    """The folders an assistant works with: where people's files go (read) and
    where results are saved (write). Same fallback as the file tags: an assistant
    with a folder tool reads from that folder."""
    files = spec.get("files") or {}
    read = {files.get("upload_volume") or ""}
    read |= {t["ref"] for t in spec.get("tools") or [] if t.get("type") == "volume"}
    write = {files.get("output_volume") or ""}
    ok = lambda vs: sorted(v for v in vs if VOLUME_RE.match(v or ""))  # noqa: E731
    return {"read": ok(read), "write": ok(write)}


def as_catalog_tools(tools: list) -> list:
    """Flag `app` tools that match a catalog app by name, so tools an assistant
    already has (loaded without the flag) are recognised when editing it."""
    by_app = {e["app_name"]: e["slug"] for e in catalog()[0]}
    return [{**t, "mcp": by_app[t["ref"]]} if t.get("type") == "app" and not t.get("mcp") and t.get("ref") in by_app else t
            for t in tools]


def users_of_volumes(tools: list) -> list:
    """Catalog entries among these tools that work with folders."""
    slugs = {t.get("mcp") for t in tools if t.get("mcp")}
    return [e for e in catalog()[0] if e["slug"] in slugs and e["needs"]["volumes"]]


def _principal(name: str, user_tok: str) -> str:
    app, _ = act("GET", "/api/2.0/apps/" + name, user_tok, quiet=True)
    return app.get("service_principal_client_id") or ""


_SQL_PRIV = {"USE_CATALOG": "USE CATALOG", "USE_SCHEMA": "USE SCHEMA", "READ_VOLUME": "READ VOLUME",
             "WRITE_VOLUME": "WRITE VOLUME"}


def _grant(kind: str, name: str, principal: str, privilege: str, user_tok: str) -> None:
    """Give `principal` one privilege, as the admin.

    Seen live (2026-10-08, deployed portal): the admin's Apps token has no scope for the UC
    permissions API ("does not have required scopes: unity-catalog"; Apps offers no user scope
    for it), so `act` retried as the portal's identity, which does not manage the admin's
    catalog and was refused too. Every folder grant failed and the new tool could not read the
    file it was built for. The same grant as a SQL statement goes through the admin's `sql`
    scope, and Databricks still decides whether this admin may grant it."""
    try:
        act("PATCH", UC + kind + "/" + name, user_tok,
            json={"changes": [{"principal": principal, "add": [privilege]}]})
        return
    except DbxError as exc:
        if exc.status not in (401, 403) or privilege not in _SQL_PRIV:
            raise
        first = exc
    if not (VOLUME_RE.match(name) or re.match(r"^[\w\-]+(\.[\w\-]+)?$", name)) or not re.match(r"^[\w\-.@]+$", principal):
        raise first
    ident = ".".join("`%s`" % p for p in name.split("."))
    stmt = "GRANT %s ON %s %s TO `%s`" % (_SQL_PRIV[privilege], kind.upper(), ident, principal)
    try:
        _run_sql_as(stmt, user_tok)
    except DbxError as exc:
        raise DbxError("%s; as SQL: %s" % (str(first)[:120], str(exc)[:160]), exc.status or 403)
    log.info("granted %s on %s %s to %s through SQL", privilege, kind, name, principal)


_wh_cache: dict = {}


def _run_sql_as(statement: str, tok: str) -> None:
    """Run one statement as `tok` on a warehouse it may use (a running one first)."""
    key = tok[-16:]
    wh = _wh_cache.get(key)
    if not wh:
        whs = (call("GET", "/api/2.0/sql/warehouses", tok, quiet=True) or {}).get("warehouses") or []
        if not whs:
            raise DbxError("you can use no SQL warehouse to make the grant with", 403)
        wh = next((w["id"] for w in whs if w.get("state") == "RUNNING"), whs[0]["id"])
        _wh_cache[key] = wh
    r = call("POST", "/api/2.0/sql/statements", tok, quiet=True, json={
        "warehouse_id": wh, "statement": statement, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(30):
        state = (r.get("status") or {}).get("state")
        if state == "SUCCEEDED":
            return
        if state in ("FAILED", "CANCELED", "CLOSED"):
            raise DbxError(((r.get("status") or {}).get("error") or {}).get("message") or state, 403)
        time.sleep(2)
        r = call("GET", "/api/2.0/sql/statements/" + str(r.get("statement_id")), tok, quiet=True)
    raise DbxError("the grant was still running after a minute", 504)


def grant_volumes(entries: list, volumes: dict, user_tok: str) -> list:
    """Give each installed app the folder access its card says it needs.

    Idempotent (a grant that already exists is a no-op) and best effort: one
    refusal is recorded and the rest still go ahead. Returns what could not be
    done, as plain sentences; also logged, since nothing waits on this.
    """
    problems: list[str] = []
    for e in entries:
        needs = set(e["needs"]["volumes"])
        want = []
        if "read" in needs:
            want += [(v, "READ_VOLUME") for v in volumes.get("read") or []]
        if "write" in needs:
            # Seen live (Oct 2026): writing a file through the Files API with only
            # WRITE_VOLUME is a 403; Databricks needs READ_VOLUME on it as well.
            for v in volumes.get("write") or []:
                want += [(v, "READ_VOLUME"), (v, "WRITE_VOLUME")]
        if not want:
            continue
        try:
            sp = _principal(e["app_name"], user_tok)
        except DbxError as exc:
            sp, why = "", str(exc)[:120]
        else:
            why = "Databricks did not say which identity the app runs as."
        if not sp:
            problems.append("%s: could not find the app's identity (%s)" % (e["name"], why))
            continue
        seen: set = set()
        for vol, priv in want:
            cat, schema = vol.split(".")[0], ".".join(vol.split(".")[:2])
            steps = [("catalog", cat, "USE_CATALOG"), ("schema", schema, "USE_SCHEMA"), ("volume", vol, priv)]
            for kind, name, p in steps:
                if (kind, name, p) in seen:
                    continue
                seen.add((kind, name, p))
                try:
                    _grant(kind, name, sp, p, user_tok)
                except DbxError as exc:
                    problems.append("%s: could not give the app %s on %s %s (%s). Grant it in Databricks to %s."
                                    % (e["name"], p, kind, name, str(exc)[:100], sp))
        log.info("folder access for %s (%s): %d grants tried, %d refused",
                 e["app_name"], sp, len(seen), len([x for x in problems if x.startswith(e["name"])]))
    for line in problems:
        log.warning("%s", line)
    return problems
