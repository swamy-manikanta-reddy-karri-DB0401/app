"""Transport adapters - one per agent wire format.

Databricks lets people build agents in many ways (Supervisor Agent, Knowledge
Assistant, "code your own" via Agent Framework, plus raw chat models), and the
resulting endpoints do NOT speak one protocol. Verified on this workspace:

    task                  request field   response shape
    --------------------  --------------  ------------------------------------
    agent/v1/responses    input           Responses API: output[]
    agent/v1/chat         messages        ChatAgent: messages[] or choices[]
    llm/v1/chat           messages        ChatCompletions: choices[]

The formats are mutually exclusive and strictly enforced - sending `messages`
to a Responses agent returns 400 "'messages' field is not supported. Please use
'input' field instead." Agent Bricks agents also serve no OpenAPI document, so
the schema cannot be discovered up front.

So: dispatch on `task` where we know it, and where we do not, PROBE - try a
shape, read the rejection, and switch. A learned choice is remembered per
endpoint so each agent pays that cost at most once. This is what lets the portal
front an agent type that did not exist when this code was written.
"""
from __future__ import annotations

import json
import re

# endpoint name -> request field ("input" | "messages") that worked
_learned: dict[str, str] = {}

TEXT_KEYS = ("output_text", "text", "summary_text")

# Fallback for agents whose response shape isn't the Responses API output[]
# (e.g. an Agent Bricks supervisor's final_response wrapper never carries
# structured tool-call data at all) - the model itself tends to state the
# generated file's Volume path in prose, so extract it from there instead.
# Greedy on purpose: a lazy match stops at the first ".word", which truncates
# names like Report_2026.10.pptx to Report_2026.10. The caller strips the
# trailing punctuation a sentence puts after a path.
VOLUME_PATH_RE = re.compile(r"/Volumes/[\w.\-/]+\.\w+")


# --------------------------------------------------------------------- requests


def _turns(history: list) -> list:
    return [
        {"role": m["role"], "content": m["content"]}
        for m in history
        if m.get("role") in ("user", "assistant", "system") and m.get("content")
    ]


def build(task: str, endpoint: str, history: list) -> tuple:
    """Return (payload, request_field) for this endpoint."""
    field = _learned.get(endpoint) or field_for_task(task)
    return shaped(field, history), field


def shaped(field: str, history: list) -> dict:
    """The request body for one wire format.

    `message` is the shape hand-written agent apps tend to take (seen live: a FastAPI agent
    whose body is `{"message": str, "history": [...]}`): the newest question on its own and
    the earlier turns beside it."""
    turns = _turns(history)
    if field == "message":
        last = next((i for i in range(len(turns) - 1, -1, -1) if turns[i]["role"] == "user"), None)
        if last is None:
            return {"message": "", "history": turns}
        return {"message": turns[last]["content"], "history": turns[:last]}
    return {field: turns}


def field_for_task(task: str) -> str:
    if task == "agent/v1/responses":
        return "input"
    if task in ("agent/v1/chat", "llm/v1/chat"):
        return "messages"
    # Unknown or absent task: agents outnumber raw models in a portal, and the
    # Responses schema is the current default for Agent Bricks, so start there.
    return "input" if task.startswith("agent/") or task == "app" else "messages"


def other_field(field: str) -> str:
    return "messages" if field == "input" else "input"


# An agent running as its own app may take any of these; tried in this order (the first is
# what Databricks' agent templates and the Agent Bricks runtime take).
APP_FIELDS = ("input", "messages", "message")


def learn(endpoint: str, field: str) -> None:
    _learned[endpoint] = field


# --------------------------------------------------------------- mcp approval


def pending_approvals(data) -> list:
    """`mcp_approval_request` items in a Responses-API reply awaiting a decision.

    A Databricks MCP tool is gated by an approval round-trip: the first reply
    stops at "I'll call ping" with status "completed" and an
    `mcp_approval_request` item - the tool has NOT run yet. Playground answers
    the request transparently; a raw API caller (like this portal) must do the
    same or the tool call silently never happens. Verified against this
    workspace's mas-77773ac2-endpoint.
    """
    if not isinstance(data, dict):
        return []
    return [
        item
        for item in (data.get("output") or [])
        if isinstance(item, dict) and item.get("type") == "mcp_approval_request"
    ]


