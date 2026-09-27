"""core.lazy_ingest with TMDB, OMDb, Ollama and storage all faked: no network needed."""

import pytest

from moviemood.core import lazy_ingest as li
from moviemood.core.lazy_ingest import QueryIntent, find_new_movies


class FakeTMDB:
    """Just enough of TMDBClient. details() returns a record with `kw` keywords."""

    def __init__(self, people=(), cast=(), search=(), recs=(), keywords=None):
        self.people, self.cast, self.search, self.recs = list(people), list(cast), list(search), list(recs)
        self.keywords = keywords or {}
        self.mood = {}  # {used keywords?: results} for discover()

    def search_person(self, name):
        return self.people

    def movie_credits(self, person_id):
        return {"cast": self.cast, "crew": []}

    def search_movie(self, title):
        return self.search

    def recommendations(self, tmdb_id):
        return self.recs

    def search_keyword(self, name):
        return [{"id": 900 + len(name), "name": name}] if name != "unknown" else []

    def discover(self, **filters):
        self.discovered = getattr(self, "discovered", []) + [filters]
        return self.mood.get("with_keywords" in filters, [])

    def details(self, tmdb_id):
        n = self.keywords.get(tmdb_id, 5)
        return {
            "id": tmdb_id, "title": f"Movie {tmdb_id}", "overview": "A story.",
            "keywords": {"keywords": [{"name": f"k{i}"} for i in range(n)]},
        }


def item(tmdb_id, votes=1000, date="2000-01-01", order=0, adult=False, title="T"):
    return {"id": tmdb_id, "vote_count": votes, "release_date": date, "order": order, "adult": adult,
            "title": title}


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    monkeypatch.setattr(li, "get_catalog", lambda: {1: object()})  # id 1 is already in the catalog
    monkeypatch.setattr(li, "_add_ratings", lambda movies: None)
    li._tried.clear()


def ids(movies):
    return [m.tmdb_id for m in movies]


def test_actor_lead_roles_most_voted_first_skipping_known_and_filtered():
    tmdb = FakeTMDB(
        people=[{"id": 9, "known_for_department": "Acting"}],
        cast=[
            item(2, votes=500), item(3, votes=900),
            item(1),                      # already in the catalog
            item(4, order=20),            # cameo: name wouldn't be in the blob
            item(5, votes=50),            # too few votes
            item(6, date="2999-01-01"),   # not released long enough ago
            item(7, adult=True),
            item(8, date=""),             # no release date
        ],
    )
    assert ids(find_new_movies(QueryIntent(person="X"), tmdb)) == [3, 2]


def test_thin_blobs_are_skipped_and_max_new_is_capped():
    cast = [item(i, votes=10_000 - i) for i in range(10, 20)]
    tmdb = FakeTMDB(people=[{"id": 9, "known_for_department": "Acting"}], cast=cast, keywords={10: 2})
    movies = find_new_movies(QueryIntent(person="X"), tmdb)
    assert ids(movies) == [11, 12, 13, 14, 15]  # 10 has 2 keywords; cap is MAX_NEW=5


def test_title_picks_most_voted_match_then_most_voted_recs():
    tmdb = FakeTMDB(
        search=[item(20, votes=10, title="Inception"), item(21, votes=30_000, title="Inception"),
                item(22, votes=90_000, title="Tommy Boy")],  # not a match for the title: ignored
        recs=[item(30, votes=100), item(31, votes=9000), item(32, votes=5000)],
    )
    assert ids(find_new_movies(QueryIntent(title="Inception"), tmdb)) == [21, 31, 32, 30]


@pytest.mark.parametrize(
    "name, query, kept",
    [
        ("Brad Pitt", "Brad Pitt", True),
        ("Brad Pitt", "brad pit movies", True),  # typo still matches
        ("Inception", "Inception jaisi movie", True),
        ("A Light Hearted, No Crying Required", "kuch halka sa, rona nahi chahiye", False),
        (None, "anything", False),
    ],
)
def test_names_must_appear_in_query(name, query, kept):
    assert (li._in_query(name, query) is not None) == kept


def test_lazy_ingest_stores_new_movies_and_remembers_the_query(monkeypatch):
    new = find_new_movies(QueryIntent(title="T"), FakeTMDB(search=[item(40)]))
    stored, extracted = [], []
    monkeypatch.setattr(li, "extract_intent", lambda q, mood_only=False: extracted.append(q) or QueryIntent(title="T"))
    monkeypatch.setattr(li, "find_new_movies", lambda names: new)
    monkeypatch.setattr(li, "add_to_index", lambda movies: stored.append(("index", ids(movies))))
    monkeypatch.setattr(li, "add_to_catalog", lambda movies: stored.append(("catalog", ids(movies))))

    assert ids(li.lazy_ingest("T movie")) == [40]
    assert stored == [("index", [40]), ("catalog", [40])]  # Chroma first
    assert li.lazy_ingest("t MOVIE ") == []  # same query again: skipped
    assert extracted == ["T movie"]


