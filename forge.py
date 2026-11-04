"""New tools for the assistant designer: SQL functions and small MCP servers.

When an admin asks for something no existing tool does, the designer can propose a
**new tool**: a Unity Catalog SQL function, or a small MCP server that deploys as a
Databricks App (the same way the catalog's tools do). Nothing here runs model-written
code in the portal, and nothing is created until the admin has read it and approved
exactly what they read.

How the risk is kept small
--------------------------
* **Review is the gate.** The admin sees the full code and a plain list of what it can
  reach (sites, folders, connection settings, whether it saves files). Approval carries
  a fingerprint of that content (`fingerprint`); creation refuses if it no longer
  matches what is being created.
* **The static check is a guard rail, not a sandbox.** `analyze` parses the code as
  text (`ast`, never imported or run) and rejects what a tool of this kind has no
  reason to do: dynamic code (`eval`, `exec`, `getattr`, ...), shelling out, `open`,
  reaching into interpreter internals, imports outside a short allowlist, anything at
  import time except constants and functions, environment variables other than the
  connection settings it declared, sites it did not declare, folders it did not
  declare. A determined author can get round a deny-list; the real limits are below.
* **The real limits are Databricks'.** The new app runs as its own service principal
  with *only* the folder access its card declares (`mcps.grant_volumes`) and *only*
  the connection settings that already exist in the secret scope; a setting that does
  not exist cannot be invented here (the portal never handles secret values).
  Packages come from the imports found in the code, mapped through an allowlist, never
  from a list the model writes, so it cannot name an arbitrary package.
* **No credentials in model-written code.** Reading and writing folder files goes
  through helpers the portal writes (`volume_read`, `volume_write`, `volume_list`).
  Those use the app's own identity and refuse any path outside the declared folders,
  so the generated code never sees a token.
* **It never overwrites.** A function or app that already exists under that name is
  refused, except the app this designer made itself (marked in its description), so a
  failed attempt can be retried.

SQL functions can only compute and read (a UC SQL function body is an expression or a
query); the check also refuses any statement other than one plain `CREATE FUNCTION`.

Unconfirmed against a live workspace (built and unit-tested offline; the assembled
server is exercised against the real FastMCP library in the tests when it is
installed): creating a function through the Statement Execution API under the
admin's Apps token, the Files API paths used by the helpers (modelled on the working
GAM media planner tool), and a generated app being accepted as a Supervisor tool.
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import textwrap
import time

import yaml

import mcps
from dbx import DbxError, app_token, call

log = logging.getLogger("portal.forge")

GEN_MARK = mcps.GEN_MARK
MAX_NEW_TOOLS = 3
MAX_CODE = 20000
MAX_ABILITIES = 8

FUNC_RE = re.compile(r"^[A-Za-z_]\w*\.[A-Za-z_]\w*\.[A-Za-z_]\w*$")
ABILITY_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,120}[a-z0-9])?$")
VOLUME_RE = mcps.VOLUME_RE

# Standard library modules a small tool may use. Deliberately not here: os (except
# `import os` for settings, handled below), subprocess, socket, shutil, pathlib,
# tempfile, pickle, ctypes, importlib, sys, threading, multiprocessing.
STD_OK = {
    "json", "re", "math", "datetime", "statistics", "decimal", "fractions", "collections", "itertools",
    "functools", "typing", "dataclasses", "enum", "csv", "io", "base64", "hashlib", "textwrap",
    "uuid", "string", "time", "calendar", "zoneinfo", "operator", "random", "difflib", "html",
    "unicodedata", "urllib.parse", "os", "__future__",
}
# import name -> package that provides it. Requirements are built from this, so the
# model can never name a package of its own.
THIRD_PARTY = {
    "httpx": "httpx", "openpyxl": "openpyxl", "pandas": "pandas", "numpy": "numpy",
    "pydantic": "pydantic", "dateutil": "python-dateutil", "pypdf": "pypdf", "docx": "python-docx",
    "pptx": "python-pptx", "yaml": "pyyaml", "xlsxwriter": "XlsxWriter", "bs4": "beautifulsoup4",
}
DENY_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input", "breakpoint", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "__builtins__", "memoryview",
}
DENY_ATTRS = {
    "unlink", "rmdir", "rmtree", "system", "popen", "__subclasses__", "__globals__", "__builtins__",
    "__code__", "__bases__", "__mro__", "__class__", "__getattribute__", "__reduce__", "__reduce_ex__",
    "__dict__", "__import__", "__loader__", "__spec__",
}
HELPERS = {"volume_read", "volume_write", "volume_list"}
OK_DUNDER = {"__name__", "__doc__", "__init__", "__post_init__", "__str__", "__repr__", "__eq__", "__len__",
             "__lt__", "__hash__", "__iter__", "__next__", "__contains__", "__getitem__", "__enter__", "__exit__"}


# --- naming -------------------------------------------------------------------

def env_name(secret: str) -> str:
    """The environment variable a connection setting arrives as: `gam-key` -> `GAM_KEY`."""
    return re.sub(r"[^A-Za-z0-9]", "_", secret).upper()


def app_name(slug: str) -> str:
    return "mcp-" + slug


def fingerprint(item: dict) -> str:
    """What the admin approved. Covers everything that decides what is created."""
    keys = ("kind", "name", "slug", "sql", "example", "code", "description", "abilities", "hosts", "secrets", "volumes")
    body = json.dumps({k: item.get(k) for k in keys}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# --- the shape of a proposed tool ---------------------------------------------

def _s(v, n: int) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def clean_item(raw) -> dict | None:
    """Keep only known fields, then attach what the checks say. Returns None for junk.

    `fingerprint`, `report` and `problems` are always recomputed here, never taken
    from the caller: the browser sends the draft back and may send anything.
    """
    if not isinstance(raw, dict) or raw.get("kind") not in ("uc_function", "mcp"):
        return None
    if raw["kind"] == "uc_function":
        item = {"kind": "uc_function", "name": _s(raw.get("name"), 200), "description": _s(raw.get("description"), 500),
                "sql": _s(raw.get("sql"), 6000), "example": _s(raw.get("example"), 1000)}
    else:
        abilities = []
        for a in (raw.get("abilities") or [])[:MAX_ABILITIES]:
            if isinstance(a, dict):
                abilities.append({"name": _s(a.get("name"), 60), "description": _s(a.get("description"), 400),
                                  "changes_data": bool(a.get("changes_data"))})
        vols = []
        for v in (raw.get("volumes") or [])[:6]:
            if isinstance(v, dict):
                vols.append({"volume": _s(v.get("volume"), 200), "access": _s(v.get("access"), 10)})
        item = {
            "kind": "mcp", "slug": _s(raw.get("slug"), 61), "name": _s(raw.get("name"), 80),
            "description": _s(raw.get("description"), 500), "abilities": abilities, "code": _s(raw.get("code"), MAX_CODE + 1),
            "hosts": [_s(h, 120).lower() for h in (raw.get("hosts") or [])[:6] if isinstance(h, str) and h.strip()],
            "secrets": [_s(x, 64) for x in (raw.get("secrets") or [])[:5] if isinstance(x, str) and x.strip()],
            "volumes": vols,
        }
    result = analyze(item)
    item["problems"], item["report"] = result["problems"], result["report"]
    item["fingerprint"] = fingerprint(item)
    return item


def key_of(item: dict) -> str:
    return item["name"] if item["kind"] == "uc_function" else item["slug"]


# --- SQL functions ------------------------------------------------------------

_SQL_BAD = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|MERGE|ALTER|GRANT|REVOKE|TRUNCATE|COPY|EXTERNAL|LANGUAGE\s+PYTHON|LANGUAGE\s+SCALA|"
    r"LOCATION|USE|SET|CALL|EXECUTE|REFRESH|OPTIMIZE|VACUUM)\b", re.I)

# Python functions (2026-11-04): a calculation or text/data transformation that needs no files, websites or keys
# is made as a Unity Catalog Python function instead of an app. Why: a workspace allows 100 Databricks Apps (fixed),
# and an app tool is attached whole, while a schema holds 10,000 functions, each shared with its own EXECUTE grant.
# Verified live (centralindia): a Python function was created, returned the right margin (0.9375), and a website call
# from inside one timed out, because Databricks runs them without network. The code is checked here as text (parsed,
# never run): imports from a short list of calculation modules only, no files, no dynamic code, no dunder tricks.
PY_FUNC_RE = re.compile(
    r"^CREATE\s+FUNCTION\s+`?([\w.]+)`?\s*\((.*?)\)\s*RETURNS\s+([\w<>, ()]+?)\s+LANGUAGE\s+PYTHON\s*"
    r"(?:COMMENT\s+'((?:[^']|'')*)'\s*)?AS\s*\$\$(.*)\$\$$", re.I | re.S)
PY_IMPORTS = {"json", "math", "re", "datetime", "decimal", "statistics", "itertools", "collections", "functools",
              "string", "fractions", "calendar", "textwrap", "unicodedata", "operator", "bisect", "heapq"}
PY_BANNED_CALLS = {"open", "eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals",
                   "vars", "input", "breakpoint", "help", "memoryview", "exit", "quit"}


def python_function_problems(body: str) -> list:
    import ast

    problems: list[str] = []
    text = textwrap.dedent(body).strip("\n")
    if not text.strip():
        return ["The Python function needs code between $$ and $$."]
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return ["The Python code does not parse (line %s: %s)." % (exc.lineno, exc.msg)]
    if not any(isinstance(n, ast.Return) for n in ast.walk(tree)):
        problems.append("The Python code must return its result.")
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names = [a.name.split(".")[0] for a in n.names] if isinstance(n, ast.Import) else [(n.module or "").split(".")[0]]
            bad = [x for x in names if x not in PY_IMPORTS]
            if bad:
                problems.append("A Python function may only import calculation modules (%s); not %s."
                                % (", ".join(sorted(PY_IMPORTS)), ", ".join(bad)))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in PY_BANNED_CALLS:
            problems.append("A Python function may not call %s()." % n.func.id)
        elif isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            problems.append("A Python function may not use %s." % n.attr)
        elif isinstance(n, ast.Name) and n.id.startswith("__") and n.id != "__name__":
            problems.append("A Python function may not use %s." % n.id)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            problems.append("A Python function may not use global or nonlocal.")
    return list(dict.fromkeys(problems))


def _no_comments_or_extra(sql: str) -> str:
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def analyze_function(item: dict) -> dict:
    problems: list[str] = []
    name, sql = item.get("name", ""), item.get("sql", "")
    if not FUNC_RE.match(name):
        problems.append("The function name must look like catalog.schema.name.")
    if not item.get("description"):
        problems.append("Say, in one line, when the assistant should use this function.")
    body = _no_comments_or_extra(sql)
    if not body:
        problems.append("The function needs its SQL.")
    else:
        if ";" in body or "--" in body or "/*" in body:
            problems.append("Use one single statement with no comments.")
        m = re.match(r"^CREATE\s+FUNCTION\s+`?([\w.]+)`?\s*\(", body, re.I)
        if not m:
            problems.append("It must start with CREATE FUNCTION (not OR REPLACE, so nothing is overwritten).")
        elif name and m.group(1).replace("`", "").lower() != name.lower():
            problems.append("The name in the SQL must be the same as the function's name.")
        py = PY_FUNC_RE.match(body) if re.search(r"\bLANGUAGE\s+PYTHON\b", body, re.I) else None
        if re.search(r"\bLANGUAGE\s+PYTHON\b", body, re.I) and not py:
            problems.append("A Python function must be: CREATE FUNCTION catalog.schema.name(arguments) RETURNS type LANGUAGE "
                            "PYTHON [COMMENT '...'] AS $$ code $$")
        elif py:
            header = body[: body.index("$$")]
            bad = _SQL_BAD.search(re.sub(r"'(?:[^']|'')*'", "''", re.sub(r"\bLANGUAGE\s+PYTHON\b", "", header, flags=re.I)))
            if bad:
                problems.append("The function's header may not use %s." % bad.group(0).upper())
            problems += python_function_problems(py.group(5))
        else:
            if not re.search(r"\bRETURN\b", body, re.I):
                problems.append("The SQL needs a RETURN with the calculation or query.")
            bad = _SQL_BAD.search(re.sub(r"'(?:[^']|'')*'", "''", body))
            if bad:
                problems.append("The SQL may only define a calculation or query, not use %s." % bad.group(0).upper())
    ex = _no_comments_or_extra(item.get("example", ""))
    if ex:
        if not re.match(r"^SELECT\b", ex, re.I) or ";" in ex or "--" in ex or "/*" in ex or _SQL_BAD.search(re.sub(r"'(?:[^']|'')*'", "''", ex)):
            problems.append("The example must be one plain SELECT that calls the function.")
        elif name and name.split(".")[-1].lower() not in ex.lower():
            problems.append("The example must call the function.")
    python = bool(re.search(r"\bLANGUAGE\s+PYTHON\b", body or "", re.I))
    return {"problems": problems, "report": {"kind": "uc_function", "language": "python" if python else "sql",
                                             "reads_data": not python, "changes_data": False, "calls_internet": False}}


def run_sql(statement: str, warehouse_id: str, tok: str) -> list:
    """Run one statement as the admin and return its rows. Raises DbxError with
    Databricks' own message when it fails."""
    if not re.match(r"^[\w\-]{4,64}$", warehouse_id or ""):
        raise DbxError("Choose a SQL warehouse to create the function with.", 400)
    r = call("POST", "/api/2.0/sql/statements", tok, json={
        "warehouse_id": warehouse_id, "statement": statement, "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE", "disposition": "INLINE", "format": "JSON_ARRAY"})
    for _ in range(30):
        state = (r.get("status") or {}).get("state")
        if state == "SUCCEEDED":
            return (r.get("result") or {}).get("data_array") or []
        if state in ("FAILED", "CANCELED", "CLOSED"):
            raise DbxError(((r.get("status") or {}).get("error") or {}).get("message") or "The statement did not finish.", 400)
        time.sleep(2)
        r = call("GET", "/api/2.0/sql/statements/" + str(r.get("statement_id")), tok)
    raise DbxError("The function was still being created after a minute.", 504)


