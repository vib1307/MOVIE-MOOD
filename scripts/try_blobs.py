"""Phase 2 checkpoint: do the blobs capture each movie's vibe?

Prints a few blobs, then embeds every movie in memory (no Chroma yet) and shows
the top matches for some mood queries.

Usage:
    python scripts/try_blobs.py
    python scripts/try_blobs.py "rainy sunday, something light"
"""

import json
import sys

import numpy as np
from langchain_ollama import OllamaEmbeddings

from moviemood.config import get_settings
from moviemood.core.models import Movie
from moviemood.core.semantic_text import build_document_text, to_metadata

SHOW_BLOBS = ["Forrest Gump", "Colony", "Dilwale Dulhania Le Jayenge"]
DEFAULT_QUERIES = [
    "feel-good heartwarming, no sad ending",
    "dark scary horror",
    "epic space adventure",
    "romantic Shah Rukh Khan",
    "funny animated movie for kids",
    "tense crime thriller",
]
TOP_K = 3


def main() -> None:
    settings = get_settings()
    movies = [Movie.model_validate(m) for m in json.loads(settings.movies_json.read_text())]
    blobs = [build_document_text(m) for m in movies]

    for movie, blob in zip(movies, blobs):
        if movie.title in SHOW_BLOBS:
            print(f"==== {movie.title}\n{blob}\n-- metadata: {to_metadata(movie)}\n")

    embedder = OllamaEmbeddings(model=settings.embed_model, base_url=settings.ollama_base_url)
    # nomic needs task prefixes. Added by hand here; Phase 3's wrapper will do it automatically.
    doc_vecs = np.array(embedder.embed_documents([f"search_document: {b}" for b in blobs]))
    doc_vecs /= np.linalg.norm(doc_vecs, axis=1, keepdims=True)  # unit length -> dot product = cosine

    queries = sys.argv[1:] or DEFAULT_QUERIES
    for query in queries:
        q = np.array(embedder.embed_query(f"search_query: {query}"))
        scores = doc_vecs @ (q / np.linalg.norm(q))
        best = np.argsort(scores)[::-1][:TOP_K]
        print(f'"{query}"')
        for i in best:
            print(f"   {scores[i]:.3f}  {movies[i].title} ({movies[i].year})")


if __name__ == "__main__":
    main()
