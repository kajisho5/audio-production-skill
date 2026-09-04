"""Structured error model. Every failure that crosses the Skill boundary is an AudioError with a code from
ERROR_CODES; the CLI turns it into {"ok": false, "error": {"code", "message", "retryable", "details"}}."""
from __future__ import annotations

from typing import Any, Dict, Optional

# code -> (exit code, retryable)
ERROR_TABLE: Dict[str, Any] = {
    "INVALID_REQUEST": (2, False),         # document shape / unknown fields / bad types
    "INVALID_INPUT": (3, False),           # an input file is missing, unreadable, not audio, or not a regular file
    "PATH_NOT_ALLOWED": (4, False),        # input outside allowed roots, output outside workspace, traversal, symlink escape
    "UNSUPPORTED_OPERATION": (5, False),   # operation type not implemented by this skill
    "UNSUPPORTED_FORMAT": (6, False),      # output format not in the contract, or encoder missing
    "MISSING_INPUT": (7, False),           # an operation references a track / operation / source that does not exist
    "INVALID_TIME_RANGE": (8, False),      # start/end/duration inconsistent or outside the media
    "INVALID_CHANNEL_LAYOUT": (9, False),  # channel layout not supported or inconsistent with the input
    "INVALID_SAMPLE_RATE": (10, False),    # sample rate not supported
    "DEPENDENCY_ERROR": (11, False),       # operation graph cycle, duplicate id, unreachable output
    "TOOL_ERROR": (12, True),              # ffmpeg-skill / ffmpeg failed, timed out, or is unavailable
    "OUTPUT_ERROR": (13, False),           # output could not be written, is empty, collides with an input, or exists
    "VALIDATION_ERROR": (14, False),       # output written but failed post-validation (streams, duration, loudness)
    "CANCELLED": (15, True),               # interrupted by signal
    "INTERNAL_ERROR": (16, False),         # a bug in this skill
}
ERROR_CODES = tuple(ERROR_TABLE)
EXIT_CODES = {code: ERROR_TABLE[code][0] for code in ERROR_CODES}


class AudioError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, retryable: Optional[bool] = None):
        if code not in ERROR_TABLE:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retryable = ERROR_TABLE[code][1] if retryable is None else bool(retryable)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable, "details": self.details}

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.code]
