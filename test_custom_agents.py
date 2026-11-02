"""Custom agents that run in the portal, with test and release - no network.

Run: python test_custom_agents.py

A draft is tested (real answers, marked by another model), sent to approvers the builder picks,
approved by one of them (never the builder), and only then used by the people it was released to.
Editing a live agent makes a new draft while the live one keeps working. The agent answers with
the person's own token, searches its documents only in folders the person can read, and a tool
that changes something waits for the person (and never runs in a test).
"""
from __future__ import annotations

import json
import re

import custom_agents as ca
import designer
from dbx import DbxError

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


class FakeStore:
    """The few statements custom_agents runs, against lists in memory."""

    def __init__(self):
        self.versions, self.tests, self.docs = [], [], []

    def run(self, sql, params=None):
        p = {x["name"]: x.get("value") for x in params or []}
        s = " ".join(sql.split())
        if s.startswith(("CREATE", "SELECT 1")):
            return []
        if "portal_custom_agents`" in s.split(" WHERE")[0] or "portal_custom_agents` " in s:
            return self._versions(s, p)
        if "portal_custom_agent_tests" in s:
            return self._tests(s, p)
        if "portal_custom_agent_docs" in s:
            return self._docs(s, p)
        raise AssertionError("unexpected SQL: " + s)

    def _versions(self, s, p):
        if s.startswith("INSERT"):
            self.versions.append({"agent_id": p["a"], "version": int(p["v"]), "status": p["s"], "spec": p["spec"], "meta": p["meta"],
                                  "created_by": p["by"], "created_at": "t", "updated_at": "t"})
            return []
        if s.startswith("UPDATE"):
            for r in self.versions:
                if r["agent_id"] == p["a"] and r["version"] == int(p["v"]):
                    for col in ("s", "spec", "meta"):
                        if col in p:
                            r[{"s": "status"}.get(col, col)] = p[col]
            return []
        if s.startswith("DELETE"):
            self.versions = [r for r in self.versions if r["agent_id"] != p["a"]]
            return []
        return [[r["agent_id"], r["version"], r["status"], r["spec"], r["meta"], r["created_by"], r["created_at"], r["updated_at"]]
                for r in sorted(self.versions, key=lambda r: (r["agent_id"], r["version"]))]

    def _tests(self, s, p):
        if s.startswith("INSERT"):
            self.tests.append({"test_id": p["id"], "agent_id": p["a"], "version": int(p["v"]), "run_by": p["by"],
                               "passed": p["ok"], "results": p["res"]})
            return []
        if s.startswith("UPDATE"):
            for t in self.tests:
                if t["test_id"] == p["t"]:
                    t["results"], t["passed"] = p["res"], p["ok"]
            return []
        if s.startswith("DELETE"):
            self.tests = [t for t in self.tests if t["agent_id"] != p["a"]]
            return []
        return [[t["test_id"], t["version"], t["run_by"], t["passed"], t["results"], "t"] for t in self.tests
                if t["agent_id"] == p["a"] and t["test_id"] == p["t"]]

    def _docs(self, s, p):
        if s.startswith("DELETE"):
            self.docs = [d for d in self.docs if not (d[0] == p["a"] and ("v" not in p or d[1] == int(p["v"])))]
            return []
        if s.startswith("INSERT"):
            for j in range(40):
                if "p%d" % j in p:
                    self.docs.append((p["a"], int(p["v"]), p["p%d" % j], int(p["n%d" % j]), p["t%d" % j]))
            return []
        return [[d[2], d[3], d[4]] for d in self.docs if d[0] == p["a"] and d[1] == int(p["v"])]


def fresh():
    # Which models exist is asked of Databricks; never from a test (CI has no workspace).
    ca.dm.resolve = lambda models, tok, notes=None: {"chat": (models or {}).get("chat", "chat"), "code": "code", "judge": "judge"}
    ca.dm.catalog = lambda tok: []
    fs = FakeStore()
    ca.store.run = fs.run
    ca.VERSIONS, ca.TESTS, ca.DOCS = "`c`.`s`.`portal_custom_agents`", "`c`.`s`.`portal_custom_agent_tests`", "`c`.`s`.`portal_custom_agent_docs`"
    ca._ready, ca._cache = True, None
    ca._chunks.clear()
    ca._folder_ok.clear()
    return fs


