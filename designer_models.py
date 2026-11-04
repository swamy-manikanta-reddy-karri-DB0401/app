"""Which AI model does which job in the assistant designer, chosen in the UI.

The designer used to read three endpoint names from the environment. That made a
model a deployment decision, when it is really the admin's: a stronger model for a
hard request, a cheaper one to try something out. So the choice now lives on the
screen and is remembered in that admin's browser; the environment variables
(`PORTAL_BUILDER_MODEL`, `_CODE_MODEL`, `_JUDGE_MODEL`) only supply defaults, so an
operator can still pin one. Nothing is stored by the portal.

Three jobs, one real choice:

* **chat**: runs the interview and proposes the assistant. The only choice most
  people make.
* **code**: writes the code of a new tool. "Same as chat" unless set.
* **judge**: marks the answers when the new assistant is tested. Defaults to a
  *different* model than `chat`, because a model asked to grade its own work goes
  easy on it.

What can be chosen is exactly what this admin can use right now: chat models that
are ready and that they hold CAN_QUERY or CAN_MANAGE on, read from the workspace
under their own token (so a model they cannot use is never offered, and a model
added tomorrow appears within a minute). The model is then called under that same
token, so Databricks enforces access whatever the browser sends; the checks here
only turn a bad name into a clear message instead of a confusing failure.

What the listing tells us, and what it does not (learnt live, 2026-10-08): each model reports
whether it supports function calling and its price in DBUs per million tokens, so both are
read from there instead of guessed from the name. It does **not** say a model is retired:
`databricks-claude-sonnet-4` was listed READY with every field normal while every call to it
was refused with "This endpoint ... is deprecated". So retirement is learnt when a call is
refused (`mark_retired`): that model is dropped from the list for the rest of the process,
and the caller switches to the next best (`fallback`) and tells the admin, rather than
showing an error for something they could not have known.

Honesty about fit: only Claude models are marked "Recommended", because the
interview depends on function calling and long tool-use conversations, which is
what they are documented for. Other families may work and may not; the picker says
"Not tested for this" rather than guessing.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time

from dbx import DbxError, call

NAME_RE = re.compile(r"^[\w\-.]{1,128}$")
ROLES = ("chat", "code", "judge")
ENV = {"chat": "PORTAL_BUILDER_MODEL", "code": "PORTAL_BUILDER_CODE_MODEL", "judge": "PORTAL_BUILDER_JUDGE_MODEL"}
QUERYABLE = ("CAN_QUERY", "CAN_MANAGE")
CACHE_SECONDS = 60
STALE_SECONDS = 15 * 60   # how long the last good list stands in when the list cannot be read
NONE_AVAILABLE = ("No AI model is available to you yet. Ask a workspace admin to give you Can Query on a chat model "
                  "(for example a Databricks Claude model), then choose it here.")

_cache: dict[str, tuple[float, list]] = {}
_lock = threading.Lock()
_failed: dict[str, str] = {}   # caller -> why the model list could not be read last time
log = logging.getLogger("portal.designer_models")
# Models Databricks refused as retired, learnt from a refused call. Lives for the process: a
# retired model does not come back, and the list is rebuilt from the workspace on restart.
_retired: set[str] = set()


def disabled() -> bool:
    """An operator's off switch (`PORTAL_DESIGNER=off`). On otherwise: the admin picks the model."""
    return os.environ.get("PORTAL_DESIGNER", "").strip().lower() in ("off", "0", "false", "no")


def env_default(role: str) -> str:
    return (os.environ.get(ENV[role]) or "").strip()


# --- names people can read -----------------------------------------------------

_MAKERS = (("claude", "Anthropic"), ("gpt", "OpenAI"), ("gemini", "Google"), ("gemma", "Google"), ("llama", "Meta"),
           ("qwen", "Alibaba"), ("glm", "Zhipu"), ("kimi", "Moonshot"), ("deepseek", "DeepSeek"))
_UPPER = {"gpt", "oss", "glm"}


def friendly(name: str) -> str:
    """`databricks-claude-sonnet-5-5` -> `Claude Sonnet 5.5`; `databricks-gpt-oss-120b` -> `GPT OSS 120B`."""
    stem = name[len("databricks-"):] if name.startswith("databricks-") else name
    out: list[str] = []
    for part in stem.split("-"):
        if part.isdigit():
            # consecutive numbers are one version: 5-5 -> 5.5
            if out and re.fullmatch(r"\d+(\.\d+)*", out[-1]):
                out[-1] += "." + part
            else:
                out.append(part)
        elif part.lower() in _UPPER:
            out.append(part.upper())
        elif re.fullmatch(r"\d+[a-z]", part):  # 70b -> 70B
            out.append(part.upper())
        elif re.fullmatch(r"[a-z]\d+b", part):  # a3b stays as is, shouted
            out.append(part.upper())
        else:
            out.append(part.capitalize())
    return " ".join(out)


