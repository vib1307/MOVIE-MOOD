"""Phase 4 checkpoint: does the LLM re-rank improve on plain retrieval?

For each query, prints retrieval-only results next to the re-ranked ones.

Usage:
    python scripts/try_recommend.py
    python scripts/try_recommend.py "rainy sunday, something light"
"""

import logging
import sys
import time

from moviemood.core.catalog import get_catalog
from moviemood.core.recommender import recommend

QUERIES = [
    "feel-good, no sad ending",        # retrieval alone put Titanic #1
    "dark scary horror",               # retrieval alone put Minions & Monsters #3
    "romantic Shah Rukh Khan",         # expect DDLJ #1
    "funny animated movie for kids",
    "mind-bending sci-fi",
    "kuch halka sa, rona nahi chahiye",  # Hinglish probe (D-017 Q9): just measure
]
K = 5


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    catalog = get_catalog()

    for query in sys.argv[1:] or QUERIES:
        retrieval = recommend(query, k=K, rerank=False)
        start = time.time()
        reranked = recommend(query, k=K)
        seconds = time.time() - start

        assert all(r.tmdb_id in catalog for r in reranked), "unknown id returned"
        assert all(r.why for r in reranked), "missing why"

        print(f'\n"{query}"   (re-rank took {seconds:.1f}s)')
        print(f"   {'retrieval only':<32}  re-ranked")
        for before, after in zip(retrieval, reranked):
            print(f"   {before.title[:32]:<32}  {after.title} [{after.source}]")
        for r in reranked:
            print(f"      - {r.title}: {r.why}")


if __name__ == "__main__":
    main()
