"""The assistant designer's side panel: the draft as the files a person can read, compare and download.

Brought over as an idea from Mia Studio (its Spec / Files / Diff panel), built the portal's way: the files
are made from the draft the browser already holds and sends each turn, nothing is written anywhere, and no
model is asked (so a turn costs nothing more). A new tool's files are exactly what would be deployed
(`forge.assemble`); they are text to read and are never run here.

    plan.md                    what it will do: purpose, the steps it follows, what it uses, rules, what was
                               checked, what is not included, who can use it, and any open questions
    instructions.md            how it is told to behave (or a data assistant's notes)
    example-questions.md       a data assistant's example questions
    tools/<slug>/...           a new tool: server.py, mcp.yaml, requirements.txt, app.yaml
    functions/<name>.sql       a new SQL function
"""
from __future__ import annotations

import difflib
import io
import time
import zipfile

import forge

KIND_NAME = {"supervisor": "Combines tools and information", "genie": "Answers questions from your data",
             "knowledge": "Answers questions from your documents"}
COVER = {"covered": "Covered", "partly": "Partly", "missing": "Not available"}
TOOL_KIND = {"uc_function": "Function", "genie": "Data assistant", "knowledge": "Document assistant", "volume": "Folder",
             "connection": "Connection", "app": "Tool", "vector": "Search index"}


def _bullets(items: list) -> str:
    return "\n".join("- " + str(i) for i in items) if items else "_None._"


def plan(d: dict) -> str:
    """The plan in plain words, for the admin to read and approve (and approvers to read later)."""
    name = d.get("display_name") or "Untitled assistant"
    out = ["# " + name, "", "_%s_" % KIND_NAME.get(d.get("kind") or "", "Kind not decided yet"), ""]
    out += ["## What it is for", "", d.get("description") or "_Not written yet._", ""]
    if d.get("kind") != "genie":
        steps = d.get("process") or []
        out += ["## How it works", ""]
        out += ["%d. %s" % (i + 1, s) for i, s in enumerate(steps)] if steps else ["_The steps are not written yet._"]
        out.append("")
    uses = []
    for t in d.get("tools") or []:
        uses.append("**%s** (%s): %s" % (t.get("ref"), TOOL_KIND.get(t.get("type"), "Tool"), t.get("description") or "no note"))
    for s in d.get("sources") or []:
        uses.append("**%s** (documents in %s): %s" % (s.get("name") or s.get("volume"), s.get("volume"), s.get("description") or ""))
    for t in d.get("tables") or []:
        uses.append("**%s** (table)" % t)
    out += ["## What it uses", "", _bullets(uses), ""]
    news = d.get("new_tools") or []
    if news:
        out += ["## New tools to create", ""]
        for n in news:
            r = n.get("report") or {}
            reach = []
            if r.get("folders"):
                reach.append("folders: " + ", ".join("%s (%s)" % (f["volume"], f["access"]) for f in r["folders"]))
            if r.get("hosts"):
                reach.append("websites: " + ", ".join(r["hosts"]))
            if r.get("settings"):
                reach.append("connection settings: " + ", ".join(r["settings"]))
            if r.get("may_change_outside"):
                reach.append("can change things outside the portal (asks first)")
            out.append("- **%s**: %s%s" % (n["name"], n.get("description") or "", (" Reaches " + "; ".join(reach) + ".") if reach else ""))
        out.append("")
    cover = d.get("coverage") or []
    if cover:
        out += ["## Checked against your request", "", "| What you asked | Status | How | Note |", "|---|---|---|---|"]
        out += ["| %s | %s | %s | %s |" % (c["need"].replace("|", "/"), COVER.get(c["status"], c["status"]),
                                          (c.get("by") or "").replace("|", "/"), (c.get("note") or "").replace("|", "/")) for c in cover]
        out.append("")
    if d.get("gaps"):
        out += ["## Not included", "", _bullets(d["gaps"]), ""]
    if d.get("connections"):
        out += ["## Connections needed", "", _bullets(["%s: %s" % (c["label"], c["what_for"]) for c in d["connections"]]), ""]
    access = d.get("access") or []
    out += ["## Who can use it", "",
            _bullets(["%s (%s)" % (a["principal"], "team" if a["kind"] == "group" else "person") for a in access])
            if access else ("Nobody yet; it can be shared later." if d.get("access_decided") else "_Not decided yet._"), ""]
    qs = d.get("open_questions") or []
    out += ["## Open questions", "", _bullets(qs) if qs else "None. Everything needed to build it has been answered.", ""]
    return "\n".join(out)


def files(d: dict, admin: str = "") -> list[dict]:
    """[{path, text, kind}] for the draft (already cleaned by designer.clean_draft)."""
    out = [{"path": "plan.md", "kind": "plan", "text": plan(d)}]
    if d.get("instructions"):
        out.append({"path": "instructions.md", "kind": "instructions", "text": d["instructions"]})
    if d.get("notes"):
        out.append({"path": "notes.md", "kind": "instructions", "text": d["notes"]})
    if d.get("sample_questions"):
        out.append({"path": "example-questions.md", "kind": "tests", "text": _bullets(d["sample_questions"])})
    for n in d.get("new_tools") or []:
        if n["kind"] == "uc_function":
            out.append({"path": "functions/%s.sql" % n["name"], "kind": "tool", "text": n.get("sql") or ""})
            continue
        if not n.get("code"):
            continue
        try:
            folder = forge.assemble(n, admin or "the admin", time.strftime("%Y-%m-%d"))
        except Exception as exc:  # noqa: BLE001 - shown as a note; the review step reports the real problem
            out.append({"path": "tools/%s/README.txt" % n["slug"], "kind": "tool", "text": "Not assembled yet: %s" % exc})
            continue
        for path in sorted(folder):
            out.append({"path": "tools/%s/%s" % (n["slug"], path), "kind": "tool",
                        "text": folder[path].decode("utf-8", "replace") if isinstance(folder[path], bytes) else str(folder[path])})
    return out


def with_changes(cur: list, prev: list) -> list:
    """Each file marked new / changed / same, with a line diff against the previous turn's version."""
    before = {f["path"]: f["text"] for f in prev}
    for f in cur:
        old = before.get(f["path"])
        if old is None:
            f["change"] = "new" if prev else "same"
            f["diff"] = []
        elif old == f["text"]:
            f["change"], f["diff"] = "same", []
        else:
            lines = list(difflib.unified_diff(old.splitlines(), f["text"].splitlines(), "before", "now", lineterm="", n=2))
            f["change"], f["diff"] = "changed", lines[:300]
    gone = [p for p in before if p not in {f["path"] for f in cur}]
    return cur + [{"path": p, "kind": "removed", "text": "", "change": "removed", "diff": []} for p in gone]


def zip_of(fs: list, name: str) -> bytes:
    folder = "".join(c if c.isalnum() or c in "-_" else "-" for c in (name or "assistant").lower()).strip("-") or "assistant"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in fs:
            if f.get("change") != "removed":
                z.writestr(folder + "/" + f["path"], f["text"])
    return buf.getvalue()
