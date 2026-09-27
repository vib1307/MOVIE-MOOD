"""OMDb client: IMDb rating by imdb_id."""

import logging
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from moviemood.config import get_settings
from moviemood.ingest.cache import read_json, write_json
from moviemood.ingest.http import install_log_redaction

log = logging.getLogger(__name__)

BASE_URL = "https://www.omdbapi.com/"
TIMEOUT = 10  # seconds per request


class OMDbError(RuntimeError):
    pass


class OMDbLimitReached(OMDbError):
    """Free tier is 1000 requests/day. Retrying won't help, so the caller should stop."""


class OMDbClient:
    def __init__(self, cache_dir: Path | None = None):
        settings = get_settings()
        self._key = settings.omdb_api_key.get_secret_value()
        self.cache_dir = cache_dir or settings.data_dir / "cache" / "omdb"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        install_log_redaction()
        self.session = self._make_session()

    def _make_session(self) -> requests.Session:
        retry = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.params = {"apikey": self._key}
        return session

    def _get(self, **params) -> dict:
        try:
            resp = self.session.get(BASE_URL, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as e:
            # As in tmdb.py: never surface the URL, it contains the apikey.
            status = getattr(e.response, "status_code", None)
            if status == 401:
                # OMDb uses 401 for both "Invalid API key!" and "Request limit reached!"
                message = _error_message(e.response)
                if "limit" in message.lower():
                    raise OMDbLimitReached(message) from None
                raise OMDbError(f"OMDb auth failed: {message}") from None
            raise OMDbError(f"OMDb request failed (status={status}, {type(e).__name__})") from None
        try:
            return resp.json()
        except ValueError:
            # OMDb sometimes breaks its own JSON (unescaped quotes in the Error text,
            # e.g. for malformed ids). Treat it as "not found" instead of crashing the run.
            log.warning("OMDb returned invalid JSON for %s", params)
            return {"Response": "False", "Error": "invalid JSON from OMDb"}

    def fetch(self, imdb_id: str) -> dict:
        """Raw OMDb response, cached on disk. "Not found" results are cached too."""
        cache_file = self.cache_dir / f"{imdb_id}.json"
        if (cached := read_json(cache_file)) is not None:
            return cached
        data = self._get(i=imdb_id)
        if data.get("Response") == "False":
            log.info("OMDb has no entry for %s: %s", imdb_id, data.get("Error"))
        write_json(cache_file, data)
        return data


def parse_rating(data: dict) -> float | None:
    """'7.5' -> 7.5; 'N/A', missing, or not found -> None."""
    if data.get("Response") == "False":
        return None
    raw = data.get("imdbRating")
    if not raw or raw == "N/A":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _error_message(resp: requests.Response | None) -> str:
    try:
        return resp.json().get("Error", "unknown error")
    except (AttributeError, ValueError):
        return "unknown error"