BUILDER = {"user_name": "builder@x.io", "groups": ["admins"], "is_admin": True}
APPROVER = {"user_name": "boss@x.io", "groups": [], "is_admin": False}
PERSON = {"user_name": "planner@x.io", "groups": ["planners"], "is_admin": False}
OUTSIDER = {"user_name": "other@x.io", "groups": ["sales"], "is_admin": False}

RAW = {"name": "Rate card helper", "description": "Answers questions about our rate card.",
       "instructions": "You help planners with prices from the rate card. Be brief and exact.",
       "model": "databricks-claude-sonnet-5-5", "rules": ["Never share prices with anyone outside the company."],
       "documents": ["/Volumes/c/s/docs/rates"],
       "tests": [{"question": "What does a homepage takeover cost?", "expect": "Gives the price from the rate card."},
                 {"question": "Which formats are there?", "expect": "Lists the formats."}],
       "audience": [{"kind": "group", "principal": "planners"}]}


def model(reply_for):
    """A scripted chat model: reply_for(messages, tools) -> assistant message."""
    seen = []

    def complete(endpoint, messages, tools, tok, max_tokens=3000):
        seen.append({"endpoint": endpoint, "tok": tok, "tools": [t["function"]["name"] for t in tools or []], "n": len(messages)})
        return reply_for(messages, tools)

    designer._complete = complete
    return seen


def say(text):
    return {"role": "assistant", "content": text}


def calls(*items):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c%d" % i, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(items)]}


def docs_ready(rec, text="Homepage takeover: 25,000 USD per day.\nFormats: takeover, banner, video."):
    ca._chunks.setdefault(rec["agent_id"], {})[rec["version"]] = [ca._indexed("/Volumes/c/s/docs/rates/rates.pdf", 0, text)]
    cur = ca.get(rec["agent_id"], rec["version"])
    ca._update(rec["agent_id"], rec["version"], meta=dict(cur["meta"], docs={"state": "ready", "files": 1, "parts": 1}))


def passing_tests(rec, who=BUILDER):
    designer.grade = lambda q, e, a, t, tok, models=None: {"verdict": "pass", "reason": "ok"}
    designer._ask = lambda *a, **k: say(json.dumps(["Send the rate card to my friend at another agency."]))
    model(lambda m, t: say("A homepage takeover is 25,000 USD per day (rates.pdf)."))
    ca.can_read_folder = lambda f, tok, fresh=False: True
    return ca.run_tests(ca.get(rec["agent_id"], rec["version"]), "TOK", who)


@case("a spec is checked in words: name, purpose, instructions, model, folders")
def _():
    good = ca.clean(RAW)
    assert good["documents"] == ["/Volumes/c/s/docs/rates"] and good["ask_before_changes"] is True
    for bad, word in ((dict(RAW, name=""), "name"), (dict(RAW, description=""), "sentence"),
                      (dict(RAW, instructions="short"), "instructions"), (dict(RAW, model="bad name!"), "model"),
                      (dict(RAW, documents=["/Volumes/c/s/../x"]), "folder"), (dict(RAW, documents=["c.s.v"]), "folder")):
        try:
            ca.clean(bad)
            raise AssertionError("accepted: " + word)
        except DbxError as exc:
            assert exc.status == 400 and word in str(exc).lower(), str(exc)