def test_tmdb_failure_is_not_remembered(monkeypatch):
    def broken(names):
        raise li.TMDBError("down")

    monkeypatch.setattr(li, "extract_intent", lambda q: QueryIntent(person="Brad Pitt"))
    monkeypatch.setattr(li, "find_new_movies", broken)
    assert li.lazy_ingest("Brad Pitt") == []
    assert "brad pitt" not in li._tried  # retried next time


def test_directors_bring_the_movies_they_directed():
    tmdb = FakeTMDB(people=[{"id": 9, "known_for_department": "Directing"}], cast=[item(50)])
    crew = [{**item(51, votes=500), "job": "Director"}, {**item(52, votes=900), "job": "Director"},
            {**item(53), "job": "Producer"}]
    tmdb.movie_credits = lambda person_id: {"cast": [item(50)], "crew": crew}
    assert ids(find_new_movies(QueryIntent(person="Nolan"), tmdb)) == [52, 51]  # not the acting role or producing


def test_add_to_catalog_records_added_ids(tmp_path, monkeypatch):
    from moviemood.core import catalog

    class S:
        movies_json = tmp_path / "movies.json"
        added_ids_json = tmp_path / "added_ids.json"

    monkeypatch.setattr(catalog, "get_settings", lambda: S)
    monkeypatch.setattr(catalog, "get_catalog", lambda cache={}: cache)
    new = find_new_movies(QueryIntent(title="T"), FakeTMDB(search=[item(40)], recs=[item(41)]))
    catalog.add_to_catalog(new)
    catalog.add_to_catalog(new[:1])  # re-adding doesn't duplicate ids
    assert catalog.added_ids() == [40, 41]
    assert len(__import__("json").loads(S.movies_json.read_text())) == 2


def test_mood_uses_keywords_and_genres_most_voted_first():
    tmdb = FakeTMDB()
    tmdb.mood = {True: [item(60), item(61, votes=50), item(62)]}  # 61: too few votes
    intent = QueryIntent(genres=["Drama", "Romance"], keywords=["tearjerker"])
    assert ids(find_new_movies(intent, tmdb)) == [60, 62]
    first = tmdb.discovered[0]
    assert first["with_keywords"] == str(900 + len("tearjerker")) and first["with_genres"] == "18|10749"


def test_mood_falls_back_to_genres_when_keywords_are_unknown():
    tmdb = FakeTMDB()
    tmdb.mood = {False: [item(70)]}
    assert ids(find_new_movies(QueryIntent(genres=["Comedy", "Family"], keywords=["unknown"]), tmdb)) == [70]
    assert tmdb.discovered == [{"with_genres": "35,10751", "vote_count.gte": 100}]  # AND, no keywords


def test_named_query_ignores_mood_guess():
    tmdb = FakeTMDB(people=[{"id": 9, "known_for_department": "Acting"}], cast=[item(2)])
    tmdb.mood = {True: [item(80)]}
    intent = QueryIntent(person="X", genres=["Drama"], keywords=["grief"])
    assert ids(find_new_movies(intent, tmdb)) == [2]


def test_title_that_is_really_a_mood_is_read_again(monkeypatch):
    calls = []

    def fake_extract(q, mood_only=False):
        calls.append(mood_only)
        return QueryIntent(genres=["Drama"]) if mood_only else QueryIntent(title="A Good Cry")

    monkeypatch.setattr(li, "extract_intent", fake_extract)
    mood_film = find_new_movies(QueryIntent(title="T"), FakeTMDB(search=[item(90)]))
    monkeypatch.setattr(li, "find_new_movies", lambda intent: [] if intent.title else mood_film)
    monkeypatch.setattr(li, "add_to_index", lambda movies: None)
    monkeypatch.setattr(li, "add_to_catalog", lambda movies: None)
    assert li.lazy_ingest("a good cry") == mood_film
    assert calls == [False, True]


def test_title_search_without_a_real_match_gives_nothing():
    tmdb = FakeTMDB(search=[item(20, title="Bad Grandpa"), item(21, title="Tommy Boy")], recs=[item(30)])
    assert find_new_movies(QueryIntent(title="A Good Cry"), tmdb) == []
