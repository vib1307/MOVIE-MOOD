"""Where to watch a recommended film: TMDB watch providers (JustWatch). See D-036.

Only `flatrate` (included with a subscription) is kept, so the line on a card answers
"can I watch this tonight?" and not "what can I rent?".

One TMDB call returns every country (~130), so the cache keeps them all and the UI's
region dropdown costs no extra requests. Trimmed to provider names it is ~1.4 MB for a
515-movie catalog; the raw responses would be ~31 MB.

    data/providers.json: {"550": {"updated": "2026-10-02", "flatrate": {"IN": ["Netflix"]}}}

scripts/fetch_providers.py fills that file. A miss (a film just added by lazy ingestion)
is fetched live, at most MAX_LIVE per search and never more than FETCH_THREADS at a time:
TMDB resets the connection on a bigger pool, which costs far more than it saves (D-036).

Like explain(), this fills fields in place and never raises: availability is a
nice-to-have, so a TMDB hiccup must not break a search.
"""

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import lru_cache

from moviemood.config import get_settings
from moviemood.core.models import Recommendation
from moviemood.ingest.cache import read_json, write_json
from moviemood.ingest.tmdb import TMDBClient, TMDBError

log = logging.getLogger(__name__)

DEFAULT_REGION = "IN"
MAX_PROVIDERS = 3  # a card shows one line, not a catalogue
MAX_LIVE = 10  # live fetches per search, so a cold cache can't stall a request
FETCH_THREADS = 2  # measured ceiling: 4+ triggers TMDB connection resets (D-036)
TTL_DAYS = 7  # availability drifts (licences expire), so entries go stale

# "HBO Max Amazon Channel", "ARD Plus Apple TV channel": the same service resold inside
# another store. Lowercase "channel" happens too, hence re.I.
RESELLER = re.compile(r" (amazon|apple tv|roku premium) channel$", re.I)
ADS_TIER = re.compile(r" (standard )?with ads$", re.I)

# TMDB's own names, shortened for the one-line label on a card ("United States of
# America" doesn't fit next to two provider names). The dropdown keeps TMDB's wording.
SHORT_NAMES = {"US": "the US", "GB": "the UK", "AE": "the UAE", "KR": "South Korea",
               "RU": "Russia", "TW": "Taiwan", "VE": "Venezuela", "BO": "Bolivia",
               "TZ": "Tanzania", "IR": "Iran", "VN": "Vietnam", "CZ": "Czechia"}

# Used only if TMDB's region list can't be fetched and isn't cached.
FALLBACK_REGIONS = (("IN", "India"), ("US", "United States"), ("GB", "United Kingdom"),
                    ("CA", "Canada"), ("AU", "Australia"), ("DE", "Germany"), ("AE", "United Arab Emirates"))

_lock = threading.Lock()  # one writer at a time for providers.json


def clean_names(flatrate: list[dict]) -> list[str]:
    """TMDB's flatrate list -> up to MAX_PROVIDERS display names, best first.

    [{"provider_name": "Netflix", "display_priority": 0}, ...] -> ["Netflix"]
    """
    names = [p["provider_name"] for p in sorted(flatrate, key=lambda p: p.get("display_priority", 999))]
    keep = [n for n in names if not RESELLER.search(n)]
    # Drop "Netflix Standard with Ads" when plain "Netflix" is already there.
    plain = {n for n in keep if not ADS_TIER.search(n)}
    keep = [n for n in keep if not ADS_TIER.search(n) or ADS_TIER.sub("", n) not in plain]
    keep = list(dict.fromkeys(keep))  # dedupe, keep order
    # Every provider was a reseller channel (The Imitation Game in the US): the film does
    # stream, so name the service behind the first one ("Britbox Apple TV channel" ->
    # "Britbox") rather than claiming it isn't available.
    if not keep and names:
        keep = [RESELLER.sub("", names[0])]
    return keep[:MAX_PROVIDERS]


def trim(results: dict) -> dict[str, list[str]]:
    """A whole watch/providers response -> {"IN": ["Netflix"], ...}. Empty regions dropped."""
    trimmed = {}
    for region, offers in (results or {}).items():
        names = clean_names((offers or {}).get("flatrate") or [])
        if names:
            trimmed[region] = names
    return trimmed


