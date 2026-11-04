"""Web security for the portal (and the deployer, which uses the same module): response headers, a
cross-site request check, request size limits and a per-person rate limit.

Why each piece, in the terms an enterprise security review uses:

- Content-Security-Policy. The UI is a static Next.js export and loads nothing from other sites, so
  everything is `'self'`. Its inline scripts (the theme boot script and Next's page data) are allowed by
  their SHA-256 hashes, computed from the built pages at start-up, so no `'unsafe-inline'` or
  `'unsafe-eval'` is needed for scripts. Styles keep `'unsafe-inline'`: React writes `style=` attributes
  and the brand colours are injected as a <style> element; CSS cannot run code. `frame-ancestors 'none'`
  stops clickjacking.
- HSTS, nosniff, Referrer-Policy, Permissions-Policy, COOP/CORP, X-Frame-Options: standard hardening.
  HSTS and upgrade-insecure-requests are sent only in Databricks Apps (always HTTPS); local dev is HTTP.
- API responses carry `Cache-Control: no-store`: they hold people's conversations and settings.
- Cross-site requests. People are signed in by the Databricks Apps proxy's cookie, so a page on another
  site could try to make their browser call the portal (CSRF). A change (POST/PUT/PATCH/DELETE) is
  refused when the browser says it came from another site (`Sec-Fetch-Site`) or its `Origin` is not this
  portal. Server-to-server callers (the deployer's health check) send neither header and are not affected.
- Size limits: a JSON body over MAX_JSON bytes and an upload over MAX_UPLOAD are refused before they are read.
- Rate limit: requests per person per minute (PORTAL_RATE_LIMIT, default 600; 0 = off), in memory.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import threading
import time

from starlette.responses import JSONResponse

MAX_JSON = 25 * 1024 * 1024
MAX_UPLOAD = 110 * 1024 * 1024  # files are limited to 100 MB; room for the form around them
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")
_SCRIPT_RE = re.compile(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def inline_script_hashes(web_dir: str) -> list:
    """'sha256-...' for every inline script in the built pages."""
    out = set()
    for root, _, files in os.walk(web_dir):
        for f in files:
            if f.endswith(".html"):
                with open(os.path.join(root, f), "rb") as fh:
                    for body in _SCRIPT_RE.findall(fh.read()):
                        out.add("'sha256-%s'" % base64.b64encode(hashlib.sha256(body).digest()).decode())
    return sorted(out)


def csp(web_dir: str, https: bool) -> str:
    parts = [
        "default-src 'self'",
        "script-src 'self' " + " ".join(inline_script_hashes(web_dir)),
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "media-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
    ]
    if https:
        parts.append("upgrade-insecure-requests")
    return "; ".join(p.strip() for p in parts)


def headers(web_dir: str, https: bool) -> dict:
    h = {
        "Content-Security-Policy": csp(web_dir, https),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), "
                              "magnetometer=(), gyroscope=(), accelerometer=(), interest-cohort=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Permitted-Cross-Domain-Policies": "none",
    }
    if https:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return h


def _host_of(origin: str) -> str:
    return re.sub(r"^[a-z]+://", "", origin or "").split("/")[0].lower()


def cross_site(method: str, h) -> str:
    """Why a change request looks like it came from another site ("" when it does not)."""
    if method not in UNSAFE:
        return ""
    site = (h.get("sec-fetch-site") or "").lower()
    if site:
        # Every current browser sends it, and it is what the browser itself saw: trusted over Host, which
        # behind the Apps proxy may not be the address the person opened.
        return "" if site in ("same-origin", "none") else "sent from another site (%s)" % site
    origin = h.get("origin") or ""
    if origin and origin != "null":
        mine = {_host_of(x) for x in (h.get("x-forwarded-host"), h.get("host")) if x}
        if _host_of(origin) not in mine:
            return "sent from another site"
    elif origin == "null":
        return "sent from a page with no origin"
    return ""


def too_big(h) -> str:
    try:
        n = int(h.get("content-length") or 0)
    except ValueError:
        return "the request size could not be read"
    multipart = "multipart/form-data" in (h.get("content-type") or "")
    cap = MAX_UPLOAD if multipart else MAX_JSON
    return "the request is too large (%d MB at most)" % (cap // 1024 // 1024) if n > cap else ""


class RateLimit:
    """A fixed one-minute window per person. Memory only: a restart starts again, which is fine for its
    purpose (stopping a runaway script or a person hammering the API), not billing."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._seen: dict = {}
        self._lock = threading.Lock()

    def over(self, who: str) -> bool:
        if not self.per_minute or not who:
            return False
        minute = int(time.time() // 60)
        with self._lock:
            if len(self._seen) > 20000:
                self._seen = {k: v for k, v in self._seen.items() if v[0] == minute}
            m, n = self._seen.get(who, (minute, 0))
            n = n + 1 if m == minute else 1
            self._seen[who] = (minute, n)
            return n > self.per_minute


def refuse(status: int, why: str) -> JSONResponse:
    return JSONResponse({"error": "Refused: %s." % why}, status_code=status)


def rate_from_env() -> int:
    try:
        return max(0, int(os.environ.get("PORTAL_RATE_LIMIT", "600")))
    except ValueError:
        return 600
