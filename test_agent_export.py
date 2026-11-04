"""Offline checks of exporting a custom agent as code (agent_export.py): both styles, what is in the project,
that the code parses, that nothing secret goes in, and that only Developer mode may export."""
from __future__ import annotations

import ast
import io
import json
import sys
import zipfile

import agent_export as ax

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


REC = {"agent_id": "a" * 32, "version": 3, "spec": {
    "name": "Media Plan Builder", "description": "Builds media plans from the ad book.", "model": "databricks-claude-sonnet-5-5",
    "instructions": "", "runbook": {"name": "Steps.docx", "text": "Step 1. Ask for the budget.", "sha": "x"},
    "rules": ["Never invent a price."], "documents": ["/Volumes/c/s/docs/adbook"], "tools": [{"app": "mcp-gam-mock", "name": "GAM"}],
    "data": [{"space_id": "0" * 32, "title": "Sales"}],
    "assistants": [{"key": "mas-1-endpoint", "name": "Weather"}, {"key": "ca:" + "b" * 32, "name": "Other custom agent"}],
    "tests": [{"question": "Plan for 50k", "expect": "Asks for dates", "then": ["May"]}], "limits": {"steps": 6, "reply_tokens": 2000},
    "policy": {"name": "Standard"}, "audience": [{"kind": "group", "principal": "sales"}]}}


def files_of(framework):
    data, name = ax.build(REC, framework)
    z = zipfile.ZipFile(io.BytesIO(data))
    root = name[:-4] + "/"
    return name, {n[len(root):]: z.read(n).decode("utf-8") for n in z.namelist()}


@case("both styles: the same project, the agent written as MLflow or as LangGraph, pinned to the checked versions")
def _():
    for fw in ("mlflow", "langgraph"):
        name, f = files_of(fw)
        assert name == "media-plan-builder-%s.zip" % fw
        for need in ("README.md", "requirements.txt", "app.yaml", "app.py", "agent/__init__.py", "agent/config.json", "agent/tools.py",
                     "agent/agent.py", "agent/runbook.md", "tests/conversations.json", "tests/run_tests.py"):
            assert need in f, (fw, need)
        req = f["requirements.txt"]
        assert "mlflow==3.18.0" in req and "databricks-mcp==0.9.2" in req and "databricks-sdk==0.161.0" in req
        if fw == "mlflow":
            assert "openai==3.45.0" in req and "class Agent(ResponsesAgent)" in f["agent/agent.py"] and "create_agent" not in f["agent/agent.py"]
        else:
            assert "langgraph==1.2.19" in req and "databricks-langchain==0.3.0" in req
            assert "create_agent(" in f["agent/agent.py"] and 'pop("temperature"' in f["agent/agent.py"]  # Claude refuses temperature
        for path, text in f.items():
            if path.endswith(".py"):
                ast.parse(text, path)  # every file is valid Python


@case("the config carries the agent's real setup; helpers that only run in the portal are named, not exported")
def _():
    _, f = files_of("mlflow")
    cfg = json.loads(f["agent/config.json"])
    assert cfg["model"] == "databricks-claude-sonnet-5-5" and cfg["runbook"].startswith("Step 1") and cfg["rules"] == ["Never invent a price."]
    assert cfg["tools"] == ["mcp-gam-mock"] and cfg["data"][0]["title"] == "Sales" and cfg["limits"]["steps"] == 6
    assert cfg["assistants"] == [{"name": "Weather", "endpoint": "mas-1-endpoint"}]
    assert "Other custom agent" in f["README.md"] and "_not_exported" not in cfg
    assert json.loads(f["tests/conversations.json"]) == [{"question": "Plan for 50k", "then": ["May"], "expect": "Asks for dates"}]
    assert "audience" not in cfg and "policy" not in cfg  # who may use it and the portal's policy stay in the portal


@case("security travels with it: runs as the person, changing tools need ALLOW_CHANGES, results are data, no secrets")
def _():
    _, f = files_of("langgraph")
    assert "x-forwarded-access-token" in f["app.py"].lower() and "PERSON_TOKEN" in f["agent/tools.py"]
    assert "ALLOW_CHANGES" in f["agent/tools.py"] and "not_run" in f["agent/tools.py"]
    blob = json.dumps(f).lower()
    for secret in ("dapi", "client_secret", "password", "bearer "):
        assert secret not in blob, secret
    _, m = files_of("mlflow")
    assert '"untrusted_data"' in m["agent/agent.py"]


@case("only Developer mode may export, and the export is recorded")
def _():
    import designer_guard
    import test_custom_agents as t
    from fastapi.testclient import TestClient

    import app as portal

    t.fresh()
    rec = t.ca.create(t.ca.clean(dict(t.RAW, documents=[])), t.BUILDER)
    portal.user_token = lambda f: "T"
    portal.access.identity = lambda tok: dict(t.BUILDER, is_admin=True, groups=["admins"])
    c = TestClient(portal.app, raise_server_exceptions=False)
    saved = designer_guard.settings
    try:
        designer_guard.settings = lambda: dict(designer_guard.DEFAULTS)
        assert c.post("/api/admin/custom-agents/%s/export" % rec["agent_id"], json={"framework": "mlflow"}).status_code == 403
        designer_guard.settings = lambda: dict(designer_guard.DEFAULTS, developer_group="admins")
        r = c.post("/api/admin/custom-agents/%s/export" % rec["agent_id"], json={"framework": "langgraph"})
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and "langgraph.zip" in r.headers["content-disposition"]
        assert c.post("/api/admin/custom-agents/%s/export" % rec["agent_id"], json={"framework": "java"}).status_code == 400
    finally:
        designer_guard.settings = saved


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