DECLINED = ("The person using the assistant declined this action. Do not try it again in any form; tell them it "
            "was not done and ask what they would like instead.")


def build_approval_replay(history_input: list, prior_output: list, approvals: list, decisions: dict | None = None) -> list:
    """The `input` for the follow-up call that actually runs the tool(s).

    Verified by hand against this workspace: the endpoint is stateless
    (`previous_response_id` is silently ignored), so the follow-up must replay
    the full prior turn - the original input, every item from the prior
    response's `output` (the assistant's message and the approval request
    itself), then an `mcp_approval_response` for each pending request. Sending
    only the approval response, or only the approval response plus the original
    user turn, both fail with "Invalid message sequence."

    **Each response goes straight after its own request.** An assistant may ask
    for several tools in one reply (seen live, 2026-10-08: `get_rate_card` and
    `find_inventory` together). Listing all the requests and then all the
    responses is answered, with HTTP 200, by a message saying "Invalid approval
    response. The approval response ID does not match the request." Interleaving
    (request A, response A, request B, response B) runs both tools. With a single
    request the two orders are the same, which is why this only showed up with
    parallel tool calls.
    """
    wanted = {a.get("id") for a in approvals if a.get("id")}
    # `decisions`: what a person chose for the requests they were asked about (id -> bool).
    # Anything not in it was a read-only tool the portal approved by itself.
    ok = lambda rid: bool((decisions or {}).get(rid, True))  # noqa: E731
    replay = list(history_input)

    def answer(rid):
        a = {"type": "mcp_approval_response", "approval_request_id": rid, "approve": ok(rid)}
        if not a["approve"]:
            # Seen live (2026-10-11): declined with no reason, the agent took it for a format error and asked
            # to run the same tool again. With this reason it says it was not done and asks what to do instead.
            a["reason"] = DECLINED
        return a
    placed: set = set()
    for item in prior_output:
        replay.append(item)
        rid = item.get("id") if isinstance(item, dict) and item.get("type") == "mcp_approval_request" else None
        if rid in wanted and rid not in placed:
            replay.append(answer(rid))
            placed.add(rid)
    for a in approvals:  # a request that was not in the output (should not happen) still gets answered
        rid = a.get("id")
        if rid and rid not in placed:
            replay.append(answer(rid))
            placed.add(rid)
    return replay


def wrong_field(status: int, body: str) -> bool:
    """True when a 4xx says we used the wrong request field, so a retry is worth it."""
    if status not in (400, 422):
        return False
    b = (body or "").lower()
    hints = (
        "is not supported",
        "please use 'input'",
        "please use 'messages'",
        "missing required chat parameter",
        "missing required parameter",
        "unexpected keyword",
        "field required",
        "model is missing inputs",
        "failed to enforce schema",
    )
    # "message" also covers "messages", and the field a hand-written app names when it is missing.
    return any(h in b for h in hints) and ("input" in b or "message" in b)


# -------------------------------------------------------------------- responses


def _push(seen: list, out: list, value) -> None:
    if isinstance(value, str) and value.strip() and value not in seen:
        seen.append(value)
        out.append(value)


def _walk_content(content, text: list, seen: list, cites: list) -> None:
    """Content may be a string, or a list of typed parts (Responses / ChatAgent)."""
    if isinstance(content, str):
        _push(seen, text, content)
        return
    if not isinstance(content, list):
        return
    for part in content:
        if isinstance(part, str):
            _push(seen, text, part)
            continue
        if not isinstance(part, dict):
            continue
        for k in TEXT_KEYS:
            if part.get(k):
                _push(seen, text, part[k])
        # Knowledge Assistants attach sources as annotations on the text part.
        for ann in part.get("annotations") or []:
            if isinstance(ann, dict):
                label = ann.get("title") or ann.get("file_path") or ann.get("url")
                if label:
                    cites.append({"label": str(label), "url": ann.get("url") or ""})


