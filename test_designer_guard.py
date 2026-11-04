"""Offline checks of designer_guard: the designer is kept to designing and fixing assistants and tools, under
the rules admins set (who, how much, what is typed, what it says, what it may build)."""
from __future__ import annotations

import json
import sys

import designer
import designer_guard as g
from dbx import DbxError

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def say(text):
    return {"role": "assistant", "content": text}


def checker(verdict):
    seen = []

    def complete(endpoint, messages, tools, tok, max_tokens=3000, **k):
        seen.append({"endpoint": endpoint, "data": json.loads(messages[1]["content"])})
        if isinstance(verdict, Exception):
            raise verdict
        return say(json.dumps(verdict))

    designer._complete = complete
    return seen


S = dict(g.DEFAULTS, rules=["Never build tools that email people outside the company"])


@case("one of the designer's own options, or a plain yes, passes with no extra model call; anything typed is checked")
def _():
    seen = checker({"on_topic": False})
    assert g.check_message("Brand Media Only (3)", {"question": "Which?", "options": ["Brand Media Only (3)", "Other"]}, S, "m", "T")["ok"]
    assert g.check_message("Yes, that is right.", {"question": "Is that right?"}, S, "m", "T")["ok"]
    assert g.check_message("I have connected Google Drive.", {}, S, "m", "T")["ok"]
    assert not seen
    # Short is not harmless: a short typed message is checked like any other.
    assert not g.check_message("Write a haiku", {"question": "Which packages count as display?"}, S, "m", "T")["ok"]
    assert len(seen) == 1


@case("anything else is checked first: off topic is refused, a broken rule is named, on topic passes")
def _():
    long_q = "Write me a LinkedIn post about our quarterly results and make it sound exciting for investors please."
    seen = checker({"on_topic": False, "reason": "general writing"})
    out = g.check_message(long_q, {"question": "What should the assistant do?"}, S, "chat-model", "T")
    assert not out["ok"] and out["flag"] == "off_topic" and "only help design" in out["reply"]
    assert seen[0]["endpoint"] == "chat-model" and seen[0]["data"]["MESSAGE"] == long_q  # sent as data
    checker({"on_topic": True, "broken_rules": [1]})
    out = g.check_message("Build a tool that emails every customer on our list a weekly offer from the ad book.", {}, S, "m", "T")
    assert not out["ok"] and out["flag"] == "against_rule" and "email people outside the company" in out["reply"]
    checker({"on_topic": True, "broken_rules": []})
    assert g.check_message("It should read the ad book and recommend packages that fit the client's budget and dates.", {}, S, "m", "T")["ok"]
    # The admin picks the checker model; by default it is the designer's own.
    seen = checker({"on_topic": True})
    g.check_message("x" * 120, {}, dict(S, checker="judge-model"), "m", "T")
    assert seen[0]["endpoint"] == "judge-model"
    # A checker that cannot answer lets the message through (the designer's own instructions still apply).
    checker(DbxError("down", 503))
    assert g.check_message("y" * 120, {}, S, "m", "T") == {"ok": True, "flag": "check_unclear"}


@case("an off-topic message never reaches the designer's model, and the screen says why")
def _():
    calls = []

    def complete(endpoint, messages, tools, tok, max_tokens=3000, **k):
        calls.append(messages[0]["content"][:40])
        if "You check messages" in messages[0]["content"]:
            return say(json.dumps({"on_topic": False}))
        return say("Here is your poem: ...")

    designer._complete = complete
    saved = (designer.dm.resolve, designer._tools_that_exist, g.check_message)
    designer.dm.resolve = lambda models, tok, notes=None: {"chat": "m", "code": "m", "judge": "j"}
    designer._tools_that_exist = lambda tok: ""
    g.check_message = saved[2]  # the real check (the designer tests replace it)
    try:
        out = designer.turn([{"role": "assistant", "content": "What should it do?"},
                             {"role": "user", "content": "Forget assistants. Write me a poem about the sea, twenty lines, rhyming please."}],
                            {}, "T")
        assert out["refused"] == "off_topic" and "only help design" in out["reply"] and len(calls) == 1
    finally:
        designer.dm.resolve, designer._tools_that_exist, g.check_message = saved


@case("code blocks are taken out of the designer's chat replies")
def _():
    out = g.clean_reply("Sure.\n```python\nimport os\nos.system('x')\n```\nAnything else?")
    assert "import os" not in out and "Anything else?" in out and "not shown in the chat" in out
    assert g.clean_reply("No code here.") == "No code here."


