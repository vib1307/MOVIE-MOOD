"""core/availability.py: name cleanup is pure, the cache layer is faked (no network)."""

import json
from datetime import date, timedelta

import pytest

from moviemood.core import availability
from moviemood.core.availability import add_availability, clean_names, providers_for, trim
from moviemood.core.models import Recommendation
from moviemood.ingest.tmdb import TMDBError


def offers(*names: str) -> list[dict]:
    """TMDB's flatrate shape, in the order given."""
    return [{"provider_name": n, "display_priority": i} for i, n in enumerate(names)]


def rec(tmdb_id: int = 550) -> Recommendation:
    return Recommendation(tmdb_id=tmdb_id, title="Fight Club", why="dark", source="llm", distance=0.3)


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch, tmp_path):
    """A fresh in-memory cache per test, and writes land in tmp_path."""
    store: dict[str, dict] = {}
    monkeypatch.setattr(availability, "cache", lambda: store)
    monkeypatch.setattr(availability, "save_entries", lambda entries: store.update(
        {str(i): e for i, e in entries.items()}))
    return store


def entry(flatrate: dict, days_old: int = 0) -> dict:
    return {"updated": (date.today() - timedelta(days=days_old)).isoformat(), "flatrate": flatrate}


# --- clean_names ------------------------------------------------------------

def test_display_priority_order_and_cap():
    out = clean_names(offers("Netflix", "JioHotstar", "Zee5", "Lionsgate Play"))
    assert out == ["Netflix", "JioHotstar", "Zee5"]  # MAX_PROVIDERS


def test_reseller_channels_dropped_case_insensitively():
    # TMDB writes both "Amazon Channel" and "Apple TV channel"
    assert clean_names(offers("Netflix", "HBO Max Amazon Channel", "ARD Plus Apple TV channel")) == ["Netflix"]


def test_all_resellers_falls_back_to_the_service_behind_the_first():
    # The Imitation Game in the US: both providers are reseller channels, but it does
    # stream, so naming one beats claiming it isn't available - without the store suffix.
    assert clean_names(offers("AMC+ Amazon Channel", "MGM+ Apple TV channel")) == ["AMC+"]
    assert clean_names(offers("Britbox Apple TV channel")) == ["Britbox"]


def test_ads_tier_collapses_only_when_the_plain_name_is_there():
    assert clean_names(offers("Netflix", "Netflix Standard with Ads")) == ["Netflix"]
    assert clean_names(offers("Amazon Prime Video with Ads")) == ["Amazon Prime Video with Ads"]


def test_empty_and_duplicate_input():
    assert clean_names([]) == []
    assert clean_names(offers("Netflix", "Netflix")) == ["Netflix"]


def test_trim_keeps_flatrate_only_and_drops_empty_regions():
    raw = {
        "IN": {"flatrate": offers("Netflix"), "buy": offers("YouTube"), "link": "..."},
        "US": {"rent": offers("Apple TV")},  # rent/buy only: not "watch it tonight"
    }
    assert trim(raw) == {"IN": ["Netflix"]}
    assert trim({}) == {} and trim(None) == {}


# --- cache / live fallback --------------------------------------------------

def test_cache_hit_makes_no_tmdb_call(empty_cache, monkeypatch):
    empty_cache["550"] = entry({"IN": ["Netflix"], "US": ["Hulu"]})
    monkeypatch.setattr(availability, "fetch_many", lambda *a, **kw: pytest.fail("should not fetch"))
    assert providers_for(550, "IN") == ["Netflix"]
    assert providers_for(550, "us") == ["Hulu"]  # region is case-insensitive
    assert providers_for(550, "DE") == []  # cached, just not streaming there


def test_miss_fetches_once_and_is_written_back(empty_cache, monkeypatch):
    calls = []

    class FakeTMDB:
        def watch_providers(self, tmdb_id):
            calls.append(tmdb_id)
            return {"IN": {"flatrate": offers("JioHotstar")}}

    monkeypatch.setattr(availability, "_client", lambda: FakeTMDB())
    assert providers_for(550, "IN") == ["JioHotstar"]
    assert calls == [550]
    assert empty_cache["550"]["flatrate"] == {"IN": ["JioHotstar"]}

    providers_for(550, "IN")  # now cached
    assert calls == [550]


def test_stale_entry_is_refetched(empty_cache, monkeypatch):
    empty_cache["550"] = entry({"IN": ["Netflix"]}, days_old=availability.TTL_DAYS + 1)

    class FakeTMDB:
        def watch_providers(self, tmdb_id):
            return {"IN": {"flatrate": offers("Zee5")}}

    monkeypatch.setattr(availability, "_client", lambda: FakeTMDB())
    assert providers_for(550, "IN") == ["Zee5"]


def test_tmdb_error_leaves_availability_empty_and_does_not_raise(monkeypatch):
    class Broken:
        def watch_providers(self, tmdb_id):
            raise TMDBError("GET failed (status=503)")

    monkeypatch.setattr(availability, "_client", lambda: Broken())
    recs = [rec()]
    add_availability(recs, "IN")  # must not raise: the line is a nice-to-have
    assert recs[0].where_to_watch == []


def test_add_availability_fills_in_place_and_caps_live_fetches(empty_cache, monkeypatch):
    fetched = []
    monkeypatch.setattr(availability, "fetch_many", lambda ids, **kw: fetched.extend(ids) or {})
    recs = [rec(i) for i in range(availability.MAX_LIVE + 5)]
    empty_cache["0"] = entry({"IN": ["Netflix"]})
    add_availability(recs, "IN")

    assert len(fetched) == availability.MAX_LIVE  # a cold cache can't stall the search
    assert 0 not in fetched  # the cached one is skipped
    assert recs[0].where_to_watch == ["Netflix"]
    assert recs[1].where_to_watch == []  # looked up, nothing found -> [], not None


def test_switching_region_needs_no_fetch(empty_cache, monkeypatch):
    empty_cache["550"] = entry({"IN": ["Netflix"], "US": ["Hulu"]})
    monkeypatch.setattr(availability, "fetch_many", lambda *a, **kw: pytest.fail("should not fetch"))
    recs = [rec()]
    add_availability(recs, "IN")
    assert recs[0].where_to_watch == ["Netflix"]
    add_availability(recs, "US")  # one response holds every country, so this is free
    assert recs[0].where_to_watch == ["Hulu"]


def test_save_entries_really_writes_the_file(monkeypatch, tmp_path):
    """save_entries is faked in the tests above, so exercise the real write once."""
    monkeypatch.undo()  # drop the autouse fakes
    availability.cache.cache_clear()
    settings = availability.get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    availability.save_entries({550: entry({"IN": ["Netflix"]})})

    written = json.loads((tmp_path / "providers.json").read_text())
    assert written["550"]["flatrate"] == {"IN": ["Netflix"]}
    availability.cache.cache_clear()
