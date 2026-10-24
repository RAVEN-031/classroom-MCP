"""Typed errors with agent-readable payloads.

Every error carries a JSON-serializable dict in ``as_payload()`` so the CLI can
print structured failures on stdout and agents can branch on ``errorcode``.
"""


class ClassroomError(Exception):
    errorcode = "internal_error"
    hint = "Read the message; retry may or may not help."

    def as_payload(self) -> dict:
        return {
            "error": True,
            "errorcode": self.errorcode,
            "message": str(self),
            "hint": getattr(self, "hint", ""),
        }

    def exit_code(self) -> int:
        return 1


class BrowserNotConnected(ClassroomError):
    """Playwright could not start Firefox with the persistent profile.

    ``errorcode`` may be overridden per instance (e.g. ``"profile_busy"``
    when the profile directory is locked by another Firefox instance) so
    ``as_payload()`` emits the effective code.
    """

    errorcode = "browser_not_connected"
    hint = "Check the profile path and permissions, then retry."

    def __init__(self, message: str, errorcode: str | None = None, hint: str | None = None) -> None:
        super().__init__(message)
        if errorcode is not None:
            self.errorcode = errorcode
        if hint is not None:
            self.hint = hint

    def as_payload(self) -> dict:
        return {
            "error": True,
            "errorcode": self.errorcode,
            "message": str(self),
            "hint": getattr(self, "hint", ""),
        }

    def exit_code(self) -> int:
        return 3


class MoodleError(ClassroomError):
    """Moodle rejected an AJAX web-service call."""

    errorcode = "moodle_error"
    hint = "Inspect the Moodle message; the sesskey path auto-refreshes once before this surfaces."


class LoginRequired(ClassroomError):
    """No valid MoodleSession; a human must run `classroom login` once.

    The human performs the myITS SSO + Microsoft Authenticator approval in a
    headed Firefox window. The persistent profile is the only credential
    store; no cookies are ever copied to disk.
    """

    errorcode = "login_required"
    hint = "Run `classroom login` (headed). It opens a browser: complete myITS SSO and approve the Microsoft push once. Sessions then live ~8h idle; activity keeps them alive."

    def exit_code(self) -> int:
        return 2


class StaleState(ClassroomError):
    """State file missing/corrupt; login needed."""

    errorcode = "stale_state"
    hint = "State file missing or unreadable. Run `classroom login` once."

    def exit_code(self) -> int:
        return 2


class DatabaseError(ClassroomError):
    """Moodle rejected an AJAX/WS call (bad function, permissions, sesskey)."""

    errorcode = "moodle_error"
    hint = "If sesskey expired mid-flow, requests auto-refresh once; otherwise inspect the Moodle message."


class SubmissionError(ClassroomError):
    """The submission flow failed before confirmation."""

    errorcode = "submission_failed"
    hint = "Retry with --headed to watch the flow; selectors live in submitter.py SECTIONS."


class TranscriptNotFound(ClassroomError):
    errorcode = "transcript_not_found"
    hint = "Check the file path. PDFs must contain selectable text."


class VerificationError(ClassroomError):
    """Server state could not be confirmed after submit."""

    errorcode = "verification_failed"
    hint = "Open the assignment page in a browser to check manually. Do NOT resubmit blindly; may create duplicate drafts."
