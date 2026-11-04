"""Model-choice tests: what the designer offers and how a choice is checked. No network.

Run: python test_designer_models.py

The picker may only ever offer what this admin can really use; a choice sent from the
browser is a request, not a command (it is checked, and the call still runs as them);
and an operator's pins and off-switch keep working.
"""
from __future__ import annotations

import os

import designer_models as dm
from dbx import DbxError

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def ep(name, perm="CAN_MANAGE", task="llm/v1/chat", ready="READY", kind="FOUNDATION_MODEL_API", served=None,
       price=None, tools=None):
    """An endpoint as the workspace lists it. `price` = (input, output) DBUs per 1M tokens; `tools` = function calling."""
    if price:
        served = [{"foundation_model": {"input_price": str(price[0]), "price": str(price[1])}}]
    out = {"name": name, "task": task, "state": {"ready": ready}, "permission_level": perm, "endpoint_type": kind,
           "config": {"served_entities": served or []}}
    if tools is not None:
        out["capabilities"] = {"function_calling": tools}
    return out


LIST = [
    ep("databricks-gpt-oss-120b"),
    ep("databricks-claude-opus-4-8"),
    ep("databricks-claude-sonnet-5"),
    ep("databricks-claude-sonnet-5-5"),
    ep("databricks-claude-haiku-4-5"),
    ep("databricks-llama-4-maverick"),
    ep("databricks-bge-large-en", task="llm/v1/embeddings"),          # not a chat model
    ep("databricks-claude-sonnet-4", ready="NOT_READY"),               # not up
    ep("databricks-claude-opus-4-1", perm="CAN_VIEW"),                 # visible but not usable by this admin
    ep("mas-1-endpoint", task="agent/v1/responses"),                   # an assistant, not a model
    ep("our-gpt", kind="EXTERNAL_MODEL", served=[{"external_model": {"provider": "openai"}}]),
    ep("our-tuned-llama", kind="CUSTOM_MODEL"),
]


def serve(listing, calls=None):
    def fake(method, path, tok, **kw):
        if calls is not None:
            calls.append((method, path, tok))
        if isinstance(listing, Exception):
            raise listing
        return {"endpoints": listing}

    dm.call = fake
    dm._cache.clear()
    dm._retired.clear()
    for k in ("PORTAL_DESIGNER", *dm.ENV.values()):
        os.environ.pop(k, None)


@case("names are readable: versions join up, sizes are capitals")
def _():
    f = dm.friendly
    assert f("databricks-claude-sonnet-5-5") == "Claude Sonnet 5.5"
    assert f("databricks-claude-opus-4-1") == "Claude Opus 4.1"
    assert f("databricks-gpt-oss-120b") == "GPT OSS 120B"
    assert f("databricks-meta-llama-3-3-70b-instruct") == "Meta Llama 3.3 70B Instruct"
    assert f("databricks-gemma-3-12b") == "Gemma 3 12B"
    assert f("our-tuned-llama") == "Our Tuned Llama"


@case("only chat models that are up and that this admin may query are offered")
def _():
    serve(LIST)
    names = [m["name"] for m in dm.catalog("T")]
    assert "databricks-bge-large-en" not in names and "mas-1-endpoint" not in names   # not chat models
    assert "databricks-claude-sonnet-4" not in names                                  # not ready
    assert "databricks-claude-opus-4-1" not in names                                  # CAN_VIEW: could not call it
    assert {"databricks-claude-sonnet-5-5", "our-gpt", "our-tuned-llama", "databricks-gpt-oss-120b"} <= set(names)


@case("the best choice is first: newest Claude Sonnet, then the other Claude models, then the rest")
def _():
    serve(LIST)
    names = [m["name"] for m in dm.catalog("T")]
    assert names[:4] == ["databricks-claude-sonnet-5-5", "databricks-claude-sonnet-5", "databricks-claude-opus-4-8", "databricks-claude-haiku-4-5"]
    assert all("claude" not in n for n in names[4:])