def create_function(item: dict, warehouse_id: str, tok: str) -> dict:
    """Create it, then try the example. A failed example is reported, not fatal: the
    function exists either way."""
    run_sql(_no_comments_or_extra(item["sql"]), warehouse_id, tok)
    out = {"name": item["name"], "created": True, "example": item.get("example") or "", "example_result": None, "example_error": ""}
    if item.get("example"):
        try:
            rows = run_sql(_no_comments_or_extra(item["example"]), warehouse_id, tok)
            out["example_result"] = rows[:5]
        except DbxError as exc:
            out["example_error"] = str(exc)[:200]
    return out


# --- MCP servers: checking the code ------------------------------------------

# Calls that build a fixed value and run nothing of the model's: allowed at the top level. Seen live
# (2026-10-09, the ad-book planner): `_ERRORS = frozenset({"#REF!", ...})` was refused three times in a
# row ("only fixed values are allowed" did not say what was wrong), eight and a half minutes in all.
_PURE_CALLS = {"frozenset", "set", "tuple", "list", "dict", "Decimal", "Fraction", "timedelta", "date"}
_PURE_ATTR_CALLS = {("re", "compile"), ("decimal", "Decimal"), ("fractions", "Fraction"), ("datetime", "timedelta"),
                    ("datetime", "date")}
