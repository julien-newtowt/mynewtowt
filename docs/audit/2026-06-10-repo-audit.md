# Repo Audit & Improvement Plan — `mynewtowt`

**Date** : 2026-06-10 · **Scope** : full repository at `42468c6` (main)
**Method** : static read of all core modules, 4 parallel deep-dive sweeps
(routers / services / tests / templates+infra), local execution of the test
suite and linters, and inspection of the last 30 GitHub Actions runs.
No code was modified.

---

## 1. Executive Summary

**Overall health grade: B−.** The application code itself is unusually good
for a small-team ERP — auth, RBAC, CSRF, CSP, audit trail, file-upload
validation and deployment scripts are all genuinely production-grade — but
the *verification layer around it is fiction*: **all 30 most recent CI runs
failed, including every run on `main`**, because a pip dependency conflict
(`safety==3.5.0` requires `pydantic<2.10`, app pins `2.10.4`) kills every
job at `pip install`, so no lint, no tests and no security scan has actually
run in CI. Run locally, the suite has **2 failing tests on `main`** (the
`leg_code` business identifier drifted from both spec and tests) and real
coverage is **25.7 % against a declared 80 % gate**. The strict CSP silently
**disables every inline `onclick`/`onsubmit` handler**, which means several
destructive actions (leg delete, share revoke) submit *without* their
confirmation dialog. Top 3 risks: (1) broken CI = unguarded merges to a
production ERP, (2) staff password login has **no rate limiting** and
sessions **cannot be revoked server-side** (14-day cookies for sailors),
(3) spec/test/code three-way disagreement on `leg_code`, the central
business identifier. Top 3 opportunities: repairing CI is a one-line-ish
fix that re-activates an already well-designed quality pipeline; an
integration-test harness (fixtures already half-paid-for via
`testcontainers` in requirements-dev) would cover the 10.4 kLOC of
untested routers; and ~42 MB of legacy artifacts can be dropped from the
repo for an instant DX win.

---

## 2. Repo Map

**Purpose** : unified platform for TransOceanic Wind Transport — internal
ERP (planning, commercial, port calls, cargo, crew, finance, MRV…) +
public client booking platform + token-based shipper portal. Maturity:
**production service** (deploy scripts, Caddy TLS, Sentry/OTel, MFA).

**Stack** : FastAPI 0.115 / Python 3.12 / SQLAlchemy 2 async + asyncpg /
PostgreSQL 16 / Alembic / Jinja2 SSR + HTMX 2 / Docker + Caddy.

**Architecture** (clean, consistent layering):

```
Caddy (TLS, HSTS) → uvicorn ×2 (--proxy-headers)
  → middlewares: CORS → SecurityHeaders → Maintenance → CSRF
                 → ForcePasswordChange → ForceMfaForAdmin
  → 30 routers (1/module) — Depends(require_permission(module, C|M|S))
  → services/ (business logic, 26 modules) → models/ (SQLAlchemy Mapped[])
  → get_db() dependency: commit-on-success / rollback-on-exception
```

| Area | Contents |
|---|---|
| `app/main.py` | app factory, middleware/router assembly (206 l) |
| `app/auth.py` | bcrypt + itsdangerous signed cookies, staff/client/MFA-pending contexts (325 l) |
| `app/permissions.py` | RBAC matrix 8 rôles × 17 modules × C/M/S (230 l) |
| `app/csrf.py` | double-submit cookie, form/multipart/header extraction |
| `app/routers/` | 30 routers, 10.4 kLOC — largest: `modules_router.py` 825 l |
| `app/services/` | 5.3 kLOC — planning, chatbot (Claude tool-use), pricing, MFA, rate-limit (DB-backed)… |
| `app/models/` | 2.6 kLOC, 25+ modèles |
| `tests/unit/` | 26 files, ~1.9 kLOC, pure unit tests (no DB/client fixtures) |
| `migrations/` | 29 Alembic versions, async `env.py` ✓ |
| `scripts/` | `deploy.sh` (433 l: snapshot, rollback, smoke tests), `install.sh` (565 l) |
| `Versions TOWT/` | ⚠️ 201 git-tracked legacy files incl. old app code + a `.pptx` — 39.4 MiB pack |

