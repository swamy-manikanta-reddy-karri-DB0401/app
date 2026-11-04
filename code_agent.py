"""Developer mode: an AI writes an agent's code (the idea of Mia's studio, built the portal's way).

A developer says in words what the agent should do. The code model writes the agent's own code into the project the
export produces (agent_export.project_files, verified running inside a Databricks App), the portal checks it as
text, the developer reads the diff and approves, and it is deployed through the upload path (code_upload.py) and
tested against the live app. A failed test or a change asked in words comes back as edits, shown and approved again.

What is different from Mia, on purpose:
- **The AI's code never runs in the portal.** Mia's coding agent runs commands with `sandbox: "danger-full-access"`
  inside its own app ("bubblewrap can't sandbox in-container"). Here the code is only parsed (`ast`), never imported
  or run; the first place it runs is its own Databricks App, as its own identity, reached with the person's token.
  The price is speed: each try is a deployment (about 40 s for a new version) instead of a local run.
- **The parts that decide identity and reach are fixed.** `app.py` (the person's token, fail closed), `agent/tools.py`
  (every call as the person, changing tools need approval, saving only into declared folders), `agent/config.json`
  (which tools, data, documents, folders and websites) and the test runner are rebuilt by the portal from the setup
  every time; the browser only ever sends the AI-editable files, so they cannot be swapped. The AI writes
  `agent/logic.py` (its own abilities: every public typed, documented function), may change `agent/agent.py` and add
  helper modules under `agent/`.
- **What it can reach is declared and shown before approval**: the setup's tools, data, documents, folders it may
  save into and websites, plus the admins' rules for the designer (allowed websites, whether tools may save or change
  things, their plain-words rules judged against the code by `designer_guard.review_plan`).

The static check is a guard rail, not a sandbox (same as forge.py): no eval/exec/open/getattr/dunder tricks, no
subprocess/socket/pickle/sys, `os` only for `os.environ`, imports from an allowlist, websites only if declared, no key
in any file. Requirements are built from the imports found, never written by the model.

Jobs are in memory (a restart loses one in progress; the page says so). Nothing is stored: the developer's words go
to the code model under their own token and are never logged; only the deployed app is recorded (`uploaded_agent`).
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import threading
import time

import agent_export
import code_upload
import custom_agents
import designer
import designer_guard
import designer_models as dm
import forge
import mcps
from dbx import DbxError

log = logging.getLogger("portal.code_agent")
HERE = os.path.dirname(os.path.abspath(__file__))

# Rebuilt from the setup every time; never taken from the browser.
FIXED = ("app.py", "app.yaml", "agent/__init__.py", "agent/tools.py", "agent/config.json", "tests/run_tests.py")
GENERATED = ("requirements.txt", "README.md", "tests/conversations.json")
EDITABLE = ("agent/logic.py", "agent/agent.py")
NEW_FILE_RE = re.compile(r"^agent/[a-z][a-z0-9_]{1,40}\.py$")
MAX_FILES = 8
MAX_CHARS = 60000
CODE_TOKENS = 20000
EDIT_ROUNDS = 4
MAX_TESTS = 10

# What agent code may import, on top of forge.STD_OK and forge.THIRD_PARTY: the libraries the project runs on,
# and its own modules.
FRAMEWORK_OK = {"mlflow", "databricks", "databricks_mcp", "databricks_langchain", "langchain", "langchain_core",
                "langgraph", "openai", "agent", "tools", "logic"}
PINNED = {"httpx": "httpx==0.28.1", "openpyxl": "openpyxl==3.1.5", "pypdf": "pypdf==6.21.4"}
URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")
FOLDER_RE = re.compile(r"^/Volumes/[\w-]+/[\w-]+/[\w-]+(/[\w .-]+)*$")
_jobs: dict = {}
_lock = threading.Lock()
BACKGROUND = True   # False runs jobs inside the call (tests)


# --- the project -------------------------------------------------------------------------------------------------

def clean_setup(raw: dict) -> dict:
    """What the agent is built on, checked. Raises DbxError(400) in plain words."""
    raw = raw if isinstance(raw, dict) else {}
    name = " ".join(str(raw.get("name") or "").split())[:80]
    if len(name) < 2:
        raise DbxError("Give the agent a name.", 400)
    framework = raw.get("framework") or "mlflow"
    if framework not in agent_export.FRAMEWORKS:
        raise DbxError("Choose MLflow or LangGraph.", 400)
    task = str(raw.get("task") or "").strip()[:6000]
    if len(task) < 20:
        raise DbxError("Say in a few sentences what the agent should do.", 400)
    folders = []
    for f in raw.get("write_folders") or []:
        f = str(f).strip().rstrip("/")
        if not FOLDER_RE.match(f) or ".." in f.split("/"):
            raise DbxError("%s is not a folder in a volume (/Volumes/catalog/schema/volume/...)." % f[:80], 400)
        folders.append(f)
    hosts = []
    for h in raw.get("websites") or []:
        h = str(h).strip().lower()
        if not forge.HOST_RE.match(h.lstrip("*.")):
            raise DbxError("%s is not a website name (for example api.example.com)." % h[:80], 400)
        hosts.append(h)
    return {"name": name, "description": " ".join(str(raw.get("description") or "").split())[:300] or name,
            "framework": framework, "source": str(raw.get("source") or "").strip(), "model": str(raw.get("model") or "").strip(),
            "task": task, "write_folders": folders[:5], "websites": hosts[:10]}


def base(setup: dict, tok: str, urls: dict | None = None) -> dict:
    """{path: text}: the project the code is written into, rebuilt from the setup (so fixed files are always ours)."""
    if setup["source"]:
        rec = custom_agents.latest(setup["source"])
        if not rec:
            raise DbxError("That custom agent was not found.", 404)
        spec = dict(rec["spec"])
        agent_id, version = rec["agent_id"], rec["version"]
    else:
        if not setup["model"]:
            raise DbxError("Choose the AI model the agent will use.", 400)
        spec = {"instructions": "", "rules": [], "documents": [], "tools": [], "data": [], "assistants": [], "tests": [],
                "limits": {"steps": 10, "reply_tokens": 4000}}
        agent_id, version = "", 0
    if setup["model"]:
        spec["model"] = setup["model"]
    spec.update(name=setup["name"], description=setup["description"], write_folders=setup["write_folders"],
                websites=setup["websites"], tests=[])
    if urls is None and spec.get("tools"):
        try:
            apps, _ = mcps._apps(tok)
            urls = {n: a.get("url") for n, a in apps.items() if a.get("url")}
        except DbxError as exc:
            log.warning("code agent: could not read the tool apps' addresses: %s", exc)
    files, _ = agent_export.project_files({"agent_id": agent_id, "version": version, "spec": spec}, setup["framework"], urls)
    return files


def assemble(setup: dict, code: dict, tok: str, tests: list | None = None, urls: dict | None = None) -> dict:
    """The whole project: our fixed files, the AI-editable ones from `code`, requirements from the imports."""
    files = base(setup, tok, urls)
    for path, text in (code or {}).items():
        if path not in FIXED and path not in GENERATED:
            files[path] = text
    files["requirements.txt"] = requirements(files, setup["framework"])
    files["tests/conversations.json"] = json.dumps([{"question": t["question"], "then": [], "expect": t["expect"]}
                                                    for t in tests or []], indent=2, ensure_ascii=False)
    return files


def editable(files: dict) -> dict:
    return {p: t for p, t in files.items() if p in EDITABLE or (NEW_FILE_RE.match(p) and p not in FIXED)}


def requirements(files: dict, framework: str) -> str:
    pins = list(agent_export.PINS["common"] + agent_export.PINS[framework])
    have = {p.split("==")[0].lower() for p in pins}
    for path, text in editable(files).items():
        for mod in _imports(text):
            pkg = forge.THIRD_PARTY.get(mod)
            if pkg and pkg.lower() not in have:
                pins.append(PINNED.get(mod, pkg))
                have.add(pkg.lower())
    return "\n".join(pins) + "\n"


def fingerprint(setup: dict, code: dict) -> str:
    """What the developer approves: the setup (what it can reach) and the agent's own code. The test list is not in
    it, so a test can be edited without approving the code again."""
    body = {"setup": {k: v for k, v in setup.items() if k != "task"}, "code": sorted(editable(code).items())}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


# --- the check -----------------------------------------------------------------------------------------------------

def _imports(text: str) -> list:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.append(n.module.split(".")[0])
    return out


def _host_ok(host: str, allowed: list) -> bool:
    host = host.lower()
    return any(host == a or (a.startswith("*.") and host.endswith(a[1:])) for a in allowed)


def check(setup: dict, files: dict, rules: dict | None = None) -> dict:
    """{problems, report}. Problems are plain sentences, also told to the code model to fix."""
    rules = rules or designer_guard.settings()
    problems: list[str] = []
    code = editable(files)
    extra = [p for p in files if p not in code and p not in FIXED and p not in GENERATED and p != "agent/runbook.md"]
    if extra:
        problems.append("Only agent/logic.py, agent/agent.py and new modules agent/<name>.py may be written; not %s." % ", ".join(extra[:4]))
    if len(code) > MAX_FILES:
        problems.append("Keep it to at most %d files of the agent's own code." % MAX_FILES)
    if "agent/logic.py" not in code or "agent/agent.py" not in code:
        problems.append("agent/logic.py and agent/agent.py must both be there.")
    allowed_hosts = setup["websites"]
    if rules.get("allowed_hosts"):
        for h in setup["websites"]:
            if not _host_ok(h.lstrip("*."), rules["allowed_hosts"]):
                problems.append("Your admins only allow these websites: %s; %s is not one." % (", ".join(rules["allowed_hosts"]), h))
    if setup["write_folders"] and not rules.get("allow_changes_data", True):
        problems.append("Your admins do not allow tools that save or change things, so this agent may not save files.")
    abilities, third, seen_hosts = [], set(), set()
    for path, text in sorted(code.items()):
        if len(text) > MAX_CHARS:
            problems.append("%s is longer than %d characters." % (path, MAX_CHARS))
            continue
        if any(p.search(text) for p in code_upload.CODE_KEYS):
            problems.append("%s contains something that looks like a password or key. Keys belong in the secret scope." % path)
        try:
            tree = ast.parse(text, path)
        except SyntaxError as exc:
            problems.append("%s line %s is not valid Python: %s" % (path, exc.lineno, exc.msg))
            continue
        _walk(path, tree, text, problems, third, seen_hosts)
        if path == "agent/logic.py":
            abilities = _abilities_of(tree, problems)
    for h in sorted(seen_hosts):
        if not _host_ok(h, allowed_hosts):
            problems.append("The code calls %s, which is not one of the websites it may use. Add it to the setup or remove it." % h)
    if third & {"httpx"} and not allowed_hosts:
        problems.append("The code imports httpx but the agent may not call any website. Remove it, or add the website to the setup.")
    report = {"abilities": abilities, "packages": sorted(third), "websites": setup["websites"],
              "write_folders": setup["write_folders"], "files": sorted(code)}
    return {"problems": problems[:20], "report": report}


def _walk(path: str, tree, text: str, problems: list, third: set, hosts: set) -> None:
    lines = text.splitlines()

    def bad(node, why):
        ln = getattr(node, "lineno", 0)
        quote = lines[ln - 1].strip()[:120] if 0 < ln <= len(lines) else ""
        problems.append("%s line %d (%s): %s" % (path, ln, quote, why))

    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else ([n.module] if n.module and not n.level else [])
            for m in mods:
                root = m.split(".")[0]
                if root in forge.THIRD_PARTY:
                    third.add(root)
                elif root == "os" or m in forge.STD_OK or root in forge.STD_OK or root in FRAMEWORK_OK:
                    pass
                else:
                    bad(n, "%s may not be imported here." % m)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in forge.DENY_NAMES:
            bad(n, "%s() is not allowed." % n.func.id)
        elif isinstance(n, ast.Attribute):
            if n.attr in forge.DENY_ATTRS or (n.attr.startswith("__") and n.attr not in forge.OK_DUNDER):
                bad(n, ".%s is not allowed." % n.attr)
            elif isinstance(n.value, ast.Name) and n.value.id == "os" and n.attr != "environ":
                bad(n, "only os.environ may be used from os.")
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            bad(n, "global and nonlocal are not allowed.")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            for h in URL_RE.findall(n.value):
                hosts.add(h.lower())


def _abilities_of(tree, problems: list) -> list:
    out = []
    for n in tree.body:
        if isinstance(n, ast.AsyncFunctionDef) and not n.name.startswith("_"):
            problems.append("agent/logic.py: %s must be a plain (not async) function." % n.name)
        if not isinstance(n, ast.FunctionDef) or n.name.startswith("_"):
            continue
        doc = ast.get_docstring(n) or ""
        if not doc:
            problems.append("agent/logic.py: %s needs a docstring saying when the agent should use it." % n.name)
        if any(a.annotation is None for a in n.args.args):
            problems.append("agent/logic.py: every input of %s needs a type (str, int, float, bool, list or dict)." % n.name)
        out.append({"name": n.name, "about": doc.split("\n")[0][:200], "inputs": [a.arg for a in n.args.args]})
    return out


def diff(old: dict, new: dict) -> list:
    """[{path, state: new|changed|same, lines}] for the AI-editable files."""
    out = []
    for path in sorted(set(editable(old)) | set(editable(new))):
        a, b = old.get(path), new.get(path)
        if b is None:
            out.append({"path": path, "state": "removed", "lines": []})
        elif a is None:
            out.append({"path": path, "state": "new", "lines": forge.code_diff("", b, limit=400, names=("before", "after"))})
        elif a != b:
            out.append({"path": path, "state": "changed", "lines": forge.code_diff(a, b, limit=400, names=("before", "after"))})
        else:
            out.append({"path": path, "state": "same", "lines": []})
    return out


# --- the code model ------------------------------------------------------------------------------------------------

FILE_RE = re.compile(r"=== FILE (\S+) ===\n(.*?)\n=== END FILE ===", re.S)
EDIT_BLOCK_RE = re.compile(r"=== EDIT (\S+) ===\n(.*?)=== END EDIT ===", re.S)
TESTS_RE = re.compile(r"=== TESTS ===\s*(\[.*?\])\s*(?:===|$)", re.S)
SUMMARY_RE = re.compile(r"=== SUMMARY ===\s*(.+?)\s*(?:===|$)", re.S)


def _prompt() -> str:
    with open(os.path.join(HERE, "code_agent_prompt.md"), encoding="utf-8") as f:
        return f.read()


def _fixed_api(files: dict) -> str:
    """What agent/tools.py offers, as signatures and first docstring lines (the model does not need its insides)."""
    tree = ast.parse(files["agent/tools.py"])
    out = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_"):
            out.append("tools.%s(%s)  # %s" % (n.name, ast.unparse(n.args), (ast.get_docstring(n) or "").split("\n")[0][:160]))
    return "\n".join(out)


def _context(setup: dict, files: dict) -> dict:
    cfg = json.loads(files["agent/config.json"])
    return {
        "what_the_agent_should_do": setup["task"], "framework": setup["framework"],
        "what_it_can_use": {k: cfg.get(k) for k in ("model", "tools", "data", "documents", "assistants", "write_folders", "websites")},
        "instructions_and_rules_already_given": {"instructions": cfg.get("instructions"), "rules": cfg.get("rules"),
                                                 "has_runbook": bool(cfg.get("runbook"))},
        "tools_py_offers": _fixed_api(files),
        "files_you_may_write": editable(files),
    }


def _strip_fence(text: str) -> str:
    m = re.match(r"^```(?:python|py)?\n(.*?)\n?```\s*$", text.strip(), re.S)
    return m.group(1) if m else text


def apply_reply(files: dict, reply: str) -> tuple[dict, list]:
    """Whole files and SEARCH/REPLACE edits from the model's reply applied to `files`. Returns (files, problems)."""
    out, problems = dict(files), []
    for path, body in FILE_RE.findall(reply or ""):
        out[path] = _strip_fence(body) + "\n"
    for path, body in EDIT_BLOCK_RE.findall(reply or ""):
        if path not in out:
            problems.append("%s does not exist, so it cannot be edited; send the whole file instead." % path)
            continue
        fixed = designer.apply_edits(out[path], body)
        if fixed is None:
            problems.append("An edit to %s did not match exactly one place; copy the SEARCH lines exactly." % path)
        else:
            out[path] = fixed
    return out, problems


