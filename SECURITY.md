# Security

How the Agent Portal (and the Portal Deployer) protect people, data and the workspace. Written for security
reviewers; each control names the code that enforces it so it can be checked. To report a problem, contact
the product owner rather than opening a public issue.

## Architecture in one paragraph

The portal is a Databricks App: a FastAPI backend serving a static web UI. People reach it only through the
Databricks Apps proxy, which signs them in with the workspace's own identity provider (SSO, MFA and
conditional access are the workspace's). The portal keeps no user accounts, passwords or sessions of its own,
and stores no configuration of its own: access, agents and settings live in Databricks (endpoint ACLs, groups,
Unity Catalog). It runs inside the client's own Databricks workspace; data does not leave it.

## Identity and access

| Control | How |
|---|---|
| Authentication | Databricks Apps proxy (workspace SSO). The portal has no login of its own. |
| Acting as the person | Every agent call, file read/write and billing query uses the signed-in person's own forwarded token (`X-Forwarded-Access-Token`, `dbx.user_token`). Databricks then enforces endpoint ACLs and Unity Catalog permissions itself. |
| Fail closed | In Apps, a request without a forwarded user token is refused (403); the portal never falls back to its own identity to call an agent. |
| Least privilege for the app | The app's service principal is used only for metadata (ACLs, groups, tags) and the portal's own Delta tables. User API scopes are listed in `update-scopes.json`. |
| Admin functions | Every `/api/admin/*` route checks membership of the workspace `admins` group (`_require_admin`) with the caller's own token. |
| Privacy between people | Saved chats are filtered by the user name taken from the caller's token, never from the request, with bound SQL parameters. Admin dashboards show counts only, never question text. |
| Revoking access | Removes only the chosen grant and keeps service-principal grants (`access.set_grant`). |

## Web application (OWASP Top 10)

| Risk | Control |
|---|---|
| Cross-site scripting | React escapes output; agent answers are rendered as Markdown without raw HTML. A strict **Content-Security-Policy** allows scripts only from the portal itself and its own inline scripts by SHA-256 hash (no `unsafe-inline`/`unsafe-eval` for scripts), `object-src 'none'`, `base-uri 'self'` (`secure.py`). |
| Clickjacking | `frame-ancestors 'none'` and `X-Frame-Options: DENY`. |
| Cross-site request forgery | Any change (POST/PUT/PATCH/DELETE) sent from another site is refused before it reaches a route, using the browser's `Sec-Fetch-Site` and `Origin` (`secure.cross_site`). No CORS is enabled. |
| Transport | HTTPS only (Databricks Apps); `Strict-Transport-Security` (1 year, subdomains) and `upgrade-insecure-requests`. |
| Content sniffing / downloads | `X-Content-Type-Options: nosniff`. Files are downloaded as `application/octet-stream` attachments, so an uploaded web page can never run in the portal. Download paths with `..` are refused; a download needs a signed, per-person, per-file permit (`signer.py`) and is read with the person's own token. |
| Information leakage | API docs are off in Apps (`PORTAL_API_DOCS=on` to enable). The server header is removed (`--no-server-header`). Errors return a plain sentence, never a stack trace. API responses are `Cache-Control: no-store`. `Referrer-Policy: strict-origin-when-cross-origin`; `Permissions-Policy` turns off camera, microphone, location, payment, USB and similar. `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy: same-origin`. |
| Injection | All SQL uses bound parameters; table names are checked against a strict pattern before use. Names that must be inlined (a model name) are checked against `^[\w\-.]{1,128}$` first. |
| Abuse and denial of service | Request size limits (25 MB JSON, 110 MB uploads; files 100 MB) and a per-person rate limit (`PORTAL_RATE_LIMIT`, default 600 requests a minute) (`secure.py`). Custom agents also have per-person daily and per-period question, token and cost limits. |
| Vulnerable components | Next.js 16.4.5 and PostCSS 8.5.32: `npm audit` reports 0 vulnerabilities. Python dependencies are pinned to exact versions and `pip-audit` reports none. **CI fails a release** if either finds a known vulnerability. The UI is a static export: no Next.js server, server actions, middleware or image optimizer run in production. |
| Secrets | Credentials a tool needs go straight to the Databricks secret scope and are never returned, logged, sent to the browser or to an AI model (`connections.py`). Tokens and request bodies are never logged. Key-like text is removed from what people type, files, runbooks and answers before an AI model or a person sees it (`attachments.redact`). |
| Logging and audit | Every meaningful action is recorded (who, what, when, outcome), never what anyone typed (`events.py`); access changes made in Databricks are read from `system.access.audit`. Retention 180 days. |