**Surprises found during discovery** : CI red across the board; 2 failing
tests on main; CSP-blocked inline handlers; a `__import__("pathlib")` inline
hack at `app/main.py:74`; deprecated `@app.on_event("startup")`
(`app/main.py:190`) on FastAPI 0.115.

---

## 3. Audit Report

Severity legend: 🔴 Critical · 🟠 High · 🟡 Medium · ⚪ Low.
**[F]** = verified fact, **[J]** = judgment.

### 3.1 DevEx & Operations — *the ugly part*

* 🔴 **CI is completely non-functional.** [F] All 30 most recent runs of
  the `CI` workflow concluded `failure`, including every push to `main`
  (latest: run 26848119478, 2026-06-02). Root cause from the job log:
  `safety==3.5.0` (requirements-dev.txt:23) depends on `pydantic<2.10.0`
  while `requirements.txt:` pins `pydantic==2.10.4` →
  `ResolutionImpossible` → **lint, mypy, pytest, bandit, gitleaks have
  never executed**. Consequence: every gate promised in CLAUDE.md
  ("review obligatoire", coverage 80 %, security scans) is currently
  decorative; regressions ship unguarded to a production ERP.
* 🟠 **CI `test` job has a second, independent blocker.** [F] The Postgres
  service container fails to pull: `docker pull postgres:16-alpine` →
  `registry-1.docker.io … context deadline exceeded` (3 retries, every
  run). Fixing pip alone will not make `test` green; the image needs a
  mirror (`public.ecr.aws/docker/library/postgres:16-alpine` or GHCR).
* 🟡 **The lint stack has never been exercised and its config is
  mis-tuned.** [F] `ruff check app tests` → **984 errors**, but 618 are
  `B008` false positives (`Depends(...)` in defaults — the standard
  FastAPI idiom; fix is one line:
  `lint.flake8-bugbear.extend-immutable-calls = ["fastapi.Depends", "fastapi.Cookie", "fastapi.Form", …]`).
  Real signal underneath: 60 × `F401` unused imports, 15 × `B904`
  (`raise … from` missing), 62 × `I001` import order. [J] `mypy --strict`
  (pyproject `strict = true`) almost certainly fails by hundreds of
  errors on the untyped router returns; unverified because CI never got
  there.

### 3.2 Testing

* 🔴 **Coverage gate is unreachable and the suite is red on `main`.** [F]
  Local run: `262 passed, 2 failed`, total coverage **25.70 %** vs
  `fail_under = 80` (pyproject.toml) — pytest-cov *does* enforce this, so
  even with green infra the test job fails. The 2 failures
  (`tests/unit/test_planning_service.py:42` and `:…sequence_bump`) are
  the `leg_code` drift, see §3.4.
* 🟠 **Zero tests for all 30 routers (10.4 kLOC) and for critical
  services.** [F] `tests/conftest.py` (16 lines) defines **no fixtures**
  — no DB session, no `TestClient`. `testcontainers[postgres]` is in
  requirements-dev.txt but never imported. Untested: `services/mfa.py`
  (TOTP + recovery codes), `services/booking_lifecycle.py`,
  `services/capacity.py`, `services/email.py`, `services/notifications.py`,
  auth dependencies (`get_current_staff/client`), every auth/permission
  *enforcement* path.
* ⚪ **One flaky pattern.** [F] `tests/unit/test_auth.py:51` uses
  `time.sleep(2.1)` to test token expiry — load-sensitive.
* **Positive** [F] : the existing 262 tests assert real behavior (computed
  prices, permission matrices, CSRF round-trips), not just "no exception".

### 3.3 Security

* 🟠 **Staff password login has no rate limiting.** [F]
  `app/routers/staff_auth_router.py:40-61` — the password POST has no
  `rate_limit` check; only the *MFA* step is limited (5/5 min, line 132)
  and client login is limited (10/10 min,
  `client_auth_router.py:72-74`). MFA is optional for non-admin roles, so
  a `marins` account is brute-forceable online (bcrypt slows but doesn't
  stop it). The DB-backed `rate_limit` service already exists — this is
  an omission, not missing infrastructure.
