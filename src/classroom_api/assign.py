"""Course / assignment discovery and submission status via FirefoxSession.

Primary path: in-page Moodle AJAX (``lib/ajax/service.php``). Fallbacks:
dashboard scrape for courses, assignment page scrape for status. All datetimes
are WIB (Asia/Jakarta, UTC+7) ISO strings.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .browser import BASE_URL, FirefoxSession
from .errors import LoginRequired, MoodleError

TZ = timezone(timedelta(hours=7))  # Asia/Jakarta (WIB)


def _epoch(ts: int | None) -> str | None:
    """Epoch seconds -> WIB ISO string (None-safe)."""
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=TZ).isoformat()


async def list_courses(s: FirefoxSession) -> list[dict]:
    """Enrolled courses ("in progress"); falls back to /my/ scraping."""
    data: Any = None
    try:
        data = await s.ajax(
            "core_course_get_enrolled_courses_by_timeline_classification",
            {"offset": 0, "limit": 0, "classification": "inprogress"},
        )
    except MoodleError:
        data = None
    if isinstance(data, dict) and data.get("courses"):
        return [
            {
                "id": c["id"],
                "shortname": c.get("shortname", ""),
                "fullname": c.get("fullname", ""),
                "progress": c.get("progress"),
                "view_url": c.get("viewurl") or f"{BASE_URL}/course/view.php?id={c['id']}",
            }
            for c in data["courses"]
        ]
    return _scrape_courses_html(await s.fetch_html("/my/"))


def _scrape_courses_html(html: str) -> list[dict]:
    """Extract /course/view.php?id=N links from dashboard HTML."""
    seen: dict[int, dict] = {}
    for m in re.finditer(r'href="[^"]*/course/view\.php\?id=(\d+)"[^>]*>(.*?)<', html):
        cid = int(m.group(1))
        name = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if cid and cid not in seen and name:
            seen[cid] = {
                "id": cid,
                "shortname": "",
                "fullname": name,
                "progress": None,
                "view_url": f"{BASE_URL}/course/view.php?id={cid}",
            }
    return list(seen.values())


async def list_assignments(s: FirefoxSession, course_id: int) -> list[dict]:
    """All mod_assign activities of a course with due dates + submission types."""
    data: Any = None
    try:
        data = await s.ajax("mod_assign_get_assignments", {"courseids": [course_id]})
    except MoodleError:
        data = None

    assigns: list[dict] = []
    if isinstance(data, dict):
        course_data = (data.get("courses") or {}).get(str(course_id)) or {}
        for a in course_data.get("assignments", []):
            cmid = a.get("cmid") or a.get("id")
            assigns.append(
                {
                    "cmid": cmid,
                    "name": a.get("name", ""),
                    "due": _epoch(a.get("duedate")),
                    "opened": _epoch(a.get("allowsubmissionsfromdate")),
                    "cutoff": _epoch(a.get("cutoffdate")),
                    "online_text": bool(a.get("online_text_submission_enabled")),
                    "file_upload": bool(a.get("file_submission_enabled")),
                    "view_url": f"{BASE_URL}/mod/assign/view.php?id={cmid}",
                }
            )
    if not assigns:
        assigns = await _assignments_from_contents(s, course_id)
    return assigns


async def _assignments_from_contents(s: FirefoxSession, course_id: int) -> list[dict]:
    """Fallback: core_course_get_contents filtered to modname=="assign"."""
    try:
        sections: Any = await s.ajax("core_course_get_contents", {"courseid": course_id})
    except (MoodleError, LoginRequired):
        return []
    out: list[dict] = []
    for section in sections or []:
        for mod in section.get("modules", []):
            if mod.get("modname") != "assign":
                continue
            out.append(
                {
                    "cmid": mod.get("id"),
                    "name": mod.get("name", ""),
                    "due": None,
                    "opened": None,
                    "cutoff": None,
                    "online_text": None,
                    "file_upload": None,
                    "view_url": mod.get("url") or f"{BASE_URL}/mod/assign/view.php?id={mod.get('id')}",
                }
            )
    return out


async def submission_status(s: FirefoxSession, cmid: int) -> dict:
    """Authoritative submission state for one assignment."""
    try:
        data = await s.ajax("mod_assign_get_submission_status", {"assignid": cmid})
        if isinstance(data, dict):
            return _summarize_status(cmid, data)
    except MoodleError:
        pass
    return _scrape_status_html(await s.fetch_html(f"/mod/assign/view.php?id={cmid}"), cmid)


def _summarize_status(cmid: int, data: dict) -> dict:
    """Map mod_assign_get_submission_status payload to a compact dict."""
    attempted = data.get("lastattempt") or {}
    sub = attempted.get("submission") or {}
    raw = sub.get("status")
    if raw == "submitted":
        status = "submitted"
    elif raw in ("draft", "new"):
        status = "draft" if raw == "draft" else "not_submitted"
    elif raw:
        status = "unknown"
    else:
        status = "not_submitted"
    prev = data.get("previoussubmissions") or []
    latest = prev[-1] if prev else sub
    return {
        "cmid": cmid,
        "status": status,
        "can_submit": bool(attempted.get("caneditsubmission", True)),
        "latest_submission": {
            "attempt": latest.get("attemptnumber"),
            "time_created": _epoch(latest.get("timecreated")),
            "grading_status": latest.get("gradingstatus"),
        },
    }


def _scrape_status_html(html: str, cmid: int) -> dict:
    """Fallback: text-match the assignment page for status labels."""
    if "Submitted for grading" in html:
        status = "submitted"
    elif "Draft (not submitted)" in html:
        status = "draft"
    elif "Add submission" in html or "No attempt" in html:
        status = "not_submitted"
    else:
        status = "unknown"
    return {"cmid": cmid, "status": status, "can_submit": True, "latest_submission": {}}


def filter_due_window(assigns: list[dict], days: float | None) -> list[dict]:
    """Keep assignments due within ``days`` days (WIB); undated rows always kept."""
    if days is None:
        return assigns
    now = datetime.now(tz=TZ).timestamp()
    out = []
    for a in assigns:
        if not a.get("due"):
            out.append(a)
            continue
        try:
            due_ts = datetime.fromisoformat(a["due"]).timestamp()
        except ValueError:
            out.append(a)
            continue
        if due_ts - now <= days * 86400:
            out.append(a)
    return out