@case("each model says honestly how well it is known to suit this: only Claude is recommended")
def _():
    serve(LIST)
    by = {m["name"]: m for m in dm.catalog("T")}
    assert by["databricks-claude-sonnet-5-5"]["fit"] == "Recommended" and by["databricks-claude-sonnet-5-5"]["recommended"]
    assert by["databricks-claude-sonnet-5-5"]["detail"] == "Anthropic · Balanced: a good default"
    assert by["databricks-claude-opus-4-8"]["detail"] == "Anthropic · Most capable"
    assert by["databricks-gpt-oss-120b"]["fit"] == "Not tested for this" and not by["databricks-gpt-oss-120b"]["recommended"]
    assert by["our-gpt"]["kind"] == "external" and by["our-gpt"]["maker"] == "OpenAI"
    assert by["our-tuned-llama"]["kind"] == "custom" and by["databricks-llama-4-maverick"]["kind"] == "hosted"


@case("the list is read once a minute per person, under their own token, and a failure just means no list")
def _():
    calls = []
    serve(LIST, calls)
    dm.catalog("alice"); dm.catalog("alice"); dm.catalog("bob")
    assert [c[2] for c in calls] == ["alice", "bob"]          # cached per token; never shared between people
    assert all(c[1] == "/api/2.0/serving-endpoints" for c in calls)
    serve(DbxError("denied", 403))
    assert dm.catalog("T") == []


@case("defaults: the best chat model, a different model to check its work, the newest Opus to write code")
def _():
    serve(LIST)
    d = dm.defaults(dm.catalog("T"))
    assert d == {"chat": "databricks-claude-sonnet-5-5", "code": "databricks-claude-opus-4-8", "judge": "databricks-gpt-oss-120b"}
    serve([e for e in LIST if "opus" not in e["name"]])
    assert dm.defaults(dm.catalog("T"))["code"] == ""   # no Opus: the code model is the chat model
    # without the open model, the judge is still never the model that did the work
    serve([e for e in LIST if "gpt-oss" not in e["name"]])
    d = dm.defaults(dm.catalog("T"))
    assert d["judge"] != d["chat"] and d["judge"] == "databricks-claude-sonnet-5"
    serve([ep("only-one")])
    assert dm.defaults(dm.catalog("T"))["judge"] == "only-one"   # nothing else to choose
    serve([])
    assert dm.defaults([]) == {"chat": "", "code": "", "judge": ""}


@case("an operator can still pin any of the three, and the pin wins over the automatic choice")
def _():
    serve(LIST)
    os.environ["PORTAL_BUILDER_MODEL"] = "databricks-claude-opus-4-8"
    os.environ["PORTAL_BUILDER_CODE_MODEL"] = "databricks-claude-opus-4-8"
    os.environ["PORTAL_BUILDER_JUDGE_MODEL"] = "databricks-llama-4-maverick"
    assert dm.defaults(dm.catalog("T")) == {"chat": "databricks-claude-opus-4-8", "code": "databricks-claude-opus-4-8", "judge": "databricks-llama-4-maverick"}
    serve(LIST)


@case("a choice is used; anything not chosen takes the default; code goes to Opus unless chosen")
def _():
    serve(LIST)
    assert dm.resolve(None, "T") == {"chat": "databricks-claude-sonnet-5-5", "code": "databricks-claude-opus-4-8", "judge": "databricks-gpt-oss-120b"}
    got = dm.resolve({"chat": "databricks-claude-opus-4-8", "judge": ""}, "T")
    assert got == {"chat": "databricks-claude-opus-4-8", "code": "databricks-claude-opus-4-8", "judge": "databricks-gpt-oss-120b"}
    got = dm.resolve({"chat": "databricks-claude-haiku-4-5", "code": "databricks-claude-opus-4-8", "judge": "our-gpt"}, "T")
    assert got == {"chat": "databricks-claude-haiku-4-5", "code": "databricks-claude-opus-4-8", "judge": "our-gpt"}
    assert dm.resolve("junk", "T")["chat"] == "databricks-claude-sonnet-5-5"


@case("a bad or unavailable choice from the browser is refused in plain words, never passed on")
def _():
    serve(LIST)
    for bad in ({"chat": "../../etc"}, {"chat": "x" * 200}, {"chat": "has space"}):
        try:
            dm.resolve(bad, "T")
        except DbxError as exc:
            assert exc.status == 400 and "valid model name" in str(exc)
        else:
            raise AssertionError(bad)
    for gone in ("databricks-claude-opus-4-1", "databricks-claude-sonnet-4", "mas-1-endpoint", "someone-elses-model"):
        try:
            dm.resolve({"chat": gone}, "T")
        except DbxError as exc:
            assert exc.status == 400 and "not available to you" in str(exc)
        else:
            raise AssertionError(gone)


