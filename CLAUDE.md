# MovieMood

Semantic, mood-based movie discovery. Users describe a feeling in plain language ("feel-good, no sad ending") and get matching films with a "why this fits" explanation. Data: TMDB (overview, keywords, genres, cast, posters) + OMDb (IMDb rating). Embeddings: Ollama `nomic-embed-text`. LLM: Ollama `llama3.2`. Vector DB: Chroma. Orchestration: LangChain. Python 3.11+.

## Working style (important)
- The user writes the code. They're a JS/Angular dev learning Python and AI/ML.
- Give scaffolds, hints, structure, and the why/what/where/when. Point out mistakes and explain them.
- Default: don't write full solutions or edit source files on your own initiative.
- When the user asks you to code ("code it", "write it", "implement X"), write the code directly in `src/`/`scripts/`, no pushback. Afterwards, briefly explain the key parts with easy examples so they still learn.
- Keep responses focused and short.
- When a design decision is made or code changes, append an entry to `docs/DECISIONS.md` in the same turn.

## Architecture
- `src/moviemood/core/` is framework-free RAG logic. **It never imports FastAPI or Gradio.**
- `src/moviemood/api/` is FastAPI (REST, `/api/v1/*`, Swagger at `/docs`).
- `src/moviemood/ui/` is Gradio, mounted inside the FastAPI app at `/`. It calls `core` directly, not over HTTP.
- One uvicorn process serves everything. Deploy target: AWS EC2 + nginx + HTTPS, with Ollama on the same box.

```
src/moviemood/
  config.py                       # pydantic-settings, reads .env
  ingest/  tmdb.py omdb.py
  core/    models.py semantic_text.py vectorstore.py recommender.py
  api/     main.py routes.py schemas.py
  ui/      gradio_app.py
scripts/   fetch_movies.py build_index.py
```

## Progress
- Full phase plan and checkpoints: `docs/PLAN.md`
- Why things are the way they are: `docs/DECISIONS.md`
- **Current phase: 5 (FastAPI)**. Phases 0–4 are done (`recommend()` in `core/recommender.py`; checks: `scripts/try_blobs.py`, `scripts/try_recommend.py`). Update this line when a phase checkpoint passes.

## Commands
- `source venv/bin/activate`
- `python scripts/fetch_movies.py` (TMDB/OMDb → `data/movies.json`)
- `python scripts/build_index.py` (embed → `chroma_db/`)
- `uvicorn moviemood.api.main:app --reload` (UI at `/`, API docs at `/docs`)

## Tools
- **Context7 MCP** (`.mcp.json`): look up current LangChain / Chroma / Gradio / FastAPI / Ollama APIs before giving hints. These libraries change fast, so don't rely on memory.
- **Superpowers** plugin: brainstorm / write-plan / debugging skills are welcome. Its execution skills (TDD, execute-plan, subagent-driven development) still follow the working-style rule above: plan, review, and hint by default; write `src/`/`scripts/` code only when the user asks.
- **`/grill-me`** (mattpocock-skills): use it before starting each phase to stress-test the design.
- **`/review-phase <n>`**: after finishing a phase, check the code against its checkpoint.

## Gotchas
- `.env` holds API keys: never read it, print it, or commit it.
- nomic-embed-text needs the `search_document:` / `search_query:` prefixes.
- Chroma metadata must be flat scalars (join lists into strings).
