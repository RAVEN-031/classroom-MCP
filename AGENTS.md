# AGENTS.md

myITS Classroom (Moodle, `classroom.its.ac.id`) automation for AI agents: a CLI + MCP server that drive a **real Playwright Firefox** with a persistent profile. There is no httpx and no cookie files — the browser's own cookies are the only credential store, and nothing may ever be copied out of them.

## Commands (`.venv` layout, Python >= 3.11)

```bash
.venv/bin/pip install -e .          # setup (playwright + bs4 + mcp pin <2)
playwright install firefox          # required once; browser binary
.venv/bin/python -m compileall -q src   # THE check — there is NO test suite
.venv/bin/classroom status          # clean error path check: not-logged-in => login_required payload, exit 2
```

Entry points (pyproject scripts): `classroom` = `classroom_api.cli:main`, `classroom-mcp` = `classroom_api.mcp_server:main` (stdio FastMCP). Run MCP smoke via the CLI instead — fewer moving parts.

## Flow & CLI contract

- `cli.py` / `mcp_server.py` are thin shells → `service.py` opens a short-lived `FirefoxSession` (`browser.connect()`), `try/finally` closes it → `assign.py` (reads) / `submitter.py` (drives the mod_assign UI).
- Every successful command = **one JSON document on stdout**. Errors = `ClassroomError.as_payload()` JSON; exit codes: 0 ok, 1 runtime, 2 login_required, 3 profile/browser problem (map lives in `cli.py` `_EXIT_BY_ERRORCODE` — keep it in sync with `errors.py` errorcodes).
- Never print cookies, sesskey, or profile contents — this constraint is load-bearing for the whole design.

## Known live bugs (verified on the real site — fix before trusting ajax())

- **P0**: `browser.py` `_ajax_once` builds the WS batch with key `"method"`; Moodle 5 requires **`"methodname"`**. Until fixed, EVERY `ajax()` call fails and is masked as "empty batch" (dict payloads have no `body`). Also raise the underlying error when the payload is a dict containing `"error"` instead of reporting an empty batch. (Scheduled as checkbox 1 of the materials plan below; a live AJAX temp-script monkeypatch that renames the key is the proven workaround meanwhile.)
- **WS whitelist is tiny**: only `core_course_get_enrolled_courses_by_timeline_classification` is confirmed enabled. `mod_assign_get_assignments` and `core_webservice_get_site_info` are **disabled** (`servicenotavailable`) — the data layer MUST keep HTML-scrape fallbacks working, not treat them as dead code.
- **Materials surface exists** (`materials.py`; CLI `materials`/`download`; MCP `classroom_materials`/`classroom_download`): `core_course_get_contents` is **disabled** on this deployment (`servicenotavailable`, proven live 2026-10-26 across 6 courses) — the `/course/view.php` HTML scrape in `materials.py` is the live listing path, and every returned dict carries `source: "webservice"|"html"` so callers can tell which path answered. Per-file `contents[]`/fileurl listings are WS-only data and thus unavailable: downloads need a URL from the course page or another source.
- Timeline `classification: "inprogress"` hides PAST courses; use `"past"` when assignments from old coursework are needed.
- `service.login()` poll loop: `session.user_id()` **raises** `LoginRequired` when anonymous, it does not return 0 — catch it inside the loop, and catch other exceptions too (SSO redirect navigation races throw transient errors).
- Login persistence bug (reproduced 2x): cookies from a headed login window don't reach `.firefox-profile/cookies.sqlite`; the next headless connect is anonymous. Working workaround: keep ONE headed session for the whole operation instead of reconnecting.
- SSO auto-click rule: when a session is anonymous, ALWAYS try programmatically CLICKING the login/SSO button first (structural selectors — `button[type=submit]`, `#loginsubmit`, anchors to `/login/` or `*sso*`; text labels "Log in"/"Login"/"MASUK"/"Masuk" as fallback) before falling back to a human-driven SSO login. Click-only — never fill username/password fields. A once-logged-in profile often re-authenticates on a single click; if the SSO provider session is dead (observed 2026-10-26: click fired, no re-auth in 90s), then show the human banner and poll for the Microsoft push.

## Profile hygiene (destructive if done wrong)

- `.firefox-profile/` is the credential store (gitignored). `rm -rf .firefox-profile` = full reset → one human re-login needed first.
- On `profile_busy`: kill only `ms-playwright` Firefox PIDs — **never** `pkill -f firefox` (kills the user's own browser). Remove `.firefox-profile/.parentlock` and the `lock` symlink with `os.remove` unconditionally: `os.path.exists()` lies on dangling symlinks.

## RemUI submit flow (calibrated live on a real submission)

- Existing submission → the button is **"Edit submission"**, not "Add submission".
- The file `input[type="file"]` does **not exist** until you click "Add…" in Moodle's filemanager; then click **"Upload this file"** to confirm the upload inside the dialog.
- Save via **`#id_submitbutton`** — text-label matching (role="button", name="Save changes") silently fails on RemUI; prefer structural ids (`#id_submitbutton`, `#id_cancel`) over bilingual label lists.
- Editing an already-submitted assignment keeps status `submitted` — no extra confirm step; always verify server-side (`submission_status`, not page text).
- Submitting needs at least one file and/or text — `service.submit` and `submitter.submit` both refuse empty payloads by design; don't "relax" that check.
- All due dates are WIB (`Asia/Jakarta`, UTC+7) ISO strings — don't convert to UTC.

## Repo-local wiring

- `opencode.json` registers the MCP server via an **absolute** path (`.venv/bin/classroom-mcp`). It breaks on rename/move; update the path whenever the directory changes — it has already bitten once.
- `.omo/` holds plan/notepad/ledger state from ulw-execute runs; it is session bookkeeping, not product code.
- **Materials plan (executed 2026-10-26, including the live probe)**: `.omo/plans/materials-surface.md` added the materials/files/links surface (`materials.py`, CLI `materials`/`download`, MCP `classroom_materials`/`classroom_download`) and fixed the P0 AJAX bug above. The probe proved `core_course_get_contents` is **disabled** (`servicenotavailable`, 2026-10-26, all 6 courses); the `/course/view.php` HTML scrape in `materials.py` is the path that answers in practice.
- README.md covers user-facing setup/usage; keep it in sync when the tool surface changes. No tests and no CI yet — `compileall` is the check. No LICENSE file yet; `pyproject.toml` (name `classroom-api`) is the publishing anchor.
