"""Agent invocation.

Always called with the signed-in user's OBO token, so the serving-endpoint ACL
and Unity Catalog decide what actually runs.

Two hazards this layer exists to absorb:

* **Agents disagree on the wire format.** `adapters` picks a shape from the
  endpoint's task, and if the endpoint rejects it, the rejection is read and the
  other shape is tried once, then remembered. That is what lets the portal front
  an agent type nobody anticipated.
* **An agent can fail with HTTP 200.** A Genie-backed agent returns an SSE body
  whose first event is `event: error`. Status-code checks alone yield a blank
  reply, so the stream is parsed and errors are raised explicitly.
"""
from __future__ import annotations

import json

import httpx

import adapters
from dbx import DbxError, host, http


def _detail(body: str, fallback: str) -> str:
    try:
        j = json.loads(body)
    except json.JSONDecodeError:
        return fallback
    if isinstance(j, dict):
        return str(j.get("message") or j.get("error_code") or fallback)
    return fallback


def _is_app(endpoint: str) -> bool:
    """A full URL is an agent running as its own Databricks App (see app_agents)."""
    return endpoint.startswith("https://")


def _post(endpoint: str, payload: dict, user_tok: str) -> httpx.Response:
    url = endpoint if _is_app(endpoint) else host() + "/serving-endpoints/" + endpoint + "/invocations"
    try:
        resp = http().post(
            url,
            headers={"Authorization": "Bearer " + user_tok, "Content-Type": "application/json"},
            json=payload,
            timeout=600,
        )
    except httpx.RequestError as exc:
        raise DbxError("could not reach the agent endpoint: " + str(exc), 504)
    if _is_app(endpoint):
        _app_refusal(endpoint, resp)
    return resp


def _app_refusal(url: str, resp: httpx.Response) -> None:
    """An app's front door (the Apps proxy) answers a token it does not accept with a
    redirect to sign in or a 401/403, sometimes as a web page. That is never the agent's
    answer: say so and point at the app, where the person signs in themselves. The portal
    does not retry as its own identity (the security model forbids it)."""
    base = url.split("/", 3)
    base = "/".join(base[:3]) if len(base) >= 3 else url
    ctype = resp.headers.get("content-type", "")
    if resp.status_code in (301, 302, 303, 307, 308, 401, 403) or (resp.status_code < 300 and "text/html" in ctype):
        raise DbxError(
            "This agent runs as its own app and did not accept your sign-in from the portal "
            "(Databricks answered %s). You can open it directly: %s" % (resp.status_code, base),
            403,
        )
    if resp.status_code in (404, 405):
        raise DbxError(
            "The agent's app has no question address at %s (Databricks answered %s). An admin can change "
            "the address in its settings on the Build page." % (url[len(base):] or "/", resp.status_code),
            502,
        )


def _body(resp: httpx.Response) -> dict | None:
    """Parsed JSON body of a 2xx response, or None for SSE / non-JSON bodies."""
    body = resp.text or ""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype or body.lstrip().startswith(("event:", "data:")):
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _read(resp: httpx.Response) -> dict:
    """Turn a 2xx response into a normalised reply, handling JSON and SSE."""
    body = resp.text or ""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype or body.lstrip().startswith(("event:", "data:")):
        try:
            return adapters.parse_sse(body)
        except adapters.AgentStreamError as exc:
            raise DbxError(str(exc), 502)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Some custom agents return bare text.
        if body.strip():
            return {"reply": body.strip(), "tools": [], "citations": [], "attachments": []}
        raise DbxError("the agent returned an empty response", 502)
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise DbxError(str(err.get("message") if isinstance(err, dict) else err), 502)
    return adapters.parse(data)


# A Databricks MCP tool call needs one human/client decision before it runs.
# The portal always approves - the agent's own ToolApprovalPlugin/governance
# already decides what it may call; this is not a second consent gate the
# user asked for, just the API round-trip Playground makes transparently.
# Every tool call is its own round (seen live, Oct 2026: a media plan is
# find inventory -> rates -> availability -> plan -> export, five or more), so
# the cap must allow a real multi-step task. It is still capped so a
# misbehaving endpoint that keeps re-requesting approval cannot loop forever;
# hitting it is said in the reply, never silently cut short.
MAX_APPROVAL_ROUNDS = 12
STOPPED_NOTE = ("\n\n_The assistant stopped part-way: it needed more steps than one question allows. "
                "Ask it to continue._")


def _resolve_approvals(endpoint: str, payload: dict, data: dict, user_tok: str, ask_person=None,
                       tools: list | None = None) -> dict:
    """Re-POST with pending mcp_approval_requests answered.

    Only meaningful for the Responses API (`input`/`output`); other shapes
    never produce an mcp_approval_request and pending_approvals() is a no-op
    for them.

    `ask_person(request) -> bool` says which requests a person must decide (see
    toolpolicy). If any does, nothing is answered: the reply comes back with `_pause`
    (everything needed to carry on, which `resume` takes), because all requests in one
    reply must be answered together. Without it every request is approved, as before.
    """
    tools = list(tools or [])
    for _ in range(MAX_APPROVAL_ROUNDS):
        approvals = adapters.pending_approvals(data)
        if not approvals:
            return dict(data, _tools=tools)
        asked = [a for a in approvals if ask_person and ask_person(a)]
        if asked:
            return dict(data, _tools=tools, _pause={"input": payload.get("input") or [], "output": data.get("output") or [],
                                                    "approvals": approvals, "asked": [a.get("id") for a in asked],
                                                    "tools": tools})
        tools += [a.get("name") for a in approvals if a.get("name")]
        payload = {
            "input": adapters.build_approval_replay(payload.get("input") or [], data.get("output") or [], approvals)
        }
        resp = _post(endpoint, payload, user_tok)
        if resp.status_code >= 400:
            detail = _detail(resp.text, resp.text[:400])
            raise DbxError("approving a tool call failed: " + detail, resp.status_code)
        next_data = _body(resp)
        if next_data is None:
            return dict(data, _tools=tools)
        data = next_data
    return dict(data, _tools=tools, _stopped=bool(adapters.pending_approvals(data)))


