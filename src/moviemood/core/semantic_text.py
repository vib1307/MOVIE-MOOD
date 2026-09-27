"""What gets embedded (the document text) vs. what stays metadata. See D-013.

Pure functions: no I/O, no framework imports, no embedding-model details.
The nomic `search_document:` / `search_query:` prefixes are added by the
embedding wrapper in vectorstore.py, not here.
"""

import difflib
import re

from moviemood.core.models import Movie

BLOB_CAST = 3  # names help "romantic Shah Rukh Khan"-style queries; more would dilute mood

Metadata = dict[str, str | int | float | bool]


def build_document_text(movie: Movie) -> str:
    """Labeled lines of meaning-bearing text. Empty fields are left out.

    Title: Forrest Gump
    Genres: Comedy, Drama, Romance
    Starring: Tom Hanks, Robin Wright, Gary Sinise
    Directed by: Robert Zemeckis
    Overview: A man with a low IQ has accomplished great things...
    Keywords: friendship, vietnam war, ..., feelgood, dramatic
    """
    lines = [
        ("Title", movie.title),
        ("Genres", ", ".join(movie.genres)),
        ("Starring", ", ".join(movie.top_cast[:BLOB_CAST])),
        ("Directed by", ", ".join(movie.directors)),  # "christopher nolan" queries (D-024)
        ("Overview", movie.overview.strip()),
        # All keywords, no cap: TMDB lists the mood words (feelgood, optimism) last.
        ("Keywords", ", ".join(movie.keywords)),
    ]
    return "\n".join(f"{label}: {value}" for label, value in lines if value)


def to_metadata(movie: Movie) -> Metadata:
    """Flat scalar metadata for Chroma: lists joined into strings, None keys omitted.

    Omitting a key (instead of a placeholder like -1) keeps filters such as
    imdb_rating >= 7 correct: a movie without a rating simply doesn't match.
    """
    fields = {
        "tmdb_id": movie.tmdb_id,
        "title": movie.title,
        "year": movie.year,
        "runtime": movie.runtime,
        "imdb_rating": movie.imdb_rating,
        "imdb_id": movie.imdb_id,
        "poster_path": movie.poster_path,
        "original_language": movie.original_language,
        "genres": ", ".join(movie.genres) or None,
        "top_cast": ", ".join(movie.top_cast) or None,
        "directors": ", ".join(movie.directors) or None,
    }
    return {key: value for key, value in fields.items() if value is not None}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def name_in_query(name: str | None, query: str, min_share: float = 0.5) -> bool:
    """True if at least `min_share` of the words of `name` appear in the query, allowing
    typos: ("Brad Pitt", "brad pit movies") -> True; ("Titanic", "cozy and light") -> False.

    The default (half) is lenient, for checking an LLM-extracted name. Use min_share=1
    to label a movie: with half, "Christoph Waltz" matched "christopher nolan".
    """
    words = _words(name or "")
    if not words:
        return False
    found = sum(_close(w, _words(query)) for w in words)
    return found >= min_share * len(words)


def _close(word: str, candidates: list[str]) -> bool:
    return bool(difflib.get_close_matches(word, candidates, n=1, cutoff=0.8))


def words_outside(names: list[str], query: str) -> list[str]:
    """Query words not part of any of `names`: ("Brad Pitt movies", ["Brad Pitt"]) -> ["movies"]."""
    name_words = [w for n in names for w in _words(n)]
    return [w for w in _words(query) if not _close(w, name_words)]