# Names that may be subscripted at the top level: type aliases such as `Row = dict[str, Any]`.
_TYPE_NAMES = {"dict", "list", "tuple", "set", "frozenset", "type", "Optional", "Union", "Literal", "Any", "Dict",
               "List", "Tuple", "Set", "FrozenSet", "Callable", "Iterable", "Sequence", "Mapping", "TypedDict",
               "str", "int", "float", "bool", "bytes", "object"}


def _simple(node, names: set) -> bool:
    """A module-level value that does nothing when imported."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in names or node.id in _TYPE_NAMES or node.id in ("None", "True", "False")
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_simple(e.value if isinstance(e, ast.Starred) else e, names) for e in node.elts)
    if isinstance(node, ast.Dict):  # a None key is `**other`
        return all(k is None or _simple(k, names) for k in node.keys) and all(_simple(v, names) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        return _simple(node.operand, names)
    if isinstance(node, ast.BinOp):
        return _simple(node.left, names) and _simple(node.right, names)
    if isinstance(node, ast.BoolOp):
        return all(_simple(v, names) for v in node.values)
    if isinstance(node, ast.Compare):
        return _simple(node.left, names) and all(_simple(c, names) for c in node.comparators)
    if isinstance(node, ast.IfExp):
        return _simple(node.test, names) and _simple(node.body, names) and _simple(node.orelse, names)
    if isinstance(node, ast.Subscript):  # a type alias, or a fixed value picked out of another
        base = node.value
        ok_base = ((isinstance(base, ast.Name) and (base.id in _TYPE_NAMES or base.id in names))
                   or (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id == "typing"))
        return ok_base and _simple(node.slice, names)
    if isinstance(node, ast.Attribute):  # typing.Any, string.ascii_uppercase
        return isinstance(node.value, ast.Name) and node.value.id in ("typing", "string", "decimal", "math")
    if isinstance(node, ast.JoinedStr):
        return False
    if isinstance(node, ast.Call):
        f = node.func
        ok = ((isinstance(f, ast.Name) and f.id in _PURE_CALLS)
              or (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and (f.value.id, f.attr) in _PURE_ATTR_CALLS))
        return ok and all(_simple(a, names) for a in node.args) and all(_simple(k.value, names) for k in node.keywords)
    return False


def _root(mod: str) -> str:
    return mod.split(".")[0]


def analyze(item: dict) -> dict:
    """{problems, report} for a proposed tool, without running anything."""
    if item.get("kind") == "uc_function":
        return analyze_function(item)
    problems: list[str] = []
    slug, code = item.get("slug", ""), item.get("code", "")
    abilities = item.get("abilities") or []
    if not mcps.SLUG_RE.match(slug):
        problems.append("The tool's id must use only lowercase letters, numbers and dashes.")
    if not item.get("name") or not item.get("description"):
        problems.append("The tool needs a name and a plain description.")
    names = [a["name"] for a in abilities]
    if not abilities:
        problems.append("The tool needs at least one ability.")
    for a in abilities:
        if not ABILITY_RE.match(a["name"]) or not a["description"]:
            problems.append("Each ability needs a lowercase name like read_rate_card and a one-line description.")
            break
    if len(set(names)) != len(names):
        problems.append("Ability names must be different from each other.")
    for h in item.get("hosts") or []:
        if not HOST_RE.match(h):
            problems.append("'%s' is not a valid website name." % h[:40])
    for v in item.get("volumes") or []:
        if not VOLUME_RE.match(v["volume"]) or v["access"] not in ("read", "write"):
            problems.append("Each folder needs a catalog.schema.volume name and read or write access.")
            break
    report = {
        "kind": "mcp", "imports": [], "packages": [], "hosts": [], "settings": [env_name(s) for s in item.get("secrets") or []],
        "folders": item.get("volumes") or [], "reads_files": False, "saves_files": False, "calls_internet": False,
        "may_change_outside": False, "lines": len(code.splitlines()),
    }
    if not code:
        return {"problems": problems + ["There is no code yet."], "report": report}
    if len(code) > MAX_CODE:
        return {"problems": problems + ["The code is too long (%d characters at most)." % MAX_CODE], "report": report}
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"problems": problems + ["The code has a mistake on line %s: %s" % (exc.lineno, str(exc.msg)[:80])], "report": report}

    # Top level: imports, functions, classes and constants only, so importing it does nothing.
    consts: set = set()
    funcs: dict = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.FunctionDef):
            funcs[node.name] = node
            if node.name in HELPERS | DRIVE_HELPERS:
                problems.append("Line %d: %s is already provided; call it, do not define it." % (node.lineno, node.name))
        elif isinstance(node, ast.ClassDef):
            continue
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if value is None and isinstance(node, ast.AnnAssign):
                continue  # a bare annotation, `x: int`
            if value is None or not _simple(value, consts) or not all(isinstance(t, ast.Name) for t in targets):
                src = (ast.get_source_segment(code, node) or "").splitlines()[0][:100] if code else ""
                problems.append("Line %d (`%s`): at the top level only fixed values are allowed (text, numbers, lists, dicts, "
                                "sets, frozenset(...), re.compile(...), Decimal(...), type aliases). Anything that computes "
                                "goes inside a function." % (node.lineno, src))
            else:
                consts |= {t.id for t in targets}
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        else:
            problems.append("Line %d: only imports, functions and fixed values may be at the top level." % node.lineno)
    for a in abilities:
        fn = funcs.get(a["name"])
        if fn is None:
            problems.append("There is no function called %s." % a["name"])
            continue
        args = fn.args
        every = args.posonlyargs + args.args + args.kwonlyargs + ([args.vararg] if args.vararg else []) + ([args.kwarg] if args.kwarg else [])
        if any(x.annotation is None for x in every) or fn.returns is None:
            problems.append("%s must give a type for every input and for what it returns." % a["name"])
        if not ast.get_docstring(fn):
            problems.append("%s needs a docstring saying what it does and what each input means." % a["name"])
    if any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree)):
        problems.append("Use ordinary functions, not async ones.")

    declared_hosts = {h.lower() for h in item.get("hosts") or []}
    drive = uses_drive(code)
    allowed_env = set(report["settings"])
    declared_vols = {v["volume"] for v in item.get("volumes") or []}
    write_vols = {v["volume"] for v in item.get("volumes") or [] if v["access"] == "write"}
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    imported_httpx = False
    env_total = env_ok = 0
    saw_write = saw_read = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                _check_import(al.name, node.lineno, report, problems)
                imported_httpx |= _root(al.name) == "httpx"
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                problems.append("Line %d: relative imports are not allowed." % node.lineno)
            elif node.module == "os":
                problems.append("Line %d: use `import os` and os.environ[...] for settings; nothing else from os." % node.lineno)
            else:
                _check_import(node.module, node.lineno, report, problems)
                imported_httpx |= _root(node.module) == "httpx"
        elif isinstance(node, ast.Name) and node.id in DENY_NAMES and isinstance(node.ctx, ast.Load):
            problems.append("Line %d: %s is not allowed." % (node.lineno, node.id))
        elif isinstance(node, ast.Attribute) and node.attr == "column_letter":
            # Seen live (2026-10-08, a tool read an ad book): a merged cell is a placeholder with no
            # `column_letter`, and walking a row of a real spreadsheet reaches one, so the tool failed
            # with "'MergedCell' object has no attribute 'column_letter'" on the first real file.
            problems.append("Line %d: .column_letter fails on merged cells, which real spreadsheets have. "
                            "Use get_column_letter(cell.column) from openpyxl.utils instead." % node.lineno)
        elif isinstance(node, ast.Attribute):
            a = node.attr
            if a in DENY_ATTRS or (a.startswith("__") and a.endswith("__") and a not in OK_DUNDER):
                problems.append("Line %d: .%s is not allowed." % (node.lineno, a))
            elif isinstance(node.value, ast.Name) and node.value.id == "os" and a not in ("environ", "getenv", "path", "sep", "linesep"):
                problems.append("Line %d: os.%s is not allowed." % (node.lineno, a))
            if isinstance(node.value, ast.Name) and node.value.id == "os" and a == "environ":
                env_total += 1
                par = parents.get(node)
                key = None
                if isinstance(par, ast.Subscript) and par.value is node and isinstance(par.slice, ast.Constant):
                    key = par.slice.value
                elif (isinstance(par, ast.Attribute) and par.attr == "get" and isinstance(parents.get(par), ast.Call)
                      and parents[par].args and isinstance(parents[par].args[0], ast.Constant)):
                    key = parents[par].args[0].value
                if isinstance(key, str) and key in allowed_env:
                    env_ok += 1
                elif isinstance(key, str):
                    problems.append("Line %d: the setting %s was not declared for this tool." % (node.lineno, key))
                    env_ok += 1
            if isinstance(node.value, ast.Name) and node.value.id == "os" and a == "getenv":
                env_total += 1
                par = parents.get(node)
                key = par.args[0].value if isinstance(par, ast.Call) and par.args and isinstance(par.args[0], ast.Constant) else None
                if isinstance(key, str) and key in allowed_env:
                    env_ok += 1
                elif isinstance(key, str):
                    problems.append("Line %d: the setting %s was not declared for this tool." % (node.lineno, key))
                    env_ok += 1
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in HELPERS:
                path = node.args[0] if node.args else None
                if f.id == "volume_write":
                    saw_write = True
                else:
                    saw_read = True
                if isinstance(path, ast.Constant) and isinstance(path.value, str):
                    _check_path(path.value, declared_vols, write_vols if f.id == "volume_write" else declared_vols, node.lineno, problems)
            if isinstance(f, ast.Name) and f.id in DRIVE_HELPERS:
                setting = node.args[0] if node.args else None
                if not (isinstance(setting, ast.Constant) and isinstance(setting.value, str)
                        and setting.value in (item.get("secrets") or [])):
                    problems.append("Line %d: %s must name, as its first input, a connection setting this tool declares "
                                    "(for example \"google-drive-key\")." % (node.lineno, f.id))
                for h in DRIVE_HOSTS:
                    if h not in report["hosts"]:
                        report["hosts"].append(h)
                report["uses_drive"] = True
                if f.id == "drive_save":
                    report["may_change_outside"] = True
            if isinstance(f, ast.Attribute) and f.attr in ("post", "put", "patch", "delete") and imported_httpx:
                report["may_change_outside"] = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for h in re.findall(r"https?://([A-Za-z0-9.-]+)", node.value):
                h = h.lower()
                if h in DRIVE_LINK_HOSTS and drive:
                    continue  # a Drive folder link handed to the helper, which takes the id out of it
                if h not in report["hosts"]:
                    report["hosts"].append(h)
                if h not in declared_hosts:
                    problems.append("It calls %s, which was not declared. Declare each website it may use." % h)
            if node.value.startswith("/Volumes/"):
                _check_path(node.value, declared_vols, declared_vols, getattr(node, "lineno", 0), problems)
    if env_total != env_ok:
        problems.append("It reads environment settings in a way that cannot be checked; read each declared setting by name.")
    report["calls_internet"] = imported_httpx
    report["reads_files"], report["saves_files"] = saw_read, saw_write
    if imported_httpx and not declared_hosts:
        problems.append("It uses the internet, so say which website it may call.")
    if saw_write and not write_vols:
        problems.append("It saves files, so a folder with write access must be declared.")
    if (saw_read or saw_write) and not declared_vols:
        problems.append("It works with files, so the folder must be declared.")
    if (saw_write or report["may_change_outside"]) and not any(a["changes_data"] for a in abilities):
        problems.append("It saves files or sends changes outside, so the ability that does it must be marked as changing data.")
    report["packages"] = sorted({THIRD_PARTY[m] for m in report["imports"] if m in THIRD_PARTY})
    report["problems_count"] = len(problems)
    # Unique and short: the same mistake on many lines is one message to fix.
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return {"problems": out[:12], "report": report}


def _check_import(module: str, line: int, report: dict, problems: list) -> None:
    root = _root(module)
    # `urllib.parse` is allowed exactly; the rest of urllib (which opens connections) is not.
    ok = module in STD_OK or (root in STD_OK and root != "urllib") or root in THIRD_PARTY
    if not ok:
        problems.append("Line %d: %s cannot be used. Allowed: %s." % (
            line, module, ", ".join(sorted(THIRD_PARTY) + ["the standard basics"])))
        return
    if root not in report["imports"]:
        report["imports"].append(root)


def _check_path(path: str, declared: set, allowed: set, line: int, problems: list) -> None:
    m = re.match(r"^/Volumes/([^/]+)/([^/]+)/([^/]+)(/.*)?$", path)
    if not m or ".." in path.split("/"):
        problems.append("Line %d: %s is not a folder path inside a declared folder." % (line, path[:60]))
        return
    if ".".join(m.groups()[:3]) not in allowed:
        problems.append("Line %d: %s is not in a folder this tool declared (with the right access)." % (line, path[:60]))


# --- MCP servers: assembling the files ----------------------------------------

_HEADER = '''"""{name}: {description}