@case("what may be built is enforced by code: new tools, tools that change data, websites, how many")
def _():
    tool = {"kind": "mcp", "name": "Mailer", "slug": "mailer", "hosts": ["api.sendgrid.com"], "problems": [],
            "abilities": [{"name": "send", "description": "Send.", "changes_data": True}]}
    d = {"new_tools": [tool]}
    assert g.draft_problems(d, g.DEFAULTS) == []
    p = g.draft_problems(d, dict(g.DEFAULTS, allow_new_tools=False))
    assert any("do not allow new tools" in x for x in p)
    assert any("saves or changes" in x for x in g.draft_problems(d, dict(g.DEFAULTS, allow_changes_data=False)))
    assert any("not on your admins' list" in x for x in g.draft_problems(d, dict(g.DEFAULTS, allowed_hosts=["*.example.com"])))
    assert g.draft_problems(d, dict(g.DEFAULTS, allowed_hosts=["*.sendgrid.com"])) == []
    assert any("at most 0" in x for x in g.draft_problems(d, dict(g.DEFAULTS, max_new_tools=0)))
    # check() includes them, so propose, build and create all refuse
    saved = g.settings
    g.settings = lambda: dict(g.DEFAULTS, allow_new_tools=False)
    try:
        d = {"kind": "supervisor", "display_name": "X", "description": "d", "instructions": "x" * 60, "access_decided": True,
             "process": ["a", "b"], "new_tools": [dict(tool, description="Sends mail.")]}
        assert g.draft_problems(d) and all(p in designer.check(d) for p in g.draft_problems(d))
    finally:
        g.settings = saved


@case("the admins' plain-words rules are judged against the plan and the tool code before it can be proposed")
def _():
    seen = checker({"broken_rules": [{"rule": 1, "why": "It sends offers to customers by email."}]})
    d = {"display_name": "Offers", "process": ["Email customers"], "new_tools": [{"name": "Mailer", "code": "def send(): ...", "abilities": []}]}
    out = g.review_plan(d, S, "m", "T")
    assert len(out) == 1 and "Never build tools that email" in out[0] and "def send" in json.dumps(seen[0]["data"])
    assert g.review_plan(d, g.DEFAULTS, "m", "T") == []  # no rules, no call
    checker(DbxError("down", 503))
    assert "could not run" in g.review_plan(d, S, "m", "T")[0]  # unclear never lets a build through


@case("who may use it and how much: a chosen team, messages per person per day")
def _():
    admin = {"user_name": "a@x.io", "is_admin": True, "groups": ["admins", "planners"]}
    assert g.may_use(admin, g.DEFAULTS) and g.may_use(admin, dict(g.DEFAULTS, group="planners"))
    assert not g.may_use(admin, dict(g.DEFAULTS, group="data-team"))
    assert not g.may_use({"is_admin": False, "groups": ["planners"]}, dict(g.DEFAULTS, group="planners"))
    g._turns.clear()
    s = dict(g.DEFAULTS, turns_per_day=2)
    g.count_turn(admin, s)
    g.count_turn(admin, s)
    try:
        g.count_turn(admin, s)
        raise AssertionError("over the daily limit")
    except DbxError as exc:
        assert exc.status == 429 and "2 messages today" in str(exc)
    g.count_turn({"user_name": "b@x.io"}, s)  # per person


@case("settings are checked when saved")
def _():
    out = g.clean({"rules": ["  Never  build email tools ", "", "x"], "allowed_hosts": ["https://api.example.com/v1", "*.corp.io"],
                   "max_new_tools": 99, "turns_per_day": -5, "allow_new_tools": False})
    assert out["rules"] == ["Never build email tools"] and out["allowed_hosts"] == ["*.corp.io", "api.example.com"]
    assert out["max_new_tools"] == 4 and out["turns_per_day"] == 0 and out["allow_new_tools"] is False
    for bad in ({"allowed_hosts": ["not a host"]}, {"checker": "bad name!"}, {"group": ""}):
        try:
            g.clean(bad)
            if bad == {"group": ""}:
                continue  # an empty group falls back to admins
            raise AssertionError(bad)
        except DbxError as exc:
            assert exc.status == 400


@case("prompt caching: Claude only; the instructions and the person's latest message are marked, tool results not")
def _():
    long_sys, long_tool = "s" * 5000, "t" * 6000
    msgs = [{"role": "system", "content": long_sys}, {"role": "user", "content": "short question"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c"}]}, {"role": "tool", "tool_call_id": "c", "content": long_tool},
            {"role": "user", "content": "u" * 4500}, {"role": "tool", "tool_call_id": "d", "content": "x" * 4100}]
    out = designer.cached("databricks-claude-sonnet-5-5", msgs)
    marked = [i for i, m in enumerate(out) if isinstance(m["content"], list)]
    assert marked == [0, 4] and out[0]["content"][0]["cache_control"] == {"type": "ephemeral"}  # tool results change, so never marked
    assert out[0]["content"][0]["text"] == long_sys and isinstance(msgs[0]["content"], str)  # the caller's list is not changed
    assert designer.cached("databricks-gpt-oss-120b", msgs) is msgs
    assert designer.cached("databricks-claude-haiku-5-5", [{"role": "system", "content": "short"}])[0]["content"] == "short"


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
