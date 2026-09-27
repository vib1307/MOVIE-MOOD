"""TMDBClient.discover_ids sizing, with /discover faked: no network, no API key needed."""

from moviemood.ingest.tmdb import TMDBClient


def client(results: dict[int, list[int]]) -> TMDBClient:
    """A TMDBClient whose _discover returns up to `want` ids from a fixed list per genre."""
    c = TMDBClient.__new__(TMDBClient)  # skip __init__: it needs settings and a session
    calls = []

    def fake_discover(want, **filters):
        calls.append(want)
        key = filters.get("with_genres", filters.get("with_original_language"))
        return results[key][:want]

    c._discover = fake_discover
    c.calls = calls
    return c


def test_overlapping_genres_still_reach_the_limit():
    # Both genres share their first 4 ids: asking each for 5 gives only 6 unique.
    shared = [1, 2, 3, 4]
    c = client({35: shared + [10, 11, 12, 13, 14, 15], 18: shared + [20, 21, 22, 23, 24, 25]})
    ids = c.discover_ids(10, genres={"Comedy": 35, "Drama": 18}, indian_languages=())
    assert len(ids) == 10 and len(set(ids)) == 10
    assert c.calls[0] == 5 and max(c.calls) > 5  # asked again for more


def test_stops_when_results_run_out():
    c = client({35: [1, 2], 18: [2, 3]})
    ids = c.discover_ids(10, genres={"Comedy": 35, "Drama": 18}, indian_languages=())
    assert sorted(ids) == [1, 2, 3]  # everything there is, no endless loop
