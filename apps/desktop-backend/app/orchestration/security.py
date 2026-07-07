"""Credential-safe error rendering for public DTOs and logs."""

from __future__ import annotations

import re

_URL = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^\s]+", re.I)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?:api[_-]?key|token|password|passwd|secret|auth|credential|aadhaar)\s*[=:]\s*[^&\s,;]+",
    re.I,
)


def sanitize_error(error: BaseException | str, *, maximum_length: int = 500) -> str:
    """Return a single-line summary with URLs and sensitive assignments removed."""
    message = str(error).replace("\r", " ").replace("\n", " ")
    message = _URL.sub(lambda match: f"{match.group('scheme')}<redacted>", message)
    message = _SENSITIVE_ASSIGNMENT.sub("<redacted>", message)
    return message[:maximum_length]