## AI-specific risks (OWASP Top 10 for LLM applications)

| Risk | Control |
|---|---|
| Prompt injection (direct) | Override attempts ("ignore your instructions", "developer mode", fake system tags) are detected; per agent they are blocked, or let through with a reminder to the agent, and recorded (`safety.py`, `custom_agents.ATTACKS`). |
| Prompt injection (indirect) | Everything a document, tool or other assistant returns is wrapped as untrusted data the model must not take orders from, and flagged when it contains instructions. |
| Sensitive information disclosure | Personal data (email, phone, card numbers with Luhn check, IBAN with mod-97 check, SSN, PAN) can be blocked or hidden from the model on the way in and removed or blocked on the way out; keys are always removed. Each agent's own rules can block or remove phrases or topics. |
| Unsafe output | An optional checker model chosen by the admin judges messages and answers against safety categories and the team's own rules; what happens when the checker cannot answer is a policy choice (let through and record, or hold back). |
| Excessive agency | A tool that changes something waits for the person to approve it (`toolpolicy.py`); tests never run such tools; another assistant never runs them on someone's behalf. |
| Model and data access | Models are called with the person's own token: they can only use models and data Databricks lets them use. The list of models offered is read with their token. |
| Unbounded consumption | Limits per answer (steps, reply length), per person per day, and per period (questions, tokens, cost in USD at Databricks' own list price). |
| Change control | Custom agents are tested (including a fixed safety test pack) and approved by a chosen person before anyone can use them; any change to what an agent does voids the test and needs a new approval. |
| Generated code | Tools the designer writes are checked statically and never run inside the portal; they run as separate Databricks Apps with only the folders and settings they declare, after an admin reviews the code. |

## What reaches an AI model

| Sent to the model | Never sent |
|---|---|
| What people type and the earlier messages of that chat | Passwords, keys and tokens (removed first, always) |
| For custom agents: passages of their documents, the runbook, tool results, up to 50 rows of a data answer | Credentials kept for tools (secret scope only) |
| Files people attach, when the agent reads them | Other people's conversations |
| In the assistant designer: what the admin types, table and column names | |

- **Personal data is hidden from the model by default** for custom agents: emails, phone and card numbers,
  IBANs, SSNs and PANs are replaced before a message is sent and removed from answers (policy "Standard").
  Pattern matching cannot recognise names or postal addresses; a checker model can judge those, but that
  also sends the text to a model. Agents saved before this default keep the settings they were tested with.
- Assistants built in Databricks (Supervisor Agents, Genie spaces, Knowledge Assistants) run their own model
  inside Databricks; the portal's policy cannot apply to them.
- **Where the model runs.** Databricks documents Anthropic models on Databricks as "hosted by Databricks
  within the Databricks security perimeter"; the portal calls no third-party AI service. Requests are
  processed in the workspace's geography for US and EU workspaces. Elsewhere most Claude models need
  Databricks' cross-Geo processing, which processes requests outside the workspace's region: the portal
  reads the workspace's region and says so on the model step and the safety card (`safety.residency`).
- **Not stated in Databricks' public documentation** (ask the Databricks account team in writing): whether
  prompts are retained and for how long, and that they are never used for training.

## Data

- Data stays in the client's Databricks account. AI models are Databricks model serving endpoints.
- The portal's own tables (problem log, activity log, chat history, custom agents and their tests, saved
  safety policies) are Delta tables in a schema the client chooses, governed by Unity Catalog. Chat history is
  optional and purged after `PORTAL_CHAT_RETENTION_DAYS` (default 180).
- Removing a client with the deployer can drop these tables.

## Checking it yourself

```bash
python test_secure.py                  # headers, browser policy, cross-site refusals, size and rate limits
curl -sI https://<portal-url>/          # the security headers (signed in through the Apps proxy)
cd ui && npm audit --omit=dev           # 0 vulnerabilities
pip-audit -r requirements.txt           # no known vulnerabilities
```
