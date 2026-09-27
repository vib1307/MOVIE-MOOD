"""Tiny JSON file cache helpers shared by the ingest clients."""

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def read_json(path: Path) -> Any | None:
    """Cached value, or None if missing. A corrupt file (e.g. a crash mid-write) is deleted."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        log.warning("corrupt cache file %s, deleting and refetching", path.name)
        path.unlink()
        return None


def write_json(path: Path, data: Any, **dumps_kwargs) -> None:
    """Write to a temp file, then rename, so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, **dumps_kwargs))
    tmp.replace(path)  # atomic on the same filesystem
