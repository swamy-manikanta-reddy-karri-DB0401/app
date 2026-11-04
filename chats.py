"""Saved conversations, kept per person in a Delta table.

Turn on by setting PORTAL_CHAT_TABLE to `catalog.schema.table`, or leave it
unset and it sits beside the problem log (`PORTAL_LOG_TABLE`) as `portal_chats`.
With neither set, history is off and chat works exactly as before.

Whose conversation is whose is decided here, not in the browser: every read,
write and delete is filtered on the caller's user name, which comes from their
own token (never from a request field), and every value is bound as a query
parameter. The portal reads and writes as its service principal, so this filter
is the whole privacy boundary. Anyone with SELECT on the table itself can read
all conversations, so grant that sparingly.

One row per message, append-only. A conversation is the rows sharing an id;
its title is its first question. Deleting a conversation deletes its rows.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time

import store
from logsink import TABLE as _LOG_TABLE

log = logging.getLogger("portal.chats")

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")

RETENTION_DAYS = int(os.environ.get("PORTAL_CHAT_RETENTION_DAYS", "180") or 180)
MAX_TEXT = 100_000
LIST_LIMIT = 50


def _table() -> str:
    explicit = os.environ.get("PORTAL_CHAT_TABLE", "").strip()
    if explicit:
        return explicit
    parts = _LOG_TABLE.split(".")
    return ".".join(parts[:-1] + ["portal_chats"]) if len(parts) == 3 else ""


TABLE = _table()
QUALIFIED = ".".join("`" + p + "`" for p in TABLE.split(".")) if _NAME_RE.match(TABLE) else ""


def enabled() -> bool:
    return bool(QUALIFIED)


def valid_id(cid: str) -> bool:
    return bool(_ID_RE.match(cid or ""))


_ready = False
_lock = threading.Lock()


def ensure_table() -> None:
    """Create the table once per process, and trim conversations past retention."""
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        store.ensure_table(
            QUALIFIED,
            "CREATE TABLE IF NOT EXISTS " + QUALIFIED + " ("
            "conversation_id STRING, user_name STRING, endpoint STRING, idx INT, "
            "role STRING, content STRING, meta STRING, created_at TIMESTAMP)",
            "DELETE FROM " + QUALIFIED + " WHERE created_at < current_timestamp() - INTERVAL "
            + str(RETENTION_DAYS) + " DAYS",
        )
        _ready = True


def _p(name: str, value, typ: str = "STRING") -> dict:
    p = {"name": name, "type": typ}
    if value is not None:
        p["value"] = str(value)
    return p


# Saved in batches by one background writer (2026-11-04, sized for 20,000 people): one INSERT per reply meant
# tens of thousands of tiny statements a day on the warehouse and as many small files in the table. Rows wait
# at most FLUSH_SECONDS; a conversation opened meanwhile includes its waiting rows (get_chat), so nothing seems
# lost. If the queue is full (the warehouse is down for long), the reply is still shown and the save is logged.
BATCH = 40          # rows per INSERT (7 parameters each; the activity log writes 50 x 13 the same way)
FLUSH_SECONDS = 2
_q: "queue.Queue" = queue.Queue(maxsize=5000)
_pending: list = []           # rows queued or being written, for get_chat
_plock = threading.Lock()
_writer: threading.Thread | None = None
_gone: set = set()            # (user, conversation) deleted while rows were waiting
_inflight: set = set()        # (user, conversation) being written right now


def save(user: str, cid: str, endpoint: str, start_idx: int, messages: list) -> None:
    """Append messages to a conversation. `messages` are {role, text, meta}. Queued; written in batches."""
    if not (enabled() and user and valid_id(cid) and messages):
        return
    at = time.time()
    for i, m in enumerate(messages):
        row = {"cid": cid, "u": user, "ep": endpoint, "i": start_idx + i, "r": m["role"], "c": (m.get("text") or "")[:MAX_TEXT],
               "m": json.dumps(m.get("meta") or {}, ensure_ascii=False),
               "t": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(at)) + ".%03d" % (int(at * 1000) % 1000)}
        try:
            _q.put_nowait(row)
        except queue.Full:
            log.warning("chat history queue is full; a message of conversation %s was not saved", cid)
            return
        with _plock:
            _pending.append(row)
    _start()


def _start() -> None:
    global _writer
    if _writer is None or not _writer.is_alive():
        _writer = threading.Thread(target=_loop, name="chat-writer", daemon=True)
        _writer.start()


def _loop() -> None:
    while True:
        rows = [_q.get()]
        try:
            while len(rows) < BATCH:
                rows.append(_q.get(timeout=FLUSH_SECONDS if len(rows) == 1 else 0.3))
        except queue.Empty:
            pass
        _write(rows)


def _write(rows: list) -> None:
    with _plock:
        keep = [r for r in rows if (r["u"], r["cid"]) not in _gone]
    if not keep:
        with _plock:
            done = {id(r) for r in rows}
            _pending[:] = [r for r in _pending if id(r) not in done]
        return
    rows, every = keep, rows
    with _plock:
        _inflight.update((r["u"], r["cid"]) for r in rows)
    try:
        ensure_table()
        params, tuples = [], []
        for i, r in enumerate(rows):
            params += [_p(f"cid{i}", r["cid"]), _p(f"u{i}", r["u"]), _p(f"ep{i}", r["ep"]), _p(f"i{i}", r["i"], "INT"),
                       _p(f"r{i}", r["r"]), _p(f"c{i}", r["c"]), _p(f"m{i}", r["m"]), _p(f"t{i}", r["t"])]
            tuples.append(f"(:cid{i}, :u{i}, :ep{i}, :i{i}, :r{i}, :c{i}, :m{i}, CAST(:t{i} AS TIMESTAMP))")
        store.run("INSERT INTO " + QUALIFIED + " VALUES " + ", ".join(tuples), params)
    except Exception as exc:  # noqa: BLE001 - a failed save never fails a chat; said in the log
        log.warning("could not save %d chat messages: %s", len(rows), str(exc)[:300])
    finally:
        with _plock:
            done = {id(r) for r in every}
            _pending[:] = [r for r in _pending if id(r) not in done]
            _inflight.clear()


def flush(timeout: float = 10) -> None:
    """Wait until queued rows are written (tests; shutting down)."""
    end = time.time() + timeout
    while time.time() < end:
        with _plock:
            if not _pending:
                return
        time.sleep(0.05)


def list_chats(user: str, endpoint: str = "") -> list:
    """This person's conversations, newest first, optionally for one agent."""
    ensure_table()
    params = [_p("u", user)]
    where = "user_name = :u"
    if endpoint:
        where += " AND endpoint = :ep"
        params.append(_p("ep", endpoint))
    rows = store.run(
        "SELECT conversation_id, first(endpoint), "
        "date_format(max(created_at), \"yyyy-MM-dd'T'HH:mm:ss'Z'\"), count(*), min_by(content, idx) "
        "FROM " + QUALIFIED + " WHERE " + where + " "
        "GROUP BY conversation_id ORDER BY max(created_at) DESC LIMIT " + str(LIST_LIMIT),
        params,
    )
    out = []
    for r in rows:
        title = " ".join((r[4] or "").split())
        out.append(
            {
                "id": r[0],
                "endpoint": r[1],
                "updated": r[2],
                "count": int(r[3] or 0),
                "title": (title[:80] + "…") if len(title) > 80 else (title or "Conversation"),
            }
        )
    return out


