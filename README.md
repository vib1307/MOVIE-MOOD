# MovieMood

**Describe a feeling, get films that fit — and a reason why.**

Type *"cozy and light"*, *"feel-good, no sad ending"* or *"kuch halka sa"* and MovieMood
returns ten films, each with a one-line explanation, where it is streaming in your country,
and a "Why this pick" panel showing the evidence behind the match.

🔗 **Live:** https://moviemood.duckdns.org

![MovieMood](Movie%20Mood-selection.png)

---

## What it does

| | |
|---|---|
| **Semantic search, not keywords** | Your mood and every film's "semantic blob" (overview + genres + keywords + cast) become vectors from `nomic-embed-text`. Closest vectors win — no tag matching. |
| **An LLM checks the shortlist** | Embeddings don't understand *"no sad ending"* (they put Titanic first). So an LLM judges the top 20: films that clearly contradict the request get **demoted**, never deleted, and each one gets its own "why". |
| **Filters from plain text** | *"action movies with IMDb 8.5+"*, *"sci-fi after 2010"*, *"funny under 2 hours"* — the numbers become Chroma metadata filters, only the mood is embedded. |
| **Where to watch** | Each card shows the subscription services the film streams on, for a country you pick: `▶ Netflix, JioHotstar (India)`. |
| **It grows itself** | Ask for an actor the catalog doesn't have and MovieMood fetches their films from TMDB mid-search, indexes them, and answers again. |
| **Hinglish works** | *"kuch halka sa"*, *"Shah Rukh Khan ki romantic film"*. |

## How a search runs

```
"cozy and light, IMDb 7+"
   │
   ├─ parse_filters        "IMDb 7+" -> metadata filter;  "cozy and light" -> embedded
   ├─ Chroma               top 20 nearest films  (nomic-embed-text)
   ├─ LLM judge            fits? + a one-line why;  misfits demoted to the bottom
   ├─ explain              cast/director you named + the film's tags closest to your mood
   └─ add_availability     subscription providers for your country (cached)
   │
   └─> 10 cards
```

Nothing is invented: the LLM only ever ranks and explains films that retrieval already found.

## Stack

Python 3.11 · **LangChain** (orchestration) · **Chroma** (vector DB) · **Ollama** `nomic-embed-text`
(embeddings) + `qwen2.5:7b` (LLM locally, OpenAI in production) · **FastAPI** (REST) ·
**Gradio** (UI, mounted inside FastAPI) · **TMDB** + **OMDb** + **JustWatch** (data).

One uvicorn process serves the UI at `/`, the API at `/api/v1/*` and Swagger at `/docs`.

```
src/moviemood/
  config.py          pydantic-settings, reads .env
  ingest/            tmdb.py  omdb.py  cache.py  http.py       external APIs
  core/              framework-free RAG logic — never imports FastAPI or Gradio
    semantic_text.py   what gets embedded
    vectorstore.py     Chroma + nomic
    recommender.py     retrieve -> LLM judges -> rank
    filters.py         "IMDb 8.5+" -> a metadata filter
    explain.py         evidence for the "Why this pick" panel
    availability.py    where to watch, per country
    lazy_ingest.py     grow the catalog on a miss
  api/               FastAPI routes + schemas
  ui/                Gradio app
scripts/             fetch_movies.py  build_index.py  fetch_providers.py
```

---

## Run it locally

### 1. Prerequisites

```bash
# Python 3.11+ and Ollama
brew install python@3.11 ollama        # macOS
ollama serve                           # keep running in another terminal
ollama pull nomic-embed-text           # embeddings (always needed)
ollama pull qwen2.5:7b                 # the LLM judge (~5 GB; llama3.2 is smaller/faster)
```