def _kept_fixed(before: dict, after: dict) -> list:
    """A fixed file the model tried to change is put back, and the model is told (the change would be lost)."""
    out = []
    for p in FIXED + GENERATED:
        if p in before and after.get(p) != before[p]:
            after[p] = before[p]
            out.append("%s is fixed and was put back: the portal writes it. Put what you meant in agent/logic.py or agent/agent.py." % p)
    return out


def tests_from(reply: str) -> list:
    m = TESTS_RE.search(reply or "")
    try:
        raw = json.loads(m.group(1)) if m else []
    except ValueError:
        raw = []
    return clean_tests(raw)


def clean_tests(raw) -> list:
    out = []
    for t in raw if isinstance(raw, list) else []:
        q = " ".join(str((t or {}).get("question") or "").split())[:1000]
        e = " ".join(str((t or {}).get("expect") or "").split())[:600]
        if q and e:
            out.append({"question": q, "expect": e})
    return out[:MAX_TESTS]


def write(setup: dict, tok: str, use: dict, notes: list, current: dict | None = None, request: str = "",
          failures: list | None = None, step=lambda text: None, urls: dict | None = None) -> dict:
    """Have the code model write (current=None) or change the agent's code, and check it until it passes.
    Returns {code, tests, summary, problems, report}."""
    project = base(setup, tok, urls)
    if current:
        project.update(current)
    system = {"role": "system", "content": _prompt()}
    ask: dict = {"project": _context(setup, project)}
    if request or failures:
        ask["change_this"] = request
        ask["failed_tests"] = (failures or [])[:6]
    msgs = [system, {"role": "user", "content": json.dumps(ask, ensure_ascii=False)}]
    step("Writing the code" if not current else "Making the change")
    msg = designer._ask("code", use, notes, msgs, None, tok, max_tokens=CODE_TOKENS)
    reply = designer._text(msg)
    files, apply_problems = apply_reply(project, reply)
    apply_problems += _kept_fixed(project, files)
    tests, summary = tests_from(reply), (SUMMARY_RE.search(reply or "") or [None, ""])[1]
    for rnd in range(EDIT_ROUNDS + 1):
        res = check(setup, files)
        problems = apply_problems + res["problems"]
        if not problems or rnd == EDIT_ROUNDS:
            break
        step("Fixing what the checks found (%d)" % (rnd + 1))
        log.info("code agent %s: round %d, %d problems: %s", setup["name"], rnd + 1, len(problems), " | ".join(problems)[:500])
        msgs = [system, {"role": "user", "content": json.dumps({"project": _context(setup, files)}, ensure_ascii=False)},
                {"role": "user", "content": designer.EDIT_PROMPT.replace("Reply ONLY with edits", "Reply ONLY with edits, inside "
                 "=== EDIT <path> === ... === END EDIT === blocks (or a whole === FILE <path> === block)") +
                 "\n\nProblems:\n- " + "\n- ".join(problems)}]
        msg = designer._ask("code", use, notes, msgs, None, tok, max_tokens=designer.EDIT_TOKENS * 2)
        before = files
        files, apply_problems = apply_reply(files, designer._text(msg))
        apply_problems += _kept_fixed(before, files)
    return {"code": editable(files), "tests": tests, "summary": " ".join(summary.split())[:600], "problems": problems,
            "report": res["report"]}