Generated by the Agent Portal's assistant designer and approved by {admin} on {date}.
Everything between the markers below was written by a model and read by that admin;
the rest is fixed. Folder access goes through volume_read / volume_write / volume_list,
which use this app's own identity and refuse any folder that was not declared.
"""
from __future__ import annotations

import os

import fastmcp
import httpx

mcp = fastmcp.FastMCP({title!r})

_READ_FOLDERS = {read!r}
_WRITE_FOLDERS = {write!r}


@mcp.custom_route("/ping", methods=["GET"])
async def _ping(request):  # noqa: ANN001
    from starlette.responses import JSONResponse

    return JSONResponse({{"status": "Healthy"}})


def _host() -> str:
    h = os.environ.get("DATABRICKS_HOST", "")
    return h if h.startswith("http") else "https://" + h


def _token() -> str:
    r = httpx.post(_host() + "/oidc/v1/token", data={{"grant_type": "client_credentials", "scope": "all-apis"}},
                   auth=(os.environ["DATABRICKS_CLIENT_ID"], os.environ["DATABRICKS_CLIENT_SECRET"]), timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _folder(path: str, allowed: tuple) -> str:
    parts = str(path).split("/")
    if len(parts) < 5 or parts[:2] != ["", "Volumes"] or ".." in parts or "" in parts[2:]:
        raise ValueError("Give a path like /Volumes/<catalog>/<schema>/<volume>/<file>.")
    if ".".join(parts[2:5]) not in allowed:
        raise ValueError("This tool is not allowed to use that folder.")
    return "/".join(parts)


def volume_read(path: str) -> bytes:
    """The bytes of a file in one of this tool's folders."""
    p = _folder(path, tuple(_READ_FOLDERS) + tuple(_WRITE_FOLDERS))
    r = httpx.get(_host() + "/api/2.0/fs/files" + p, headers={{"Authorization": "Bearer " + _token()}}, timeout=120)
    if r.status_code >= 400:
        raise ValueError("Could not read that file (HTTP %d)." % r.status_code)
    return r.content


