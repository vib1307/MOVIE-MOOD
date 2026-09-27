"""Evidence for the "Why this pick" panel. See D-024.

The LLM's one-line "why" is an opinion. This adds the facts behind a match:
    named:     people from the query who are in the movie ("Starring Brad Pitt")
    mood_tags: the movie's genres/keywords closest in meaning to the query, using the
               same nomic embeddings as search ("cozy and light" -> feel-good, friendship)
"""

import logging

import numpy as np

from moviemood.core.catalog import get_catalog
from moviemood.core.filters import parse_filters
from moviemood.core.models import Recommendation
from moviemood.core.semantic_text import BLOB_CAST, name_in_query, words_outside
from moviemood.core.vectorstore import get_vectorstore

log = logging.getLogger(__name__)

TOP_TAGS = 3
# Cosine similarity a tag needs to count as "close to your mood". Measured on the
# catalog: real matches sit at 0.42+ ("mind-bending" -> mind reading 0.61, time warp
# 0.49), noise at 0.35-0.40 ("cozy and light" -> silent film 0.36).
MIN_TAG_SIMILARITY = 0.42
# Filler around a name ("Brad Pitt movies", "SRK ki film"): not a mood to match tags to.
FILLER = {"movie", "movies", "film", "films", "ki", "ke", "ka", "wali", "wala", "jaisi",
          "like", "with", "by", "starring", "something", "kuch", "the", "a", "and"}
_tag_vectors: dict[str, np.ndarray] = {}  # tag -> unit vector; tags repeat across searches


def explain(query: str, recs: list[Recommendation]) -> None:
    """Fill `named` and `mood_tags` on each recommendation, in place.

    Never raises: the panel is a nice-to-have, so on failure the fields stay empty.
    """
    query, _ = parse_filters(query)  # tags match the mood words, not "imdb 8.5+"
    catalog = get_catalog()
    people: set[str] = set()  # every name from the query found in some movie
    for rec in recs:
        movie = catalog.get(rec.tmdb_id)
        if movie is None:
            continue
        cast = [n for n in movie.top_cast[:BLOB_CAST] if name_in_query(n, query, 1)]
        directors = [n for n in movie.directors if name_in_query(n, query, 1)]
        rec.named = [f"Starring {n}" for n in cast] + [f"Directed by {n}" for n in directors]
        people.update(cast + directors)

    # "Brad Pitt" is a name, not a mood: tags "close" to it are noise (Once Upon a
    # Time... in Hollywood -> "sharon tate" 0.49). Skip tags when only names + filler remain.
    if not [w for w in words_outside(list(people), query) if w not in FILLER]:
        return
    try:
        _add_mood_tags(query, recs)
    except Exception as e:  # e.g. Ollama hiccup: show the panel without tags
        log.warning("mood tags failed: %s: %s", type(e).__name__, e)


def _add_mood_tags(query: str, recs: list[Recommendation]) -> None:
    catalog = get_catalog()
    tags_by_rec = {}
    for rec in recs:
        movie = catalog.get(rec.tmdb_id)
        if movie is not None:
            # dict.fromkeys: dedupe but keep order ("Comedy" genre + "comedy" keyword)
            tags_by_rec[rec.tmdb_id] = list(dict.fromkeys(t.lower() for t in movie.genres + movie.keywords))
    all_tags = {t for tags in tags_by_rec.values() for t in tags}
    if not all_tags:
        return

    embeddings = get_vectorstore().embeddings
    new = sorted(all_tags - _tag_vectors.keys())
    if new:  # one batched call for every tag we haven't seen before
        for tag, vec in zip(new, embeddings.embed_documents(new)):
            _tag_vectors[tag] = _unit(vec)
    q = _unit(embeddings.embed_query(query))

    for rec in recs:
        tags = tags_by_rec.get(rec.tmdb_id) or []
        # cosine similarity = dot product of unit vectors
        scored = sorted(((float(_tag_vectors[t] @ q), t) for t in tags), reverse=True)
        rec.mood_tags = [t for sim, t in scored[:TOP_TAGS] if sim >= MIN_TAG_SIMILARITY]


def _unit(vec: list[float]) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32)
    return v / (np.linalg.norm(v) or 1.0)