def review(setup: dict, result: dict, who: dict, tok: str, use: dict) -> list:
    """The admins' plain-words rules against the code (designer_guard.review_plan); [] when none are broken."""
    s = designer_guard.settings()
    code = "\n\n".join("# %s\n%s" % (p, t) for p, t in sorted(result["code"].items()))
    draft = {"display_name": setup["name"], "description": setup["description"], "instructions": setup["task"],
             "new_tools": [{"name": setup["name"] + " (its code)", "description": setup["task"][:500], "hosts": setup["websites"],
                            "abilities": [a["name"] for a in result["report"]["abilities"]], "code": code}]}
    return designer_guard.review_plan(draft, s, use["chat"], tok)


# --- jobs ----------------------------------------------------------------------------------------------------------

def _job(owner: str, kind: str, fn) -> dict:
    now = time.time()
    j = {"id": hashlib.sha256(("%s|%s|%s" % (owner, kind, now)).encode()).hexdigest()[:20], "owner": owner, "kind": kind,
         "state": "working", "step": "Starting", "started": now, "result": None, "error": "", "notes": []}
    with _lock:
        for k in [k for k, v in _jobs.items() if now - v["started"] > 3 * 3600]:
            _jobs.pop(k, None)
        _jobs[j["id"]] = j

    def run():
        try:
            j["result"] = fn(j)
            j["state"] = "done"
        except Exception as exc:  # noqa: BLE001 - shown on the screen
            log.warning("code agent %s job failed: %s", kind, str(exc)[:300])
            j.update(state="failed", error=str(exc)[:400])
        j["ended"] = time.time()

    if BACKGROUND:
        threading.Thread(target=run, name="code-agent-" + kind, daemon=True).start()
    else:
        run()
    return j


