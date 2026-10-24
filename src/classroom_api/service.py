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
from .browser import connect
from .errors import ClassroomError, LoginRequired, SubmissionError

_LOGIN_TIMEOUT_SECONDS = 600.0
_LOGIN_POLL_INTERVAL = 2.0


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
    """Open the persistent-profile browser for one-time human login.

    The human completes myITS SSO + Microsoft Authenticator push inside the
    tool's own Firefox window. We poll until ``user_id()`` is non-zero.
    """
    session = await connect(headed=headed)
    try:
        try:
            await session.sesskey()  # force the page/window to settle
        except ClassroomError:
            pass  # not logged in yet - that is what the human is about to fix
        print(
            "A Firefox window is open on classroom.its.ac.id.\n"
            "Complete myITS SSO and approve the Microsoft Authenticator push there.\n"
            f"Waiting up to {_LOGIN_TIMEOUT_SECONDS:.0f}s ...",
            file=sys.stderr,
            flush=True,
        )
        deadline = time.monotonic() + _LOGIN_TIMEOUT_SECONDS
        while True:
            try:
                user_id = await session.user_id()
            except LoginRequired:
                user_id = 0
            if user_id:
                break
            if time.monotonic() >= deadline:
                raise LoginRequired("Timed out waiting for human login in the browser window.")
            await asyncio.sleep(_LOGIN_POLL_INTERVAL)
        fullname = await _fullname(session)
        return {
            "ok": True,
            "message": "Login complete. The session lives ~8h idle; activity keeps it alive.",
            "user": {"id": user_id, "fullname": fullname},
        }
    finally:
        await session.close()


async def status() -> dict[str, Any]:
    session = await connect()
    try:
        user_id = await session.user_id()
        if not user_id:
            raise LoginRequired("No active session; the browser window is not logged in.")
        return {"ok": True, "logged_in": True, "user": {"id": user_id, "fullname": await _fullname(session)}}
    finally:
        await session.close()


async def courses() -> dict[str, Any]:
    session = await connect()
    try:
        rows = await list_courses(session)
        return {"ok": True, "count": len(rows), "courses": rows}
    finally:
        await session.close()


async def assignments(course_id: int | None = None, due_within: float | None = None) -> dict[str, Any]:
    session = await connect()
    try:
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
        result = await _submit(session, cmid=cmid, file_paths=file_paths or [], text=text, note=note)
        return {"ok": True, "cmid": cmid, **result}
    finally:
        await session.close()


async def verify(cmid: int) -> dict[str, Any]:
    session = await connect()
    try:
        state = await submission_status(session, cmid)
        return {"ok": True, **state}
    finally:
        await session.close()
