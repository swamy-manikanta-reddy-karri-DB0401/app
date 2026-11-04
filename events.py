"""What people do in the portal, and how the assistants are holding up.

Two admin views are built from one record per meaningful action:

* **Activity** - who opened the portal, asked an assistant something, sent or
  downloaded a file, deleted a conversation, changed who can use an assistant,
  or built / edited / deleted one. Listing screens and polling are not
  recorded; they are noise, not actions.
* **Agent health** - every question is recorded with its outcome, so each
  assistant gets a failure rate, a response time and its last problem, with the
  reason sorted into a few plain-English kinds an admin can act on.

Privacy: what people typed is **never** stored here - not questions, not
answers, not chat titles. A question is recorded as "asked <assistant> a
question", with how long it took and whether it worked. File names are kept
because they are what an admin needs to trace "my report never came back".

Storage follows the problem log: an in-memory ring (one process, lost on
restart) always, plus a Delta table when one is configured. The table is
`PORTAL_EVENTS_TABLE`, else `portal_events` beside `PORTAL_LOG_TABLE`. Writes are
queued and batched on a background thread, so recording never slows a request,
and a failure to record is printed, never raised.

How a request gets recorded: the access-log middleware opens a `begin()` for
every API call; a route that does something worth recording calls `note()` with
the action and what it acted on; the middleware then `finish()`es it with the
real status and timing. Errors are noted by the exception handlers, so the
reason Databricks gave ends up on the record.
"""
from __future__ import annotations

import contextvars
import json
import os
import queue
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import store
from logsink import TABLE as _LOG_TABLE

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def _table() -> str:
    explicit = os.environ.get("PORTAL_EVENTS_TABLE", "").strip()
    if explicit:
        return explicit
    parts = _LOG_TABLE.split(".")
    return ".".join(parts[:-1] + ["portal_events"]) if len(parts) == 3 else ""


TABLE = _table()
QUALIFIED = ".".join("`" + p + "`" for p in TABLE.split(".")) if _NAME_RE.match(TABLE) else ""
RETENTION_DAYS = 180
MEMORY = 3000
BATCH = 50
FLUSH_SECONDS = 5
THREAD_NAME = "event-sink"
SESSION_GAP = 30 * 60  # "opened the portal" at most once per person per half hour

# Action -> category, so the UI can filter without knowing every action.
CATEGORY = {
    "opened_portal": "sessions",
    "asked": "questions",
    "deleted_conversation": "questions",
    "uploaded": "files",
    "downloaded": "files",
    "granted": "access",
    "revoked": "access",
    "created_team": "access",
    "changed_team": "access",
    "changed_settings": "access",
    "created_assistant": "build",
    "created_tool": "build",
    "fixed_tool": "build",
    "edited_assistant": "build",
    "deleted_assistant": "build",
    "attached_file": "build",
    "saved_connection": "access",
    "tested_assistant": "build",
    "sent_for_approval": "build",
    "approved_release": "build",
    "sent_back": "build",
    "retired_assistant": "build",
    "restored_version": "build",
    "designer_refused": "build",
    "exported_agent": "build",
    "forgot_memory": "questions",
}

COLUMNS = ("at", "actor", "action", "category", "target", "label", "status", "http_status", "ms", "error_kind", "error", "detail")


def enabled() -> bool:
    """Durable storage is configured. The in-memory view always works."""
    return bool(QUALIFIED)


# ------------------------------------------------------------ the request ----

_current: contextvars.ContextVar = contextvars.ContextVar("portal_event", default=None)


def begin() -> dict:
    """Open a record for this request. Mutated in place by note(), so a route
    running in a worker thread (which gets a copy of the context) still writes
    to the same dict the middleware reads back."""
    rec: dict = {}
    _current.set(rec)
    return rec


def note(**fields) -> None:
    rec = _current.get()
    if rec is not None:
        rec.update({k: v for k, v in fields.items() if v is not None})