def ask(
    endpoint: str,
    task: str,
    history: list,
    user_tok: str,
    files: list | None = None,
    output_dir: str = "",
    ask_person=None,
) -> dict:
    """Send a conversation to an agent and return a normalised reply. With `ask_person`,
    a reply may instead come back with `pause` (a tool call waiting for a person)."""
    history = list(history or [])
    if files:
        # Agents cannot see a browser upload; they read the volume. Naming the
        # paths in the turn is what lets a file-driven agent find its input.
        note = "Uploaded file" + ("s" if len(files) > 1 else "") + ": " + ", ".join(files)
        if output_dir:
            # Stated every turn rather than relying on the agent's own
            # Instructions to hardcode a destination - the portal computes
            # this from the agent's output_volume tag, so it stays correct if
            # that tag ever changes.
            note += ". Save any generated output file to this directory: " + output_dir
        if history and history[-1].get("role") == "user":
            history[-1] = dict(history[-1], content=(history[-1]["content"] + "\n\n" + note).strip())
        else:
            history.append({"role": "user", "content": note})

    payload, field = adapters.build(task, endpoint, history)
    if not (payload.get("input") or payload.get("messages") or payload.get("message")):
        raise DbxError("nothing to send", 400)

    resp = _post(endpoint, payload, user_tok)

    # Wrong request field? The endpoint says so in the 400. Switch once (an app agent may
    # take any of three shapes, so it tries each).
    if resp.status_code >= 400 and adapters.wrong_field(resp.status_code, resp.text):
        others = [f for f in adapters.APP_FIELDS if f != field] if _is_app(endpoint) else [adapters.other_field(field)]
        for alt in others:
            payload = adapters.shaped(alt, history)
            resp = _post(endpoint, payload, user_tok)
            if resp.status_code < 400:
                adapters.learn(endpoint, alt)
                break
            if not adapters.wrong_field(resp.status_code, resp.text):
                break
    elif resp.status_code < 400:
        adapters.learn(endpoint, field)

    if resp.status_code >= 400:
        detail = _detail(resp.text, resp.text[:400])
        if resp.status_code in (401, 403):
            raise DbxError(
                "Databricks refused this call under your identity. You may have lost "
                "CAN_QUERY on this agent, or it reads data you lack access to. " + detail,
                403,
            )
        raise DbxError(detail, resp.status_code)

    data = _body(resp)
    if data is not None and adapters.pending_approvals(data):
        return _finish(endpoint, payload, data, user_tok, ask_person)
    out = _read(resp)
    if not out["reply"] and not out["attachments"]:
        raise DbxError("the agent completed but returned no text", 502)
    return out


def _finish(endpoint: str, payload: dict, data: dict, user_tok: str, ask_person=None, tools: list | None = None) -> dict:
    data = _resolve_approvals(endpoint, payload, data, user_tok, ask_person, tools)
    out = adapters.parse(data)
    # Each round only returns its own step; name every tool that ran.
    out["tools"] = list(dict.fromkeys(list(data.get("_tools") or []) + list(out.get("tools") or [])))
    if data.get("_pause"):
        # Waiting for a person: whatever the assistant said so far, and what to carry on from.
        out["pause"] = data["_pause"]
        return out
    if data.get("_stopped"):
        out["reply"] = (out["reply"] or "").rstrip() + STOPPED_NOTE
    if not out["reply"] and not out["attachments"]:
        raise DbxError("the agent completed but returned no text", 502)
    return out


def resume(endpoint: str, pause: dict, decisions: dict, user_tok: str, ask_person=None) -> dict:
    """Carry on an answer that waited for a person: the asked requests are answered as they
    decided (id -> True/False), the others approved, and the conversation continues (and may
    pause again for the next tool that changes something)."""
    asked = set(pause.get("asked") or [])
    chosen = {rid: bool(decisions.get(rid)) for rid in asked}  # anything not decided is declined
    payload = {"input": adapters.build_approval_replay(pause.get("input") or [], pause.get("output") or [],
                                                       pause.get("approvals") or [], chosen)}
    resp = _post(endpoint, payload, user_tok)
    if resp.status_code >= 400:
        raise DbxError("carrying on after your decision failed: " + _detail(resp.text, resp.text[:400]), resp.status_code)
    data = _body(resp)
    if data is None:
        return _read(resp)
    ran = [a.get("name") for a in pause.get("approvals") or [] if a.get("name") and chosen.get(a.get("id"), True)]
    return _finish(endpoint, payload, data, user_tok, ask_person, list(pause.get("tools") or []) + ran)
