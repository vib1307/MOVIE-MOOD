"""core.filters: numbers in a query become metadata filters; the rest is the semantic query."""

import pytest

from moviemood.core.filters import Filters, describe, parse_filters, to_where


@pytest.mark.parametrize(
    "query, semantic, label",
    [
        ("action movies with more 8.5 above imdb", "action movies", "IMDb 8.5+"),  # the reported bug
        ("imdb 8.5+ thriller", "thriller", "IMDb 8.5+"),
        ("comedy rated 7 or more", "comedy", "IMDb 7+"),
        ("8 se upar rating wali action film", "action film", "IMDb 8+"),
        ("movies with imdb below 6", "movies", "IMDb up to 6"),
        ("Romantic 90s movies, no sad ending", "Romantic movies, no sad ending", "1990–1999"),
        ("romantic comedy from the 90s", "romantic comedy", "1990–1999"),
        ("Sci-Fi after 2010", "Sci-Fi", "2010 or later"),
        ("horror before 2000", "horror", "1999 or earlier"),
        ("films between 2000 and 2010", "films", "2000–2010"),
        ("funny movie under 2 hours", "funny movie", "up to 120 min"),
        ("2 ghante se kam wali comedy", "comedy", "up to 120 min"),
        ("rating above 8", "a good movie", "IMDb 8+"),  # nothing left: generic query
        ("comedy under 2 hours imdb 7+", "comedy", "IMDb 7+ · up to 120 min"),  # "under" is the runtime's
    ],
)
def test_parse(query, semantic, label):
    got_semantic, f = parse_filters(query)
    assert got_semantic == semantic
    assert describe(f) == label


@pytest.mark.parametrize("query", ["top 10 action movies", "2012", "Brad Pitt", "cozy and light", "Ra.One"])
def test_no_filter_leaves_query_untouched(query):
    semantic, f = parse_filters(query)
    assert semantic == query and not f.active()


def test_where():
    assert to_where(Filters()) is None
    assert to_where(Filters(min_rating=8.5)) == {"imdb_rating": {"$gte": 8.5}}
    assert to_where(Filters(min_rating=8, min_year=2000)) == {
        "$and": [{"imdb_rating": {"$gte": 8}}, {"year": {"$gte": 2000}}]
    }