def failed(message: str, http_status: int) -> None:
    """Called by the exception handlers: the reason, as the caller was told it."""
    rec = _current.get()
    if rec is not None and rec.get("action"):
        rec["error"] = str(message)[:600]
        rec["http_status"] = http_status


_last_session: dict = {}
_session_lock = threading.Lock()


def finish(rec: dict, http_status: int, ms: int, actor_hint: str = "") -> None:
    """Record the request if a route marked it as an action."""
    action = rec.get("action")
    if not action:
        return
    actor = rec.get("actor") or actor_hint or "unknown"
    if action == "opened_portal":
        now = time.time()
        with _session_lock:
            if now - _last_session.get(actor, 0) < SESSION_GAP:
                return
            _last_session[actor] = now
    status = rec.get("status") or ("error" if http_status >= 400 else "ok")
    error = rec.get("error") or ""
    row = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "actor": actor,
        "action": action,
        "category": CATEGORY.get(action, "other"),
        "target": str(rec.get("target") or "")[:300],
        "label": str(rec.get("label") or rec.get("target") or "")[:300],
        "status": status,
        "http_status": int(rec.get("http_status") or http_status),
        "ms": int(ms),
        "error_kind": classify(error, int(rec.get("http_status") or http_status), status) if status != "ok" else "",
        "error": error[:600],
        "detail": json.dumps(rec.get("detail") or {}, ensure_ascii=False)[:2000],
    }
    record(row)


# ------------------------------------------------------- why it failed -------

# Plain-English kinds an admin can act on, checked in order. The text comes
# from Databricks (or the agent), so match generously and fall back to "other".
KINDS = [
    ("no_access", "Access denied", re.compile(r"PERMISSION_DENIED|do not have access|not authori[sz]ed|forbidden|lacks? .*permission", re.I)),
    ("data_access", "Data permission", re.compile(r"USE CATALOG|USE SCHEMA|SELECT on|TABLE_OR_VIEW_NOT_FOUND|INSUFFICIENT_PERMISSIONS|WRITE VOLUME|READ VOLUME|does not have .*privilege", re.I)),
    ("timeout", "Timeout", re.compile(r"timed? ?out|deadline|504|could not reach", re.I)),
    ("not_ready", "Unavailable", re.compile(r"not ready|scaling|starting|RESOURCE_DOES_NOT_EXIST|endpoint .*not found|503", re.I)),
    ("tool_error", "Tool error", re.compile(r"Error calling tool|tool .*fail|❌", re.I)),
    ("rate_limit", "Rate limited", re.compile(r"rate limit|429|REQUEST_LIMIT_EXCEEDED|quota", re.I)),
    ("bad_request", "Invalid request", re.compile(r"INVALID_PARAMETER|BAD_REQUEST|400|malformed", re.I)),
]
KIND_LABEL = {k: label for k, label, _ in KINDS} | {"agent_error": "Agent error", "other": "Other"}


def classify(text: str, http_status: int = 0, status: str = "error") -> str:
    if status == "tool_error":
        return "tool_error"
    for kind, _label, pattern in KINDS:
        if pattern.search(text or ""):
            return kind
    if http_status == 403:
        return "no_access"
    if http_status in (502, 500):
        return "agent_error"
    return "other"


# A reply can come back "successfully" while a tool inside it failed - the agent
# then apologises in prose. `adapters.parse` renders each tool result as a line
# "Called <tool> -> <output>"; an output that is an error marks the answer.
_TOOL_FAIL = re.compile(r"Called (\S+) -> (?=.*(?:Error calling tool|❌|\berror\b|failed|exception))([^\n]{0,400})", re.I)


def tool_failure(reply: str) -> str:
    m = _TOOL_FAIL.search(reply or "")
    return (m.group(1) + ": " + m.group(2)).strip()[:500] if m else ""


# ------------------------------------------------------------- storage -------

