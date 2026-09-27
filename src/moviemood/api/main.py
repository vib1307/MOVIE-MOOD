"""FastAPI app: `uvicorn moviemood.api.main:app --reload` (docs at /docs). See D-019."""

import logging
import threading
from contextlib import asynccontextmanager

import gradio as gr
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ollama import ResponseError

from moviemood.api.routes import health_router, router
from moviemood.config import get_settings
from moviemood.core.catalog import get_catalog
from moviemood.core.vectorstore import get_vectorstore
from moviemood.ui.gradio_app import CSS, HEAD, THEME, build_ui

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Required data: fail fast at startup instead of on the first request.
    settings = get_settings()
    if not settings.movies_json.exists():
        raise RuntimeError(f"{settings.movies_json} missing: run scripts/fetch_movies.py")
    catalog = get_catalog()
    count = get_vectorstore()._collection.count()
    if count == 0:
        raise RuntimeError("Chroma index is empty: run scripts/build_index.py")
    log.info("loaded %d movies, %d indexed", len(catalog), count)

    # Optional: load the models into Ollama's memory so the first user doesn't wait ~20s.
    # Runs in the background and never blocks or fails startup.
    threading.Thread(target=_warm_up_ollama, daemon=True).start()
    yield


def _warm_up_ollama() -> None:
    from moviemood.core.recommender import _get_llm  # local import: only needed here

    try:
        get_vectorstore().embeddings.embed_query("warm up")
        _get_llm().invoke("Reply with an empty verdict list.")
        log.info("Ollama models warmed up")
    except Exception as e:
        log.warning("Ollama warm-up failed (the server still runs): %s: %s", type(e).__name__, e)


app = FastAPI(
    title="MovieMood API",
    description="Describe a mood, get movies that fit, each with a reason.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(health_router)

if get_settings().cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )


# Ollama problems become 503 ("try again shortly") in every route, not 500.
# ConnectionError: Ollama not running. ResponseError: e.g. a model isn't pulled.
# httpx.TimeoutException: Ollama too slow to answer.
@app.exception_handler(ConnectionError)
@app.exception_handler(ResponseError)
@app.exception_handler(httpx.TimeoutException)
async def ollama_unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.error("Ollama unavailable on %s: %s: %s", request.url.path, type(exc).__name__, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Recommendation engine unavailable, try again shortly"},
    )


# Gradio UI at "/". Mounted last so /api/v1/*, /health and /docs are matched first.
# ssr_mode=False: no Node.js process needed on the server (D-020).
app = gr.mount_gradio_app(app, build_ui(), path="/", ssr_mode=False, css=CSS, head=HEAD, theme=THEME)