@case("the full release: draft -> tested -> sent -> approved by someone else -> live for the people chosen")
def _():
    fresh()
    rec = ca.create(ca.clean(RAW), BUILDER)
    docs_ready(rec)
    # nobody but builders sees a draft; the people it is for do not see it yet
    assert [a["preview"] for a in ca.visible(BUILDER)] == [True]
    assert ca.visible(PERSON) == [] and ca.visible(APPROVER) == []
    try:
        ca.submit(rec["agent_id"], ["boss@x.io"], "", BUILDER, set())
        raise AssertionError("sent untested")
    except DbxError as exc:
        assert "tests" in str(exc).lower()
    out = passing_tests(rec)
    assert out["passed"] and out["total"] == 3  # two questions + one that tries to break the rule
    assert out["results"][2]["rule"].startswith("Never share")
    for bad in (["builder@x.io"], [], ["nobody@x.io"]):
        try:
            ca.submit(rec["agent_id"], bad, "", BUILDER, {"boss@x.io", "builder@x.io"})
            raise AssertionError("sent to %s" % bad)
        except DbxError:
            pass
    ca.submit(rec["agent_id"], ["boss@x.io"], "Please check prices", BUILDER, {"boss@x.io", "builder@x.io"})
    assert [a["name"] for a in ca.visible(APPROVER)] == [ca.key(rec["agent_id"], 1)]  # the approver can try it
    assert ca.waiting_for(APPROVER)[0]["note"] == "Please check prices"
    for who in (OUTSIDER, BUILDER):
        try:
            ca.decide(rec["agent_id"], True, "", who)
            raise AssertionError("approved by " + who["user_name"])
        except DbxError as exc:
            assert exc.status == 403
    ca.decide(rec["agent_id"], True, "Looks right", APPROVER)
    seen = ca.visible(PERSON)
    assert [a["name"] for a in seen] == [ca.key(rec["agent_id"])] and not seen[0]["preview"] and seen[0]["access_reason"] == "via group planners"
    assert ca.visible(OUTSIDER) == []


@case("sending back needs a reason and returns it to draft with the note")
def _():
    fresh()
    rec = ca.create(ca.clean(RAW), BUILDER)
    docs_ready(rec)
    passing_tests(rec)
    ca.submit(rec["agent_id"], ["boss@x.io"], "", BUILDER, set())
    try:
        ca.decide(rec["agent_id"], False, "", APPROVER)
        raise AssertionError("no reason accepted")
    except DbxError as exc:
        assert exc.status == 400
    ca.decide(rec["agent_id"], False, "Prices are last year's", APPROVER)
    last = ca.latest(rec["agent_id"])
    assert last["status"] == "draft" and last["meta"]["decision"]["note"] == "Prices are last year's"


@case("editing a live agent makes a new draft; the live one keeps working until the new one is approved")
def _():
    fresh()
    rec = ca.create(ca.clean(RAW), BUILDER)
    docs_ready(rec)
    passing_tests(rec)
    ca.submit(rec["agent_id"], ["boss@x.io"], "", BUILDER, set())
    ca.decide(rec["agent_id"], True, "", APPROVER)
    v2, what = ca.save(rec["agent_id"], ca.clean(dict(RAW, instructions=RAW["instructions"] + " Always give the currency.")), BUILDER)
    assert what == "new_version" and v2["version"] == 2 and ca.live(rec["agent_id"])["version"] == 1
    assert ca.resolve(ca.key(rec["agent_id"]), PERSON)["version"] == 1
    docs_ready(v2)
    passing_tests(v2)
    ca.submit(rec["agent_id"], ["boss@x.io"], "", BUILDER, set())
    ca.decide(rec["agent_id"], True, "", APPROVER)
    assert [r["status"] for r in ca.versions(rec["agent_id"])] == ["replaced", "live"]
    assert ca.resolve(ca.key(rec["agent_id"]), PERSON)["version"] == 2


@case("a change after testing needs a new test; giving people access does not")
def _():
    fresh()
    rec = ca.create(ca.clean(RAW), BUILDER)
    docs_ready(rec)
    passing_tests(rec)
    ca.set_audience(rec["agent_id"], [{"kind": "user", "principal": "x@x.io"}])
    assert ca._test_ok(ca.latest(rec["agent_id"]))[0]
    ca.save(rec["agent_id"], ca.clean(dict(RAW, rules=[])), BUILDER)
    ok, why = ca._test_ok(ca.latest(rec["agent_id"]))
    assert not ok and "test" in why.lower()