_memory: deque = deque(maxlen=MEMORY)
_q: queue.Queue = queue.Queue(maxsize=2000)
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_run = store.run  # tests replace this
_ready = False
_ready_lock = threading.Lock()


def _err(msg: str) -> None:
    print("events: " + msg, file=sys.stderr, flush=True)


def record(row: dict) -> None:
    _memory.append(row)
    if not enabled():
        return
    try:
        _q.put_nowait(row)
    except queue.Full:
        return
    global _thread
    with _thread_lock:
        if _thread is None:
            _thread = threading.Thread(target=_loop, name=THREAD_NAME, daemon=True)
            _thread.start()


def ensure_table() -> None:
    global _ready
    if _ready:
        return
    with _ready_lock:
        if _ready:
            return
        store.ensure_table(
            QUALIFIED,
            "CREATE TABLE IF NOT EXISTS " + QUALIFIED + " ("
            "at TIMESTAMP, actor STRING, action STRING, category STRING, target STRING, label STRING, "
            "status STRING, http_status INT, ms INT, error_kind STRING, error STRING, detail STRING)",
            "DELETE FROM " + QUALIFIED + " WHERE at < current_timestamp() - INTERVAL " + str(RETENTION_DAYS) + " DAYS",
            _run,
        )
        _ready = True


def _loop() -> None:
    while True:
        rows = [_q.get()]
        try:
            while len(rows) < BATCH:
                rows.append(_q.get(timeout=FLUSH_SECONDS if len(rows) == 1 else 0.5))
        except queue.Empty:
            pass
        try:
            _write(rows)
        except Exception as exc:  # a lost activity row must never break the app
            _err("could not write %d event(s) to %s: %s" % (len(rows), TABLE, str(exc)[:200]))


def _write(rows: list) -> None:
    ensure_table()
    params, tuples = [], []
    for i, row in enumerate(rows):
        marks = []
        for col in COLUMNS:
            name = f"{col}{i}"
            typ = "INT" if col in ("http_status", "ms") else "STRING"
            val = row.get(col)
            p = {"name": name, "type": typ}
            if val is not None and val != "":
                p["value"] = str(val)
            params.append(p)
            marks.append(f"CAST(:{name} AS TIMESTAMP)" if col == "at" else ":" + name)
        tuples.append("(" + ", ".join(marks) + ")")
    _run("INSERT INTO " + QUALIFIED + " VALUES " + ", ".join(tuples), params)


# --------------------------------------------------------------- reading -----

def _days(days) -> int:
    try:
        d = int(days)
    except (TypeError, ValueError):
        return 7
    return 1 if d <= 1 else (7 if d <= 7 else (30 if d <= 30 else 90))


def _row(r) -> dict:
    out = dict(zip(COLUMNS, r)) if not isinstance(r, dict) else dict(r)
    for k in ("http_status", "ms"):
        try:
            out[k] = int(float(out.get(k) or 0))
        except (TypeError, ValueError):
            out[k] = 0
    try:
        out["detail"] = json.loads(out.get("detail") or "{}")
    except (TypeError, ValueError):
        out["detail"] = {}
    at = str(out.get("at") or "")
    if at and "T" not in at:
        at = at.replace(" ", "T")
    if at and not at.endswith("Z") and "+" not in at:
        at += "Z"
    out["at"] = at
    for k in ("actor", "action", "category", "target", "label", "status", "error_kind", "error"):
        out[k] = out.get(k) or ""
    return out