@case("with no models available it says so, and how to fix it; an unreadable list does not block a typed pin")
def _():
    serve([ep("only-embeddings", task="llm/v1/embeddings")])
    try:
        dm.resolve(None, "T")
    except DbxError as exc:
        assert exc.status == 409 and "Can Query" in str(exc)
    else:
        raise AssertionError("must refuse")
    info = dm.describe("T")
    assert info["enabled"] and not info["ready"] and "Can Query" in info["reason"] and info["models"] == []
    serve(DbxError("denied", 403))
    os.environ["PORTAL_BUILDER_MODEL"] = "pinned-endpoint"
    assert dm.resolve(None, "T")["chat"] == "pinned-endpoint"
    assert dm.resolve({"chat": "any-valid-name"}, "T")["chat"] == "any-valid-name"  # cannot be checked, still runs as them
    os.environ.pop("PORTAL_BUILDER_MODEL")


@case("what the screen gets: the models, the defaults, a pinned model that is not in the list, and the off switch")
def _():
    serve(LIST)
    info = dm.describe("T")
    assert info["enabled"] and info["ready"] and info["reason"] == ""
    assert info["defaults"]["chat"] == "databricks-claude-sonnet-5-5" and info["models"][0]["label"] == "Claude Sonnet 5.5"
    os.environ["PORTAL_BUILDER_MODEL"] = "hidden-pinned"
    pinned = [m for m in dm.describe("T")["models"] if m["name"] == "hidden-pinned"][0]
    assert pinned["detail"] == "Set up by your administrator" and pinned["label"] == "Hidden Pinned"
    os.environ["PORTAL_DESIGNER"] = "off"
    off = dm.describe("T")
    assert off["enabled"] is False and off["models"] == [] and not off["ready"]
    for v in ("0", "false", "No", " OFF "):
        os.environ["PORTAL_DESIGNER"] = v
        assert dm.disabled()
    os.environ["PORTAL_DESIGNER"] = "on"
    assert not dm.disabled()
    serve(LIST)


@case("the price comes from the workspace's own figures, and the cost word from the real number")
def _():
    serve([ep("databricks-claude-sonnet-5-5", price=(28.57142857, 142.8571429)),
           ep("databricks-gpt-oss-120b", price=(2.143, 8.571)),
           ep("databricks-claude-opus-4-1", price=(214.286, 1071.42)),
           ep("databricks-claude-haiku-5-5")])   # no price reported
    by = {m["name"]: m for m in dm.catalog("T")}
    s = by["databricks-claude-sonnet-5-5"]
    assert s["cost"] == "Moderate cost" and round(s["price_out"], 1) == 142.9
    assert s["detail"] == "Anthropic · Balanced: a good default · Moderate cost · 28.6 in, 142.9 out per 1M tokens (DBUs)"
    assert by["databricks-gpt-oss-120b"]["cost"] == "Low cost" and by["databricks-claude-opus-4-1"]["cost"] == "Higher cost"
    assert by["databricks-claude-haiku-5-5"]["cost"] == "" and by["databricks-claude-haiku-5-5"]["price_out"] is None
    assert "DBUs" not in by["databricks-claude-haiku-5-5"]["detail"]   # nothing invented when there is no price


