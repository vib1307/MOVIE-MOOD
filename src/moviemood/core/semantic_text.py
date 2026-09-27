"""What gets embedded (the document text) vs. what stays metadata. See D-013.

Pure functions: no I/O, no framework imports, no embedding-model details.
The nomic `search_document:` / `search_query:` prefixes are added by the
embedding wrapper in vectorstore.py, not here.
"""

from moviemood.core.models import Movie

BLOB_CAST = 3  # names help "romantic Shah Rukh Khan"-style queries; more would dilute mood

Metadata = dict[str, str | int | float | bool]


def build_document_text(movie: Movie) -> str:
    """Labeled lines of meaning-bearing text. Empty fields are left out.

    Title: Forrest Gump
    Genres: Comedy, Drama, Romance
    Starring: Tom Hanks, Robin Wright, Gary Sinise
    Overview: A man with a low IQ has accomplished great things...
    Keywords: friendship, vietnam war, ..., feelgood, dramatic
    """
    lines = [
        ("Title", movie.title),
        ("Genres", ", ".join(movie.genres)),
        ("Starring", ", ".join(movie.top_cast[:BLOB_CAST])),
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
    }
    return {key: value for key, value in fields.items() if value is not None}
