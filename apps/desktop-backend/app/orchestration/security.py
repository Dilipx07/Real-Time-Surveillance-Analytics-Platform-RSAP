"""Credential-safe error rendering for worker status and logs."""

from __future__ import annotations

import re

_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<credentials>[^/@\s]+)@", re.I
)
_SENSITIVE_QUERY = re.compile(
    r"(?P<name>(?:token|password|passwd|secret|key|auth|credential))=(?P<value>[^&\s]+)",
    re.I,
)


def sanitize_error(error: BaseException | str, *, maximum_length: int = 500) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ")
    message = _URL_CREDENTIALS.sub(r"\g<scheme><redacted>@", message)
    message = _SENSITIVE_QUERY.sub(r"\g<name>=<redacted>", message)
    return message[:maximum_length]