* 🟠 **No server-side session revocation.** [F] Sessions are stateless
  signed cookies (`app/auth.py:86-93`, payload = `uid` + `iat` only);
  logout merely deletes the cookie (`staff_auth_router.py:191-195`,
  notably a **GET**). A stolen cookie stays valid until expiry — **14
  days** for `marins`/`manager_maritime` (`auth.py:37-40`) — and password
  change/account compromise cannot invalidate it (only `is_active=False`
  can, `auth.py:214`).
* 🟡 **Outdated deps with known CVEs.** [F] `jinja2==3.1.5` —
  CVE-2025-27516 (sandbox escape via `|attr`), fixed in 3.1.6;
  `cryptography==44.0.0` — CVE-2024-12797, fixed in 44.0.1. [J] Low
  exploitability here (no Jinja sandbox use), but trivial bumps.
  `passlib==1.7.4` is unmaintained (hence the `bcrypt==4.0.1` pin);
  medium-term, migrate to direct `bcrypt` or `argon2-cffi`.
* ⚪ `--forwarded-allow-ips "*"` (Dockerfile:39) trusts any
  X-Forwarded-* sender — acceptable because port 8000 is not published
  (compose `expose:` only), but worth narrowing to the compose network.
* ⚪ Username enumeration by timing: `verify_password` is skipped when the
  user doesn't exist (`staff_auth_router.py:50`).
* ⚪ MFA recovery-code comparison is not constant-time
  (`services/mfa.py:112-123`); single-use codes make this near-theoretical.
* **Positives** [F] : both API-token endpoints use
  `secrets.compare_digest` (`tracking_router.py:373`,
  `veille_router.py:294`); **zero raw/f-string SQL** anywhere; **zero IDOR**
  found — all client object loads check `booking.client_account_id ==
  client.id` (`client_dashboard_router.py:119-120, 178, 281, 307`);
  portal tokens stored as SHA-256 as documented; uploads validated by
  extension + magic bytes + 20 MB cap + random hex names + path-traversal
  guard (`utils/file_validation.py`, `services/safe_files.py:46-54`);
  CSRF double-submit covers form, multipart and header; weak-secret
  refusal at startup (`config.py:115-148`); containers run non-root; no
  hardcoded secrets anywhere; chatbot tools re-check RBAC per call and
  track cost (`services/chatbot.py:130-203, 345`).

### 3.4 Correctness / Code quality

