"""core.explain + the name matcher, with a fake embedding model: no Ollama needed."""

import numpy as np
import pytest

from moviemood.core import explain as ex
from moviemood.core.models import Movie, Recommendation
from moviemood.core.semantic_text import build_document_text, name_in_query, words_outside

# Tiny 2-D "embedding space": dark things point one way, light things the other.
VECTORS = {"grimdark": [1, 0], "neo-noir": [0.9, 0.1], "comedy": [0, 1], "thriller": [0.7, 0.3]}
QUERIES = {"dark and scary": [1, 0], "brad pitt dark": [1, 0]}


class FakeEmbeddings:
    def embed_documents(self, texts):
        return [VECTORS.get(t, [0.5, 0.5]) for t in texts]

    def embed_query(self, text):
        return QUERIES.get(text, [0.5, 0.5])


class FakeStore:
    embeddings = FakeEmbeddings()


MOVIES = {
    1: Movie(tmdb_id=1, title="Se7en", top_cast=["Brad Pitt", "Morgan Freeman"], directors=["David Fincher"],
             genres=["Thriller"], keywords=["grimdark", "neo-noir"]),
    2: Movie(tmdb_id=2, title="Minions", top_cast=["Steve Carell"], genres=["Comedy"]),
}


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(ex, "get_catalog", lambda: MOVIES)
    monkeypatch.setattr(ex, "get_vectorstore", lambda: FakeStore())
    ex._tag_vectors.clear()


def recs():
    return [Recommendation(tmdb_id=i, title=MOVIES[i].title, why="w", source="llm", distance=0.3) for i in MOVIES]


def test_mood_tags_are_the_closest_tags_above_the_threshold():
    rs = recs()
    ex.explain("dark and scary", rs)
    assert rs[0].mood_tags == ["grimdark", "neo-noir", "thriller"]
    assert rs[1].mood_tags == []  # "comedy" points the other way: below MIN_TAG_SIMILARITY
    assert rs[0].named == []


def test_named_people_and_no_tags_for_a_name_only_query():
    rs = recs()
    ex.explain("Brad Pitt movies", rs)
    assert rs[0].named == ["Starring Brad Pitt"]
    assert rs[0].mood_tags == []  # only a name + filler: nothing to match tags to


def test_name_plus_mood_gets_both():
    rs = recs()
    ex.explain("brad pitt dark", rs)
    assert rs[0].named == ["Starring Brad Pitt"] and rs[0].mood_tags[0] == "grimdark"


def test_directors_are_named():
    rs = recs()
    ex.explain("david fincher", rs)
    assert rs[0].named == ["Directed by David Fincher"]


def test_embedding_failure_leaves_tags_empty(monkeypatch):
    class Broken:
        embeddings = None  # AttributeError on use

    monkeypatch.setattr(ex, "get_vectorstore", lambda: Broken())
    rs = recs()
    ex.explain("dark and scary", rs)  # must not raise
    assert rs[0].mood_tags == []


def test_name_matching():
    assert name_in_query("Brad Pitt", "brad pit movies", 1)  # typo OK
    assert not name_in_query("Christoph Waltz", "christopher nolan", 1)  # strict: all words
    assert name_in_query("Christoph Waltz", "christopher nolan")  # lenient default: half
    assert words_outside(["Brad Pitt"], "Brad Pitt dark movies") == ["dark", "movies"]


def test_blob_has_director_line():
    assert "Directed by: David Fincher" in build_document_text(MOVIES[1])
    assert "Directed by" not in build_document_text(MOVIES[2])
