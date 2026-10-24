# Learnings — firefox-classroom-agent-submission

Conventions, patterns, and successful approaches discovered during work on this plan.

_Auto-scaffolded by /ulw-execute. Append new entries below - never overwrite._

---
# Learnings

## 2026-10-24 Session: ses_e91979192ffeqhQUCbMFaagyxy
- classroom.its.ac.id: Moodle 4.x + Edwiser RemUI, PHP 8.3. M.cfg.sesskey/userId available in page JS. sessiontimeout=28800 (8h idle).
- /webservice/rest/server.php enabled but no user-mintable token path (mobile launcher 404). login/token.php useless for SSO accounts.
- mcp SDK v2 renamed FastMCP -> pin mcp<2. Python 3.14 system.
- User browser: Firefox; dedicated Playwright persistent profile at .firefox-profile/ (gitignored).

## 2026-10-24 Worker 3 (Surfaces)
- cli.py exit codes now resolved via errorcode-string map (login_required/stale_state=2, browser_not_connected/profile_busy=3) with exc.exit_code() fallback - immune to worker 1's concurrent errors.py rework; no hard class imports.
- service.py: every fn opens browser.connect() and closes in finally; login polls user_id() != 0 for 600s (2s interval); human instruction printed to STDERR so stdout stays pure JSON (MCP stdio uses stdout).
- mcp_server.py needed NO changes - existing file already matched the pinned v1 FastMCP shape exactly.
- browser.py does not exist yet at time of writing (worker 1 concurrent); service.py imports it - compileall passes (import not executed); runtime lands once worker 1 merges.

## 2026-10-24 Worker 1 (Transport)
- browser.py landed: BASE_URL module constant; FirefoxSession(page, browser=BrowserContext, _pw) with sesskey()/user_id()/ajax()/fetch_html()/close(); connect(headed, profile_dir) lazily imports playwright.async_api so module imports fine without playwright installed.
- errors.py: BrowserNotConnected(message, errorcode=None, hint=None) with instance-level errorcode override + exit_code 3; MoodleError exit 1. All legacy classes (incl. StaleState/DatabaseError) kept intact — old auth.py still imports them.
- ajax() batch-entry error handling: message mentioning sesskey/session -> clear cache, retry ONCE; otherwise MoodleError immediately. HTTP>=400/null body -> MoodleError.
- user_id() RAISES LoginRequired on 0/None (per pinned contract; service.login() poll fix is another dispatch — see issues.md).
- auth.py, assign.py, submitter.py still import .state/.client (now dangling at runtime); compileall passes because imports are not executed. Those files are owned by other workers.
- 2026-10-24 Worker 1 fix: Moodle /lib/ajax/service.php responds with the batch entries as a TOP-LEVEL JSON ARRAY (res.json() -> [entry]), not a dict; _AJAX_JS result["body"] is therefore a list and must not be .get()-ed. _ajax_once now normalizes: list -> entries directly, dict-with-"body" (defensive) -> .get("body"). Also corrected ajax() to return body[0].data per pinned contract (was returning the whole batch entry).

## 2026-10-24 Worker 2 (Data layer rewrite)
- assign.py rewritten onto FirefoxSession: ajax() method-name strings inline (no client.py constants); list_courses falls back to /my/ regex scrape via fetch_html; list_assignments falls back to core_course_get_contents (due None there); submission_status maps lastattempt.submission.status ("submitted"->submitted, "draft"->draft, "new"/absent->not_submitted, other->unknown), falls back to view.php text match.
- submitter.py rewritten onto s.page (session's own page, no storage_state, no chromium); validation before any page action; final verification via assign.submission_status per contract. Kept asyncio.sleep(1.5) post-set_input_files.
- auth.py DELETED after grep confirmed zero imports (only mcp_server docstring mention of "verify" matched, unrelated).
- compileall gate passes; basedpyright not installed (user declined) so LSP diagnostics unavailable.
- KNOWN BUG (other dispatch): service.login() line 61 calls session.user_id() expecting 0 return, but user_id() raises LoginRequired on 0 -> login() crashes instead of polling. Do not fix here.
- login() poll loop: user_id() raises LoginRequired when not logged in; now caught in the poll and treated as 0 until deadline, per browser.py contract.

## 2026-10-25 Worker (Verify, checkbox 4)
- Clean install: `python3 -m venv .venv && .venv/bin/pip install -e .` OK (mcp 1.30.0, playwright 1.64.1, bs4 4.15.0; no httpx dep). `.venv/bin/playwright install firefox` OK (browser + ffmpeg to ~/.cache/ms-playwright).
- CLI error paths: `.venv/bin/classroom status` and `courses` both print single clean JSON {"error":true,"errorcode":"login_required",message,hint}, exit code 2, no traceback.
- MCP stdio smoke (/tmp/opencode/mcp_smoke.py): initialize (2024-11-05) -> notifications/initialized -> tools/list -> tools/call classroom_status. All 6 tools present: classroom_assignments, classroom_courses, classroom_login, classroom_status, classroom_submit, classroom_verify. Status call returns isError:false with structuredContent = typed JSON error {"error":true,"errorcode":"login_required",...}. SMOKE_OK.
- GOTCHA: notifications/initialized must NOT be sent via a request/response helper — it has no response and readline() blocks forever (first smoke run timed out on this).
- Wrote project opencode.json: mcp.classroom local, command [.venv/bin/classroom-mcp], $schema preserved (no pre-existing file). compileall + JSON parse both OK.

## F2 Reviewer audit (2026-10-25) — PASS
- Architecture verified: only 9 expected modules in src/classroom_api (state.py + client.py deleted); persistent Firefox profile is the sole credential store; all Moodle HTTP via page.evaluate fetch (browser.py:32-61,145,168); no storage_state/cookie-file/MoodleSession reads anywhere (grep: docstring mentions only).
- pyproject deps: playwright, beautifulsoup4, mcp>=1.9,<2 — no httpx.
- Typed errors: all user-facing failures raise ClassroomError subclasses; CLI (cli.py:80-83) prints as_payload() JSON + mapped exit codes; MCP tools (mcp_server.py) funnel ClassroomError into as_payload() — never raw tracebacks.
- Secrets: single print() (service.py:52) is login instructions to stderr; no cookies/sesskey/profile contents ever emitted.
- login() poll loop (service.py:59-69) catches LoginRequired, keeps polling to 600s deadline. profile_busy detection present (browser.py:192-229). WIB TZ (assign.py:17). EN/ID labels (submitter.py:21-24). Server-side verify after submit (submitter.py:101-103). Empty-submission refusal (service.py:117-118, submitter.py:47-48). File-existence check (submitter.py:49-51).
- opencode.json: $schema declared, mcp.classroom type local, command array -> .venv/bin/classroom-mcp. .gitignore covers .firefox-profile/ + state/.
- compileall passes (python 3.14). No TODO/FIXME/HACK; bare excepts are documented best-effort (goto tolerate browser.py:79-86, note fill, checkbox tick, waits).
- Minor notes (non-blocking): errors.py StaleState/DatabaseError unused legacy (accepted gap); browser.py profile-busy heuristic marker "profile" is broad but safe-side.