@lru_cache
def _client() -> TMDBClient:
    return TMDBClient()


@lru_cache
def cache() -> dict[str, dict]:
    """{"550": {"updated": ..., "flatrate": {...}}}, loaded once per process.

    Callers mutate this dict in place (like get_catalog), so everyone sees new entries.
    """
    cached = read_json(get_settings().providers_json)
    return cached if isinstance(cached, dict) else {}


def is_fresh(entry: dict | None) -> bool:
    try:
        return date.today() - date.fromisoformat(entry["updated"]) <= timedelta(days=TTL_DAYS)
    except (TypeError, KeyError, ValueError):  # missing entry, or a hand-edited date
        return False


def fetch_many(ids: list[int], client: TMDBClient | None = None, save: bool = True) -> dict[int, dict]:
    """Fetch and trim providers for `ids`. Failures are skipped, not raised."""
    if not ids:
        return {}
    client = client or _client()
    today = date.today().isoformat()

    def one(tmdb_id: int) -> tuple[int, dict | None]:
        try:
            return tmdb_id, {"updated": today, "flatrate": trim(client.watch_providers(tmdb_id))}
        except (TMDBError, KeyError, TypeError) as e:
            log.warning("watch providers for %s failed: %s: %s", tmdb_id, type(e).__name__, e)
            return tmdb_id, None

    with ThreadPoolExecutor(max_workers=FETCH_THREADS) as pool:
        fetched = {i: entry for i, entry in pool.map(one, ids) if entry is not None}
    if fetched and save:
        save_entries(fetched)
    return fetched


def save_entries(entries: dict[int, dict]) -> None:
    """Merge entries into the cache and rewrite providers.json (atomic write)."""
    with _lock:
        cached = cache()
        cached.update({str(tmdb_id): entry for tmdb_id, entry in entries.items()})
        write_json(get_settings().providers_json, cached, ensure_ascii=False)


def providers_for(tmdb_id: int, region: str = DEFAULT_REGION) -> list[str]:
    """Subscription providers for one movie in one region. Fetches on a miss."""
    entry = cache().get(str(tmdb_id))
    if not is_fresh(entry):
        entry = fetch_many([tmdb_id]).get(tmdb_id, entry)
    return ((entry or {}).get("flatrate") or {}).get(region.upper(), [])


def add_availability(recs: list[Recommendation], region: str = DEFAULT_REGION) -> None:
    """Fill rec.where_to_watch in place for `region`. Never raises.

    Cached movies cost nothing (a dict lookup), so re-running this for another region is
    free - that's what lets the UI switch country without a new search.
    """
    region = (region or DEFAULT_REGION).upper()
    cached = cache()
    stale = [rec.tmdb_id for rec in recs if not is_fresh(cached.get(str(rec.tmdb_id)))]
    if stale:
        try:
            fetch_many(stale[:MAX_LIVE])
        except Exception as e:  # the line is a nice-to-have; a search must still answer
            log.warning("availability lookup failed: %s: %s", type(e).__name__, e)
    for rec in recs:
        entry = cached.get(str(rec.tmdb_id)) or {}
        rec.where_to_watch = (entry.get("flatrate") or {}).get(region, [])


@lru_cache
def regions() -> list[tuple[str, str]]:
    """[("India", "IN"), ...] by name, for a gr.Dropdown's (label, value) choices."""
    cache_file = get_settings().data_dir / "cache" / "watch_regions.json"
    raw = read_json(cache_file)
    if not raw:
        try:
            raw = _client().watch_regions()
            write_json(cache_file, raw, ensure_ascii=False)
        except TMDBError as e:
            log.warning("region list failed (%s), using the short fallback list", e)
            raw = [{"iso_3166_1": code, "english_name": name} for code, name in FALLBACK_REGIONS]
    return sorted((r["english_name"], r["iso_3166_1"]) for r in raw)


@lru_cache
def region_name(code: str) -> str:
    """"IN" -> "India"; an unknown code comes back unchanged."""
    code = (code or DEFAULT_REGION).upper()
    if code in SHORT_NAMES:
        return SHORT_NAMES[code]
    return {value: label for label, value in regions()}.get(code, code)