def volume_write(path: str, data: bytes, overwrite: bool = False) -> str:
    """Save bytes as a file in one of this tool's write folders. Returns the path.
    Never replaces an existing file unless overwrite=True: a saved plan or report is not
    silently lost when a version number is worked out wrong."""
    p = _folder(path, tuple(_WRITE_FOLDERS))
    r = httpx.put(_host() + "/api/2.0/fs/files" + p, params={{"overwrite": "true" if overwrite else "false"}}, content=data,
                  headers={{"Authorization": "Bearer " + _token()}}, timeout=120)
    if r.status_code == 409 and not overwrite:
        raise ValueError("A file already exists at %s. Save it under a new name (for example the next version)." % p)
    if r.status_code >= 400:
        raise ValueError("Could not save that file (HTTP %d). The tool needs write access to the folder." % r.status_code)
    return p


def volume_list(folder: str) -> list:
    """The names of the files directly inside a folder."""
    p = _folder(folder.rstrip("/") + "/x", tuple(_READ_FOLDERS) + tuple(_WRITE_FOLDERS)).rsplit("/", 1)[0]
    r = httpx.get(_host() + "/api/2.0/fs/directories" + p, headers={{"Authorization": "Bearer " + _token()}}, timeout=60)
    if r.status_code >= 400:
        raise ValueError("Could not list that folder (HTTP %d)." % r.status_code)
    return [c.get("name") for c in r.json().get("contents") or [] if not c.get("is_directory")]


# ---- written by a model, read by an admin -------------------------------------
'''

# Added to the fixed part only when the model's code calls drive_list / drive_save. Written and
# tested here once, because signing in to Google needs a signed token (RS256) that a model would
# otherwise have to hand-roll, and there is no library for it on the model's list. The key is a
# connection setting the admin saved through Connect: a service-account JSON key, or a user's
# OAuth client id + secret + refresh token. Seen in Google's docs and commonly hit: a service
# account has no storage of its own, so uploading into a folder in someone's "My Drive" fails
# with storageQuotaExceeded; the folder must be in a Shared Drive. That case is said in words.
_DRIVE = '''

