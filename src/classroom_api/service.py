"""Service orchestration shared by the CLI and the MCP server.

Every function opens a short-lived FirefoxSession via ``browser.connect()``,
closes it in ``finally``, and returns JSON-serializable dicts or raises a
``ClassroomError`` subclass (agent-readable via ``as_payload()``). The
browser's own cookies are the credential - nothing is ever copied out or
printed.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from typing import Any

from .assign import filter_due_window, list_assignments, list_courses, submission_status
from .browser import BASE_URL, connect
from .errors import ClassroomError, LoginRequired, SubmissionError
from .materials import download as materials_download
from .materials import list_materials

_LOGIN_TIMEOUT_SECONDS = 600.0
_LOGIN_POLL_INTERVAL = 2.0
_AUTOCLICK_POLL_SECONDS = 90.0

# The myITS SSO entry: identity-provider button on the Moodle login page
# (bare icon link, no text; proven live 2026-10-25). Navigating to the
# Moodle-hosted /auth/oidc/ endpoint is equivalent to clicking it.
_SSO_IDP_SELECTOR = "a.login-identityprovider-btn"
_SSO_IDP_PATH = "/auth/oidc/?source=loginpage"

# Generic login click candidates, LAST RESORT only. Structural selectors
# first (RemUI: structural beats text); bilingual text labels after that.
_LOGIN_STRUCTURAL_SELECTORS = (
    "button[type=submit]",
    "#loginsubmit",
    'a[href*="/login/"]',
    'a[href*="sso"]',
)
_LOGIN_TEXT_LABELS = ("Log in", "Login", "MASUK", "Masuk")


async def _probe_user_id(session: Any) -> int:
    """user_id() that never raises: 0 when anonymous or transiently broken.

    ``user_id()`` RAISES ``LoginRequired`` when anonymous (it never returns
    0 itself); SSO redirect races and a closed browser window also throw,
    and all of those mean "not logged in (yet)" for the poll loops.
    """
    try:
        return await session.user_id()
    except Exception:
        return 0


def _browser_closed(session: Any) -> bool:
    try:
        return session.page.is_closed()
    except Exception:
        return True


def _is_target_closed_error(exc: BaseException) -> bool:
    if type(exc).__name__ == "TargetClosedError":
        return True
    text = str(exc).lower()
    return "target closed" in text or "browser has been closed" in text


async def _sso_autoclick(session: Any) -> bool:
    """Click-only attempt to (re)start myITS SSO from the login page.

    The primary target is the identity-provider button
    (``a.login-identityprovider-btn``, "Masuk dengan myITS"); if the anchor
    is missing or invisible, navigating to the Moodle-hosted
    ``/auth/oidc/?source=loginpage`` entry is the equivalent fallback.
    Generic login buttons/links are demoted to last resort. Ensures the
    page sits on the Moodle login entry point (never navigating away from
    an external SSO provider mid-flow). NEVER fills username/password
    fields. Every step is individually bounded in try/except - a missing
    button must never crash the login flow. Returns True when a click or
    the equivalent navigation landed.
    """
    page = session.page
    url = page.url or ""
    # Only re-navigate when we are on (or blank off) the Moodle site; never
    # yank the page away from a half-finished SSO/login flow.
    on_moodle = url.startswith(BASE_URL) or url.startswith("about:")
    if on_moodle and "/login/" not in url and "/auth/" not in url:
        try:
            await page.goto(BASE_URL + "/login/index.php", wait_until="domcontentloaded")
        except Exception:
            pass  # stay wherever we are; selectors below may still match
    # 1. The myITS identity-provider button itself.
    try:
        locator = page.locator(_SSO_IDP_SELECTOR).first
        if await locator.is_visible() and await locator.is_enabled():
            await locator.click(timeout=5000)
            return True
    except Exception:
        pass
    # 2. Equivalent: goto the Moodle-hosted /auth/oidc/ SSO entry.
    if on_moodle and "/auth/" not in (page.url or ""):
        try:
            await page.goto(BASE_URL + _SSO_IDP_PATH, wait_until="domcontentloaded")
            return True
        except Exception:
            pass
    # 3. Last resort: generic login buttons/links.
    for selector in _LOGIN_STRUCTURAL_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible() and await locator.is_enabled():
                await locator.click(timeout=5000)
                return True
        except Exception:
            continue
    for label in _LOGIN_TEXT_LABELS:
        for role in ("button", "link"):
            try:
                locator = page.get_by_role(role, name=label).first
                if await locator.is_visible() and await locator.is_enabled():
                    await locator.click(timeout=5000)
                    return True
            except Exception:
                continue
    return False


async def _fullname(session: Any) -> str:
    """Best-effort fullname scrape from /my/; may return ""."""
    try:
        html = await session.fetch_html("/my/")
    except ClassroomError:
        return ""
    match = re.search(r'"fullname"\s*:\s*"([^"]{1,200})"', html)
    if match is None:
        match = re.search(r'class="usertext"[^>]*>\s*([^<]{1,200})\s*<', html)
    if match is None:
        return ""
    return match.group(1).strip()


async def login(headed: bool = True) -> dict[str, Any]:
    """Open the persistent-profile browser and log in.

    Order of attack: (1) if the profile session is still alive, return
    immediately with no banner; (2) otherwise attempt a click-only SSO
    re-authentication via the myITS identity-provider button
    ("Masuk dengan myITS"; goto of the Moodle-hosted /auth/oidc/ entry if
    the anchor is missing - never any username/password autofill) and poll
    ``user_id()`` for ``_AUTOCLICK_POLL_SECONDS``; (3) if that fails
    (ceiling: the SSO provider session itself is dead, so the click cannot
    re-authenticate and one human Microsoft Authenticator push is
    unavoidable), print the human banner and poll up to
    ``_LOGIN_TIMEOUT_SECONDS`` while the human completes myITS SSO inside
    the tool's own Firefox window. If the window/browser is closed early,
    a clean ``ClassroomError`` is raised instead of a raw Playwright
    traceback.
    """
    session = await connect(headed=headed)
    try:
        user_id = await _probe_user_id(session)
        if user_id:
            return {
                "ok": True,
                "message": "Already logged in.",
                "user": {"id": user_id, "fullname": await _fullname(session)},
            }
        autoclick_note = ""
        if await _sso_autoclick(session):
            deadline = time.monotonic() + _AUTOCLICK_POLL_SECONDS
            while not user_id and time.monotonic() < deadline:
                await asyncio.sleep(_LOGIN_POLL_INTERVAL)
                user_id = await _probe_user_id(session)
                if not user_id and _browser_closed(session):
                    raise ClassroomError("Browser window was closed before login completed.")
            if user_id:
                return {
                    "ok": True,
                    "message": "Login complete via SSO button click; no human step needed.",
                    "user": {"id": user_id, "fullname": await _fullname(session)},
                }
            autoclick_note = "\n(Automatic SSO button click was already attempted and did not re-authenticate.)"
        print(
            "A Firefox window is open on classroom.its.ac.id.\n"
            "Complete myITS SSO and approve the Microsoft Authenticator push there."
            f"{autoclick_note}\n"
            f"Waiting up to {_LOGIN_TIMEOUT_SECONDS:.0f}s ...",
            file=sys.stderr,
            flush=True,
        )
        deadline = time.monotonic() + _LOGIN_TIMEOUT_SECONDS
        while not user_id:
            if time.monotonic() >= deadline:
                raise LoginRequired("Timed out waiting for human login in the browser window.")
            await asyncio.sleep(_LOGIN_POLL_INTERVAL)
            user_id = await _probe_user_id(session)
            if not user_id and _browser_closed(session):
                raise ClassroomError("Browser window was closed before login completed.")
        fullname = await _fullname(session)
        return {
            "ok": True,
            "message": "Login complete. The session lives ~8h idle; activity keeps it alive.",
            "user": {"id": user_id, "fullname": fullname},
        }
    except ClassroomError:
        raise
    except Exception as exc:
        if _is_target_closed_error(exc):
            raise ClassroomError("Browser window was closed before login completed.") from exc
        raise
    finally:
        await session.close()


async def _ensure_session(session: Any, timeout: float = 20.0) -> int:
    """Ensure session is logged in, auto-reauthenticating via SSO click if needed."""
    user_id = await _probe_user_id(session)
    if user_id:
        return user_id
    if await _sso_autoclick(session):
        deadline = time.monotonic() + timeout
        while not user_id and time.monotonic() < deadline:
            await asyncio.sleep(_LOGIN_POLL_INTERVAL)
            user_id = await _probe_user_id(session)
            if not user_id and _browser_closed(session):
                raise ClassroomError("Browser window was closed before login completed.")
        if user_id:
            return user_id
    raise LoginRequired("M.cfg.userId is 0: not logged in.")


async def status() -> dict[str, Any]:
    session = await connect()
    try:
        user_id = await _ensure_session(session, timeout=15.0)
        return {"ok": True, "logged_in": True, "user": {"id": user_id, "fullname": await _fullname(session)}}
    finally:
        await session.close()


async def courses() -> dict[str, Any]:
    session = await connect()
    try:
        await _ensure_session(session)
        rows = await list_courses(session)
        return {"ok": True, "count": len(rows), "courses": rows}
    finally:
        await session.close()


async def assignments(course_id: int | None = None, due_within: float | None = None) -> dict[str, Any]:
    session = await connect()
    try:
        await _ensure_session(session)
        if course_id:
            rows = await list_assignments(session, course_id)
        else:
            rows = []
            for course in await list_courses(session):
                rows.extend(await list_assignments(session, course["id"]))
        rows = filter_due_window(rows, due_within)
        return {"ok": True, "count": len(rows), "assignments": rows}
    finally:
        await session.close()


async def submit(cmid: int, file_paths: list[str] | None = None, text: str | None = None,
                 note: str | None = None, headed: bool = False) -> dict[str, Any]:
    if not (file_paths and any(f.strip() for f in file_paths)) and not (text and text.strip()):
        raise SubmissionError("Provide files and/or text; refusing empty submission.")
    from .submitter import submit as _submit

    session = await connect(headed=headed)
    try:
        await _ensure_session(session)
        result = await _submit(session, cmid=cmid, file_paths=file_paths or [], text=text, note=note)
        return {"ok": True, "cmid": cmid, **result}
    finally:
        await session.close()


async def materials(course_id: int) -> dict[str, Any]:
    session = await connect()
    try:
        await _ensure_session(session)
        rows = await list_materials(session, course_id)
        return {"ok": True, "course_id": course_id, "count": len(rows), "materials": rows}
    finally:
        await session.close()


async def download(fileurl: str, out_path: str, overwrite: bool = False) -> dict[str, Any]:
    session = await connect()
    try:
        await _ensure_session(session)
        result = await materials_download(session, fileurl, out_path, overwrite=overwrite)
        return {"ok": True, **result}
    finally:
        await session.close()


async def verify(cmid: int) -> dict[str, Any]:
    session = await connect()
    try:
        await _ensure_session(session)
        state = await submission_status(session, cmid)
        return {"ok": True, **state}
    finally:
        await session.close()
