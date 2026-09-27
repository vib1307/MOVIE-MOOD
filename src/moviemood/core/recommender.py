"""recommend(query): retrieve candidates from Chroma, re-rank + explain with llama3.2. See D-017.

Flow:
    query -> Chroma top `candidates` -> LLM picks k by number + writes "why"
          -> validate (real numbers only, no duplicates) -> fill gaps in retrieval order
If the LLM step fails in any way, results fall back to plain retrieval order.
Retrieval errors (e.g. Ollama down, so no query embedding) are NOT caught: the API
turns those into a 503.
"""

import logging
from functools import lru_cache

from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

from moviemood.config import get_settings
from moviemood.core.catalog import get_catalog
from moviemood.core.models import Movie, Recommendation
from moviemood.core.semantic_text import build_document_text
from moviemood.core.vectorstore import get_vectorstore

log = logging.getLogger(__name__)

DEFAULT_CANDIDATES = 15
LLM_TIMEOUT = 120  # seconds; CPU-only machines are slow

SYSTEM_PROMPT = """You are a movie recommender. The user describes a mood or what they want to watch.
You get a numbered list of candidate movies. Pick the best matches for the user's request.

Rules:
- Only use numbers from the candidate list.
- Respect negatives: "no sad ending" means skip movies whose story ends tragically;
  "not scary" means skip horror.
- Match the feeling, not just shared words (a family cartoon with "Monsters" in the title is not horror).
- For each pick, write one short sentence (max 25 words) explaining why THIS movie fits THIS request.
  Mention something specific about the movie. Do not start every sentence the same way."""


class _Pick(BaseModel):
    n: int = Field(description="candidate number from the list")
    why: str = Field(description="one sentence: why this movie fits the request")


class _Rerank(BaseModel):
    # min_length=1 becomes "minItems": 1 in the JSON schema. Without it llama3.2
    # sometimes answered {"picks": []}, which is valid JSON but useless.
    picks: list[_Pick] = Field(min_length=1)


@lru_cache
def _get_llm() -> ChatOllama:
    settings = get_settings()
    return ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=0,  # same query -> same answer (D-017)
        format=_Rerank.model_json_schema(),  # Ollama constrains output to this JSON shape
        client_kwargs={"timeout": LLM_TIMEOUT},
    )


def recommend(
    query: str,
    k: int = 5,
    candidates: int = DEFAULT_CANDIDATES,
    rerank: bool = True,
) -> list[Recommendation]:
    """Top-k movies for a mood query, each with a "why". Always returns min(k, candidates found)."""
    catalog = get_catalog()
    hits = get_vectorstore().similarity_search_with_score(query, k=max(k, candidates))
    # [(Movie, distance)] in retrieval order; skip ids missing from the catalog (stale index)
    pool = [
        (catalog[doc.metadata["tmdb_id"]], distance)
        for doc, distance in hits
        if doc.metadata["tmdb_id"] in catalog
    ]

    picks: list[tuple[int, str]] = []  # (index into pool, why)
    if rerank and pool:
        picks = _llm_picks(query, pool, k)

    results = [_to_rec(pool[i], why, "llm") for i, why in picks]
    # Fill the remaining slots in retrieval order.
    used = {i for i, _ in picks}
    for i, entry in enumerate(pool):
        if len(results) >= k:
            break
        if i not in used:
            results.append(_to_rec(entry, _template_why(entry[0]), "fallback"))
    return results


def _llm_picks(query: str, pool: list[tuple[Movie, float]], k: int) -> list[tuple[int, str]]:
    """Ask the LLM for picks; return valid (pool index, why) pairs. [] on any failure."""
    numbered = "\n\n".join(
        f"[{n}]\n{build_document_text(movie)}" for n, (movie, _) in enumerate(pool, start=1)
    )
    messages = [
        ("system", SYSTEM_PROMPT),
        (
            "human",
            f"Request: {query}\n\nCandidates:\n{numbered}\n\n"
            f"Pick the best {k}, best first. Answer as JSON, for example:\n"
            '{"picks": [{"n": 4, "why": "..."}, {"n": 1, "why": "..."}]}',
        ),
    ]
    try:
        raw = _get_llm().invoke(messages).content
        parsed = _Rerank.model_validate_json(raw)
    except Exception as e:  # timeout, Ollama error, bad JSON: all mean "use retrieval order"
        log.warning("LLM re-rank failed, using retrieval order: %s: %s", type(e).__name__, e)
        return []

    picks: list[tuple[int, str]] = []
    seen: set[int] = set()
    for pick in parsed.picks:
        i = pick.n - 1  # the prompt is 1-based
        if not 0 <= i < len(pool):
            log.info("LLM picked [%d], not in 1..%d; dropped", pick.n, len(pool))
            continue
        if i in seen or not pick.why.strip():
            continue
        seen.add(i)
        picks.append((i, pick.why.strip()))
        if len(picks) == k:
            break
    return picks


def _template_why(movie: Movie) -> str:
    # TMDB lists mood keywords last (feelgood, lighthearted), so take from the end.
    words = movie.keywords[-3:][::-1] or movie.genres[:3]
    return f"Close match for your mood: {', '.join(words)}." if words else "Close match for your mood."


def _to_rec(entry: tuple[Movie, float], why: str, source: str) -> Recommendation:
    movie, distance = entry
    return Recommendation(
        tmdb_id=movie.tmdb_id,
        title=movie.title,
        year=movie.year,
        runtime=movie.runtime,
        poster_path=movie.poster_path,
        imdb_rating=movie.imdb_rating,
        why=why,
        source=source,
        distance=round(distance, 4),
    )
