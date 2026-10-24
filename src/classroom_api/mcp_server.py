"""MCP server exposing myITS Classroom as agent tools.

Run by OpenCode (stdio). Tools:

- classroom_status: check session validity (exit path for 2FA expiry)
- classroom_login: opens a HEADED browser; requires a human present to
  complete myITS SSO + approve the Microsoft Authenticator push. Only needed
  once per session lifetime (~8h idle); never rows cookies into results.
- classroom_courses
- classroom_assignments (course optional, due-within window optional)
- classroom_submit: files and/or text into mod_assign; verifies server-side
- classroom_verify: authoritative submission state

No tool ever returns cookies or secrets.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import errors as err
from . import service

mcp = FastMCP(
    "classroom",
    instructions=(
        "myITS Classroom (Moodle) at classroom.its.ac.id. Start with classroom_status; "
        "if login is required, tell the user to be present and call classroom_login "
        "(a headed browser opens; the human completes myITS SSO + Microsoft 2FA once). "
        "All other tools then work cookie-only. Submission flows verify server-side "
        "state and refuse to run with empty payloads."
    ),
)


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    # FastMCP will wrap this as structured output + JSON text content
    return result


@mcp.tool()
async def classroom_status() -> dict[str, Any]:
    """Check whether a valid classroom session exists. Call first; returns user identity or login_required."""
    try:
        return _payload(await service.status())
    except err.ClassroomError as exc:
        return exc.as_payload()


@mcp.tool()
async def classroom_login() -> dict[str, Any]:
    """Open a headed browser for ONE-TIME human login (SSO + Microsoft 2FA). Requires the user present. Ask the user to watch for it."""
    try:
        return _payload(await service.login(headed=True))
    except err.ClassroomError as exc:
        return exc.as_payload()


@mcp.tool()
async def classroom_courses() -> dict[str, Any]:
    """List enrolled courses as [{id, shortname, fullname, view_url, ...}]."""
    try:
        return _payload(await service.courses())
    except err.ClassroomError as exc:
        return exc.as_payload()


@mcp.tool()
async def classroom_assignments(
    course_id: int | None = None, due_within_days: float | None = None
) -> dict[str, Any]:
    """List assignments. Filter: course_id optional; due_within_days keeps only those due (or overdue) within N days."""
    try:
        return _payload(await service.assignments(course_id=course_id, due_within=due_within_days))
    except err.ClassroomError as exc:
        return exc.as_payload()


@mcp.tool()
async def classroom_submit(
    cmid: int,
    file_paths: list[str] | None = None,
    text: str | None = None,
    note: str | None = None,
    headed: bool = False,
) -> dict[str, Any]:
    """Submit an assignment (cmid = course module id). Provide file_paths (absolute) and/or text. note = comment for grader. headed=True to watch the flow."""
    try:
        return _payload(
            await service.submit(cmid=cmid, file_paths=file_paths, text=text, note=note, headed=headed)
        )
    except err.ClassroomError as exc:
        return exc.as_payload()


@mcp.tool()
async def classroom_verify(cmid: int) -> dict[str, Any]:
    """Authoritative server-side submission state for an assignment (submitted/draft/not_submitted)."""
    try:
        return _payload(await service.verify(cmid=cmid))
    except err.ClassroomError as exc:
        return exc.as_payload()


def main() -> None:
    """stdio entrypoint configured in opencode.json as a local MCP server."""
    mcp.run(transport="stdio")
