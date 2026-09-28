"""HTTP routes. Endpoints are plain `def` (not `async def`): core calls block for
seconds, and FastAPI runs sync endpoints in a threadpool so one slow request
doesn't freeze the server. See D-019."""

import httpx
from fastapi import APIRouter, HTTPException, Response

from moviemood.api.schemas import (
    Health,
    MovieDetail,
    MovieResult,
    RecommendRequest,
    RecommendResponse,
)
from moviemood.config import get_settings
from moviemood.core.catalog import get_catalog
from moviemood.core.recommender import recommend
from moviemood.core.vectorstore import get_vectorstore

router = APIRouter(prefix="/api/v1", tags=["movies"])
health_router = APIRouter(tags=["health"])


@router.post("/recommend", response_model=RecommendResponse)
def recommend_movies(req: RecommendRequest) -> RecommendResponse:
    """Movies matching a mood, each with a one-line "why this fits"."""
    recs = recommend(req.query, k=req.k, rerank=req.rerank)
    return RecommendResponse(
        query=req.query, results=[MovieResult.from_recommendation(r) for r in recs]
    )


@router.get("/movies/{tmdb_id}", response_model=MovieDetail)
def get_movie(tmdb_id: int) -> MovieDetail:
    movie = get_catalog().get(tmdb_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return MovieDetail.from_movie(movie)


@health_router.get("/health", response_model=Health)
def health(response: Response) -> Health:
    """200 when everything works, 503 when Ollama is down or a model it serves is missing."""
    ollama = ollama_ready()
    if not ollama:
        response.status_code = 503
    return Health(status="ok" if ollama else "degraded", ollama=ollama, movies_indexed=movies_indexed())


def ollama_ready() -> bool:
    """Ollama reachable and its models pulled: nomic always, the LLM only when Ollama
    serves it (with LLM_PROVIDER=openai, OpenAI isn't pinged: that would cost money on
    every monitor hit, D-033). Cheap: lists models, runs nothing."""
    settings = get_settings()
    try:
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=2)
        resp.raise_for_status()
    except httpx.HTTPError:
        return False
    names = {m["name"] for m in resp.json().get("models", [])}
    # Ollama reports "llama3.2:latest"; settings may say "llama3.2"
    needed = [settings.embed_model] + ([settings.llm_model] if settings.llm_provider == "ollama" else [])
    return all(model in names or f"{model}:latest" in names for model in needed)


def movies_indexed() -> int:
    return get_vectorstore()._collection.count()
