# MovieMood — Build Plan (FastAPI backend + Gradio UI)

The recommender is exposed as a separate REST API (FastAPI) so any client can use it. Gradio is mounted inside the FastAPI app: one process, one port. Both call the same framework-free core. v1 = pure semantic search; hybrid filters/agent = v2.

## How to work each phase
1. **Understand**: get a concept briefing for the phase (Claude checks current APIs via Context7).
2. **Design**: `/grill-me` on your approach.
3. **Build**: write it yourself; ask for hints when stuck.
4. **Checkpoint**: run the ✅ check below.
5. **Review**: `/review-phase N`, fix findings.
   - **Log decisions**: add any new design choices (and their why) to `docs/DECISIONS.md`.
6. **Commit**: `phase-N: <summary>`; update "Current phase" in `CLAUDE.md`.

## Sprint order (thin slice first)
- **A**: Phases 0–4 with ~20 movies, recommending from a plain script (proves the RAG core early).
- **B**: scale Phase 1 to 300–500 movies, then Phase 5 (FastAPI).
- **C**: Phase 6 (Gradio), README + screenshots, Phase 7 (EC2).
- **D**: v2 hybrid filters.

## Phases (each ends with a checkpoint)

### Phase 0 — Environment
- Recreate venv with Python 3.11 (`/opt/homebrew/bin/python3.11`). Old venv was 3.9.6.
- Add `fastapi`, `uvicorn[standard]`, `pydantic-settings` to `requirements.txt`. Pin with `pip freeze` once Phase 5 works.
- Restructure into `src/moviemood/` package; add `pyproject.toml` and `pip install -e .`
- Fill `.env` (`TMDB_API_KEY`, `OMDB_API_KEY`, `OLLAMA_BASE_URL`), `Settings(BaseSettings)` in `config.py`.
- `ollama pull nomic-embed-text` and `ollama pull qwen2.5:7b` (default LLM since D-026; `llama3.2` is the lighter option)

✅ `python -c "import fastapi, gradio, langchain_chroma, moviemood"`; `ollama list` shows both; `git status` doesn't show `.env`.

### Phase 1 — Fetch data (`ingest/`, `scripts/fetch_movies.py`)
TMDB discover across several genres (~300–500 movies), one details call per movie with `append_to_response=keywords,credits,external_ids`, then OMDb by `imdb_id`. Save to `data/movies.json`. Concepts: rate limits, retries, caching re-runs, missing OMDb data.

✅ JSON has N movies each with title, overview, genres, keywords, top cast, runtime, year, poster_path, imdb_rating.

### Phase 2 — Semantic blob (`core/semantic_text.py`)
What goes in the embedded text (overview, keywords, genres, tone) vs. what stays metadata (rating, runtime, year — for v2 filters). Use nomic `search_document:` / `search_query:` prefixes.

✅ Print 3 blobs — do they "sound like" the movie's vibe?

### Phase 3 — Embed + store (`core/vectorstore.py`, `scripts/build_index.py`)
LangChain `Document(page_content=blob, metadata={...})`, `OllamaEmbeddings`, `Chroma(persist_directory=...)`. Stable IDs = TMDB id (idempotent re-index). Flat scalar metadata only.

✅ `similarity_search_with_score("cozy feel-good")` returns sensible titles.

### Phase 4 — Recommender (`core/recommender.py`)
`recommend(query, k)`: retrieve ~15 → prompt llama3.2 with query + candidates → request JSON (ranked ids + one-line "why") → validate; fall back to retrieval order on bad output. Only return candidate ids (no hallucinated movies).

✅ The 5 brief queries return reasonable, explained results from a plain script.

### Phase 5 — FastAPI (`api/`)
| Method | Path | Body / Params | Returns |
|---|---|---|---|
| GET | `/health` | – | `{status, ollama: bool, movies_indexed: int}` |
| POST | `/api/v1/recommend` | `{query: str, k: int = 5}` | `{query, results: [{tmdb_id, title, year, runtime, poster_url, imdb_rating, why}]}` |
| GET | `/api/v1/movies/{tmdb_id}` | – | full movie metadata |

Concepts: Pydantic schemas + validation, `lifespan` to load Chroma once, `Depends`, sync `def` endpoints for blocking Ollama calls, CORS (only for cross-origin browser clients), Ollama down → 503.

✅ `uvicorn moviemood.api.main:app --reload`; test at `/docs`; `curl` a recommend call.

### Phase 6 — Gradio UI (`ui/gradio_app.py`)
`gr.Blocks`: textbox + example chips + movie cards (poster, title, IMDb, why). `gr.mount_gradio_app(app, ui, path="/")` after API routes. Calls `recommend()` directly. SVG logo/placeholder poster.

✅ One uvicorn process serves UI at `/`, API at `/api/v1/*`, docs at `/docs`.

### Phase 6.5 — Lazy ingestion (`core/lazy_ingest.py`)
When every result is demoted, llama3.2 extracts `{person, title}`. TMDB search → ≤ 5 new, filtered movies → OMDb rating → Chroma + `movies.json` → recommend again. It's a third Gradio step; the API is unchanged. See D-023.

✅ "Brad Pitt" (not in the catalog) → step 3 adds his films and shows them; a repeat search is instant, with no step 3.

### Phase 7 — Deploy (Hetzner CAX31, D-028)
uvicorn under systemd, nginx + certbot HTTPS on a DuckDNS subdomain, Ollama as its own systemd service (localhost only). Rsync data once, rebuild the index on the box. Runbook: `deploy/README.md`. Rate limits in nginx.

✅ Public HTTPS URL: UI works, `/docs` reachable, `/health` green.

### v2
~~Hybrid filters via Chroma `where` (runtime, rating, year)~~ done from query text (D-027); explicit API fields are still optional later; LLM/agent query → filters; `/api/v1/movies/{id}/similar`; caching.

## End-to-end verification
1. `python scripts/fetch_movies.py && python scripts/build_index.py`
2. `uvicorn moviemood.api.main:app` → `/health` shows `ollama: true` + count
3. POST the 5 brief queries via `/docs` — valid schema, 5 results each with `why`
4. Same queries in Gradio at `/` match
5. Stop Ollama → 503, not a 500
6. Repeat 2–4 on the EC2 HTTPS URL
