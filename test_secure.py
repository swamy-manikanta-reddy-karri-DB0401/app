"""Offline checks of the web security layer (secure.py): headers, the browser policy, cross-site
requests, size limits, the rate limit, API docs, and that the deployer carries the same copy."""
from __future__ import annotations

import base64
import filecmp
import hashlib
import os
import re
import sys

from fastapi.testclient import TestClient

import secure

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


import app as portal  # noqa: E402

client = TestClient(portal.app)


@case("every response carries the security headers; API answers are never cached")
def _():
    for path in ("/api/health", "/"):
        r = client.get(path)
        for h in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
                  "Permissions-Policy", "Cross-Origin-Opener-Policy", "Cross-Origin-Resource-Policy"):
            assert h in r.headers, (path, h)
        assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert client.get("/api/health").headers["Cache-Control"] == "no-store"


@case("the browser policy allows only this site, and each inline script by its own hash")
def _():
    policy = client.get("/").headers["Content-Security-Policy"]
    script = next(p for p in policy.split("; ") if p.startswith("script-src"))
    assert "'unsafe-inline'" not in script and "'unsafe-eval'" not in script and "http" not in policy
    assert "frame-ancestors 'none'" in policy and "object-src 'none'" in policy
    html = open(os.path.join(HERE, "web", "index.html"), "rb").read()
    inline = re.findall(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert inline, "the built page has inline scripts to allow"
    for body in inline:
        assert "'sha256-%s'" % base64.b64encode(hashlib.sha256(body).digest()).decode() in script
    assert "upgrade-insecure-requests" not in policy  # local dev is plain HTTP
    assert "upgrade-insecure-requests" in secure.csp(os.path.join(HERE, "web"), True)
    assert "Strict-Transport-Security" in secure.headers(os.path.join(HERE, "web"), True)


@case("a change sent from another site is refused before it reaches a route; the portal's own page and server calls are not")
def _():
    for h in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Origin": "https://evil.example"}, {"Origin": "null"}):
        r = client.post("/api/chat", json={}, headers=h)
        assert r.status_code == 403 and ("another site" in r.json()["error"] or "no origin" in r.json()["error"]), (h, r.text)
    assert secure.cross_site("POST", {"sec-fetch-site": "same-origin", "host": "internal:8000"}) == ""  # behind the proxy
    assert secure.cross_site("POST", {"origin": "https://portal.example", "x-forwarded-host": "portal.example"}) == ""
    assert secure.cross_site("POST", {}) == ""  # server to server (the deployer's health check)
    assert secure.cross_site("GET", {"sec-fetch-site": "cross-site"}) == ""  # reading is not a change
    r = client.post("/api/chat", json={}, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code != 403 or "another site" not in r.text


@case("oversized requests are refused before they are read")
def _():
    assert secure.too_big({"content-length": str(secure.MAX_JSON + 1), "content-type": "application/json"})
    assert not secure.too_big({"content-length": str(50 * 1024 * 1024), "content-type": "multipart/form-data; boundary=x"})
    assert secure.too_big({"content-length": str(secure.MAX_UPLOAD + 1), "content-type": "multipart/form-data; boundary=x"})
    assert secure.too_big({"content-length": "abc"})


@case("a person sending too many requests a minute is slowed down, others are not")
def _():
    rl = secure.RateLimit(3)
    assert [rl.over("a@x.io") for _ in range(4)] == [False, False, False, True]
    assert not rl.over("b@x.io") and not secure.RateLimit(0).over("a@x.io")


@case("API docs are off in Databricks Apps unless switched on")
def _():
    assert client.get("/api/docs").status_code == 200  # local dev
    src = open(os.path.join(HERE, "app.py"), encoding="utf-8").read()
    assert 'PORTAL_API_DOCS' in src and 'docs_url="/api/docs" if _DOCS else None' in src


@case("the deployer carries the same security module and uses it")
def _():
    assert filecmp.cmp(os.path.join(HERE, "secure.py"), os.path.join(HERE, "deployer", "secure.py"), shallow=False), \
        "copy secure.py to deployer/secure.py"
    src = open(os.path.join(HERE, "deployer", "app.py"), encoding="utf-8").read()
    assert "secure.headers(" in src and "secure.cross_site(" in src
    for y in ("app.yaml", os.path.join("deployer", "app.yaml")):
        assert "--no-server-header" in open(os.path.join(HERE, y), encoding="utf-8").read(), y


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
