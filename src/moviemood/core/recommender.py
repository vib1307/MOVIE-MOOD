"""recommend(query): retrieve candidates from Chroma, LLM demotes misfits + explains. See D-017, D-018.

Flow:
    query -> Chroma top `candidates` (retrieval order)
          -> LLM judges the top `2*k`: fits yes/no + "why"
          -> order: fits (retrieval order) -> judged misfits -> unjudged rest
The LLM never reorders freely and never removes: in testing, llama3.2 (3B) re-ranking
made good retrieval results worse, and hard removal over-vetoed (Zootopia 2 "not a
comedy") so far-down junk filled the gaps. Demotion keeps the upside (Titanic drops
for "no sad ending") while a wrong veto only moves a movie down a few places.

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

SYSTEM_PROMPT = """You check movie recommendations. The user describes a mood or what they want to watch.
You get a numbered list of candidate movies. For EACH candidate, decide if it fits the request.

Rules:
- Answer for every candidate number in the list, in order.
- fits=false when the movie clearly goes against the request:
  - negatives: "no sad ending" -> a story that ends tragically does not fit; "not scary" -> horror does not fit.
  - wrong kind of movie: a family cartoon does not fit "dark scary horror", even if its title shares a word.
- Otherwise fits=true.
- why: one short sentence (max 25 words) about THIS movie and THIS request. Do not copy the overview.
  For fits=true say why it matches; for fits=false say what goes against the request."""


class _Verdict(BaseModel):
    n: int = Field(description="candidate number from the list")
    fits: bool = Field(description="false only if the movie clearly goes against the request")
    why: str = Field(description="one sentence about this movie and this request")


class _Judgement(BaseModel):
    # min_length=1 becomes "minItems": 1 in the JSON schema. Without it llama3.2
    # answered {"picks": []} for every query in the first version (D-017).
    verdicts: list[_Verdict] = Field(min_length=1)


@lru_cache
def _get_llm() -> ChatOllama:
    settings = get_settings()
    return ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=0,  # same query -> same answer (D-017)
        format=_Judgement.model_json_schema(),  # Ollama constrains output to this JSON shape
        client_kwargs={"timeout": LLM_TIMEOUT},
    )


def recommend(
    query: str,
    k: int = 5,
    candidates: int = DEFAULT_CANDIDATES,
    rerank: bool = True,
) -> list[Recommendation]:
    """Top-k movies for a mood query, each with a "why". Always min(k, movies found) results.

    With rerank=True the LLM judges the top 2*k: movies it says fit keep their
    retrieval order and get its "why"; misfits move below them (source="demoted").
    """
    catalog = get_catalog()
    hits = get_vectorstore().similarity_search_with_score(query, k=max(k, candidates))
    # [(Movie, distance)] in retrieval order; skip ids missing from the catalog (stale index)
    pool = [
        (catalog[doc.metadata["tmdb_id"]], distance)
        for doc, distance in hits
        if doc.metadata["tmdb_id"] in catalog
    ]

    window = 2 * k
    verdicts: dict[int, tuple[bool, str]] = {}  # pool index -> (fits, why)
    if rerank and pool:
        verdicts = _llm_verdicts(query, pool[:window])

    # Tier 0: fits, or inside the window but unjudged (LLM skipped it / rerank off)
    # Tier 1: judged a misfit by the LLM (demoted, not removed)
    # Tier 2: beyond the window, never judged
    ranked: list[tuple[int, Recommendation]] = []
    for i, entry in enumerate(pool):
        movie = entry[0]
        if i in verdicts:
            fits, why = verdicts[i]
            if fits and why:
                ranked.append((0, _to_rec(entry, why, "llm")))
            else:
                if not fits:
                    log.info("demoted %s: %s", movie.title, why)
                # the LLM's reason is about a misfit, so show the neutral template instead
                ranked.append((0 if fits else 1, _to_rec(entry, _template_why(movie), "fallback" if fits else "demoted")))
        else:
            tier = 0 if i < window else 2
            ranked.append((tier, _to_rec(entry, _template_why(movie), "fallback")))
    ranked.sort(key=lambda pair: pair[0])  # stable: keeps retrieval order inside each tier
    return [rec for _, rec in ranked[:k]]


def _llm_verdicts(query: str, window: list[tuple[Movie, float]]) -> dict[int, tuple[bool, str]]:
    """Ask the LLM to judge each movie in `window`. {} on any failure."""
    numbered = "\n\n".join(
        f"[{n}]\n{build_document_text(movie)}" for n, (movie, _) in enumerate(window, start=1)
    )
    messages = [
        ("system", SYSTEM_PROMPT),
        (
            "human",
            f"Request: {query}\n\nCandidates:\n{numbered}\n\n"
            f"Judge all {len(window)} candidates. Answer as JSON, for example:\n"
            '{"verdicts": [{"n": 1, "fits": true, "why": "..."}, {"n": 2, "fits": false, "why": "..."}]}',
        ),
    ]
    try:
        raw = _get_llm().invoke(messages).content
        parsed = _Judgement.model_validate_json(raw)
    except Exception as e:  # timeout, Ollama error, bad JSON: all mean "use retrieval order"
        log.warning("LLM judge failed, using retrieval order: %s: %s", type(e).__name__, e)
        return {}

    verdicts: dict[int, tuple[bool, str]] = {}
    for v in parsed.verdicts:
        i = v.n - 1  # the prompt is 1-based
        if not 0 <= i < len(window):
            log.info("LLM judged [%d], not in 1..%d; ignored", v.n, len(window))
            continue
        if i in verdicts:  # keep the first verdict for a number
            continue
        verdicts[i] = (v.fits, v.why.strip())
    return verdicts


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
