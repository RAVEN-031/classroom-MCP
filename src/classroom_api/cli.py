"""`classroom` CLI - thin wrapper over service.py.

Kept for humans (especially `classroom login`) and agents that prefer shell.
The MCP server (classroom-mcp) is the primary agent surface.

All successful output is a single JSON document on stdout. Exit codes:
    0 ok | 1 runtime error | 2 login required (human runs `classroom login`)
    | 3 browser/profile problem (Firefox closed or profile busy)

Exit codes are resolved from the typed error's ``errorcode`` (defensive
against concurrent error-class changes) with ``exit_code()`` as fallback.
Cookies/profile contents are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from . import service
from .errors import ClassroomError

_EXIT_BY_ERRORCODE: dict[str, int] = {
    "login_required": 2,
    "stale_state": 2,
    "browser_not_connected": 3,
    "profile_busy": 3,
}


def _exit_code(exc: ClassroomError) -> int:
    return _EXIT_BY_ERRORCODE.get(getattr(exc, "errorcode", "internal_error"), exc.exit_code())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="classroom",
        description=(
            "Human-friendly front-end for myITS Classroom automation. "
            "AI agents should use the MCP server instead (classroom-mcp)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "typical human setup:\n"
            "  classroom login          # browser opens; complete SSO + Microsoft push once\n"
            "then, for agents, register the MCP server (see opencode.json in this repo)\n"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="One-time headed login: human completes SSO + Microsoft 2FA")
    p_login.add_argument("--headless", action="store_true", help="Advanced: no visible browser")
    sub.add_parser("status", help="Check session validity + user identity")
    sub.add_parser("courses", help="List enrolled courses")

    p_assign = sub.add_parser("assignments", help="List assignments (optionally filtered)")
    p_assign.add_argument("--course", type=int, help="Course id")
    p_assign.add_argument("--due-within", type=float, default=None, metavar="DAYS", help="Only due within N days")

    p_submit = sub.add_parser("submit", help="Submit an assignment (file(s) and/or online text)")
    p_submit.add_argument("--assignment", type=int, required=True, help="Assignment cmid (course module id)")
    p_submit.add_argument("--file", action="append", default=[], help="File to attach (repeatable)")
    p_submit.add_argument("--text", type=str, default=None, help="Online text content")
    p_submit.add_argument("--note", type=str, default=None, help="Comment for the grader")
    p_submit.add_argument("--headed", action="store_true", help="Watch the flow in a visible browser")

    p_verify = sub.add_parser("verify", help="Confirm server-side submission state")
    p_verify.add_argument("--assignment", type=int, required=True)

    p_materials = sub.add_parser("materials", help="List course materials (files/folders/links)")
    p_materials.add_argument("--course", type=int, required=True, help="Course id")

    p_download = sub.add_parser("download", help="Download a Moodle file URL via the browser session")
    p_download.add_argument("--url", type=str, required=True, help="Moodle file URL")
    p_download.add_argument("--out", type=str, required=True, help="Output path")
    p_download.add_argument("--overwrite", action="store_true", help="Overwrite existing file")

    args = parser.parse_args(argv)

    try:
        result = asyncio.run(_dispatch(args))
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except ClassroomError as exc:
        json.dump(exc.as_payload(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return _exit_code(exc)


async def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    match args.cmd:
        case "login":
            return await service.login(headed=not args.headless)
        case "status":
            return await service.status()
        case "courses":
            return await service.courses()
        case "assignments":
            return await service.assignments(course_id=args.course, due_within=args.due_within)
        case "submit":
            return await service.submit(
                cmid=args.assignment, file_paths=args.file, text=args.text, note=args.note, headed=args.headed
            )
        case "verify":
            return await service.verify(cmid=args.assignment)
        case "materials":
            return await service.materials(course_id=args.course)
        case "download":
            return await service.download(fileurl=args.url, out_path=args.out, overwrite=args.overwrite)
        case _:
            raise ClassroomError(f"Unknown command {args.cmd}")