def _all(days: int, limit: int = 5000) -> list:
    """Every recorded row in the window, newest first."""
    if enabled():
        ensure_table()
        rows = _run(
            "SELECT date_format(at, \"yyyy-MM-dd'T'HH:mm:ss'Z'\"), actor, action, category, target, label, status, "
            "http_status, ms, error_kind, error, detail FROM " + QUALIFIED + " "
            "WHERE at >= current_timestamp() - INTERVAL " + str(days) + " DAYS ORDER BY at DESC LIMIT " + str(int(limit))
        )
        return [_row(r) for r in rows]
    since = datetime.now(timezone.utc) - timedelta(days=days)
    keep = []
    for r in reversed(_memory):
        try:
            if datetime.strptime(r["at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) >= since:
                keep.append(_row(r))
        except (ValueError, KeyError):
            continue
    return keep[:limit]


def activity(days=7, category: str = "", status: str = "", actor: str = "", limit: int = 300) -> dict:
    """Recent actions for the Activity view, newest first, filtered in Python
    (the window is bounded, and it keeps every value out of the SQL text)."""
    d = _days(days)
    rows = _all(d)
    cats: dict = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    a = (actor or "").strip().lower()
    hits = [
        r for r in rows
        if (not category or r["category"] == category)
        and (not status or (status == "failed") == (r["status"] != "ok"))
        and (not a or a in r["actor"].lower())
    ]
    people = len({r["actor"] for r in rows if r["actor"] and r["actor"] != "unknown"})
    return {
        "stored": enabled(),
        "days": d,
        "total": len(rows),
        "people": people,
        "failed": sum(1 for r in rows if r["status"] != "ok"),
        "categories": cats,
        "events": hits[: max(1, min(int(limit), 1000))],
    }


def health(days=7) -> dict:
    """Per-assistant reliability from recorded questions, plus recent failures."""
    d = _days(days)
    asks = [r for r in _all(d) if r["action"] == "asked"]
    per: dict = {}
    # Only questions that reached a real assistant count towards its
    # reliability; a refused or mistyped name is a request problem, which still
    # shows up in the causes and the failure list. Rows from before `known` was
    # recorded count if they carry a friendly name (the label was only ever set
    # once the assistant had been found).
    for r in asks:
        if not ((r["detail"] or {}).get("known") or (r["label"] and r["label"] != r["target"])):
            continue
        a = per.setdefault(r["target"], {"endpoint": r["target"], "label": r["label"] or r["target"], "questions": 0,
                                         "failed": 0, "tool_errors": 0, "ms": [], "last_problem": None, "people": set()})
        a["questions"] += 1
        a["people"].add(r["actor"])
        if r["status"] == "ok":
            a["ms"].append(r["ms"])
        else:
            a["failed"] += 1
            if r["status"] == "tool_error":
                a["tool_errors"] += 1
            if a["last_problem"] is None:  # rows are newest first
                a["last_problem"] = {"at": r["at"], "kind": r["error_kind"], "error": r["error"]}
    agents = []
    for a in per.values():
        ms = sorted(a.pop("ms"))
        a["people"] = len(a["people"])
        a["avg_ms"] = int(sum(ms) / len(ms)) if ms else 0
        a["p90_ms"] = ms[min(len(ms) - 1, int(len(ms) * 0.9))] if ms else 0
        a["failure_rate"] = round(a["failed"] / a["questions"], 4) if a["questions"] else 0.0
        agents.append(a)
    agents.sort(key=lambda a: (-a["failure_rate"], -a["failed"], -a["questions"]))
    problems = [r for r in asks if r["status"] != "ok"]
    kinds: dict = {}
    for r in problems:
        kinds[r["error_kind"] or "other"] = kinds.get(r["error_kind"] or "other", 0) + 1
    ok_ms = sorted(r["ms"] for r in asks if r["status"] == "ok")
    return {
        "stored": enabled(),
        "days": d,
        "questions": len(asks),
        "failed": len(problems),
        "tool_errors": sum(1 for r in problems if r["status"] == "tool_error"),
        "avg_ms": int(sum(ok_ms) / len(ok_ms)) if ok_ms else 0,
        "kinds": [{"kind": k, "label": KIND_LABEL.get(k, k), "count": n} for k, n in sorted(kinds.items(), key=lambda x: (-x[1], x[0]))],
        "agents": agents,
        "recent": problems[:100],
    }