def parse(data) -> dict:
    """Normalise any agent reply into {reply, tools, citations, attachments}.

    Deliberately shape-agnostic: it looks for every known carrier of assistant
    text rather than trusting the endpoint's declared task, because a "code your
    own agent" deployment can return whichever schema its author chose.
    """
    text: list = []
    seen: list = []
    tools: list = []
    cites: list = []
    files: list = []

    if isinstance(data, str):
        return {"reply": data.strip(), "tools": [], "citations": [], "attachments": []}
    if not isinstance(data, dict):
        return {"reply": "", "tools": [], "citations": [], "attachments": []}

    # 1. Responses API (agent/v1/responses)
    #
    # A tool call and its result are two separate sibling items, linked by
    # call_id: {"type": "function_call", ...} then later
    # {"type": "function_call_output", "call_id": ..., "output": ...}. The
    # portal only ever replays flattened reply text on the next turn (see
    # Chat.tsx history()), so without folding the result into that text here,
    # the agent has no way to know on the next turn whether its own tool call
    # actually happened.
    #
    # An MCP tool approved via mcp_approval_response (see pending_approvals())
    # never gets its own function_call item - the request was the approval
    # item from the prior turn - so its function_call_output carries the name
    # directly. Trust that name too, not just a sibling function_call.
    call_names: dict = {}
    call_results: list = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "reasoning":
            continue
        if kind in ("function_call", "tool_call", "custom_tool_call"):
            name = item.get("name") or (item.get("function") or {}).get("name")
            if name:
                tools.append(str(name))
                call_id = item.get("call_id") or item.get("id")
                if call_id:
                    call_names[call_id] = str(name)
            continue
        if kind == "function_call_output":
            call_id = item.get("call_id")
            if item.get("name") and call_id and call_id not in call_names:
                call_names[call_id] = str(item["name"])
                tools.append(str(item["name"]))
            out = item.get("output")
            # A file-generating tool (e.g. get_mid_campaign_ppt_report) returns
            # a result object with output_volume_path, not a plain string -
            # surface that as a downloadable attachment before it gets
            # flattened to JSON text below.
            out_obj = out if isinstance(out, dict) else None
            if out_obj is None and isinstance(out, str):
                try:
                    parsed = json.loads(out)
                except (json.JSONDecodeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    out_obj = parsed
            if out_obj and out_obj.get("output_volume_path"):
                vpath = str(out_obj["output_volume_path"])
                files.append({"name": vpath.rsplit("/", 1)[-1], "path": vpath})
            if isinstance(out, dict):
                out = out.get("output") or out.get("text") or json.dumps(out)
            if call_id and out is not None:
                call_results.append((call_id, str(out).strip()))
            continue
        _walk_content(item.get("content"), text, seen, cites)

    for call_id, out in call_results:
        name = call_names.get(call_id, "tool")
        if out:
            _push(seen, text, "Called " + name + " -> " + out[:500])

    # 2. ChatCompletions (llm/v1/chat) and some ChatAgent deployments
    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        msg = choice.get("message") or choice.get("delta") or {}
        _walk_content(msg.get("content"), text, seen, cites)
        for tc in msg.get("tool_calls") or []:
            name = (tc.get("function") or {}).get("name") if isinstance(tc, dict) else None
            if name:
                tools.append(str(name))

    # 3. ChatAgent returning a messages[] transcript
    for msg in data.get("messages") or []:
        if isinstance(msg, dict) and msg.get("role") in (None, "assistant"):
            _walk_content(msg.get("content"), text, seen, cites)

    # A hand-written agent app's own list of the tools it ran.
    for tc in data.get("tool_calls") if isinstance(data.get("tool_calls"), list) else []:
        name = (tc.get("name") or tc.get("tool") or (tc.get("function") or {}).get("name")) if isinstance(tc, dict) else None
        if name:
            tools.append(str(name))

    # 4. Agent Bricks supervisors wrap the answer, and MLflow models use
    #    predictions. Both appear in the wild; unwrap one level.
    # `reply` / `message` / `text`: what hand-written agent apps answer with (seen live:
    # `{"reply": ..., "tool_calls": [...]}`). Only a plain string counts.
    for key in ("final_response", "response", "answer", "result", "output_text", "reply", "message", "text"):
        if isinstance(data.get(key), str):
            _push(seen, text, data[key])
    preds = data.get("predictions")
    if isinstance(preds, list):
        for p in preds:
            if isinstance(p, str):
                _push(seen, text, p)
            elif isinstance(p, dict):
                inner = parse(p)
                _push(seen, text, inner["reply"])
                cites.extend(inner["citations"])
                files.extend(inner["attachments"])
    elif isinstance(preds, (str, dict)):
        inner = parse(preds) if isinstance(preds, dict) else {"reply": preds, "citations": [], "attachments": [], "tools": []}
        _push(seen, text, inner["reply"])
        cites.extend(inner["citations"])
        files.extend(inner["attachments"])

    # 5. Explicit citation / attachment carriers, wherever they hang.
    for key in ("citations", "sources", "references"):
        for c in data.get(key) or []:
            if isinstance(c, dict):
                label = c.get("title") or c.get("label") or c.get("file_path") or c.get("url")
                if label:
                    cites.append({"label": str(label), "url": c.get("url") or ""})
            elif isinstance(c, str):
                cites.append({"label": c, "url": ""})

    custom = data.get("custom_outputs")
    if isinstance(custom, dict):
        for key in ("files", "attachments", "artifacts", "generated_files"):
            for f in custom.get(key) or []:
                if isinstance(f, str):
                    files.append({"name": f.rsplit("/", 1)[-1], "path": f})
                elif isinstance(f, dict):
                    path = f.get("path") or f.get("file_path") or f.get("url") or ""
                    files.append({"name": f.get("name") or path.rsplit("/", 1)[-1], "path": path})
        inner = parse(custom)
        _push(seen, text, inner["reply"])
        cites.extend(inner["citations"])

    # Fallback: no structured attachment was found (e.g. an Agent Bricks
    # final_response wrapper, which carries no tool-result data at all), but
    # the model's own reply names the generated file's Volume path anyway.
    if not files:
        reply_text = "\n\n".join(text)
        seen_paths: set = set()
        for m in VOLUME_PATH_RE.finditer(reply_text):
            vpath = m.group(0).rstrip(").,;:\"'")
            if vpath not in seen_paths:
                seen_paths.add(vpath)
                files.append({"name": vpath.rsplit("/", 1)[-1], "path": vpath})

    # de-dupe, preserving order
    tools = list(dict.fromkeys(tools))
    seen_c: set = set()
    citations = []
    for c in cites:
        k = (c["label"], c["url"])
        if k not in seen_c:
            seen_c.add(k)
            citations.append(c)

    return {
        "reply": "\n\n".join(text).strip(),
        "tools": tools,
        "citations": citations,
        "attachments": files,
    }


def parse_sse(body: str) -> dict:
    """Parse an SSE stream, accumulating deltas and raising on an error event.

    A Genie-backed agent answers `event: error` inside an HTTP 200 stream, so
    errors here are real failures, not warnings.
    """
    deltas: list = []
    final = None
    event = None
    merged = {"reply": "", "tools": [], "citations": [], "attachments": []}

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            event = None
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("[DONE]", ""):
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if event == "error" or (isinstance(data, dict) and data.get("error_code")):
            msg = data.get("message") or data.get("error") or "the agent returned an error"
            raise AgentStreamError(str(msg))
        if not isinstance(data, dict):
            continue
        dtype = str(data.get("type") or "")
        if dtype.endswith("output_text.delta") and data.get("delta"):
            deltas.append(data["delta"])
        elif dtype.endswith(".done") and data.get("text") and not deltas:
            deltas.append(data["text"])
        elif data.get("object") == "response" or "output" in data or "choices" in data:
            final = data

    if final is not None:
        merged = parse(final)
    if not merged["reply"] and deltas:
        merged["reply"] = "".join(deltas).strip()
    return merged


class AgentStreamError(RuntimeError):
    """An error event carried inside an otherwise-successful stream."""
