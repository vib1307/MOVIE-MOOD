"""Shared HTTP safety: keep API keys out of log output."""

import logging
import re

# Matches api_key=... (TMDB) and apikey=... (OMDb) in URLs
_KEY_PARAM = re.compile(r"(api_?key=)[^&\s'\"]+", re.IGNORECASE)


class RedactApiKeys(logging.Filter):
    """urllib3 logs full request URLs (e.g. on retries), and those include the key."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _KEY_PARAM.search(message):
            record.msg = _KEY_PARAM.sub(r"\1***", message)
            record.args = None
        return True  # never drop the record, only rewrite it


def install_log_redaction() -> None:
    # Logger-level filters only see records logged on that exact logger,
    # so attach to the urllib3 loggers that emit URLs.
    for name in ("urllib3.connectionpool", "urllib3.util.retry"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactApiKeys) for f in logger.filters):
            logger.addFilter(RedactApiKeys())
