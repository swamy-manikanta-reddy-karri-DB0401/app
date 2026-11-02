"""Custom-code agents (Databricks Apps) - no network.

Run: python test_app_agents.py

An agent written in code runs as its own app. The portal records which apps it offers in a
small table, decides who sees one from the app's own permission list, and asks it questions
with the person's own token, trying the request shapes agent apps actually take.
"""
from __future__ import annotations

import json

import httpx

import access
import adapters
import app_agents
import chat
from dbx import DbxError

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


URL = "https://planner-123.azure.databricksapps.com/api/chat"


class Http:
    """Plays one agent app behind the Apps proxy."""

    def __init__(self, takes="message", status=200, ctype="application/json", reply=None):
        self.takes, self.status, self.ctype, self.reply, self.sent = takes, status, ctype, reply, []

    def post(self, url, headers=None, json=None, timeout=None):
        self.sent.append((url, headers, json))
        req = httpx.Request("POST", url)
        if self.status != 200 or "html" in self.ctype:
            return httpx.Response(self.status, headers={"content-type": self.ctype}, text="<html>sign in</html>", request=req)
        if self.takes not in json:
            # what FastAPI/pydantic v2 says for a missing field
            body = {"detail": [{"type": "missing", "loc": ["body", self.takes], "msg": "Field required", "input": json}]}
            return httpx.Response(422, json=body, request=req)
        return httpx.Response(200, json=self.reply or {"reply": "Plan ready.", "tool_calls": [{"name": "get_rates"}]}, request=req)


def wire(h):
    chat.http = lambda: h
    adapters._learned.clear()


@case("an app that takes {message, history} is found by trying the shapes in turn, then remembered")
def _():
    h = Http(takes="message")
    wire(h)
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}, {"role": "user", "content": "plan it"}]
    out = chat.ask(URL, "app", hist, "USER")
    assert out["reply"] == "Plan ready." and out["tools"] == ["get_rates"], out
    assert [list(s[2])[0] for s in h.sent] == ["input", "messages", "message"], h.sent
    assert h.sent[-1][2] == {"message": "plan it", "history": hist[:2]}
    assert all(s[1]["Authorization"] == "Bearer USER" for s in h.sent)  # always the person's own token
    h.sent.clear()
    chat.ask(URL, "app", hist, "USER")
    assert [list(s[2])[0] for s in h.sent] == ["message"]  # learnt


@case("an app that takes Responses input is asked once")
def _():
    h = Http(takes="input", reply={"output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done."}]}]})
    wire(h)
    out = chat.ask(URL, "app", [{"role": "user", "content": "x"}], "USER")
    assert out["reply"] == "Done." and len(h.sent) == 1


@case("the Apps proxy refusing the person's token says so and links to the app; never retried as the portal")
def _():
    for status, ctype in ((302, "text/html"), (401, "text/html"), (403, "application/json"), (200, "text/html")):
        h = Http(status=status, ctype=ctype)
        wire(h)
        try:
            chat.ask(URL, "app", [{"role": "user", "content": "x"}], "USER")
            raise AssertionError("not refused for %s" % status)
        except DbxError as exc:
            assert exc.status == 403 and "https://planner-123.azure.databricksapps.com" in str(exc), str(exc)
        assert len(h.sent) == 1 and h.sent[0][1]["Authorization"] == "Bearer USER"


@case("a wrong address says which address, in words")
def _():
    wire(Http(status=404, ctype="application/json"))
    try:
        chat.ask(URL, "app", [{"role": "user", "content": "x"}], "USER")
        raise AssertionError("no error")
    except DbxError as exc:
        assert "/api/chat" in str(exc) and exc.status == 502


@case("settings are checked: address, folders and file types")
def _():
    assert app_agents.clean({"path": ""})["path"] == "/invocations"
    for bad in ("invocations", "/../x", "/a b"):
        try:
            app_agents.clean({"path": bad})
            raise AssertionError(bad)
        except DbxError as exc:
            assert exc.status == 400
    assert app_agents.clean({"accepts": ".XLSX, csv, bad type"})["accepts"] == "xlsx, csv"
    try:
        app_agents.clean({"upload_volume": "/Volumes/c/s/v"})
        raise AssertionError("path accepted")
    except DbxError:
        pass
    assert app_agents.clean({"output_volume": "c.s.out, c.s.proto"})["output_volume"] == "c.s.out, c.s.proto"


ACL = [
    {"group_name": "planners", "all_permissions": [{"permission_level": "CAN_USE"}]},
    {"user_name": "owner@x.io", "all_permissions": [{"permission_level": "CAN_MANAGE"}]},
    {"service_principal_name": "sp-1", "all_permissions": [{"permission_level": "CAN_MANAGE"}]},
]
ROW = {"app_name": "planner", "display_name": "Media planner", "blurb": "", "path": "/api/chat",
       "upload_volume": "", "accepts": "", "output_volume": "", "added_by": "owner@x.io"}
APP = {"name": "planner", "url": "https://planner-123.azure.databricksapps.com/", "compute_status": {"state": "ACTIVE"},
       "app_status": {"state": "RUNNING"}}


def fake_workspace(acl=ACL, app=APP, rows=(ROW,)):
    app_agents.registered = lambda fresh=False: list(rows)
    app_agents.acl = lambda name: (_ for _ in ()).throw(DbxError("no", 403)) if acl is None else acl
    app_agents.app_info = lambda name, tok="": app