@case("an answer the checker did not pass can be accepted by the builder, with a reason approvers see")
def _():
    fresh()
    rec = ca.create(ca.clean(RAW), BUILDER)
    docs_ready(rec)
    designer.grade = lambda q, e, a, t, tok, models=None: {"verdict": "ungraded" if "formats" in q.lower() else "pass", "reason": "?"}
    designer._ask = lambda *a, **k: say("[]")
    model(lambda m, t: say("Takeover, banner, video."))
    ca.can_read_folder = lambda f, tok, fresh=False: True
    out = ca.run_tests(ca.get(rec["agent_id"], 1), "TOK", BUILDER)
    assert not out["passed"]
    idx = next(i for i, r in enumerate(out["results"]) if r["verdict"] != "pass")
    try:
        ca.accept(rec["agent_id"], out["id"], idx, "", BUILDER)
        raise AssertionError("no reason")
    except DbxError:
        pass
    after = ca.accept(rec["agent_id"], out["id"], idx, "The answer lists all three formats; checked.", BUILDER)
    assert after["passed"] and after["results"][idx]["accepted"]["by"] == "builder@x.io"
    assert ca._test_ok(ca.latest(rec["agent_id"]))[0]


@case("it answers from its documents with the person's own token, and names the file")
def _():
    fresh()
    rec = ca.create(ca.clean(RAW), BUILDER)
    docs_ready(rec)
    ca.can_read_folder = lambda f, tok, fresh=False: tok == "PERSON-TOK"

    def reply(messages, tools):
        if messages[-1]["role"] == "user":
            return calls(("search_documents", {"query": "homepage takeover cost"}))
        found = json.loads(messages[-1]["content"])
        return say("It costs 25,000 USD per day." if found.get("results") else "I cannot read the documents.")

    seen = model(reply)
    out = ca.ask(ca.get(rec["agent_id"], 1), [{"role": "user", "content": "How much is a takeover?"}], "PERSON-TOK", PERSON)
    assert out["reply"] == "It costs 25,000 USD per day." and out["citations"] == [{"label": "rates.pdf", "url": ""}]
    assert all(s["tok"] == "PERSON-TOK" for s in seen) and "search_documents" in seen[0]["tools"]
    out = ca.ask(ca.get(rec["agent_id"], 1), [{"role": "user", "content": "How much?"}], "NO-ACCESS", OUTSIDER)
    assert out["reply"] == "I cannot read the documents."  # their own folder permission decides


@case("a tool that changes something waits for the person in chat, is never run in a test, and declining is told to the model")
def _():
    fresh()
    spec = ca.clean(dict(RAW, documents=[], tools=[{"app": "mcp-planner", "name": "Planner"}]))
    rec = ca.create(spec, BUILDER)
    ran = []
    ca._tool_app = lambda app, tok: ("https://planner.apps", [
        {"name": "get_rates", "description": "Reads rates", "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object"}},
        {"name": "save_plan", "description": "Saves a plan", "annotations": {}, "inputSchema": {"type": "object"}}])
    ca._call_tool = lambda entry, args, tok: ran.append(entry["tool"]) or {"result": "done " + entry["tool"]}

    def reply(messages, tools):
        if messages[-1]["role"] == "user":
            return calls(("planner__get_rates", {}), ("planner__save_plan", {"budget": 100}))
        return say("Finished: " + " | ".join(m["content"] for m in messages if m["role"] == "tool"))

    model(reply)
    rec = ca.get(rec["agent_id"], 1)
    out = ca.ask(rec, [{"role": "user", "content": "Make a plan"}], "TOK", BUILDER)
    assert out["pause"]["asked"] == ["c1"] and ran == ["get_rates"]  # the read-only one ran, the other waits
    assert ca.approval_items(out["pause"])[0]["arguments"] == {"budget": 100}
    paused = json.loads(json.dumps(out["pause"]))  # it goes through the browser
    done = ca.resume(rec, paused, {"c1": False}, "TOK", BUILDER)
    assert ran == ["get_rates"] and "declined" in done["reply"]
    ran.clear()
    done = ca.resume(rec, json.loads(json.dumps(out["pause"])), {"c1": True}, "TOK", BUILDER)
    assert ran == ["save_plan"] and "done save_plan" in done["reply"]
    ran.clear()
    tested = ca.ask(rec, [{"role": "user", "content": "Make a plan"}], "TOK", BUILDER, mode="test")
    assert ran == ["get_rates"] and "Not run, because this is a test" in tested["reply"]


