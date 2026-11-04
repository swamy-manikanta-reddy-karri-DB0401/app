"""Offline checks of what a custom agent remembers about each person (memory.py): only their own notes, keys
and personal data kept out, never used in a test, and each person can see and clear their own."""
from __future__ import annotations

import json
import sys

import memory

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class Fake:
    def __init__(self, other=None):
        self.rows, self.n, self.other = [], 0, other

    def run(self, sql, params=None):
        if "portal_custom_agent_memory" not in sql and self.other:
            return self.other(sql, params)  # the custom agents' own tables (test_custom_agents' fake)
        p = {x["name"]: x.get("value") for x in params or []}
        if sql.startswith("INSERT"):
            self.n += 1
            self.rows.append({"a": p["a"], "u": p["u"], "n": p["n"], "t": p["t"], "at": str(self.n).zfill(6)})
            return []
        if sql.startswith("DELETE"):
            self.rows = [r for r in self.rows if not (r["a"] == p["a"] and r["u"] == p["u"] and r["n"] == p["n"])]
            return []
        if sql.startswith("SELECT"):
            mine = [r for r in self.rows if r["a"] == p["a"] and r["u"] == p["u"]]
            return [[r["n"], r["t"], r["at"]] for r in sorted(mine, key=lambda r: r["at"], reverse=True)]
        return []


def fresh(keep=False):
    f = Fake(memory.store.run if keep else None)
    memory.store.run = f.run
    memory.TABLE = "`c`.`s`.`portal_custom_agent_memory`"
    memory._ready = True
    memory._cache.clear()
    return f


@case("each person has their own notes; nobody else's are ever read")
def _():
    fresh()
    memory.remember("ag", "a@x.io", "Works in INR")
    memory.remember("ag", "b@x.io", "Plans for the Display team")
    assert [n["text"] for n in memory.notes("ag", "a@x.io")] == ["Works in INR"]
    assert [n["text"] for n in memory.notes("ag", "b@x.io")] == ["Plans for the Display team"]
    assert memory.notes("other-agent", "a@x.io") == []
    assert "Works in INR" in memory.prompt_block("ag", "a@x.io") and "Display" not in memory.prompt_block("ag", "a@x.io")


@case("keys are removed, personal data is masked when the agent's policy masks it, duplicates are not kept")
def _():
    fresh()
    out = memory.remember("ag", "a@x.io", "My token is dapi" + "0123456789abcdef" * 2 + " and email jo@corp.com",
                          {"input": {"personal_data": "mask"}})
    assert "dapi" not in out["text"] and "jo@corp.com" not in out["text"] and "[email removed]" in out["text"]
    assert memory.remember("ag", "a@x.io", out["text"])["kept"] is False


@case("at most 200 notes per person: the oldest goes; forget one, some or all")
def _():
    f = fresh()
    for i in range(memory.MAX_NOTES + 3):
        memory.remember("ag", "a@x.io", "fact number %d" % i)
    have = memory.notes("ag", "a@x.io")
    assert len(have) == memory.MAX_NOTES and have[0]["text"] == "fact number %d" % (memory.MAX_NOTES + 2)
    assert memory.forget("ag", "a@x.io", text="number 202") == 1
    assert memory.forget("ag", "a@x.io", note_id=memory.notes("ag", "a@x.io")[0]["id"]) == 1
    assert memory.forget("ag", "a@x.io", text="*") == memory.MAX_NOTES - 2 and memory.notes("ag", "a@x.io") == []
    assert memory.forget("ag", "b@x.io", text="*") == 0


@case("a custom agent with memory on: notes in its instructions, remember/forget in a chat; never in a test")
def _():
    import test_custom_agents as t

    t.fresh()
    fresh(keep=True)
    rec = t.ca.create(t.ca.clean(dict(t.RAW, documents=[], memory={"on": True})), t.BUILDER)
    rec = t.ca.get(rec["agent_id"], 1)
    assert rec["spec"]["memory"] == {"on": True}
    assert t.ca.checksum(rec["spec"]) != t.ca.checksum(dict(rec["spec"], memory={"on": False}))  # part of what is tested
    memory.remember(rec["agent_id"], t.PERSON["user_name"], "Works in INR")
    seen = []

    def reply(messages, tools):
        seen.append((messages[0]["content"], [x["function"]["name"] for x in tools or []]))
        if messages[-1]["role"] == "user":
            return t.calls(("remember", {"note": "Usual market is India"}))
        return t.say("Noted.")

    t.model(reply)
    t.ca.ask(rec, [{"role": "user", "content": "Remember my market is India"}], "TOK", t.PERSON)
    assert "Works in INR" in seen[0][0] and "remember" in seen[0][1]
    assert [n["text"] for n in memory.notes(rec["agent_id"], t.PERSON["user_name"])] == ["Usual market is India", "Works in INR"]
    seen.clear()
    t.model(reply)
    t.ca.ask(rec, [{"role": "user", "content": "test"}], "TOK", t.BUILDER, mode="test")
    assert "remember" not in seen[0][1] and "remember about this person" not in seen[0][0]


@case("the person sees and clears only their own notes through the routes")
def _():
    import test_custom_agents as t

    t.fresh()
    fresh(keep=True)
    rec = t.ca.create(t.ca.clean(dict(t.RAW, documents=[], memory={"on": True})), t.BUILDER)
    memory.remember(rec["agent_id"], t.BUILDER["user_name"], "Prefers short answers")
    memory.remember(rec["agent_id"], "someone@else.io", "Secret project")
    from fastapi.testclient import TestClient

    import app as portal

    portal.user_token = lambda f: "T"
    portal.access.identity = lambda tok: t.BUILDER
    portal.access.VISIBLE.clear()
    c = TestClient(portal.app, raise_server_exceptions=False)
    key = t.ca.key(rec["agent_id"], 1)  # the draft: its builder may use it as a preview
    r = c.get("/api/custom-agents/memory", params={"key": key})
    assert r.status_code == 200 and [n["text"] for n in r.json()["notes"]] == ["Prefers short answers"], r.text
    assert c.delete("/api/custom-agents/memory", params={"key": key}).json()["forgotten"] == 1
    assert c.delete("/api/custom-agents/memory", params={"key": key, "note": "not-a-note"}).status_code == 400
    portal.access.identity = lambda tok: {"user_name": "outsider@x.io", "is_admin": False, "groups": []}
    assert c.get("/api/custom-agents/memory", params={"key": key}).status_code == 403  # not theirs to use
    assert [n["text"] for n in memory.notes(rec["agent_id"], "someone@else.io")] == ["Secret project"]


if __name__ == "__main__":
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  " + name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL  " + name + "\n        " + type(exc).__name__ + ": " + str(exc)[:300])
    print("--- %d passed, %d failed ---" % (len(CASES) - failed, failed))
    sys.exit(1 if failed else 0)