def get_chat(user: str, cid: str) -> list:
    """The messages of one of this person's conversations, oldest first."""
    if not valid_id(cid):
        return []
    ensure_table()
    rows = store.run(
        # A retry or a second open tab can write the same position twice; keep the latest.
        "SELECT idx, role, content, meta, endpoint FROM " + QUALIFIED + " "
        "WHERE user_name = :u AND conversation_id = :cid "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY idx ORDER BY created_at DESC) = 1 ORDER BY idx",
        [_p("u", user), _p("cid", cid)],
    )
    with _plock:
        waiting = [[w["i"], w["r"], w["c"], w["m"], w["ep"]] for w in _pending if w["u"] == user and w["cid"] == cid]
    if waiting:
        have = {int(r[0]) for r in rows}
        rows = sorted(list(rows) + [w for w in waiting if w[0] not in have], key=lambda r: int(r[0]))
    out = []
    for r in rows:
        try:
            meta = json.loads(r[3]) if r[3] else {}
        except json.JSONDecodeError:
            meta = {}
        out.append({"role": r[1], "text": r[2] or "", "endpoint": (r[4] if len(r) > 4 else "") or "",
                    **{k: meta[k] for k in ("tools", "citations", "attachments", "files") if k in meta}})
    return out


def delete_chat(user: str, cid: str) -> None:
    if not valid_id(cid):
        return
    # Rows still waiting are dropped first, so a deleted conversation is never written back by the writer.
    with _plock:
        if len(_gone) > 10_000:
            _gone.clear()
        _gone.add((user, cid))
        _pending[:] = [r for r in _pending if not (r["u"] == user and r["cid"] == cid)]
    # A batch already being written may hold this conversation: delete only after it is in.
    end = time.time() + 30
    while time.time() < end:
        with _plock:
            if (user, cid) not in _inflight:
                break
        time.sleep(0.05)
    ensure_table()
    store.run(
        "DELETE FROM " + QUALIFIED + " WHERE user_name = :u AND conversation_id = :cid",
        [_p("u", user), _p("cid", cid)],
    )