* 🟠 **`leg_code` three-way drift.** [F] CLAUDE.md (glossary) and the
  tests specify `{seq}{vessel_code}{POL}{POD}{year}` → `1CFRBR6`; the
  implementation (`services/planning.py:176-197`) now produces
  `{vessel_code}{seq_letter}…` → `CAFRBR6`; its own docstring example
  (`1AFRBR6`) matches *neither*. This is the central business identifier
  printed on documents — somebody must declare the canonical format
  (Open Question #1), then code+tests+docs get realigned.
* 🟡 **CSP silently breaks ~10 inline handlers, removing confirmation
  guards on destructive actions.** [F] CSP is
  `script-src 'self' https://unpkg.com` with no
  `unsafe-inline`/`unsafe-hashes` (`middlewares/security_headers.py:17`),
  which blocks all `on*=` attributes in modern browsers. Affected (grep
  verified): `staff/planning/leg_detail.html:14`
  (`onsubmit="return confirm('Supprimer définitivement le leg …')"` → the
  **delete-leg form submits with no confirmation**), `staff/planning/shares.html`
  (revoke share), `staff/captain/index.html:190,202`,
  `staff/captain/next_port.html:159` (sign & freeze SOF),
  `staff/onboard/navigation.html:13` + `staff/mrv/index.html:63` +
  `staff/kpi/index.html:73` (selects that no longer auto-submit/route —
  features quietly dead). Fix: `data-confirm` attributes + one external
  listener in `kairos`-style JS.
* 🟡 Swallowed exceptions in the voyage-closure workflow:
  `captain_router.py:669-678, 711-720, 754-757` (`except Exception:
  pass` around KPI compute / notifications) and `services/kpi.py:65-66`
  (silent `pass` in CO₂ aggregation) — failures invisible to ops.
* ⚪ Deprecated `@app.on_event("startup")` (`main.py:190`),
  `__import__("pathlib")` inline (`main.py:74`), ~60 unused imports
  (ruff F401).
* **Positives** [J] : no god-functions (longest ≈100 lines and readable),
  consistent `flush → 303` mutation pattern everywhere, `activity_record()`
  on essentially all writes (2 gaps: `crew_router.py:335`,
  `tickets_router.py:218`), services cleanly separated from routers, no
  dead service modules.

### 3.5 Performance

* 🟡 **WeasyPrint PDF rendering runs synchronously on the event loop.**
  [F] `services/pdf_generator.py:39-45` (`HTML().write_pdf()`) is called
  directly from async routes (`cargo_router.py:246-296`). One BL/invoice
  render stalls *every* in-flight request on that worker for its duration.
  Wrap in `run_in_executor`.
* 🟡 **N+1 queries on the public catalogue.** [F]
  `public_router.py:236-273` and `:276-327` issue 2 `db.get(Port, …)` per
  leg in a loop — up to ~100 queries per `/routes` search; same pattern in
  `client_dashboard_router.py:159-163` (messages per booking). These are
  the highest-traffic unauthenticated pages.
* ⚪ `get_db()` commits even on pure GETs (`database.py:51`) — harmless
  with no writes, minor overhead. Pool 10+10 with 2 workers is sane. [J]

### 3.6 Dependencies & repo hygiene

* 🟡 **42 MB of legacy artifacts are git-tracked.** [F] `Versions TOWT/`
  = 201 tracked files (old app code, `.pptx`, old CLAUDE.md, design PNGs)
  → pack size 39.4 MiB; plus committed `.DS_Store` files (root, several
  subdirs) despite `.gitignore` covering them. Old code duplicates
  actively confuse code search.
* 🟡 No lockfile for transitive deps (pins are `==` for direct deps only);
  the safety/pydantic conflict shows resolution is fragile. [J] Adopt
  `uv pip compile`/`pip-tools` for a constraints file.
* ⚪ `anthropic==0.49.0` is old but functional; model id
  `claude-sonnet-4-6` (`services/chatbot.py:35`) passes through fine.

### 3.7 Documentation

* 🟡 Docs claim things the code doesn't do: CLAUDE.md says auth includes
  **WebAuthn** (only TOTP exists in `services/mfa.py`); CLAUDE.md
  `leg_code` format contradicts the implementation (§3.4);
  `docs/security/01-security-review.md` still references Stripe (removed
  in v3.1 per `security_headers.py:7` and `config.py:77-79`).
* **Positive** : README quick-start commands verified accurate; docs/
  volume (runbooks, ADRs, personas) is far above average for this size of
  team. [J]

### 3.8 Healthy dimensions (one line each)

Architecture & module boundaries: clean, no cycles, no layering
violations found. Auth/MFA flow design: solid (signed short-lived
MFA-pending cookies, device-detection alerts). Async hygiene: exemplary
apart from WeasyPrint (SMTP in executor, httpx everywhere with timeouts
8–15 s). Deployment: `deploy.sh` with pre-flight checks, DB snapshot,
health-gated rolling restart, smoke tests, rollback — excellent.

---

## 4. Improvement Strategy

### Theme A — Rebuild trust in the verification layer *(explains most Critical/High findings)*
**Target state** : CI green on `main`, every job actually executes, and a
red check blocks merge. **Principle** : a gate that never runs is worse
than no gate — it manufactures false confidence.
Concretely: resolve the pip conflict, mirror the Postgres image, set the
coverage gate to reality (start 25 %, ratchet +5 pts per milestone), tune
ruff for FastAPI, demote `mypy --strict` to non-strict until typed.

### Theme B — Close the session-lifecycle gaps
**Target state** : online brute force impossible on any login; any session
revocable within seconds. **Principle** : stateless cookies are fine *if*
there is one server-side check — reuse the existing per-request user
lookup (`auth.py:213-215`) by adding a `session_epoch`/`token_version`
column compared against the cookie payload; bump it on logout-all /
password change / admin action. Reuse the existing DB `rate_limit` service
on `POST /login`.

### Theme C — One source of truth for business rules (spec = tests = code)
**Target state** : `leg_code` has a single canonical definition; CLAUDE.md,
docstrings, tests and `planning.py` agree; security docs match v3.1.
**Principle** : in an ERP the documents *are* the product — identifier
drift eventually reaches a printed Bill of Lading.

### Theme D — Don't block the event loop, don't multiply queries
**Target state** : PDF generation off-loop; public catalogue ≤ 5 queries
per request. Small, surgical changes.

### Theme E — Shrink the repo, keep deps current
**Target state** : `Versions TOWT/` out of HEAD, lockfile for transitive
deps, CVE bumps applied (jinja2, cryptography).

### Explicitly NOT recommended now
- **Chasing 80 % coverage** — at 25.7 % real, the honest move is a low
  ratcheting gate plus targeted integration tests on auth/booking/portal;
  blanket coverage of 30 routers is poor ROI for a small team.
- **Refactoring large routers** (`modules_router.py` 825 l) — cohesive and
  readable; splitting is churn without payoff.
- **Replacing passlib immediately** — pinned `bcrypt==4.0.1` works; plan
  the migration, don't rush it.
- **WebAuthn implementation** — fix the docs instead, unless product wants
  it (Open Question #4).
- **Git history rewrite** to purge the 39 MiB pack — needs owner sign-off
  (breaks clones); removing the directory from HEAD is enough for DX.

### "Done" signals
1. 10 consecutive green CI runs on `main`; branch protection requires CI.
2. `pytest` green locally and in CI; coverage gate ≥ 25 % and ratcheting.
3. Zero Critical, zero High findings open from this report.
4. `ruff check` = 0 errors with FastAPI-tuned config, enforced by CI.
5. Staff login rate-limited (verifiable in `activity_logs`); a revoked
   session is unusable within ≤ 60 s.
6. `leg_code` asserted by tests that match CLAUDE.md.

---

## 5. Task Plan

### Milestone 0 — Safety net (unblock verification)

| # | Task | Files | Acceptance | Effort | Risk | Deps |
|---|------|-------|------------|--------|------|------|
| 0.1 | **Fix CI pip conflict** — move `safety` out of the shared install (own step with `pipx run safety` or pin `safety` compatible with pydantic 2.10; alternatively swap to `pip-audit`) | `requirements-dev.txt`, `.github/workflows/ci.yml` | all 3 jobs reach their main step | S | Low | — |
| 0.2 | **Mirror Postgres image** — `public.ecr.aws/docker/library/postgres:16-alpine` (or GHCR) in the service container | `ci.yml` | `test` job starts pytest | S | Low | — |
| 0.3 | **Honest quality gates** — `--cov-fail-under=25` (ratchet plan documented), ruff `extend-immutable-calls` for `Depends/Form/Cookie/File/Query`, mypy non-strict baseline | `pyproject.toml`, `ci.yml` | lint+test jobs green except real failures | S | Low | 0.1 |
| 0.4 | **Decide & fix `leg_code`** (after Open Question #1) — realign `planning.py:176-197`, tests, CLAUDE.md | `services/planning.py`, tests, CLAUDE.md | 2 red tests green; doc matches | M | **Med** (existing codes in DB/printed docs) | OQ#1 |
| 0.5 | Branch protection: require CI on `main` | GitHub settings | red CI blocks merge | S | Low | 0.1-0.3 |

### Milestone 1 — Critical & correctness fixes

| # | Task | Files | Acceptance | Effort | Risk | Deps |
|---|------|-------|------------|--------|------|------|
| 1.1 | **Rate-limit staff password login** (reuse `services/rate_limit`, 10/10 min per IP + per username) | `staff_auth_router.py:40-61` | 429 after threshold; `activity_logs` shows attempts | S | Low | — |
| 1.2 | **Server-side session revocation** — `session_epoch` int on `User`/`ClientAccount`, embed in cookie payload, compare in `get_current_staff/client`; bump on password change + new "logout all" | `auth.py`, `models/user.py`, migration, `admin_router.py` | old cookie rejected after epoch bump | M | Med (forces re-login on deploy) | 0.x |
| 1.3 | **Restore confirmation guards killed by CSP** — replace all `on*=` attributes with `data-confirm`/`data-autosubmit` + one external JS listener | ~10 templates (§3.4), `app/static/js/` | delete-leg & sign-SOF show confirm again; selects auto-submit | M | Low | — |
| 1.4 | CVE bumps: `jinja2==3.1.6`, `cryptography==44.0.1` | `requirements.txt` | CI green, app boots | S | Low | 0.1 |
| 1.5 | Logout as POST + CSRF (keep GET redirecting to a confirm page) | `staff_auth_router.py:191`, client equivalent | GET no longer ends session | S | Low | — |

### Milestone 2 — High-leverage improvements

| # | Task | Files | Acceptance | Effort | Risk | Deps |
|---|------|-------|------------|--------|------|------|
| 2.1 | **Integration-test harness** — conftest fixtures: testcontainers Postgres + `httpx.ASGITransport` client + factory helpers; first tests: staff/client login (incl. MFA + rate limit), RBAC 403s, booking happy path, portal token expiry | `tests/conftest.py`, new `tests/integration/` | ≥ 15 router tests green in CI | L | Low | 0.1-0.3 |
| 2.2 | Off-load PDF rendering to executor (and cap concurrency with a semaphore) | `services/pdf_generator.py:39-45` | event loop responsive during render (load test) | S | Low | — |
| 2.3 | Kill N+1s: batch-load ports (`IN` query / `selectinload`) in public catalogue + messages overview | `public_router.py:236-327`, `client_dashboard_router.py:159-163` | ≤ 5 queries per request (echo log) | S | Low | — |
| 2.4 | **Remove `Versions TOWT/` from HEAD** (archive branch `archive/v2-artifacts`), delete committed `.DS_Store`s; keep `newtowt-design-tokens.json` (referenced by CLAUDE.md) in `docs/design/` | repo root | clone of HEAD < 5 MB working tree of legacy files; CLAUDE.md updated | S | Low | OQ#2 |
| 2.5 | Lockfile: `uv pip compile` → `requirements.lock`, CI installs from it | requirements*, `ci.yml` | reproducible installs | M | Low | 0.1 |

### Milestone 3 — Quality & polish

| # | Task | Effort |
|---|------|--------|
| 3.1 | `ruff --fix` cleanup (F401 unused imports, I001, UP017) + B904 `raise … from` | S |
| 3.2 | Log swallowed exceptions: `captain_router.py:669-757`, `services/kpi.py:65` | S |
| 3.3 | Add missing `activity_record()`: `crew_router.py:335`, `tickets_router.py:218` | S |
| 3.4 | Docs sync: CLAUDE.md (WebAuthn→TOTP, leg_code), `docs/security/01` (Stripe) | S |
| 3.5 | Replace `time.sleep(2.1)` flake with clock injection (`test_auth.py:51`) | S |
| 3.6 | Migrate `@app.on_event` → lifespan; tidy `main.py:74` pathlib import | S |
| 3.7 | Narrow `--forwarded-allow-ips` to the compose subnet | S |
| 3.8 | Plan passlib → direct bcrypt/argon2 migration (design note only) | S |
| 3.9 | Equalize login timing (dummy hash verify when user unknown) | S |

### Quick wins (do immediately — all S, high impact)
0.1, 0.2, 0.3 (CI alive again) · 1.1 (staff rate limit) · 1.4 (CVE bumps)
· 2.3 (N+1 batch) · 3.2 (stop swallowing closure errors).

### Implementation sketches — top 3

**0.1 + 0.2 + 0.3 — Resurrect CI.**
Move `safety` into its own CI step that doesn't share the app venv
(`pipx run safety check -r requirements.txt` or replace with `pip-audit`,
which has no pydantic constraint); drop it from requirements-dev.txt.
Point the service container at `public.ecr.aws/docker/library/postgres:16-alpine`.
In pyproject: `fail_under = 25`; add
`[tool.ruff.lint.flake8-bugbear] extend-immutable-calls = ["fastapi.Depends","fastapi.Cookie","fastapi.Form","fastapi.File","fastapi.Query","fastapi.Header"]`;
set `strict = false` for mypy with a TODO ratchet. Gotchas: don't "fix"
the conflict by downgrading pydantic (app code uses 2.10 features); after
ruff config change run `ruff check` once locally — expect ~300 real
errors to triage into task 3.1; the 2 red planning tests still block the
test job until 0.4 — mark them `xfail(reason="leg_code spec pending OQ#1")`
*temporarily* so CI goes green without hiding the issue.

**1.1 + 1.2 — Session hardening.**
1.1: copy the exact pattern from `client_auth_router.py:72-99` into
`staff_login_submit` (scope `staff_login_ip`, plus a per-username scope to
stop distributed attempts); count failures only, reset on success.
1.2: add `session_epoch: Mapped[int] = mapped_column(default=0)` to `User`
and `ClientAccount` (one Alembic migration); include `"epoch"` in cookie
payload at `create_staff_session`/`create_client_session`; in
`get_current_staff/client` compare `payload.get("epoch", 0) !=
user.session_epoch → AuthExpired`. Bump epoch in change-password handler
and a new admin "force logout" action (with `activity_record`). Gotcha:
treat missing `epoch` in old cookies as 0 so the deploy doesn't log out
everyone *unless* you want that (single forced re-login is acceptable —
announce it).

**2.1 — Integration harness.**
`tests/integration/conftest.py`: session-scoped
`PostgresContainer("postgres:16-alpine")` → set `DATABASE_URL` env
*before* importing `app.config` (mirror existing unit conftest trick) →
`Base.metadata.create_all` once → function-scoped nested-transaction
session; app client via
`httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`.
First tests in priority order: staff login + cookie flags; RBAC matrix
spot-checks (marins POST → 403); CSRF reject/accept; booking wizard happy
path; portal token 410 after expiry. Gotchas: `settings` is an
`lru_cache` singleton resolved at import — env vars must be set before any
`app.*` import (same pattern as `tests/conftest.py:7-15`); CSRF middleware
requires fetching the cookie then echoing `x-csrf-token` in test POSTs —
write one helper and reuse.

---

## 6. Open Questions (need a human decision)

1. **`leg_code` canonical format** — spec/tests say `1CFRBR6`
   (`{seq}{vessel}{POL}{POD}{year}`), code produces `CAFRBR6`
   (`{vessel}{letter}…`). Which is correct? Are codes of the current
   format already on printed/issued documents or stored in production
   rows (constrains migration)?
2. **`Versions TOWT/` purge depth** — remove from HEAD only (history keeps
   39 MiB pack) or rewrite history (smaller clones, but breaks existing
   clones/forks)?
3. **MFA policy for sea roles** — `marins` get 14-day sessions *without*
   mandatory MFA. Accepted risk (satcom constraints are documented in
   `auth.py:33-36`) or should MFA become mandatory for all staff?
4. **WebAuthn** — CLAUDE.md promises it; only TOTP exists. Implement or
   correct the docs?
5. **Coverage ambition** — is the 80 % aspiration real? Proposed: ratchet
   25 → 40 → 55 % focused on auth/booking/portal, revisit after M2.
6. **Performance targets** — expected peak traffic on the public
   catalogue? Determines whether task 2.3 needs caching on top of query
   batching.

---

*Lighter-review areas: `app/i18n/` catalogues, `app/schemas/`, individual
Jinja page templates beyond security-relevant grep sweeps, and
`docs/strategy|personas` content — none load-bearing for the findings
above.*
