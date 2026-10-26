# classroom-api

AI-agent-friendly CLI and MCP server for **myITS Classroom** (`classroom.its.ac.id`, Moodle 5) — view courses, list assignments, submit work, and verify submissions verifiably.

This is **not a REST scraper**. It drives a real Playwright Firefox against the live web UI, so it works exactly the way you do: one real logged-in browser session, no passwords stored anywhere, no cookies copied out of the browser.

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Setup](#setup)
- [First login](#first-login)
- [CLI usage](#cli-usage)
- [Using it from AI agents (MCP)](#using-it-from-ai-agents-mcp)
- [Safety design](#safety-design)
- [Current limitations](#current-limitations)

## How it works

```
CLI / MCP tool call
        │
        ▼
service.py            one short-lived FirefoxSession per call, closed afterwards
        │
        ▼
assign.py             course/assignment data (Moodle AJAX, HTML-scrape fallbacks)
submitter.py          drives the real mod_assign submission form
materials.py          course materials/files/links + Moodle file downloads
        │
        ▼
browser.py            persistent Firefox profile (.firefox-profile/) — the ONLY credential store
```

All dates (due dates, submission times) are **WIB (`Asia/Jakarta`, UTC+7)**.

## Requirements

- Linux (the profile-lock cleanup paths assume it) — macOS mostly works
- Python **>= 3.11**
- A myITS (Moodle) account with SSO + Microsoft Authenticator push access
- You, present at the keyboard — **once**, for the login

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install firefox
```

## First login

Login is the only step that needs a human: a **visible Firefox window** opens on `classroom.its.ac.id`, and you complete the myITS SSO and approve the Microsoft Authenticator push yourself. The tool waits up to 10 minutes and polls for success.

Before asking you for anything, login tries pressing the SSO/login button on its own. If the SSO provider session is still alive, you're in with zero interaction. If it's dead, you'll be asked to approve the Microsoft push as before (the window notes the click was already attempted).

```bash
.venv/bin/classroom login          # browser opens; complete SSO + Microsoft 2FA there
```

Something went wrong if it exits with code 2/3 and a JSON `error` payload — see [Troubleshooting](#troubleshooting).

## CLI usage

Every command prints **one JSON document** on success. Exit codes: `0` ok · `1` runtime error · `2` login required (run `classroom login`) · `3` browser/profile problem.

```bash
# session check — run this first; if it says login_required, run `classroom login`
.venv/bin/classroom status

# everything you're enrolled in
.venv/bin/classroom courses

# assignments — everything, or one course, or a deadline window (days, WIB)
.venv/bin/classroom assignments
.venv/bin/classroom assignments --course 7966
.venv/bin/classroom assignments --due-within 7

# submit: files and/or online text (: needs the assignment's cmid from listings)
.venv/bin/classroom submit --assignment 298204 --file ./report.pdf
.venv/bin/classroom submit --assignment 298204 --file ./a.png --file ./b.png
.venv/bin/classroom submit --assignment 298204 --text "Answers below..." --note "For the grader"

# course materials — files, folders, and links for one course
.venv/bin/classroom materials --course 7966

# download a Moodle file URL (fileurl from listings) to a local path
.venv/bin/classroom download --url <pluginfile-url> --out ./file.pdf   # add --overwrite to replace an existing file

# authoritative server-side status (submitted / draft / not_submitted)
.venv/bin/classroom verify --assignment 298204
```

When the web service is unavailable, `materials` falls back to HTML scraping and enriches the listing by fetching each resource/folder module page, so per-file `fileurl`s appear whenever those pages expose them — pipe one into `download --url` and it works end-to-end. Per-file sizes may show as `filesize: null` because the HTML path can't always see them.

`submit --headed` lets you watch the run in a visible browser. `login --headless` exists but is not recommended — the human login needs a window.

## Using it from AI agents (MCP)

The MCP server (`classroom-mcp`) is the primary surface for agents — no shell needed. Register it in `opencode.json` (or any MCP client that speaks stdio):

```json
{
  "mcp": {
    "classroom": {
      "type": "local",
      "enabled": true,
      "command": ["/absolute/path/to/.venv/bin/classroom-mcp"]
    }
  }
}
```

Exposed tools:

| Tool | Purpose |
|---|---|
| `classroom_status` | check session validity (call first) |
| `classroom_login` | opens the headed login window — **tell the human to be ready** |
| `classroom_courses` | enrolled courses with ids and URLs |
| `classroom_assignments` | listings, optional course id / due-within window |
| `classroom_submit` | files and/or text; verifies server-side before returning ok |
| `classroom_verify` | authoritative submission state |
| `classroom_materials` | course materials (files/folders/links) with download URLs |
| `classroom_download` | download a Moodle file URL to a local path |

Agents should be coachable on exactly one rule: when a tool returns `login_required`, ask the human to run `classroom login` (or call `classroom_login`) and wait — never try to authenticate programmatically.

## Safety design

- **Credentials never leave the browser.** The persistent Firefox profile is the only credential store; nothing reads cookies from it, nothing prints sesskeys, and no tool output contains them.
- **Every submit is verified server-side** (`classroom_verify`-style check) before a tool reports success — a green result means Moodle actually has the submission, not just that the page looked right.
- **Empty submissions are refused.** Files and/or text are required; nothing is sent "just to try".
- **The submission-page flow never trusts page text alone** — labels are bilingual-proofed (EN/ID) and the server is asked for the final state.

## Current limitations

- **One login session (~8h idle)**; activity keeps it alive, but a long idle means repeating the [first login](#first-login).
- Materials listings use the Moodle web service (`core_course_get_contents`) when it's enabled, with an automatic HTML-scrape fallback; the HTML path enriches its listing from module pages, but per-file sizes may be missing (`filesize: null`).
- Moodle's webservice whitelist on this deployment is narrow, so listings fall back to HTML scraping in places — expect the same data but a slightly slower call on some courses.
- Frankly tuned for the myITS RemUI theme; other Moodle themes may shift button labels (the bilingual label lists cover EN/ID).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `profile_busy` payload (exit 3) | A Firefox on this profile is already running or crashed. Close it; if it persists, remove `.firefox-profile/.parentlock` and the `lock` symlink. |
| `login_required` payload (exit 2) | Run `classroom login` (needs you at the keyboard). |
| Login timed out (10 min) | Run login again while keeping the Firefox window focused; complete the Authenticator push in time. |
| Everything anonymous after a fresh login | Session persistence occasionally fails: redo `classroom login`, or work in one headed session (`submit --headed`) for the whole task. |

## Development

See [AGENTS.md](AGENTS.md) — there is no test suite yet; `python -m compileall -q src` is the build check, and `AGENTS.md` documents the live-calibrated Moodle/RemUI gotchas (webservice quirks, selector flow, profile-lock hygiene).