_DRIVE_API = "https://www.googleapis.com/drive/v3/files"
_DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
_DRIVE_TOKENS: dict = {{}}


def _b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _drive_token(setting: str) -> tuple:
    """(access token, who it signs in as) for the Google key kept in a connection setting."""
    import json
    import time

    env = "".join(c if c.isalnum() else "_" for c in setting).upper()
    if env not in _DRIVE_SETTINGS:
        raise ValueError("This tool may not use the setting %s." % setting)
    cached = _DRIVE_TOKENS.get(env)
    if cached and cached[2] > time.time() + 60:
        return cached[0], cached[1]
    raw = os.environ.get(env, "")
    if not raw:
        raise ValueError("The Google Drive connection %s is not set up here. An admin connects it in the portal." % setting)
    try:
        info = json.loads(raw)
    except ValueError:
        raise ValueError("The Google Drive connection %s is not a JSON key file." % setting)
    if info.get("private_key") and info.get("client_email"):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(time.time())
        aud = info.get("token_uri") or "https://oauth2.googleapis.com/token"
        head = _b64url(json.dumps({{"alg": "RS256", "typ": "JWT"}}).encode())
        body = _b64url(json.dumps({{"iss": info["client_email"], "scope": "https://www.googleapis.com/auth/drive",
                                   "aud": aud, "iat": now, "exp": now + 3600}}).encode())
        key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        sig = key.sign((head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256())
        r = httpx.post(aud, data={{"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                  "assertion": head + "." + body + "." + _b64url(sig)}}, timeout=30)
        who = info["client_email"]
    elif info.get("refresh_token") and info.get("client_id"):
        r = httpx.post(info.get("token_uri") or "https://oauth2.googleapis.com/token", timeout=30, data={{
            "grant_type": "refresh_token", "refresh_token": info["refresh_token"],
            "client_id": info["client_id"], "client_secret": info.get("client_secret", "")}})
        who = "the Google user who made the key"
    else:
        raise ValueError("The Google Drive connection %s is neither a service-account key nor an OAuth refresh token." % setting)
    if r.status_code >= 400:
        raise ValueError("Google refused the Drive connection %s (HTTP %d). The key may be revoked or wrong." % (setting, r.status_code))
    tok = r.json()["access_token"]
    _DRIVE_TOKENS[env] = (tok, who, time.time() + int(r.json().get("expires_in", 3600)))
    return tok, who


def _drive_folder(folder: str) -> str:
    import re

    m = re.search(r"/folders/([A-Za-z0-9_-]{{10,}})", folder or "") or re.match(r"^([A-Za-z0-9_-]{{10,}})$", (folder or "").strip())
    if not m:
        raise ValueError("Give a Google Drive folder link or id.")
    return m.group(1)


def _drive_error(r, who: str, folder: str) -> ValueError:
    reason = ""
    try:
        reason = ((r.json().get("error") or {{}}).get("errors") or [{{}}])[0].get("reason", "")
    except ValueError:
        pass
    # Seen live (2026-10-09): a real key signed in fine and Drive answered 403 accessNotConfigured,
    # because the Drive API was not switched on in the key's Google Cloud project.
    if reason in ("accessNotConfigured", "SERVICE_DISABLED") or "has not been used in project" in r.text:
        project = who.split("@")[-1].split(".")[0] if "@" in who else "the key's project"
        return ValueError("The Google Drive API is switched off in the Google Cloud project of this key (%s). Turn it on "
                          "at https://console.cloud.google.com/apis/library/drive.googleapis.com?project=%s , wait a few "
                          "minutes, and try again." % (project, project))
    if reason == "storageQuotaExceeded" or "storage quota" in r.text.lower():
        return ValueError("Google Drive refused the upload: %s has no storage of its own. Put the folder in a Shared "
                          "Drive and add %s to it, or use a key for a real Google user." % (who, who))
    if r.status_code == 404:
        return ValueError("Google Drive folder %s was not found, or it is not shared with %s." % (folder, who))
    if r.status_code in (401, 403):
        return ValueError("Google Drive refused access to folder %s for %s (HTTP %d, %s). Share the folder with it "
                          "as an editor." % (folder, who, r.status_code, reason or "no reason given"))
    return ValueError("Google Drive answered HTTP %d (%s)." % (r.status_code, reason or r.text[:120]))


def drive_list(setting: str, folder: str) -> list:
    """The files in a Google Drive folder: [{{"name", "id", "link"}}], newest first.
    `setting` is the connection that holds the Google key; `folder` a folder link or id."""
    fid = _drive_folder(folder)
    tok, who = _drive_token(setting)
    out, page = [], ""
    for _ in range(20):
        params = {{"q": "'%s' in parents and trashed = false" % fid, "pageSize": 1000, "orderBy": "createdTime desc",
                  "fields": "nextPageToken, files(id, name, webViewLink)", "supportsAllDrives": "true",
                  "includeItemsFromAllDrives": "true"}}
        if page:
            params["pageToken"] = page
        r = httpx.get(_DRIVE_API, params=params, headers={{"Authorization": "Bearer " + tok}}, timeout=60)
        if r.status_code >= 400:
            raise _drive_error(r, who, fid)
        j = r.json()
        out += [{{"name": f.get("name"), "id": f.get("id"), "link": f.get("webViewLink")}} for f in j.get("files") or []]
        page = j.get("nextPageToken") or ""
        if not page:
            break
    return out


def drive_save(setting: str, folder: str, name: str, data: bytes,
               mime: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") -> dict:
    """Upload bytes as a new file in a Google Drive folder and return {{"name", "id", "link"}}.
    Never replaces anything: if a file of that name is already there it refuses, so a saved
    version is never lost; choose the next version's name instead."""
    import json

    fid = _drive_folder(folder)
    name = str(name).strip()
    if not name or "/" in name or len(name) > 200:
        raise ValueError("Give the file a plain name.")
    if any(f["name"] == name for f in drive_list(setting, fid)):
        raise ValueError("A file called %s is already in that Drive folder. Save it as the next version." % name)
    tok, who = _drive_token(setting)
    meta = json.dumps({{"name": name, "parents": [fid]}}).encode()
    boundary = "agentportal7f3a9c"
    body = (b"--" + boundary.encode() + b"\\r\\nContent-Type: application/json; charset=UTF-8\\r\\n\\r\\n" + meta
            + b"\\r\\n--" + boundary.encode() + b"\\r\\nContent-Type: " + mime.encode() + b"\\r\\n\\r\\n" + bytes(data)
            + b"\\r\\n--" + boundary.encode() + b"--")
    r = httpx.post(_DRIVE_UPLOAD, params={{"uploadType": "multipart", "supportsAllDrives": "true",
                                          "fields": "id, name, webViewLink"}}, content=body, timeout=120,
                   headers={{"Authorization": "Bearer " + tok, "Content-Type": "multipart/related; boundary=" + boundary}})
    if r.status_code >= 400:
        raise _drive_error(r, who, fid)
    j = r.json()
    return {{"name": j.get("name"), "id": j.get("id"), "link": j.get("webViewLink")}}
'''

DRIVE_HELPERS = {"drive_list", "drive_save"}
DRIVE_HOSTS = ["oauth2.googleapis.com", "www.googleapis.com"]
DRIVE_LINK_HOSTS = {"drive.google.com", "docs.google.com"}

_FOOTER = '''

# ---- end of model-written code ------------------------------------------------
{registrations}

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
'''


def uses_drive(code: str) -> bool:
    """Does the model's code call the Google Drive helpers (so the fixed part must include them)?"""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return False
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in DRIVE_HELPERS for n in ast.walk(tree))


def _doc(text: str) -> str:
    """Text safe to put inside the generated file's triple-quoted docstring."""
    return str(text).replace("\\", "/").replace('"""', "'''")


def assemble(item: dict, admin: str, date: str) -> dict:
    """The folder that gets deployed, as {path: bytes}."""
    read = sorted({v["volume"] for v in item["volumes"] if v["access"] == "read"})
    write = sorted({v["volume"] for v in item["volumes"] if v["access"] == "write"})
    regs = "\n".join(
        "mcp.tool(annotations={%r: %s, %r: %s})(%s)" % (
            "readOnlyHint", not a["changes_data"], "destructiveHint", False, a["name"])
        for a in item["abilities"])
    drive = uses_drive(item["code"])
    header = _HEADER.format(name=_doc(item["name"]), description=_doc(item["description"]), admin=_doc(admin or "an admin"),
                            date=_doc(date), read=read, write=write, title=item["name"])
    if drive:  # before the marker, so it stays part of the fixed (not model-written) code
        cut = header.index(BEGIN_MARK)
        header = (header[:cut].rstrip("\n") + "\n\n_DRIVE_SETTINGS = %r\n" % sorted(env_name(s) for s in item["secrets"])
                  + _DRIVE.format() + "\n\n" + header[cut:])
    server = header + item["code"].rstrip() + "\n" + _FOOTER.format(registrations=regs)
    try:
        ast.parse(server)  # what is deployed must at least compile
    except SyntaxError as exc:
        raise DbxError("The tool's files could not be put together (%s)." % str(exc.msg)[:80], 400)
    report = analyze(item)["report"]
    card = {
        "name": item["name"], "description": item["description"], "version": "0.1.0", "owner": admin or "",
        "tools": [{"name": a["name"], "description": a["description"], "changes_data": a["changes_data"]} for a in item["abilities"]],
        "needs": {"secrets": list(item["secrets"]),
                  "volumes": sorted({v["access"] for v in item["volumes"]})},
    }
    app = {"command": ["python", "server.py"]}
    if item["secrets"]:
        app["env"] = [{"name": env_name(s), "valueFrom": s} for s in item["secrets"]]
    reqs = (["fastmcp>=2.3", "httpx>=0.27"] + (["cryptography>=42"] if drive else [])
            + [p for p in report["packages"] if p != "httpx"])
    readme = (
        "# %s\n\n%s\n\nCreated by the Agent Portal's assistant designer (%s) and approved by %s.\n"
        "To keep it, copy this folder into the MCP catalog repository.\n\nAbilities:\n%s\n"
        % (item["name"], item["description"], date, admin or "an admin",
           "\n".join("- `%s`: %s" % (a["name"], a["description"]) for a in item["abilities"])))
    return {
        "server.py": server.encode("utf-8"),
        "requirements.txt": ("\n".join(reqs) + "\n").encode("utf-8"),
        "app.yaml": yaml.safe_dump(app, sort_keys=False).encode("utf-8"),
        "mcp.yaml": yaml.safe_dump(card, sort_keys=False).encode("utf-8"),
        "README.md": readme.encode("utf-8"),
    }


def entry_for(item: dict) -> dict:
    """The catalog-style card the existing install code works from."""
    return {
        "slug": item["slug"], "name": item["name"], "description": GEN_MARK + " " + item["description"],
        "version": "0.1.0", "owner": "", "app_name": app_name(item["slug"]), "problem": "",
        "tools": [{"name": a["name"], "description": a["description"], "changes_data": a["changes_data"]} for a in item["abilities"]],
        "needs": {"secrets": list(item["secrets"]), "volumes": sorted({v["access"] for v in item["volumes"]})},
    }


def volumes_of(item: dict) -> dict:
    return {"read": sorted({v["volume"] for v in item["volumes"] if v["access"] == "read"}),
            "write": sorted({v["volume"] for v in item["volumes"] if v["access"] == "write"})}


# --- reading a tool back --------------------------------------------------------

BEGIN_MARK = "# ---- written by a model, read by an admin"
END_MARK = "# ---- end of model-written code"


def model_code_of(server_py: str) -> str:
    """The model-written section of a generated server file: what lies between the two markers
    `assemble` puts around it. "" if the markers are missing (not a file this designer wrote)."""
    a = server_py.find(BEGIN_MARK)
    b = server_py.find(END_MARK)
    if a < 0 or b < a:
        return ""
    return server_py[server_py.index("\n", a) + 1:b].strip("\n") + "\n"


def item_from_files(files: dict, slug: str) -> dict | None:
    """Rebuild the tool as the designer first described it, from the files it deployed, so it can be
    tested, repaired and deployed again. The websites it declared are not stored in its files; they
    are recovered from the code, which the first approval already required to match them."""
    try:
        card = yaml.safe_load(files.get("mcp.yaml", b"")) or {}
    except yaml.YAMLError:
        return None
    server = files.get("server.py", b"").decode("utf-8", "replace")
    code = model_code_of(server)
    if not isinstance(card, dict) or not code:
        return None
    src = mcps.parse_source(files)
    vols = ([{"volume": v, "access": "read"} for v in src["folders"]["read"]]
            + [{"volume": v, "access": "write"} for v in src["folders"]["write"]])
    needs = mcps._needs(card.get("needs"))
    base = {
        "kind": "mcp", "slug": slug, "name": str(card.get("name") or slug), "description": " ".join(str(card.get("description") or "").split()),
        "abilities": [{"name": t["name"], "description": t["description"], "changes_data": t["changes_data"]} for t in mcps._tools(card.get("tools"))],
        "code": code, "hosts": [], "secrets": needs["secrets"], "volumes": vols,
    }
    found = analyze(clean_item(base) or base)["report"]["hosts"]
    return clean_item({**base, "hosts": found})


def item_from_workspace(slug: str, tok: str) -> dict:
    """The deployed tool `mcp-<slug>`, as an item. Only a tool this designer made can be read this way
    (the app's description carries its mark): the designer repairs its own tools, nobody else's."""
    if not mcps.SLUG_RE.match(slug or ""):
        raise DbxError("That is not a tool id.", 400)
    # act, not call: a deployed portal's user token has no apps scope (seen live, 2026-10-09: "403 ... does not have
    # required scopes: apps", which stopped the tool check before it began), so the portal's identity reads it.
    app, _ = mcps.act("GET", "/api/2.0/apps/" + app_name(slug), tok, quiet=True)
    if not (app.get("description") or "").startswith(GEN_MARK):
        raise DbxError("%s was not made by the assistant designer, so it cannot be tested or repaired here." % app_name(slug), 403)
    base = mcps.SOURCE_ROOT + "/" + slug
    files = {}
    for f in ("mcp.yaml", "server.py", "README.md"):
        try:
            files[f] = mcps._export(base + "/" + f, tok)
        except DbxError:
            pass
    item = item_from_files(files, slug)
    if not item:
        raise DbxError("The source of %s is not kept in this workspace in the form the designer writes, so it cannot be repaired here." % app_name(slug), 409)
    return item


def code_diff(old: str, new: str, context: int = 3, limit: int = 220) -> list[str]:
    """A unified diff of the model-written code, for the admin to read before a repair is applied."""
    import difflib

    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "current", "repaired", lineterm="", n=context))
    return lines[:limit] + (["... (the rest is not shown)"] if len(lines) > limit else [])


# --- before creating ----------------------------------------------------------

def name_problems(item: dict, tok: str, secrets: bool = True) -> list[str]:
    """Is the name free, and does everything the tool needs exist? Refuses to
    overwrite anything that is not this designer's own earlier attempt."""
    out: list[str] = []
    if item["kind"] == "uc_function":
        try:
            call("GET", "/api/2.1/unity-catalog/functions/" + item["name"], tok, quiet=True)
            out.append("A function called %s already exists. Pick another name." % item["name"])
        except DbxError as exc:
            if exc.status not in (404, 403):
                out.append("Could not check whether %s exists (%s)." % (item["name"], str(exc)[:80]))
        return out
    try:
        if item["slug"] in {e["slug"] for e in mcps.catalog()[0]}:
            out.append("There is already a ready-made tool called %s. Use that, or pick another id." % item["slug"])
    except DbxError:
        pass
    try:
        # act, not call: a deployed portal's user token has no apps scope (a 403), so
        # the app identity checks the name, as it does for every other apps call.
        app, _ = mcps.act("GET", "/api/2.0/apps/" + app_name(item["slug"]), tok, quiet=True)
        if not (app.get("description") or "").startswith(GEN_MARK):
            out.append("An app called %s already exists and was not made by this designer. Pick another id." % app_name(item["slug"]))
    except DbxError as exc:
        if exc.status != 404:
            out.append("Could not check whether the app exists (%s)." % str(exc)[:80])
    # While designing, a credential that is not connected yet is the draft's to track (the admin connects it
    # on screen); a tool is only ever installed once it exists (designer.check, mcps.start).
    missing = mcps.missing_secrets(entry_for(item), tok) if secrets else []
    if missing:
        out.append("These connection settings do not exist yet: %s. Connect them first." % ", ".join(missing))
    return out


# --- creating -------------------------------------------------------------------

def install_app(item: dict, who: dict, tok: str, date: str) -> None:
    """Background: copy the files, deploy, wait until it runs, then give it its folders.
    Failures are recorded for the status screen and never raised."""
    entry = entry_for(item)
    name = entry["app_name"]
    try:
        files = list(assemble(item, who.get("user_name") or "", date).items())
        mcps.install(entry, who, tok, files=files)
        deadline = time.time() + 20 * 60
        retries = 0
        while True:
            apps, _ = mcps._apps(tok)
            state, line = mcps._state(apps.get(name), mcps._progress.get(name))
            if state == "running":
                break
            if state == "failed":
                # Databricks sometimes fails to copy the files in ("Failed to download source code ... timed out",
                # seen live 2026-10-09). The files are fine, so say it again rather than make the admin do it.
                if retries < mcps.DEPLOY_RETRIES and mcps.transient(line):
                    retries += 1
                    log.warning("new tool %s: the deployment failed (%s); trying again (%d of %d)", item["slug"], line[:120], retries, mcps.DEPLOY_RETRIES)
                    mcps.redeploy(entry, tok)
                    time.sleep(20)
                    continue
                raise DbxError(line or "the deployment failed", 502)
            if time.time() > deadline:
                raise DbxError("it was still not running after 20 minutes", 504)
            time.sleep(10)
        vols = volumes_of(item)
        problems: list = []
        if entry["needs"]["volumes"]:
            # Until its folders are granted the tool is not ready: tried earlier it can only say
            # "could not read that file". So the status stays "getting ready" through this.
            mcps._progress[name] = {"phase": "copying", "message": "Giving it access to its folders.", "at": time.time()}
            problems = mcps.grant_volumes([entry], vols, tok)
            for problem in problems:
                log.warning("new tool %s: %s", item["slug"], problem)
        mcps._progress[name] = {"phase": "done", "at": time.time(),
                                "message": ("Running, but its folder access is incomplete: " + " ".join(problems))[:400] if problems else ""}
        log.info("new tool %s is running as app %s", item["slug"], name)
    except DbxError as exc:
        mcps._progress[name] = {"phase": "failed", "message": str(exc)[:240], "at": time.time()}
        log.warning("new tool %s failed: %s", item["slug"], str(exc)[:200])
    except Exception:  # never leave a background task dying silently
        mcps._progress[name] = {"phase": "failed", "message": "Unexpected error while installing.", "at": time.time()}
        log.exception("new tool %s crashed", item["slug"])


def app_states(names: list[str], tok: str) -> dict:
    """{app name: {state, note}} for the status screen."""
    apps, _ = mcps._apps(tok)
    out = {}
    for n in names:
        prog = mcps._progress.get(n)
        state, line = mcps._state(apps.get(n), prog)
        if state == "running" and prog and prog.get("phase") == "done" and prog.get("message"):
            line = prog["message"]  # it runs, but a folder grant was refused: say so before it is tried
        out[n] = {"state": state, "note": line}
    return out
