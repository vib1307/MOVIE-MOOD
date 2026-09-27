"""Embed data/movies.json into Chroma (chroma_db/). Full rebuild every run.

Usage:
    python scripts/build_index.py
    python scripts/build_index.py "cozy feel-good" "dark scary horror"   # custom smoke queries
"""

import json
import logging
import sys

from moviemood.config import get_settings
from moviemood.core.models import Movie
from moviemood.core.vectorstore import build_index, get_vectorstore

SMOKE_QUERIES = ["cozy feel-good", "romantic Shah Rukh Khan"]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # ollama client logs every request

    movies = [Movie.model_validate(m) for m in json.loads(get_settings().movies_json.read_text())]
    build_index(movies)

    store = get_vectorstore()
    for query in sys.argv[1:] or SMOKE_QUERIES:
        print(f'"{query}"   (cosine distance, lower = closer)')
        for doc, distance in store.similarity_search_with_score(query, k=3):
            print(f"   {distance:.3f}  {doc.metadata['title']} ({doc.metadata.get('year')})")


if __name__ == "__main__":
    main()
