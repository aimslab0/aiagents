"""Console output suitable for the host's error log, without credential payloads."""

import copy
import json
import logging
import re
import traceback

from django.conf import settings


class SafeConsoleFormatter(logging.Formatter):
    def format(self, record):
        safe = copy.copy(record)
        safe.msg = record.getMessage()
        safe.args = ()
        if not settings.DEBUG:
            if record.name.startswith(("django.request", "django.security", "django.server")):
                safe.msg = f"HTTP event status={getattr(record, 'status_code', 'unknown')}"
            # Keep exception type and code locations, not exception text, locals or source lines.
            if record.exc_info:
                locations = " -> ".join(
                    f"{frame.name}:{frame.lineno}"
                    for frame in traceback.extract_tb(record.exc_info[2])
                )
                safe.msg += f" exception={record.exc_info[0].__name__} frames={locations}"
            safe.exc_info = None
            safe.exc_text = None
            safe.stack_info = None
        result = super().format(safe)
        for name in ("SECRET_KEY", "OPENROUTER_API_KEY", "CONSENSUS_API_KEY"):
            secret = getattr(settings, name, "")
            if secret:
                result = result.replace(secret, "[REDACTED]")
                result = result.replace(json.dumps(secret)[1:-1], "[REDACTED]")
        result = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", result)
        return re.sub(r"sk-or-[A-Za-z0-9_-]+", "[REDACTED]", result)