def start_write(setup: dict, who: dict, tok: str, models, current: dict | None = None, request: str = "",
                failures: list | None = None) -> dict:
    notes: list = []
    use = dm.resolve(models, tok, notes)

    def fn(j):
        j["notes"] = notes
        out = write(setup, tok, use, notes, current, request, failures, step=lambda t: j.update(step=t))
        if not out["problems"]:
            j["step"] = "Checking it against your admins' rules"
            out["problems"] = review(setup, out, who, tok, use)
        old = base(setup, tok) if current is None else {**base(setup, tok), **current}
        files = assemble(setup, out["code"], tok, out["tests"])
        out.update(diff=diff(old, files), fingerprint=fingerprint(setup, out["code"]), models={"code": use["code"]})
        return out
    return _job(who.get("user_name") or "", "write", fn)


def start_tests(app_name: str, tests: list, who: dict, tok: str, models) -> dict:
    import app_agents
    import chat

    notes: list = []
    use = dm.resolve(models, tok, notes)

    def fn(j):
        info = app_agents.check(app_name, tok)
        if not info["running"]:
            raise DbxError(info["message"], 409)
        url = info["url"].rstrip("/") + "/invocations"
        results = []
        for i, t in enumerate(tests):
            j["step"] = "Asking %d of %d" % (i + 1, len(tests))
            t0 = time.monotonic()
            try:
                out = chat.ask(url, "app", [{"role": "user", "content": t["question"]}], tok)
                answer = out.get("reply") or ""
                g = designer.grade(t["question"], t["expect"], answer, out.get("tools") or [], tok, use)
            except DbxError as exc:
                answer, g = "", {"verdict": "fail", "reason": "It did not answer: %s" % str(exc)[:200]}
            results.append({**t, "answer": answer[:4000], "verdict": g["verdict"], "reason": g["reason"],
                            "seconds": round(time.monotonic() - t0, 1)})
        return {"results": results, "passed": sum(r["verdict"] == "pass" for r in results), "judge": use["judge"]}
    return _job(who.get("user_name") or "", "test", fn)


def job(job_id: str, who: dict) -> dict:
    j = _jobs.get(job_id or "")
    if not j or j["owner"] != (who.get("user_name") or ""):
        raise DbxError("That job is not running any more (the portal may have restarted). Start it again.", 404)
    return {k: v for k, v in j.items() if k != "owner"}


def deploy(setup: dict, code: dict, tests: list, approved: str, display_name: str, app_name: str, who: dict, tok: str) -> dict:
    """Re-check exactly what was approved and deploy it (code_upload checks the files again on the way in)."""
    files = assemble(setup, code, tok, clean_tests(tests))
    if fingerprint(setup, files) != approved:
        raise DbxError("The code changed after you approved it. Review it again.", 409)
    res = check(setup, files)
    if res["problems"]:
        raise DbxError("It does not pass the checks: " + res["problems"][0], 400)
    root = agent_export._slug(setup["name"])
    return code_upload.start(agent_export.zip_of(files, root), display_name or setup["name"], setup["description"],
                             app_name, who, tok)