@case("people see an app agent only through the app's own permission list (direct, or a group)")
def _():
    fake_workspace()
    seen = app_agents.visible({"user_name": "a@x.io", "groups": ["planners"]})
    assert len(seen) == 1 and seen[0]["name"] == "app:planner" and seen[0]["access_reason"] == "via group planners"
    assert seen[0]["ready"] and seen[0]["kind"] == "custom" and seen[0]["app_url"] == "https://planner-123.azure.databricksapps.com"
    assert app_agents.target(seen[0]) == URL
    assert app_agents.visible({"user_name": "b@x.io", "groups": ["sales"]}) == []
    fake_workspace(acl=None)  # cannot read the list: nothing can confirm access, so it is not shown
    assert app_agents.visible({"user_name": "a@x.io", "groups": ["planners"]}) == []


@case("a stopped app is listed as not ready and is never called")
def _():
    fake_workspace(app=dict(APP, compute_status={"state": "STOPPED"}))
    agent = app_agents.visible({"user_name": "owner@x.io", "groups": []})[0]
    assert not agent["ready"] and agent["state"] == "stopped"
    agent["app_url"] = ""
    try:
        app_agents.target(agent)
        raise AssertionError("called")
    except DbxError as exc:
        assert exc.status == 503


@case("granting on an app writes Can use to the app's list; a revoke keeps the portal's own grant")
def _():
    calls = []

    def fake_call(method, path, tok, **kw):
        calls.append((method, path, kw.get("json")))
        if method == "GET":
            return {"access_control_list": ACL}
        return {"access_control_list": []}

    access.call = fake_call
    target = {"app_name": "planner", "id": "app:planner"}
    assert access.perm_paths(target) == ["/api/2.0/permissions/apps/planner"]
    access.set_grant(target, "group", "finance", "CAN_QUERY", "APP")
    assert calls[-1] == ("PATCH", "/api/2.0/permissions/apps/planner",
                         {"access_control_list": [{"group_name": "finance", "permission_level": "CAN_USE"}]})
    access.set_grant(target, "group", "planners", None, "APP")
    kept = calls[-1][2]["access_control_list"]
    assert calls[-1][0] == "PUT" and {"service_principal_name": "sp-1", "permission_level": "CAN_MANAGE"} in kept
    assert not any(k.get("group_name") == "planners" for k in kept)


@case("registering writes bound parameters only, and keeps settings not given")
def _():
    ran = []
    app_agents.QUALIFIED = "`c`.`s`.`portal_app_agents`"
    app_agents._ready = True
    app_agents.registered = lambda fresh=False: [dict(ROW, upload_volume="c.s.in")]
    app_agents.store.run = lambda sql, params=None: ran.append((sql, params)) or []
    app_agents.save("planner", {"display_name": "Planner'); DROP TABLE x;--"}, "owner@x.io")
    sql = " ".join(s for s, _ in ran)
    assert "DROP" not in sql and "Planner" not in sql
    values = {p["name"]: p["value"] for p in ran[-1][1]}
    assert values["display_name"].startswith("Planner')") and values["upload_volume"] == "c.s.in" and values["path"] == "/api/chat"


@case("an app agent's reply in a plain {reply} or {text} wrapper is read")
def _():
    assert adapters.parse({"reply": "hi", "tool_calls": [{"name": "t1"}, {"function": {"name": "t2"}}]})["tools"] == ["t1", "t2"]
    assert adapters.parse({"text": "plain"})["reply"] == "plain"
    assert adapters.shaped("message", [{"role": "user", "content": "only"}]) == {"message": "only", "history": []}


def client(user="owner@x.io", admin=True):
    from fastapi.testclient import TestClient

    import app as portal

    portal.user_token = lambda f: "T"
    portal.app_token = lambda: "APP"
    portal.access.identity = lambda tok: {"user_name": user, "groups": ["admins"] if admin else [], "is_admin": admin}
    portal.chats.enabled = lambda: False
    return portal, TestClient(portal.app, raise_server_exceptions=False)


@case("the chat route sends a question for an app agent to the app's address, as the person")
def _():
    fake_workspace()
    h = Http(takes="message")
    wire(h)
    portal, c = client(user="owner@x.io", admin=False)
    portal.access.visible_agents = lambda who, app_tok, tok="": app_agents.visible(who)
    r = c.post("/api/chat", json={"endpoint": "app:planner", "history": [{"role": "user", "content": "plan"}]})
    assert r.status_code == 200 and r.json()["reply"] == "Plan ready.", r.text
    assert h.sent[-1][0] == URL and h.sent[-1][1]["Authorization"] == "Bearer T"


@case("registering needs Can Manage on the app; the portal is given access first")
def _():
    fake_workspace()
    portal, c = client(user="someone@x.io")
    app_agents.check = lambda name, tok: {"found": True, "running": True, "url": APP["url"], "message": "It is running."}
    shared = []
    app_agents.share_with_portal = lambda name, tok: shared.append(name) or ""
    app_agents.enabled = lambda: True
    saved = []
    app_agents.save = lambda name, values, who: saved.append((name, values)) or dict(ROW, **values)
    r = c.post("/api/admin/app-agents", json={"app_name": "planner", "display_name": "Planner"})
    assert r.status_code == 403 and "Can Manage" in r.text and shared == ["planner"] and not saved
    portal2, c2 = client(user="owner@x.io")
    r = c2.post("/api/admin/app-agents", json={"app_name": "planner", "display_name": "Planner", "path": "/api/chat"})
    assert r.status_code == 200, r.text
    assert saved and saved[0][1]["display_name"] == "Planner"


def main() -> int:
    passed = failed = 0
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
    print("--- %d passed, %d failed ---" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    json.dumps({})
    raise SystemExit(main())