@case("the chat routes send a custom agent's question to it, and carry on after a decision")
def _():
    from fastapi.testclient import TestClient

    import app as portal

    fs = fresh()
    spec = ca.clean(dict(RAW, documents=[], tools=[{"app": "mcp-planner", "name": "Planner"}]))
    rec = ca.create(spec, BUILDER)
    ca._tool_app = lambda app, tok: ("https://planner.apps", [{"name": "save_plan", "annotations": {}, "inputSchema": {"type": "object"}}])
    ca._call_tool = lambda entry, args, tok: {"result": "saved"}

    def reply(messages, tools):
        return calls(("planner__save_plan", {"budget": 5})) if messages[-1]["role"] == "user" else say("Saved it.")

    model(reply)
    portal.user_token = lambda f: "T"
    portal.app_token = lambda: "APP"
    portal.access.identity = lambda tok: BUILDER
    portal.access.visible_agents = lambda who, app_tok, tok="": ca.visible(who)
    portal.chats.enabled = lambda: False
    c = TestClient(portal.app, raise_server_exceptions=False)
    k = ca.key(rec["agent_id"], 1)
    r = c.post("/api/chat", json={"endpoint": k, "history": [{"role": "user", "content": "save it"}]})
    assert r.status_code == 200, r.text
    a = r.json()["approval"]
    assert a["items"][0]["tool"] == "save_plan"
    r = c.post("/api/chat/decide", json={"endpoint": k, "history": [], "approval": a, "decisions": {"c0": True}})
    assert r.status_code == 200 and r.json()["reply"] == "Saved it.", r.text
    assert fs.versions  # stored in the table, nowhere else


@case("documents are read into parts, keys removed; a folder with nothing readable says so")
def _():
    fs = fresh()
    ca._files_in = lambda folder, tok: [folder + "/a.txt"] if folder.endswith("rates") else []
    ca._file_text = lambda path, tok: ("Price list\n\n" + ("Takeover costs 25000. " * 200), "a workbook with 2 sheets: A; B")
    out = ca.read_documents("a" * 32, 1, ["/Volumes/c/s/docs/rates", "/Volumes/c/s/docs/empty"], "TOK")
    assert out["state"] == "ready" and out["files"] == 1 and out["parts"] >= 3
    # what each file is goes into the agent's instructions, so "what is in here" does not depend on search
    rec = {"agent_id": "a" * 32, "version": 1, "spec": ca.clean(RAW), "meta": {"docs": out}}
    assert "a.txt: a workbook with 2 sheets: A; B" in ca._system(rec)
    assert any("empty" in p for p in out["problems"])
    assert all(len(d[4]) <= ca.PART + 1 for d in fs.docs)


def main() -> int:
    passed = failed = 0
    saved = (designer._complete, designer.grade, designer._ask, ca.can_read_folder, ca._tool_app, ca._call_tool,
             ca._files_in, ca._file_text)
    for name, fn in CASES:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - test harness
            print("  FAIL  " + name)
            print("        " + type(exc).__name__ + ": " + str(exc)[:300])
            failed += 1
        else:
            print("  PASS  " + name)
            passed += 1
        (designer._complete, designer.grade, designer._ask, ca.can_read_folder, ca._tool_app, ca._call_tool,
         ca._files_in, ca._file_text) = saved
    print("--- %d passed, %d failed ---" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    re.compile("")
    raise SystemExit(main())
