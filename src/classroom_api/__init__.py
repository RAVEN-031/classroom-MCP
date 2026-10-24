"""classroom_api - AI-agent-friendly client for myITS Classroom (Moodle)."""

from .errors import (
    ClassroomError,
    LoginRequired,
    SubmissionError,
    TranscriptNotFound,
    VerificationError,
)

__all__ = [
    "ClassroomError",
    "LoginRequired",
    "SubmissionError",
    "TranscriptNotFound",
    "VerificationError",
]

__version__ = "0.1.0"