_PROVIDERS = {"openai": "OpenAI", "anthropic": "Anthropic", "cohere": "Cohere", "amazon-bedrock": "Amazon Bedrock",
              "google-cloud-vertex-ai": "Google Vertex AI", "ai21labs": "AI21 Labs", "databricks-model-serving": "Databricks"}


def _maker(name: str, external: dict | None) -> str:
    if external and external.get("provider"):
        prov = str(external["provider"])
        return _PROVIDERS.get(prov, prov.replace("-", " ").title())
    low = name.lower()
    for key, maker in _MAKERS:
        if key in low:
            return maker
    return ""


def _tier(name: str) -> str:
    low = name.lower()
    if "opus" in low or "fable" in low:
        return "Most capable"
    if "sonnet" in low:
        return "Balanced: a good default"
    if any(k in low for k in ("haiku", "mini", "nano", "flash", "-8b", "-12b", "-20b")):
        return "Fastest; best for simple requests"
    return ""


def _version(name: str) -> tuple:
    """Numbers in a name as a fixed-length version, so 5 reads as 5.0.0 and sorts below 5.5."""
    nums = [int(x) for x in re.findall(r"\d+", name)][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def _rank(name: str) -> tuple:
    low = name.lower()
    claude = "claude" in low
    family = next((i for i, k in enumerate(("sonnet", "opus", "fable", "haiku")) if k in low), 9)
    return (0 if claude else 1, family, tuple(-v for v in _version(name)), name)


def _number(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cost(out: float | None) -> str:
    """A word for the price, from the real number (DBUs per million output tokens) rather than the name."""
    if out is None:
        return ""
    return "Low cost" if out <= 40 else "Moderate cost" if out <= 200 else "Higher cost"


def _entry(name: str, kind: str, maker: str = "", pinned: bool = False, tools: bool | None = None,
           price_in: float | None = None, price_out: float | None = None) -> dict:
    recommended = "claude" in name.lower()
    price = ("%s in, %s out per 1M tokens (DBUs)" % (("%g" % round(price_in, 1)), ("%g" % round(price_out, 1)))
             if price_in is not None and price_out is not None else "")
    bits = [maker, "Set up by your administrator" if pinned else _tier(name), _cost(price_out), price]
    return {
        "name": name, "label": friendly(name), "kind": kind, "maker": maker,
        "recommended": recommended,
        # False = the model cannot call functions, which the interview needs. None = not reported.
        "tools": tools,
        "price_in": price_in, "price_out": price_out, "cost": _cost(price_out),
        "detail": " · ".join(b for b in bits if b),
        # What it is good at, on its own line in the picker (the detail above keeps everything for search).
        "tier": "Set up by your administrator" if pinned else _tier(name),
        "fit": "Cannot use tools" if tools is False else "Recommended" if recommended else "Not tested for this",
    }


# --- what this admin can use -------------------------------------------------------

def catalog(tok: str) -> list[dict]:
    """Chat models that are ready and that this token may query, best first."""
    key = hashlib.sha256(tok.encode("utf-8")).hexdigest()[:16]
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        eps = call("GET", "/api/2.0/serving-endpoints", tok, quiet=True).get("endpoints") or []
    except DbxError as exc:
        # Listing is a convenience; a pinned or typed model still works. A failure for a moment (an expired
        # sign-in, a timeout) must not look like "no models": the last good list for this caller is used for a
        # while, and the reason is kept so the message can say the list could not be read.
        log.warning("model list could not be read: %s", str(exc)[:200])
        with _lock:
            _failed[key] = str(exc)[:200]
            if hit and now - hit[0] < STALE_SECONDS:
                return hit[1]
        return []
    out = []
    for e in eps:
        name = e.get("name") or ""
        if e.get("task") != "llm/v1/chat" or not NAME_RE.match(name) or name in _retired:
            continue
        if (e.get("state") or {}).get("ready") != "READY":
            continue
        perm = e.get("permission_level")
        if perm and perm not in QUERYABLE:
            continue
        served = (e.get("config") or {}).get("served_entities") or []
        external = next((s["external_model"] for s in served if isinstance(s.get("external_model"), dict)), None)
        kind = "external" if external else "hosted" if (e.get("endpoint_type") == "FOUNDATION_MODEL_API" or name.startswith("databricks-")) else "custom"
        fm = next((s["foundation_model"] for s in served if isinstance(s.get("foundation_model"), dict)), {})
        fn = (e.get("capabilities") or {}).get("function_calling")
        out.append(_entry(name, kind, _maker(name, external), tools=fn if isinstance(fn, bool) else None,
                          price_in=_number(fm.get("input_price")), price_out=_number(fm.get("price"))))
    out.sort(key=lambda m: _rank(m["name"]))
    with _lock:
        _cache[key] = (now + CACHE_SECONDS, out)
        _failed.pop(key, None)
    return out


def usable_for_chat(avail: list[dict]) -> list[dict]:
    """The interview calls functions to look things up, so a model that cannot is no use for it."""
    return [m for m in avail if m.get("tools") is not False]


def best_chat(avail: list[dict]) -> str:
    ok = usable_for_chat(avail)
    return ok[0]["name"] if ok else ""  # best-first already: Claude Sonnet, then Opus, ...


def best_code(avail: list[dict]) -> str:
    """The newest Claude Opus for writing a new tool's code, else "" (the same as chat).
    Seen live (2026-10-09) on a real ad-book tool: Claude Sonnet as the code writer took
    2 to 4 minutes a try, failed all three tries once (the last reply cut off mid-line),
    and needed three rounds of corrections; Claude Opus wrote the same tool in about
    6000 tokens. Writing code is the one job where the strongest model pays for itself."""
    opus = [m["name"] for m in avail if "claude-opus" in m["name"].lower() and m.get("tools") is not False]
    return max(opus, key=_version) if opus else ""


def best_judge(avail: list[dict], chat: str) -> str:
    """A different model from the one that built the assistant; the open GPT-OSS
    model when there is one (verified to follow the "reply with JSON only" grading
    instructions), otherwise the best other model."""
    others = [m["name"] for m in avail if m["name"] != chat]
    for pref in ("databricks-gpt-oss-120b",):
        if pref in others:
            return pref
    return others[0] if others else chat


def defaults(avail: list[dict], chat: str = "") -> dict:
    """The model for each job when the admin has not chosen. Pass `chat` (what they
    did choose) so the checker is picked to differ from *that* one."""
    chat = chat or env_default("chat") or best_chat(avail)
    return {
        "chat": chat,
        "code": env_default("code") or best_code(avail),  # "" = the same as chat
        "judge": env_default("judge") or best_judge(avail, chat),
    }


def mark_retired(name: str) -> None:
    """A call to this model was refused as retired: stop offering it (until the process restarts)."""
    with _lock:
        _retired.add(name)
        _cache.clear()


def fallback(tok: str, role: str, exclude: list[str], chat: str = "") -> str:
    """The next best model for a job after one was refused. "" when nothing is left."""
    avail = [m for m in catalog(tok) if m["name"] not in exclude]
    if role == "judge":
        pick = best_judge(avail, chat)
        return pick if pick and pick not in exclude else ""
    ok = usable_for_chat(avail)
    return ok[0]["name"] if ok else ""


def resolve(requested, tok: str, notes: list | None = None) -> dict:
    """The model to use for each job: what the admin chose, else the default.

    A chosen name must be a real chat model this admin can use (when the list can
    be read), so a typo or a stale choice from another workspace gives a plain
    message rather than a confusing upstream error.
    """
    avail = catalog(tok)
    names = {m["name"] for m in avail}
    d = defaults(avail)
    ask = requested if isinstance(requested, dict) else {}
    out = {}
    for role in ROLES:
        if role == "judge":  # decided last: the default checker must differ from the model actually chosen
            d = defaults(avail, out.get("chat") or d["chat"])
        v = str(ask.get(role) or "").strip()
        if v in _retired:  # chosen earlier, retired since: use the default and say so
            if notes is not None:
                notes.append("%s has been retired by Databricks, so %s was used instead." % (friendly(v), friendly(d.get(role) or out.get("chat") or "another model")))
            v = ""
        if v:
            if not NAME_RE.match(v):
                raise DbxError("That is not a valid model name.", 400)
            if names and v not in names and v != d.get(role):
                raise DbxError("The model %s is not available to you. Choose another." % friendly(v), 400)
            if role == "chat" and next((m for m in avail if m["name"] == v), {}).get("tools") is False:
                raise DbxError("%s cannot use tools, which the designer needs to look things up. Choose another model." % friendly(v), 400)
            out[role] = v
        else:
            out[role] = d[role]
    if not out["chat"]:
        why = _failed.get(hashlib.sha256(tok.encode("utf-8")).hexdigest()[:16])
        if why:
            raise DbxError("The list of AI models could not be read just now (%s). Try again in a moment." % why[:160], 503)
        raise DbxError(NONE_AVAILABLE, 409)
    out["code"] = out["code"] or out["chat"]
    out["judge"] = out["judge"] or out["chat"]
    return out


def describe(tok: str) -> dict:
    """What the screen needs to offer a choice: the models, the defaults, and why not."""
    if disabled():
        return {"enabled": False, "ready": False, "reason": "", "models": [], "defaults": {}}
    avail = catalog(tok)
    d = defaults(avail)
    models = list(avail)
    have = {m["name"] for m in models}
    for role in ROLES:  # a model pinned by the operator is always offered, even if it is not in the list
        if d[role] and d[role] not in have:
            models.append(_entry(d[role], "custom", pinned=True))
            have.add(d[role])
    ready = bool(d["chat"])
    return {"enabled": True, "ready": ready, "reason": "" if ready else NONE_AVAILABLE, "models": models, "defaults": d}
