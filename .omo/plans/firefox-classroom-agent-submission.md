# Plan: myITS Classroom agent-submission via the user's Firefox

## Context

- Target: `classroom.its.ac.id` (Moodle 4.x, Edwiser RemUI theme, PHP 8.3).
- Auth: myITS SSO -> Microsoft Authenticator 2FA. 2FA is human-only, once per
  session lifetime (~8h idle; activity keeps it alive).
- Probe results (2026-10-24): `/webservice/rest/server.php` enabled but no
  user-mintable token (mobile app launcher 404; `login/token.php` useless for
  SSO accounts and we refuse to store SSO passwords). `/r.php/api` is a 404.
  => No token API; the browser's own session is the credential.
- User browser: **Firefox** (Chrome CDP attach is out).
- Existing code (from CLI-first iteration, needs rework): `src/classroom_api/`
  has `errors.py`, `state.py`, `client.py` (httpx+cookie file), `assign.py`,
  `submitter.py`, `service.py`, `cli.py`, `mcp_server.py`. The cookie-file
  state approach is being replaced by the browser-anchored design below.

## Decision (confirmed by user)

**Option A: dedicated Firefox profile.**
Playwright `firefox.launch_persistent_context(".firefox-profile")`; user logs
in once inside that window (normal SSO + Authenticator push). All agent
traffic runs *in page context* (`page.evaluate(fetch(...))`) so the browser's
own cookies are used - nothing copied to disk, nothing printed. Firefox
profile dir is gitignored.

Fallback (rejected for now): Option B (WebDriver BiDi via `-marionette`) -
same architecture behind `browser.py`, different transport.

## Architecture (Option A)

```
browser.py   FirefoxSession: launch/attach persistent profile; ensure page on
             classroom.its.ac.id; read sesskey/userId from live window.M.cfg;
             ajax(method,args) -> in-page fetch to /lib/ajax/service.php with
             one sesskey-refresh retry; fetch_html(path) in page context.
assign.py    list_courses / list_assignments / submission_status via ajax()
             (core_course_get_enrolled_courses_by_timeline_classification,
             mod_assign_get_assignments, mod_assign_get_submission_status),
             HTML-scrape fallbacks. WIB (UTC+7) due dates.
submitter.py UI-driven submit on /mod/assign/view.php?id=CMID:
             Add submission -> fill editor / set input[type=file] -> Save ->
             optional Submit+checkbox panel -> verify via submission_status.
service.py   login_/status/courses/assignments/submit/verify orchestration.
cli.py       thin argparse wrapper (status/courses/assignments/submit/verify;
             `login` opens the persistent-profile window for the human).
mcp_server.py FastMCP ("classroom") tools: classroom_status/login/courses/
             assignments/submit/verify. stdio; errors returned as typed
             payloads, never cookies.
```

Error model: `BrowserNotConnected` (profile launch failed / port issues),
`LoginRequired` (M.cfg.userId==0 -> human logs in inside the tool window),
`MoodleError`, `SubmissionError`, `VerificationError`, `TranscriptNotFound`.

## Phases

1. **Transport rework** - delete `state.py`, `client.py`; write `browser.py`
   per above; add `.firefox-profile/` + `state/` to `.gitignore`.
2. **Data layer** - rewrite `assign.py` + `submitter.py` onto
   FirefoxSession/page-context calls (no httpx, no cookie files).
3. **Surfaces** - rewrite `service.py`, `cli.py`, `mcp_server.py` around the
   session; pyproject: drop httpx, pin `mcp<2` (v2 renamed FastMCP; smoke test
   failed on that), keep playwright + bs4.
4. **Verify** - compileall; CLI no-session error path; MCP stdio smoke
   (initialize -> tools/list -> status) after `pip install "mcp<2"`;
   opencode.json project config (mcp entry, `type: local`,
   `command: ["<abs>/.venv/bin/classroom-mcp"]`, `$schema` declared) + restart
   reminder.
5. **Live calibration (needs user)** - first real run: user logs in once in
   the tool's Firefox window; verify `courses`/`assignments` JSON; one
   low-stakes `submit` (draft or real, user's call) to calibrate selectors
   (Add submission / editor / filemanager inputs are theme-dependent and
   currently best-effort).

## Verification criteria

- `classroom status` with Firefox closed: clean `browser_not_connected` JSON,
  exit != 0, no traceback.
- After one human login in the tool profile: `courses` returns real enrolled
  courses; `assignments --course X` returns cmids with WIB due dates;
  `verify` matches what the site shows in the browser.
- MCP smoke handshake passes; tools appear in a restarted OpenCode session.

## Risks / notes

- Submission selectors are uncalibrated until a live run; `submit --headed`
  exists for that. Server-side verify is authoritative; we never trust the
  page alone.
- RemUI theme may rename buttons; fallbacks scrape by text (EN/ID labels).
- Compliance: automating one's own account should pass ITS acceptable-use
  review; the user owns that check. Agent only ever submits what the user
  (or their own judgment) provides - academic-integrity rules still apply.

## CLI runs (manual; no scheduler in scope)

The CLI (`classroom status|courses|assignments|submit|verify`) is the
manual-use surface; it runs the same engine headless and spawns a
short-lived Firefox per invocation (no window unless `--headed`/login).
- **Profile lock**: if the tool's Firefox profile is open in another process,
  `launch_persistent_context` fails immediately - detect and return a typed
  `profile_busy` error (never a traceback).
- Listing does not keep the session warm by itself; if the ~8h idle timeout
  expires, commands return `login_required` JSON (exit 2) and the user just
  re-logs in inside the tool's Firefox window - the ONLY human-in-loop event.

## TODOs

- [x] 1. Transport rework — delete `state.py` + `client.py`; write `browser.py` (FirefoxSession: persistent profile, in-page sesskey/AJAX/fetch_html, `profile_busy` detection); extend `errors.py` (`BrowserNotConnected`, `MoodleError`, `profile_busy` payload); gitignore `.firefox-profile/` + `state/`. (Recommended task executor category: deep-low)
- [x] 2. Data layer — rewrite `assign.py` + `submitter.py` onto FirefoxSession page-context calls; no httpx, no cookie files; WIB due dates; EN/ID selector fallbacks. (Recommended task executor category: deep-low)
- [x] 3. Surfaces — rewrite `service.py`, `cli.py`, `mcp_server.py` around FirefoxSession; pyproject: drop httpx, pin `mcp<2`, keep playwright+bs4. (Recommended task executor category: deep-low)
- [x] 4. Verify — compileall; CLI no-session error path (clean JSON, exit != 0); MCP stdio smoke (initialize → tools/list → status); write project `opencode.json` mcp entry. (Recommended task executor category: quick)
- [ ] 5. Live calibration (needs user present) — user logs in once in tool's Firefox window; verify courses/assignments JSON; one low-stakes submit to calibrate selectors, performed ONLY on a past, finished course the user explicitly designates as safe (never an active course). (Recommended task executor category: deep-low)

## Final Verification Wave

- [x] F1. Full automated gate: `python3 -m compileall src/classroom_api`, CLI `classroom status`/`courses` error paths emit typed JSON with nonzero exit, MCP stdio handshake lists all 6 tools, venv installs with `mcp<2` + no httpx. (Recommended task executor category: quick)
- [x] F2. Reviewer audit: architecture matches plan (no cookie files, no httpx, in-page fetch only, typed errors everywhere, no secrets printed), code quality, and missed-context check. (Recommended task executor category: unspecified-high)

## Execution

Implementation starts via `/ulw-execute` in a worker session; this session
stays in plan mode.