Free API keys: [TMDB](https://www.themoviedb.org/settings/api) and [OMDb](https://www.omdbapi.com/apikey.aspx).

### 2. Install

```bash
git clone git@github.com:vib1307/MOVIE-MOOD.git
cd MOVIE-MOOD
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && pip install -e .
```

### 3. Configure

Create `.env` in the project root (it is gitignored — never commit it):

```ini
TMDB_API_KEY=your_tmdb_key
OMDB_API_KEY=your_omdb_key

# optional, these are the defaults
OLLAMA_BASE_URL=http://localhost:11434
EMBED_MODEL=nomic-embed-text
LLM_MODEL=qwen2.5:7b
LLM_PROVIDER=ollama          # "openai" uses OPENAI_API_KEY + OPENAI_MODEL instead
```

### 4. Build the data (first time only, ~15 min)

```bash
python scripts/fetch_movies.py --limit 500   # TMDB + OMDb  -> data/movies.json
python scripts/build_index.py                # embeddings   -> chroma_db/
python scripts/fetch_providers.py            # streaming    -> data/providers.json  (~1 min)
```

All three are safe to re-run: raw API responses are cached in `data/cache/`, so a re-run
only fetches what's new.

### 5. Start

```bash
uvicorn moviemood.api.main:app --reload
```

| | |
|---|---|
| UI | http://localhost:8000/ |
| API docs (Swagger) | http://localhost:8000/docs |
| Health | http://localhost:8000/health |

---

## Commands

| What | Command |
|---|---|
| Activate the venv | `source venv/bin/activate` |
| Fetch movies (TMDB + OMDb) | `python scripts/fetch_movies.py --limit 500` |
| Build the vector index | `python scripts/build_index.py` |
| Fetch streaming availability | `python scripts/fetch_providers.py` *(re-run weekly — licences expire)* |
| Refetch all availability | `python scripts/fetch_providers.py --all` |
| Run the app | `uvicorn moviemood.api.main:app --reload` |
| Tests (no Ollama needed, ~2s) | `python -m pytest` |
| Try the retrieval alone | `python scripts/try_blobs.py` |
| Try the recommender alone | `python scripts/try_recommend.py` |
| Compare LLMs on labeled cases | `python scripts/compare_llms.py` |

## API

### `POST /api/v1/recommend`

```bash
curl -s localhost:8000/api/v1/recommend \
  -H 'content-type: application/json' \
  -d '{"query": "feel-good, no sad ending", "k": 5, "region": "IN"}' | jq
```

| Field | Default | Meaning |
|---|---|---|
| `query` | — | 3–300 chars; the mood, optionally with filters |
| `k` | `10` | how many films (1–20) |
| `rerank` | `true` | `false` = retrieval only, no LLM (<1s) |
| `region` | `"IN"` | ISO 3166-1 country for streaming availability |

```json
{
  "query": "feel-good, no sad ending",
  "results": [
    {
      "tmdb_id": 19404,
      "title": "Dilwale Dulhania Le Jayenge",
      "year": 1995,
      "runtime": 190,
      "poster_url": "https://image.tmdb.org/t/p/w500/ddlj.jpg",
      "imdb_rating": 8.0,
      "why": "A warm romance that ends happily, exactly the mood you asked for.",
      "where_to_watch": ["Netflix"]
    }
  ]
}
```

`where_to_watch` is `[]` when the film isn't on a subscription service in that country,
and `null` when availability wasn't looked up.

### `GET /api/v1/movies/{tmdb_id}`
Full metadata for one film.

### `GET /health`
`{"status": "ok", "ollama": true, "movies_indexed": 515}` — `503` when Ollama is down.

---

## Deploying

Full runbook (Hetzner CX23 + nginx + Let's Encrypt + systemd, with Day-2 commands):
**[`deploy/README.md`](deploy/README.md)**. In production the LLM runs on OpenAI
(`LLM_PROVIDER=openai`) and Ollama on the box only serves embeddings.

## Project docs

| | |
|---|---|
| [`docs/PLAN.md`](docs/PLAN.md) | the build phases and their checkpoints |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | every design decision and **why** (D-001 … D-036) |
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | crash-recovery / where things stand |

## Credits

Film data from [TMDB](https://www.themoviedb.org/) (this product uses the TMDB API but is
not endorsed or certified by TMDB), ratings from [OMDb](https://www.omdbapi.com/), and
streaming availability from [JustWatch](https://www.justwatch.com/) via TMDB.