@case("a model that cannot call functions never runs the interview, but may still write code or mark answers")
def _():
    serve([ep("databricks-meta-llama-3-1-8b-instruct", tools=False), ep("databricks-claude-sonnet-5-5", tools=True), ep("other", tools=None)])
    by = {m["name"]: m for m in dm.catalog("T")}
    assert by["databricks-meta-llama-3-1-8b-instruct"]["tools"] is False and by["databricks-meta-llama-3-1-8b-instruct"]["fit"] == "Cannot use tools"
    assert by["other"]["tools"] is None   # not reported is not the same as "cannot"
    # only models that cannot are left out of the automatic choice
    serve([ep("databricks-meta-llama-3-1-8b-instruct", tools=False)])
    assert dm.defaults(dm.catalog("T"))["chat"] == ""
    serve([ep("databricks-meta-llama-3-1-8b-instruct", tools=False), ep("databricks-claude-sonnet-5-5", tools=True)])
    assert dm.defaults(dm.catalog("T"))["chat"] == "databricks-claude-sonnet-5-5"
    try:
        dm.resolve({"chat": "databricks-meta-llama-3-1-8b-instruct"}, "T")
    except DbxError as exc:
        assert exc.status == 400 and "cannot use tools" in str(exc)
    else:
        raise AssertionError("the interview needs a model that can call functions")
    got = dm.resolve({"code": "databricks-meta-llama-3-1-8b-instruct", "judge": "databricks-meta-llama-3-1-8b-instruct"}, "T")
    assert got["code"] == got["judge"] == "databricks-meta-llama-3-1-8b-instruct"   # neither needs tools


@case("a model found to be retired disappears from the list, and an old choice of it is replaced and explained")
def _():
    serve(LIST)
    assert "databricks-claude-sonnet-5" in [m["name"] for m in dm.catalog("T")]
    dm.mark_retired("databricks-claude-sonnet-5")
    assert "databricks-claude-sonnet-5" not in [m["name"] for m in dm.catalog("T")]   # cache cleared too
    notes = []
    got = dm.resolve({"chat": "databricks-claude-sonnet-5"}, "T", notes)
    assert got["chat"] == "databricks-claude-sonnet-5-5"
    assert notes == ["Claude Sonnet 5 has been retired by Databricks, so Claude Sonnet 5.5 was used instead."]
    assert dm.describe("T")["defaults"]["chat"] == "databricks-claude-sonnet-5-5"


@case("the next best model after a refusal skips what was tried, keeps the checker different, and says when none is left")
def _():
    serve(LIST)
    first = dm.catalog("T")[0]["name"]
    assert first == "databricks-claude-sonnet-5-5"
    assert dm.fallback("T", "chat", [first]) == "databricks-claude-sonnet-5"
    assert dm.fallback("T", "code", [first, "databricks-claude-sonnet-5"]) == "databricks-claude-opus-4-8"
    assert dm.fallback("T", "judge", ["databricks-gpt-oss-120b"], chat=first) != first
    serve([ep("only-one")])
    assert dm.fallback("T", "chat", ["only-one"]) == "" and dm.fallback("T", "judge", ["only-one"], chat="only-one") == ""


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


@case("a model list that cannot be read for a moment: the last good one stands in; with none, the message says why")
def _():
    from dbx import DbxError
    real = dm.call
    good = {"endpoints": [{"name": "databricks-claude-sonnet-5-5", "task": "llm/v1/chat", "state": {"ready": "READY"},
                           "permission_level": "CAN_QUERY", "endpoint_type": "FOUNDATION_MODEL_API"}]}
    try:
        dm._cache.clear(); dm._failed.clear()
        dm.call = lambda *a, **k: good
        assert [m["name"] for m in dm.catalog("tok-a")] == ["databricks-claude-sonnet-5-5"]
        key = next(iter(dm._cache))
        dm._cache[key] = (__import__("time").time() - 1, dm._cache[key][1])  # the minute is just over, so it is read again...
        dm.call = lambda *a, **k: (_ for _ in ()).throw(DbxError("Invalid Token", 403))
        assert [m["name"] for m in dm.catalog("tok-a")] == ["databricks-claude-sonnet-5-5"]  # ...and fails: the last list stands in
        try:
            dm.resolve({}, "tok-b")                       # never read for this caller: say the list could not be read
            raise AssertionError("no error")
        except DbxError as exc:
            assert exc.status == 503 and "could not be read just now" in str(exc) and "Invalid Token" in str(exc), str(exc)
        out = dm.resolve({"chat": "databricks-claude-sonnet-5-5", "judge": "databricks-gpt-oss-120b"}, "tok-b")
        assert out["chat"] == "databricks-claude-sonnet-5-5" and out["judge"] == "databricks-gpt-oss-120b"  # named models still work
    finally:
        dm.call = real
        dm._cache.clear(); dm._failed.clear()


if __name__ == "__main__":
    raise SystemExit(main())
