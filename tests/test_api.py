"""API tests. Core is replaced with fakes, so these run in ~1s without Ollama or Chroma.

TestClient(app) is used without `with`, so the lifespan (data loading, Ollama
warm-up) doesn't run.
"""

import pytest
from fastapi.testclient import TestClient
from ollama import ResponseError

from moviemood.api import routes
from moviemood.api.main import app
from moviemood.core.models import Movie, Recommendation

DDLJ = Movie(
    tmdb_id=19404,
    title="Dilwale Dulhania Le Jayenge",
    year=1995,
    runtime=190,
    genres=["Comedy", "Drama", "Romance"],
    top_cast=["Kajol", "Shah Rukh Khan"],
    poster_path="/ddlj.jpg",
    imdb_rating=8.0,
)


@pytest.fixture
def client() -> TestClient:
    # raise_server_exceptions=False: let exception handlers turn errors into responses
    return TestClient(app, raise_server_exceptions=False)


def fake_recommend(query: str, k: int = 5, rerank: bool = True) -> list[Recommendation]:
    return [
        Recommendation(
            tmdb_id=DDLJ.tmdb_id, title=DDLJ.title, year=DDLJ.year, runtime=DDLJ.runtime,
            poster_path=DDLJ.poster_path, imdb_rating=DDLJ.imdb_rating,
            why="Romantic comedy with Shah Rukh Khan and Kajol", source="llm", distance=0.286,
        )
    ][:k]


# --- POST /api/v1/recommend ------------------------------------------------

def test_recommend_returns_api_shape(client, monkeypatch):
    monkeypatch.setattr(routes, "recommend", fake_recommend)
    resp = client.post("/api/v1/recommend", json={"query": "romantic Shah Rukh Khan"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "romantic Shah Rukh Khan"
    result = body["results"][0]
    assert result["poster_url"] == "https://image.tmdb.org/t/p/w500/ddlj.jpg"
    assert result["why"].startswith("Romantic comedy")
    assert "distance" not in result and "source" not in result  # internal fields stay internal


def test_recommend_passes_k_and_rerank_through(client, monkeypatch):
    calls = []
    monkeypatch.setattr(routes, "recommend", lambda q, k, rerank: calls.append((q, k, rerank)) or [])
    client.post("/api/v1/recommend", json={"query": "  cozy  ", "k": 3, "rerank": False})
    assert calls == [("cozy", 3, False)]  # whitespace stripped


@pytest.mark.parametrize("body", [
    {"query": "hi"},                    # too short
    {"query": "x" * 301},               # too long
    {"query": "cozy", "k": 0},
    {"query": "cozy", "k": 11},
    {},                                 # missing query
])
def test_recommend_rejects_bad_input(client, monkeypatch, body):
    monkeypatch.setattr(routes, "recommend", fake_recommend)
    assert client.post("/api/v1/recommend", json=body).status_code == 422


@pytest.mark.parametrize("error", [
    ConnectionError("Failed to connect to Ollama"),
    ResponseError('model "llama3.2" not found', 404),
])
def test_recommend_503_when_ollama_unavailable(client, monkeypatch, error):
    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(routes, "recommend", broken)
    resp = client.post("/api/v1/recommend", json={"query": "cozy feel-good"})
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"]


# --- GET /api/v1/movies/{id} -----------------------------------------------

def test_movie_found(client, monkeypatch):
    monkeypatch.setattr(routes, "get_catalog", lambda: {DDLJ.tmdb_id: DDLJ})
    resp = client.get("/api/v1/movies/19404")
    assert resp.status_code == 200
    assert resp.json()["title"] == DDLJ.title
    assert resp.json()["top_cast"] == ["Kajol", "Shah Rukh Khan"]  # full lists, not joined strings
    assert resp.json()["poster_url"].endswith("/ddlj.jpg")


def test_movie_not_found(client, monkeypatch):
    monkeypatch.setattr(routes, "get_catalog", lambda: {})
    resp = client.get("/api/v1/movies/999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Movie not found"}


# --- GET /health -----------------------------------------------------------

def test_health_ok(client, monkeypatch):
    monkeypatch.setattr(routes, "ollama_ready", lambda: True)
    monkeypatch.setattr(routes, "movies_indexed", lambda: 20)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "ollama": True, "movies_indexed": 20}


def test_health_degraded_when_ollama_down(client, monkeypatch):
    monkeypatch.setattr(routes, "ollama_ready", lambda: False)
    monkeypatch.setattr(routes, "movies_indexed", lambda: 20)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
