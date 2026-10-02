# Decision log

The *why* behind MovieMood's design choices. Git records **what** changed; this file records **why**.
Add new entries at the bottom. Don't edit old entries: if a decision changes, add a new entry that says it replaces the old one.

Format: **Decision** / **Why** / **Revisit if**.

---

## D-001 · Phase 0 · Phase numbering + working style
**Decision:** Phase 0 = environment, Phase 1 = fetch data (matches `PLAN.md`; `CLAUDE.md` had them mislabeled). Claude writes code when the user asks ("code it"); otherwise gives hints and scaffolds.
**Why:** The mismatch made "current phase" ambiguous. Hints-only was slowing things down when the user just wanted code.
**Revisit if:** the user wants to go back to writing everything themselves.

## D-002 · Phase 1 · One `Movie` record type in `core/models.py`
**Decision:** A Pydantic `Movie` model is the single normalized record. Only `tmdb_id` and `title` are required; everything else has a default. `ingest/` imports it from `core/`.
**Why:** One source of truth shared by ingest, the vector store, and the API (like a shared TS interface, but validated at runtime). Lenient defaults so patchy TMDB/OMDb data still loads; missing id/title fails loudly.
**Revisit if:** the API needs a different shape (then add schemas in `api/schemas.py`; don't bend the core model).

## D-003 · Phase 1 · Round-robin discover across genres
**Decision:** `TMDBClient.discover_ids(limit)` runs `/discover/movie` per genre (8 genres, `popularity.desc`, `vote_count.gte=300`), interleaves the lists, dedupes, and caps at `limit`. `limit` is a parameter: 20 for Sprint A, 300–500 later.
**Why:** Popularity sort alone clusters in one genre, which means narrow mood coverage. The vote floor filters out obscure titles with thin overviews.
**Revisit if:** results skew too recent (see Open questions).

## D-004 · Phase 1 · Raw per-movie cache on disk
**Decision:** Each raw TMDB details response is saved to `data/cache/tmdb/{id}.json` (gitignored) and reused on re-runs.
**Why:** A crash at movie 340 shouldn't cost 340 API calls again. OMDb's free tier is capped at 1000 requests/day, so the same pattern will apply there.
**Revisit if:** we need fresh data. Delete the cache dir to force a refetch.

## D-005 · Phase 1 · `requests.Session` + urllib3 `Retry`
**Decision:** One session with `Retry(total=5, backoff_factor=1, status_forcelist=[429,500,502,503,504], respect_retry_after_header=True)` and a 10s timeout per request.
**Why:** `requests` is already a dependency. The session reuses connections, and the retry handles rate limits and flaky 5xx errors without hand-written loops.
**Revisit if:** we go async (then switch to `httpx`).

## D-006 · Phase 1 · TMDB auth detection + no key in errors
**Decision:** If the key starts with `eyJ` (a v4 JWT), send it as a Bearer header; otherwise send it as the `api_key` query param. `_get()` raises `TMDBError` with only the path and status, `from None`.
**Why:** Claude may not read `.env`, so the code handles either key type. `requests` error messages include the full URL, which would put a v3 key in logs and tracebacks.
**Revisit if:** never, unless TMDB changes its auth.

## D-007 · Phase 1 · Missing data becomes `None`, don't drop the movie
**Decision:** OMDb `"N/A"`, runtime `0`, and an empty `release_date` all map to `None`.
**Why:** Rating, runtime, and year are metadata for v2 filters, not part of the embedding, so a movie without them is still useful for mood search.
**Revisit if:** Phase 3. Chroma metadata can't hold `None`, so we'll have to decide then whether to omit the key or use a sentinel value.

## D-008 · Phase 1 · OMDb client (`ingest/omdb.py`)
**Decision:** `OMDbClient.fetch(imdb_id)` caches raw responses in `data/cache/omdb/{imdb_id}.json`, **including "not found" results**. `parse_rating()` is separate and turns `"N/A"`, a missing rating, or `Response: "False"` into `None`. A 401 containing "limit" raises `OMDbLimitReached`; any other 401 raises `OMDbError`. Invalid JSON is treated as "not found". `_make_session`/`_get` are copied from `tmdb.py` for now.
**Why:** OMDb has several quirks:
- It returns HTTP 200 for errors.
- Ratings are strings.
- Its error text sometimes breaks its own JSON (unescaped quotes for malformed ids; seen live).
- The free tier is 1000 requests/day, and retrying a daily limit is pointless, so the run should stop. Caching negative results means missing movies don't burn quota on every re-run.
- Fetching and parsing are split so the parsing is testable offline.
**Revisit if:** a third HTTP client appears (then extract the session and error handling to `ingest/http.py`), or cached "not found" entries need a refresh (delete `data/cache/omdb/`).

## D-009 · Phase 1 · `scripts/fetch_movies.py` behavior
**Decision:** `--limit` flag (default 20). A movie whose TMDB call fails is skipped and counted. When the OMDb daily limit is hit, the run **continues without ratings** instead of aborting. `movies.json` is rebuilt from the caches on every run and written atomically (temp file, then rename).
**Why:** One bad movie shouldn't kill a 500-movie run. Because everything is cached, a re-run is cheap (1.7s vs 14s cold for 20 movies) and fills in missing ratings the next day. Atomic write means a crash never leaves a half-written JSON file for Phase 3 to choke on.
**Revisit if:** runs get long enough that we want progress saved mid-run (not needed while caches exist).

## D-010 · Phase 1 · Skip movies released in the last 90 days
**Decision:** `discover_ids(min_age_days=90)` adds `primary_release_date.lte = today - 90 days` to discover. Still sorted by `popularity.desc`. The cutoff is relative to today, so it moves forward on future runs.
**Why:** The first run had 12/20 movies from 2026. The 7 newest (11–81 days old) had few votes (500–800), and one had no IMDb rating. After the change: newest release is 2026-06-24 and 20/20 have a rating. 6 months was considered and rejected: it would also drop solid titles (Toy Story 5: 27 keywords; Obsession: 5.7k votes).
**Revisit if:** the index feels dated, or new releases matter for the product.

## D-011 · Phase 1 · Fixes from `/review-phase 1`
**Decision:**
- Cache writes go through `ingest/cache.py`: `write_json` is atomic (temp file, then rename). `read_json` deletes a corrupt file and returns `None` so the entry is refetched.
- `fetch_movies.py` saves the chosen ids to `data/cache/discover_ids.json` together with the **requested** `limit`, and reuses them. `--refresh` forces a new discover; a larger `--limit` than the saved one also rediscovers. (Comparing against `len(ids)` was a bug caught in review: after dedup, discover can return fewer ids than asked, which would rediscover on every run at scale.)
- The per-movie `except` also catches `KeyError` and `pydantic.ValidationError`. `to_movie` uses `x.get(k) or {}` so fields that are present but null don't crash, and TMDB `_get` turns non-JSON 200 responses into `TMDBError`.
**Why:** A crash mid-write used to leave a truncated cache file that crashed every later run. Discover results change daily (popularity, moving cutoff), so "re-run tomorrow to fill ratings" would have fetched a different list. One malformed TMDB record could still kill the run.
**Revisit if:** the list should follow current popularity. Use `--refresh`.

## D-012 · Phase 1 · Redact API keys from urllib3 logs (corrects D-006)
**Decision:** `ingest/http.py` adds a logging filter that rewrites `api_key=…`/`apikey=…` to `***` in the `urllib3.connectionpool` and `urllib3.util.retry` loggers. Both clients install it on init.
**Why:** D-006's claim that the key "can't leak into logs" was wrong. `_get()` hides the key in its own errors, but urllib3 logs its own WARNING with the full URL on every retry. This happened live during testing on 2026-09-27 (a connection reset → retry warning printed the TMDB key). The key went to terminal output only, not into any file or commit. **The TMDB key should be rotated.**
**Status:** TMDB key rotated by the user on 2026-09-27; the new key was verified with one uncached TMDB call.
**Revisit if:** we switch TMDB to the v4 bearer token (header instead of URL), which removes the key from URLs entirely.

## D-013 · Phase 2 · Semantic blob design (from `/grill-me`)
**Decision:**
- **Blob fields:** title, genres, top 3 cast (`Starring:`), overview, and **all** keywords, as labeled lines. Year, runtime, rating, and poster stay metadata only.
- **No nomic prefix in the blob.** A small `OllamaEmbeddings` wrapper in `core/vectorstore.py` (Phase 3) adds `search_document:` / `search_query:` automatically. The installed langchain-ollama 1.1.0 has no prefix option.
- **`core/semantic_text.py`:** pure functions `build_document_text(movie) -> str` and `to_metadata(movie) -> dict` (flat scalars, lists joined into strings, `None` keys **omitted**; resolves the D-007 follow-up).
- **Checkpoint:** print 3 blobs + `scripts/try_blobs.py`, which embeds all blobs in memory and prints the top 3 for a few mood queries.
- **Not yet:** a Themes/Tone keyword split, or LLM mood tags. Add them only if the ranking test shows noise or thin movies ranking badly.
**Why:**
- Only meaning-bearing text helps embeddings. Cast was added after the user's "romantic Shah Rukh Khan" example: shared names boost similarity at ~10 tokens of cost. Exact actor matching needs a v2 metadata filter, since embeddings are fuzzy on names.
- A keyword cap would drop the mood words, because TMDB lists them last (`feelgood`, `optimism`).
- A clean blob keeps the prefix out of Chroma's `page_content` and out of the Phase 4 LLM prompt.
- A placeholder like `-1` for a missing rating would break `rating >= 7` filters.
**Revisit if:** the ranking test fails for noisy or thin movies.

## D-014 · Phase 1 (revisit) · Include Indian films
**Decision:** a second discover pass with `with_original_language` in `hi`, `ta`, `te`, for **25%** of `limit` (parameter `indian_share=0.25`), with `min_votes=100` for that slice, mixed into the main list.
**Why:** The dataset was Hollywood-only (global popularity + en-US), so queries like "romantic Shah Rukh Khan" had nothing to match. South Indian films (RRR, Baahubali, Pushpa) are hugely popular. Indian films get fewer TMDB votes, so 300 would cut good films.
**Revisit if:** Indian overviews and keywords turn out too thin (then LLM mood tags, D-013), or the share needs tuning after the first run.
**Implemented:** `TMDBClient.discover_ids(indian_share=0.25, indian_languages=("hi","ta","te"), indian_min_votes=100)`. The Indian ids are spread evenly through the list (`_spread`), so any prefix keeps the ratio. `Movie` gained `original_language` for future language filters. First run: 3 Idiots, Leo, RRR, DDLJ, Maharaja. Their keyword counts are thin (6–7 for three of them).
**Note:** changing discover parameters requires `fetch_movies.py --refresh`, because the saved id list is keyed only by `limit`.

## D-015 · Phase 2 · Checkpoint accepted; LLM mood tags deferred
**Decision:** Phase 2 passes with known limits. No LLM mood tags yet; rerun `scripts/try_blobs.py` after scaling to 300–500 movies and decide then.
**Why:** 4 of 6 test queries were good, including the cast-driven SRK query. The two failures have different causes. Negation ("no sad ending" → Titanic) can't be fixed in the blob; it's the Phase 4 LLM re-rank's job. Thin keywords (Colony) are one data point out of 20 movies, too few to justify a per-movie LLM step.
**Revisit if:** at 500 movies, thin-keyword films still miss obvious mood queries.

## D-016 · Phase 3 · Vector store design (from `/grill-me`)
**Decision:**
- `NomicEmbeddings(OllamaEmbeddings)` subclass in `core/vectorstore.py` adds `search_document:` / `search_query:` prefixes. Its `embed_query` calls `super().embed_documents` **directly**, because the parent's `embed_query` calls `self.embed_documents` and would otherwise double-prefix the query (found by reading langchain-ollama 1.1.0's source).
- **Cosine** distance: `collection_configuration={"hnsw": {"space": "cosine"}}` (Chroma's default is l2).
- **Full rebuild** on every `build_index`: delete the collection, re-embed everything. Ids = `str(tmdb_id)`.
- `get_vectorstore()` (`@lru_cache`, shared by the API and UI) and `build_index(movies)` live in core; `scripts/build_index.py` is a thin CLI.
- At runtime, **Chroma finds** and **`movies.json` provides the full details** (`{tmdb_id: Movie}`). Deploy needs both files.
**Why:**
- A subclass is ~10 lines.
- Cosine distances map directly to `try_blobs.py` scores (distance = 1 − score).
- Upsert-only would leave stale movies (e.g. Forrest Gump after the Indian slice) that the API can't find details for. Sync would leave old blob formats behind.
- One place for store setup means the API and script can't drift apart.
**Verified:** all 6 test queries return the same top 3 as `try_blobs.py`, with distance = 1 − score (DDLJ 0.714 → 0.286). A re-run keeps count = 20.
**Known limit:** the `PLAN.md` checkpoint query "cozy feel-good" returns Interstellar, Practical Magic, Toy Story 5, all at ~0.54 distance: weak separation. The storage is correct; this is the Phase 2 mood-signal limit (D-015), left for the Phase 4 LLM re-rank and the 500-movie retest.

## D-017 · Phase 4 · Recommender design (from `/grill-me`) + first results
**Decision:**
- `core/recommender.py` › `recommend(query, k=5, candidates=15, rerank=True)`: Chroma top 15 → llama3.2 picks by **short number** `[1]..[15]` (not TMDB id) and writes "why".
- `ChatOllama(format=<JSON schema>, temperature=0)`. Schema requires at least one pick (`minItems: 1`).
- Validation: drop out-of-range numbers and duplicates, then fill empty slots in retrieval order with a template "why" (last 3 keywords, i.e. the mood words). Any LLM failure means pure retrieval order. Each result has `source: "llm" | "fallback"`.
- Retrieval errors (Ollama down) are not caught; Phase 5 maps them to a 503.
- Returns `Recommendation` objects (in `core/models.py`); details come from `core/catalog.py` › `get_catalog()` (`movies.json`).
- `rerank=False` fast mode (no LLM, <1s).
- Checkpoint: `scripts/try_recommend.py`, 5 queries + 1 Hinglish probe, retrieval vs re-ranked side by side.
**Why:**
- In a pre-test, llama3.2 mistyped a 7-digit TMDB id (1084242 for 1084244); short numbers avoid that.
- The schema forces the JSON structure; validation still guards the ids.
- temperature=0 makes runs reproducible.
- The fallback always returns k results.
- Fast mode is a backup if CPU-only EC2 is too slow.
**Found while building:** without `minItems`, llama3.2 returned `{"picks": []}` for every query (valid JSON, zero picks, so silent 100% fallback).
**First results (20 movies, ~5s per query on the Mac):** the plumbing works (all picks valid, every result has a why), but **quality is mixed; the 3B model's re-rank is not reliably better than retrieval:**
- ✅ Horror: Minions & Monsters removed, Colony surfaced. Hinglish "kuch halka sa, rona nahi chahiye" → 3 Idiots, DDLJ (good, but Colony at #4).
- ⚠️ "feel-good, no sad ending": Titanic moved from #3 to #4 but stayed in, and its why says "tragic twist, but ultimately feel-good".
- ❌ "romantic Shah Rukh Khan": Maharaja #1 over DDLJ, plus Interstellar and Blade Runner with an invented "romantic storyline".
- ❌ "funny animated for kids": 3 Idiots #1, Zootopia 2 dropped.
- Some "why"s just copy the overview (horror query) and aren't explanations.
- Template why can show non-English TMDB keywords (Maharaja: 因果报应).
**Status:** Phase 4 checkpoint **not passed on quality** yet. The next step is a design choice (see Open questions).

## D-018 · Phase 4 · LLM demotes misfits instead of re-ranking (replaces D-017's re-rank)
**Decision:** Final order = retrieval order. The LLM judges the top `2*k` candidates (`fits` + `why`), and the output is ordered in three tiers:
1. fits, plus unjudged movies inside the window (retrieval order)
2. judged misfits: `source="demoted"`, template why
3. candidates beyond the window

Nothing is removed, and the LLM never reorders freely.
**Why:** Three designs were tested on the same 6 queries:
- **Free re-rank (D-017)** made correct retrieval results worse (Maharaja over DDLJ for SRK; 3 Idiots over Toy Story for kids).
- **Hard veto** fixed the negation cases (Titanic out for "no sad ending", Minions out of horror), but the 3B model over-vetoed ("Zootopia 2 is not primarily a comedy"). The gaps were then filled from ranks 11–15, putting Colony, a zombie film, into the kids' results.
- **Demotion** keeps the wins and makes a wrong veto cheap.

Results with demotion:
- feel-good: DDLJ, Practical Magic, RRR; **Titanic 3→5**
- horror: Resident Evil, **Colony ↑**, **Minions 3→4**
- SRK: **DDLJ #1**
- kids: identical to retrieval
- sci-fi: identical to retrieval
- Hinglish: DDLJ, 3 Idiots, Practical Magic (better)

Latency is ~7–9s on the Mac.
**Known limits:** Titanic is still in the feel-good top 5 (at #5). Demoted movies show the neutral "Close match for your mood: …" template, which reads oddly for a demoted Titanic. The LLM's reasons for demotions are only logged.
**Revisit if:** the bigger-model test (open question) shows the LLM can be trusted with more (removal or reordering).

## D-019 · Phase 5 · FastAPI design (from `/grill-me`)
**Decision:**
- **503 via app-level exception handlers** in `api/main.py` for `ConnectionError` (Ollama down) and `ollama.ResponseError` (model missing), with the body `{"detail": "Recommendation engine unavailable, try again shortly"}`. `core/` stays framework-free.
- **Separate API schemas** in `api/schemas.py`:
  - `RecommendRequest {query: 3–300 chars, k: 1–10 = 5, rerank: bool = true}`
  - `RecommendResponse {query, results: [{tmdb_id, title, year, runtime, poster_url, imdb_rating, why}]}`
  - `MovieDetail` = all `Movie` fields + `poster_url`

  `distance` and `source` stay internal. Poster URLs use `https://image.tmdb.org/t/p/w500`.
- **`lifespan`:** load the catalog and Chroma at startup (a missing `movies.json`/`chroma_db` means the server won't start). Warm up Ollama in the background, non-fatal.
- **Sync `def` endpoints.** Core calls block, so `async def` would freeze the event loop.
- **`GET /health`:** 200 `{status: "ok", ollama: true, movies_indexed}` or 503 with `status: "degraded"`. It checks Ollama `/api/tags` (2s timeout) and that both models are present.
- **`GET /api/v1/movies/{id}`:** 404 if not in the catalog.
- **CORS** allowlist from the `CORS_ORIGINS` setting, default empty (never `*`).
- **Tests:** `pytest` + `TestClient`, with core mocked (no Ollama needed). Covers the happy path, 422, 503, 404, and degraded health.
**Why:**
- One handler can't be forgotten in a new route.
- The API contract is decoupled from core internals.
- Fail fast on missing data, but don't block on an optional warm-up (the first llama3.2 call took ~22s).
- Monitors read status codes.
- Keep the door open for an Angular client (`localhost:4200`) without opening the API to the world.
- API edge cases are hard to test by hand.
**Revisit if:** concurrent users pile up behind Ollama (Phase 7: rate limiting).
**Verified (real server):**
- `/health` → 200 ok.
- `/recommend` (SRK, k=2) → DDLJ + RRR with poster_urls: 10.2s, or 0.02s with `rerank:false`.
- `/movies/19404` → 200, `/movies/999` → 404, a short query → 422, `/docs` → 200.
- With `OLLAMA_BASE_URL` pointing nowhere: `/health` → 503 degraded, `/recommend` → 503, and `/movies` still 200.
- An empty Chroma dir makes startup fail with "run scripts/build_index.py".
- `pytest`: 13 tests pass in 0.6s.

---

## D-021 · Phase 6 · Example chips trimmed
- Removed the "feel-good, no sad ending" chip, and "romantic Shah Rukh Khan" became "Shah Rukh Khan". User's call, to keep the chips simple.
- The load screen shows no posters: `cards` starts with the empty-state text only, and there's no `ui.load` event.

---

## D-022 · Phase 6 · Honest "no match" (e.g. "Brad Pitt", with no Pitt films in the catalog)
- Problem: Chroma always returns the nearest k, however far away they are. The LLM demoted all 6, but they still showed, labelled "Close match for your mood".
- B (core): a demoted movie's why is now `DEMOTED_WHY` ("Nearest in our catalog, but it may not fit your request."), not the "Close match" template.
- A (UI): if every result is `source == "demoted"`, `search_final` adds the note "Nothing in our catalog really fits that…". The API is unchanged; clients can check `source` themselves.
- Placeholder and warning examples no longer say "no sad ending" (follows D-021).

---

## D-023 · Phase 6.5 · Lazy ingestion: grow the catalog on a miss (from `/grill-me`)
**Decision:**
- **Trigger:** only when step 2 demoted **every** result (the D-022 signal). Not a distance threshold (scores cluster), not every query (OMDb has 1000 requests/day).
- **Names:** llama3.2 with a `format=` JSON schema extracts `{person, title}`. A name is kept only if at least half its words appear in the query (difflib, so typos are OK). This guard exists because llama3.2 invented the title "A Light Hearted, No Crying Required" for "kuch halka sa, rona nahi chahiye".
- **Person:** `/search/person` → `/person/{id}/movie_credits`. Cast roles billed in the top `BLOB_CAST` (3) only, most-voted first. Brad Pitt in Deadpool 2 (billed 20th) is dropped because his name wouldn't be in the blob.
- **Title:** `/search/movie`, taking the most-voted of the top 5 results, plus 4 of `/movie/{id}/recommendations` sorted by vote_count. TMDB's own order was noisy: The Dark Tower, Paycheck, Push for Inception.
- **Filters:** votes ≥ 100, released 90+ days ago, not adult, overview present, ≥ 3 keywords, not already in the catalog. Max 5 new movies and 15 detail lookups per miss. If OMDb hits its limit, the movie is kept with rating `None`.
- **Storage:** Chroma first (`add_to_index`), then `movies.json` + the in-memory catalog (`add_to_catalog`), under one `threading.Lock`. If embedding fails, the catalog stays unchanged, so the query can be retried.
- **Repeat queries:** an in-memory `_tried` set (lowercased query) skips queries already handled. TMDB failures are not remembered.
- **UI:** a third step, `search_ingest` (same `"llm"` queue). Step 2 passes the all-demoted results through `gr.State`, and its note says "🔎 Checking TMDB for more…". Step 3 either shows the new results with "🆕 Added from TMDB: …", or falls back to the plain no-match note.
- **API unchanged:** lazy ingest is UI-only for now (Q11). `core.lazy_ingest()` is ready if the API wants it later.
**Verified (real):**
- "Brad Pitt" on the 20-movie catalog: step 2 took 11.5s (all demoted), step 3 took 15.2s and added Fight Club, Inglourious Basterds, Se7en, World War Z, Once Upon a Time… in Hollywood (25 in catalog and Chroma).
- Searching again: step 2 took 9.8s, with fits and no step 3.
- "Inception jaisi movie" (dry run) → Inception, The Matrix, The Matrix Reloaded, Oblivion.
- `pytest`: 35 pass.
**Known limits / open:**
- **Directors are skipped.** The blob has no director line, so Nolan's movies couldn't be found by name after ingesting. Fix option: add `directors` to `Movie` + a "Directed by:" blob line, then run `fetch_movies.py` (cached, cheap) and `build_index.py`.
- **`fetch_movies.py` rewrites `movies.json` from its discover list, which drops lazily-added movies.** It needs a merge before the next refetch.
- llama3.2 still demoted World War Z and Once Upon a Time… for "Brad Pitt" (see D-018).

---

## D-024 · Phase 6.5 · Directors, refetch merge, Nocturne UI, "Why this pick" panel
**Decision:**
- **Directors:** `Movie.directors` (from TMDB crew, `job == "Director"`), a "Directed by:" blob line, and `directors` metadata. Lazy ingest now handles directors too (the movies they directed, most-voted first). This resolves the D-023 open question. After `fetch_movies.py` (from cache) + `build_index.py`, "christopher nolan" → Interstellar is #1.
- **Refetch keeps lazy movies:** `add_to_catalog` records ids in `data/added_ids.json`, and `fetch_movies.py` appends them to the discover list. The 5 Brad Pitt ids were seeded by hand, and the refetch kept all 25.
- **UI = the user's Claude Design "Nocturne" mock** (`Movie Mood.dc.html`):
  - Dark theme (`#161826`, accent `#9184d9`, Inter).
  - Home: "Tonight / How are you feeling?" + the design's six chips (replaces the D-021 chips). The hero and chips hide once a search starts.
  - Suggestions: "Four films for “…”", numbered cards, meta "2009 · 171 min · IMDb 8.4". Phones get a list layout with the poster on the left.
  - `K = 4` (the design shows four), so the LLM judges 8 instead of 12 and is faster.
  - "Try another mood" and the logo are `href=''` (reload = back to Home).
- **"Why this pick" panel** (user's choice: open on card click). Each card is a `<details>`, so it needs no JavaScript. The panel shows:
  - **You asked for:** people from the query who are in the film ("Starring Brad Pitt", "Directed by Christopher Nolan"). Every word of the name must match (`name_in_query(..., 1)`); with half, "Christoph Waltz" matched "christopher nolan".
  - **Closest to your mood:** the film's genres/keywords nearest the query, using the same nomic embeddings. Top 3, cosine ≥ 0.42. Measured: real matches were 0.42+ (mind reading 0.61), noise 0.35–0.40 (silent film for "cozy"). Skipped when the query is only a name + filler ("Brad Pitt" → "sharon tate" was noise). Tag vectors are cached per process; this costs ~0.2–0.6s per search.
  - **How we checked:** from `source` (llm / fallback / demoted).
  - **The story:** the overview, clipped to 5 lines.
  - Logic lives in `core/explain.py`; the UI calls it after steps 2 and 3. The API is unchanged; the new `Recommendation` fields are internal.
- **Gradio gotcha:** Gradio prefixes selectors inside a custom-CSS `@media` block, so page-level rules (body, container padding) go in `head=HEAD` instead.
**Verified:**
- Chrome at 1456px and in a 390px iframe:
  - Home + Suggestions match the mock.
  - The Se7en panel shows rage and hate / grimdark / neo-noir.
  - "Brad Pitt dark" → Inglourious Basterds with "Starring Brad Pitt" + "dark comedy".
- `pytest`: 46 pass.
**Known limits:** The catalog is only 25 films, so "cozy and light" has few real fits and often gets no mood tags (honest, but sparse). The Gradio footer still shows.

---

## D-025 · Phase 6.5 · Catalog to 500 + mood-based lazy ingestion
**Decision (user: "dono karo, pehle B phir A, 500 kam se kam"):**
- **B, a bigger catalog:** `fetch_movies.py --limit 500`. `discover_ids` now uses `_discover_mix`, which asks each genre/language query for 1.5x more until there are `limit` unique ids (or every query runs out). This fixes the "dedup shortfall" open question: overlapping genres (a romcom is in both Comedy and Romance) used to leave fewer than `limit`.
- **A, lazy ingest for moods:** the llama3.2 intent schema now also has `genres` (an enum of TMDB's 18 genre names, so Ollama can only pick real ones) and `keywords` (1–3 TMDB-style tags).
  - When the query has no person/title: `/search/keyword` (exact name first) → `/discover/movie?with_keywords=k1|k2&with_genres=g1|g2`, most-voted first.
  - If there are fewer than 5 results, genres alone (`g1,g2` = AND).
  - The same filters and max 5 as before, and step 3 re-judges them with `recommend()`.
  - Named queries ignore the mood guess.
- **Title-that-is-a-mood retry:** llama3.2 read "a good cry" as the title "A Good Cry". The name guard can't catch this, because the words really are in the query. If a title-only intent finds nothing on TMDB, the query is read again with a "names nothing, fill only genres and keywords" hint.
**Dry runs (real Ollama + TMDB):**
- "a good cry" → Drama/Romance + tearjerker/grief → The Pianist, Manchester by the Sea, The Tree of Life, The Lion King (2019), Collateral Beauty.
- "kuch halka sa, rona nahi chahiye" → Comedy/Family + feel-good → Back to the Future, Monsters Inc., Amélie.
- "heist movie with a twist" → The Prestige, Ocean's Eleven, The Usual Suspects.
- "Brad Pitt" and "Inception jaisi" still resolve as names. "asdf qwerty" → None.
- **Title matches must be real:** every word of the asked-for title must be in the TMDB result's title. Before this, "A Good Cry" pulled Bad Grandpa / Tommy Boy + their recs, and only the keyword filter stopped them. Now it gives `[]`, and the mood retry runs. "Inception", "titanic", "the dark knight" still resolve.
- **`build_index` embeds in batches of 64:** at 500 docs, LangChain sent them all in one Ollama call, and the runner crashed ("POST /tokenize: EOF"). Note: `build_index` deletes the collection first, so a crash mid-run leaves an empty index; re-run it.
**Verified (real):**
- The fetch wrote 500 movies (0 failed, 496 rated, all 500 with directors). The main slice first came back 317 of 375 unique, and `_discover_mix` topped it up.
- The Brad Pitt ids were kept (they're in the new list anyway).
- The index has 500.
- `lazy_ingest("a good cry")` took 4s: title retry → mood → The Lion King (2019), The Pianist, Manchester by the Sea, Collateral Beauty, The Tree of Life. Chroma/JSON 505, `added_ids` 10.
- `pytest`: 53 pass.
**Known limits:**
- **Step 3 rarely fires on moods now.** At 500 films, the LLM usually calls at least one retrieved film a fit, so step 3 (all demoted) rarely runs for moods. "kuch halka sa, rona nahi chahiye" → Kuch Kuch Hota Hai (word overlap on "kuch") and Kal Ho Naa Ho (a tearjerker) passed the 3B judge.
- **Ingested films may not surface.** After ingesting for "a good cry", `recommend()` still showed Send Help / A Star Is Born first: the new films are in the catalog but not in the top 4 by embedding.
- These are retrieval/judge quality issues (see D-018's bigger-LLM question), not ingestion issues.
- Keyword mapping is the LLM's guess: "dark scary horror" pulled John Wick 2 (Horror|Thriller OR). Step 3's re-judge and demotion are the safety net.

---

## D-026 · Phase 6.5 · Bigger LLM test: qwen2.5:7b vs llama3.2 (D-018 open question)
**Setup:** `scripts/compare_llms.py`. Both models judge the **same 8 retrieved candidates** for 10 queries (moods, Hinglish, SRK, Brad Pitt), with the recommender's own prompt and schema, on the 505-film catalog. Hand labels cover the clear-cut cases only (55 verdicts). Machine: 16 GB Mac.
**Result:**

| | llama3.2 (3B) | qwen2.5:7b |
|---|---|---|
| Correct labeled verdicts | 34/55 (62%) | **47/55 (85%)** |
| Judge time per query | 8.2s | 15.6s (~1.9x) |
| Size | 2.0 GB | 4.7 GB |

- **llama3.2's errors are mostly false demotions:**
  - 5 of 8 SRK films ("genre does not match the request for a Shah Rukh Khan movie")
  - 4 of 8 Brad Pitt films
  - Ocean's Eleven / Now You See Me for "heist with a twist"
  - Insidious for "dark scary horror"
  - It also passed Kal Ho Naa Ho as "light-hearted" and A Quiet Place as "a good cry".
- **qwen2.5:7b** got every SRK / Brad Pitt / heist / horror / no-sad-ending label right. Its misses: "halka sa" (passed Kal Ho Naa Ho and K3G; demoted Koi… Mil Gaya and Hichki), and it is strict on kids/cozy (demoted Up, The Lorax).
- **"Why" style:** qwen writes shorter, flatter reasons ("Drama with Brad Pitt."). llama's are longer and more specific.
- **Intent extraction (lazy ingest):** qwen read "a good cry" as a mood on the first try (llama needed the D-025 retry), at 1.2–2.0s vs 0.6–1.0s. Both keep "SRK" as-is instead of expanding it.
**Decision (user):** `qwen2.5:7b` is the default in `config.py`. Switching is config only (`LLM_MODEL` in `.env`, or the default in `config.py`), because `recommender` and `lazy_ingest` both read `settings.llm_model`.
**Trade-off:** +13 correct verdicts (62% → 85%) vs 2x judge time locally. On EC2 (CPU only) that means ~16 GB RAM (t3.xlarge-class, not t3.large) and a slower step 2. Step 1 still shows results instantly, so the wait is on the "refining" step only.

---

## D-027 · Phase 6.5 · Hard filters in the query (IMDb rating, year, runtime)
**Bug (user):** "action movies with more 8.5 above imdb" returned The Equalizer 7.3, Fast X 5.7, V 6.8, Sholay 8.1, even though the catalog has 12 action films ≥ 8.5 (The Dark Knight 9.1, Gladiator, T2, The Matrix…).
**Root cause:** the rating is Chroma metadata, not blob text. Embeddings can't compare numbers, and the LLM judge sees only the blob, so nothing in the pipeline could apply "≥ 8.5".
**Decision:**
- `core/filters.py` parses rating / year / runtime from the query **with rules, not an LLM**: instant (step 1 has no LLM), deterministic, testable. `recommend()` passes `to_where(filters)` as Chroma's `filter`. Only the rest of the query ("action movies") is embedded and judged. This is the PLAN v2 "hybrid filters" item, pulled in early.
- **Understood:**
  - Rating: "8.5+", "above/over/more/at least 8", "rated 7 or more", "8 se upar rating", "imdb below 6".
  - Years: "after/since 2010", "before 2000", "between 2000 and 2010", decades "90s"/"1990s".
  - Runtime: "under 2 hours", "less than 90 min", "2 ghante se kam".
  - A number counts as a rating only next to a rating word (imdb/rating/rated/stars), so "top 10" is left alone.
- **Bare years are ignored:** "2012" is also a movie title.
- **Each phrase claims only its own words:** in "under 2 hours imdb 7+", "under" belongs to the runtime, and `+` always means "at least". This bug was caught in the real run ("IMDb up to 7").
- **Unfiltered queries pass through byte-for-byte**, and a filtered query keeps its casing and punctuation ("Sci-Fi").
- **Movies without a rating never match a rating bound:** keys are omitted rather than `-1` (D-013 pays off).
- **UI:**
  - A "Filtered: IMDb 8.5+" note.
  - A "Your filter" row in the why panel ("IMDb 8.5+ · this film: 2008 · 152 min · IMDb 9.1").
  - An explicit empty state ("No films in our catalog match IMDb 9.9+. Try loosening it.").
  - `explain()` matches mood tags on the filter-free text.
- The API gets the filters for free, because they are parsed inside `recommend()`.
**Verified (real, qwen2.5:7b):**
- "action movies with more 8.5 above imdb" → step 1: Fight Club, Gladiator, Pulp Fiction, The Dark Knight. Step 2: Gladiator 8.5, The Dark Knight 9.1, Star Wars 8.6, The Matrix 8.7.
- "romantic 90s movies" → Pretty Woman, Titanic, 10 Things I Hate About You, You've Got Mail.
- "comedy under 2 hours imdb 7+" → The Hangover 7.7, Shrek 7.9…
- `pytest`: 74 pass.
**Known limits:**
- English/Hinglish phrasings only; odd wordings fall through unfiltered (no harm, just no filter).
- No genre or language hard filter: "action" stays semantic.

---

## D-028 · Phase 7 · Deploy: Hetzner CAX31 + local qwen2.5:7b + DuckDNS (from the user's Q1–Q3)
**Decision:**
- **Host: Hetzner Cloud CAX31** (Arm64, 8 vCPU / 16 GB, ~€13/month, billed hourly).
  - Normal free tiers can't hold qwen2.5:7b + nomic + the app (~8 GB+): AWS t3.micro has 1 GB, Render 512 MB.
  - AWS t3.xlarge (the old PLAN target) is ~$120/month.
  - Oracle Always Free and HF Spaces (₹0, 2 vCPU, sleeps, loses runtime data) were offered; the user chose Hetzner. **Correction (2026-09-27, checked against Oracle's docs):** Always Free A1 is now **2 OCPU / 12 GB** (1,500 OCPU-hours + 9,000 GB-hours a month), not the 4 / 24 GB quoted earlier.
- **LLM: local qwen2.5:7b**, unchanged (D-026): no code change, no third party. Step 2 latency is to be measured on the box. The fallbacks are `LLM_MODEL=llama3.2`, or a later Groq mini-phase.
- **URL: a DuckDNS subdomain + Let's Encrypt** (certbot --nginx, auto-renew), ₹0.
- **Layout:** one VM running nginx (public, 80/443) → uvicorn on 127.0.0.1:8000 (**1 worker**: the Gradio queue and the lazy-ingest lock are per process) → Ollama on 127.0.0.1:11434 (`KEEP_ALIVE=24h`, `NUM_PARALLEL=1`). Hetzner firewall + ufw allow 22/80/443 only.
- **Reproducible installs:** `requirements.lock`, frozen from the dev venv, installed with `uv` on **Python 3.11**. Ubuntu 24.04 ships 3.12, and the unpinned `requirements.txt` would have pulled a different Gradio than the 6.28 the UI CSS was verified on.
- **Rate limits (nginx, per IP):** `/api/v1/recommend` 6/min (burst 3). `/gradio_api/queue/join` 30/min (burst 10), because each UI search joins the queue 3 times.
- **Data:** `movies.json`, `added_ids.json` and `data/cache/` are rsynced once, and the index is rebuilt on the box (Chroma files built on macOS aren't guaranteed portable). After go-live the server's catalog is the source of truth (lazy ingestion), so it is never overwritten from the laptop.
- **`setup.sh` is re-runnable:** it re-installs certbot's HTTPS block if a cert exists. Otherwise a re-run would have overwritten the site file and silently dropped HTTPS.
**Files:** `deploy/` (setup.sh, moviemood.service, ollama.override.conf, nginx-moviemood.conf, nginx-proxy-snippet.conf, README.md runbook), `requirements.lock`.
**Update 2026-09-27 (server creation):**
- **Hetzner charged $25 at verification.** Hetzner normally credits this to the account balance, which then pays future invoices (check Console → Billing). **Plan: use this $25 later** against the MovieMood server, so it isn't wasted.
- **The Cost-Optimized (Arm64 CAX) category was disabled** for this account at Falkenstein. The only 16 GB option in Regular Performance is CPX42 at ~$82/month, which is too expensive.
- The host decision is reopened (Oracle Always Free vs. a smaller Hetzner box); see HANDOFF.

**Not yet verified:** nginx config (`nginx -t` runs in setup.sh; local Docker was off) and step-2 latency on CAX31. Fill these in after the first deploy.

---

## D-029 · Phase 7 · UI fixes: invisible warning toast, short query stays on Home
- **Bug (user):** the `gr.Warning` toast (e.g. for "hi") showed white text on a near-white box (`rgb(250,250,250)`). Our theme's light text color applied, but Gradio kept the toast's own light background. Fix in `HEAD` CSS: `.toast-body` gets the Nocturne surface `#232532` with border `#3f424d` and text `#e9e9ed`. Warning titles are amber, error titles red. Checked with computed styles in Chrome.
- **Bug (found while testing):** a too-short query hid the hero and chips, leaving an empty page with no way back but a reload. `to_results` now only switches to the Suggestions screen for queries of `MIN_QUERY`+ characters.
- Testing note: in a background automation tab (`visibilityState: hidden`), Gradio's toast fade-in stalls at low opacity. Real, focused tabs aren't affected.

---

## D-030 · Phase 7 · Filter example chips on Home
- **Why (user):** MovieMood isn't mood-only; it also combines hard filters (D-027), so Home should show that.
- **What:** a second chip row under the mood chips, labeled "Or mix a mood with filters: rating, year, length". It uses dashed accent chips, so it reads as a different kind of example. `FILTER_EXAMPLES` has 5 entries:
  - "action movies with IMDb 8.5+"
  - "romantic comedy from the 90s"
  - "funny under 2 hours, IMDb 7+"
  - "sci-fi after 2010"
  - "Shah Rukh Khan, rated 7.5+"

  Each was checked to parse and to return films on the 505-film catalog. The row hides with the hero when results show.
- **Parser tweak:** "from"/"in" now belong to a year phrase, so "romantic comedy from the 90s" embeds as "romantic comedy" (it used to keep a stray "from").
- **Verified in Chrome:** the chip gives Gladiator 8.5, The Dark Knight 9.1, Star Wars 8.6, The Matrix 8.7 with "Filtered: IMDb 8.5+". `pytest`: 75 pass.

---

## D-031 · Phase 7 · Traffic numbers only, from nginx logs (no user data collection yet)
- **User:** collecting user data can wait. For now, just see how much traffic comes in.
- **Decision:** no analytics script, no cookies (so no consent banner), no app changes. The traffic comes from nginx's access log, which exists anyway.
  - `deploy/traffic.sh [days]` prints a per-day table: visitors (unique IPs, bots included), home page views, UI searches (successful `POST /gradio_api/queue/join` ÷ 3, because each search joins the queue for steps 1–3), API calls, and 429s. It reads rotated and gzipped logs too.
  - `traffic.sh --report` builds a **GoAccess** HTML dashboard with `--anonymize-ip --no-query-string`. It is copied to the laptop with `scp` and never served publicly. `setup.sh` installs `goaccess`.
- **Privacy:** query text is never logged (Gradio sends it in the request body, and nginx doesn't log bodies). IPs sit only in nginx logs, which Ubuntu's logrotate keeps ~14 days. That matters because the server is in the EU and IPs count as personal data.
- **Later (when needed):** anonymous in-app events (top queries, chip clicks, "Why this pick" opens) + a privacy note.
- **Verified:** `traffic.sh` on sample plain + gzipped logs gave the expected per-day counts.

---

## D-032 · Phase 7 · Host switched to Oracle Cloud Always Free (supersedes D-028's Hetzner choice)
- **Why:** on Hetzner, "Cost-Optimized" (ARM CAX) was disabled for the new account, and the only 16 GB AMD box was ~$82/month. The user then chose Oracle Always Free.
  - Oracle facts, checked on docs.oracle.com: A1 **2 OCPU / 12 GB**, 200 GB block storage, free for the account's life in the **home region**; the card is for verification only.
  - AWS Lambda/S3 and HF Spaces were discussed. Lambda needs a UI rewrite + Bedrock (a possible later Phase 8). HF now needs PRO for Gradio/Docker Spaces.
- **Kept:** local qwen2.5:7b (no code change), DuckDNS + Let's Encrypt, the same `deploy/` files. Memory budget: ~6 GB qwen + ~0.5 GB nomic + ~1 GB app of 12 GB, plus 4 GB swap from `setup.sh`.
- **Oracle-specific changes:**
  - The login user is `ubuntu`, so the README copies to `~` first, then `sudo mv`.
  - Oracle's Ubuntu image has its own iptables rules (SSH + REJECT, saved by netfilter-persistent). `setup.sh` detects them and inserts ACCEPT 80/443 above the first REJECT instead of enabling ufw (ufw on top of them is known to break). 80/443 must also be opened in the VCN security list.
- **Idle reclamation:** Oracle may reclaim a VM idle for 7 days (CPU **and** network **and** memory under 20%). qwen stays loaded (`OLLAMA_KEEP_ALIVE=24h`, ~50% of RAM), so it shouldn't count as idle.
- **Expected speed:** step 2 on 2 OCPUs will be slow (maybe 1–2 min; to be measured). Fallbacks: `LLM_MODEL=llama3.2`, or a hosted LLM later.
- **Hetzner:** no server was created. The **$25 paid at verification is kept for later use** (it should be account credit; confirm in Billing).

---

## D-033 · Phase 7 · Hetzner CPX22 + OpenAI LLM (embeddings stay local) + 10 films per search
**Why (user, 2026-09-28):**
- Oracle kept failing with "Out of capacity for shape VM.Standard.A1.Flex in AD-1". Mumbai/Hyderabad have one AD, so there's nowhere else to try.
- The user chose: "hetzner pe deploy karte hain, qwen use nai kar skte, we will use gpt".
- The user also asked for at least 10 films per search, not 4–5.

**Decision:**
- **Host: Hetzner CPX22** (2 vCPU AMD / 4 GB / 80 GB, ~$23/month; the $25 verification credit covers month one). Supersedes D-032's host.
  - Without a local LLM the box only needs nomic (~0.5 GB) + the app (~1 GB).
  - Login is `root`. The ufw branch of `setup.sh` + a Hetzner Cloud Firewall (22/80/443) handle the firewall.
- **LLM provider switch.** In `config.py`, `llm_provider: "ollama" | "openai"` (default `ollama`, so local dev is unchanged), plus `openai_api_key` and `openai_model` (default `gpt-4.1-mini`: cheap, fast, accepts `temperature=0`).
  - `LLM_PROVIDER=openai` without a key fails at startup.
  - The server `.env` sets `LLM_PROVIDER=openai`.
- **New `core/llm.py` › `structured_llm(schema, timeout)`:** messages in, a validated Pydantic object out, for either provider.
  - Ollama: exactly the old `ChatOllama(format=<schema>)` + `model_validate_json`.
  - OpenAI: `ChatOpenAI(...).with_structured_output(schema, method="function_calling")`. It isn't strict `json_schema`, because strict mode rejects our optional fields and `minItems`. Pydantic still validates.
  - `recommender._get_llm` and `lazy_ingest._get_llm` both use it, and their `except Exception` fallbacks are unchanged. An OpenAI error means retrieval order / no lazy ingest.
- **Embeddings stay on Ollama nomic** (user's choice): search stays free and the index is unchanged (no rebuild).
- **`/health`:** needs `embed_model` in Ollama, plus `llm_model` only when the provider is ollama. It never pings OpenAI (that would cost money on every monitor hit).
- **Warm-up:** embeddings always, the LLM only for ollama.
- **UI queue:** the "llm" queue (steps 2–3) allows 4 at once with OpenAI, 1 with Ollama (`LLM_CONCURRENCY`).
- **10 films:**
  - UI `K = 10` (grid of 5 columns: two rows).
  - `recommend(k=10)` by default, and retrieval fetches `max(2*k, candidates)`, so the LLM always judges a full window of 20.
  - API `k` default 10, max 20.
  - The "About 10 seconds" note became "a few seconds".
- **Deploy:** `setup.sh` pulls only nomic (a local LLM only with `PULL_LLM=qwen2.5:7b`). The README is Hetzner-first, with an OpenAI budget step, and Oracle is kept as "Other hosts".
- **Deps:** `langchain-openai` (lock: `langchain-openai 1.6.6`, `openai 3.19.2`, `tiktoken`, `jiter`, `regex`).

**Trade-offs:**
- Each search now costs a little (a fraction of a cent; capped by an OpenAI budget + the nginx rate limits).
- Queries leave the box (to OpenAI).
- qwen's 85% judge accuracy (D-026) has not been re-measured for GPT yet.

**Verified (local):**
- `pytest`: 83 pass (+ `tests/test_llm.py`, and health per provider).
- The real Ollama path with k=10: "feel-good, no sad ending" gives 10 results in 43.6s (qwen judging 20 on the Mac). 5 fit (Happy New Year, Puss in Boots 2, Inside Out, Coco, Anyone but You) and 5 were demoted (Smile 2, Manchester by the Sea, …). `extract_intent("Brad Pitt")` gives the person.

**Known limit:** with 10 slots, when fewer than 10 of the 20 judged films fit, the rest are shown as demoted ("may not fit"). Fix if needed: judge more (3*k), or drop demoted films from the grid.
**Verified on the server (2026-09-30, CX23 Helsinki, D-035), timed from the laptop in India over HTTPS:**
- Retrieval only (`rerank=false`): ~0.6s. With the OpenAI judge: **4.8–5.1s** ("cozy and light", "a good cry"); the first search after start took 8.3s.
- So step 2 on the server is ~4.5s, not the ~2s measured on the laptop. The time is OpenAI writing 20 verdicts + whys, not the box (load 0.7, 2.7 GB RAM free).
- Speed-ups if needed: a smaller model (`OPENAI_MODEL`), shorter whys, or judging fewer than 20.
- Results look right: Pride & Prejudice for "cozy and light", Fury for "Brad Pitt", Gladiator (8.5) for "action movies with IMDb 8.5+".
- nginx rate limit: 10 quick API calls → 2× 200, 8× 429.

---

## D-034 · Phase 7 · Results are drawn once, after the LLM (loader instead of a draft list)
**Problem:** step 1 showed 10 films in plain retrieval order, and ~2–10s later step 2 replaced the whole grid: misfits dropped, fits from places 11–20 jumped in, every "why" changed, and any open "Why this pick" panel snapped shut (the HTML is re-rendered). Users read it as a bug.
**Decision:** step 1 (`show_loader`) no longer calls `recommend()`. It clears the last results and shows the results heading + a spinner ("Reading your mood…"). Step 2 (`search_final`) draws the real grid once.
- The short-query warning stays in step 1.
- Step 2 on an engine error now renders the "unavailable" state instead of `gr.skip()`, which would leave the spinner forever.
- Step 3 (TMDB ingest) is unchanged: its swap is announced by the "Checking TMDB…" note.
- `search_fast` and the "Refining with AI…" note are gone. The spinner stops under `prefers-reduced-motion`.
**Why:** with OpenAI, step 2 takes ≤2s (user's measurement), so an honest short wait beats a draft that changes under the user. Considered: skeleton cards (overkill for 2s), keeping the draft order and updating only the "why" lines (fits from places 11–20 could never appear), a visibly "draft" list (still a swap).
**Trade-off:** with local Ollama (~10s+, more when queued) users watch a spinner, with no early films. Fine now that the server uses OpenAI (D-033).
**Verified:** `pytest` 84 pass (loader has no cards; engine error replaces the loader).

---

## D-035 · Phase 7 · Hetzner CX23 instead of CPX22 (same 4 GB, ~¼ the price)
**Context:** "Cost-Optimized" x86 became available on the account (it was disabled before, see HANDOFF). The CX23 has the same 2 vCPU / 4 GB as the CPX22, 40 GB disk instead of 80, on older shared hardware, for $6.49/month instead of ~$23.
**Decision:** CX23 in Helsinki (`89.167.119.84`), Ubuntu **26.04.1 LTS** (the console's default; the runbook said 24.04).
**Why:** since D-033 the box only embeds queries (nomic, ~0.5 GB) and runs Chroma + uvicorn. The LLM is OpenAI. 4 GB RAM was the sizing number, and it is unchanged. The disk needs ~10 GB (OS, venv, nomic, index, data, 4 GB swap).
**Trade-off:** `build_index.py` may take a few minutes longer, and the shared CPU can vary. Moving to CPX22 later is a Hetzner "Rescale" (same IP).
**Note:** `setup.sh` installs its own Python 3.11 (uv), so 26.04's system Python doesn't matter. The rest (nginx, certbot, ufw, Ollama) is checked during this deploy.
**26.04 fix:** `ufw` "Breaks" `netfilter-persistent` there, so apt refused the whole install (exit 100). `setup.sh` no longer installs `netfilter-persistent` by default; only the Oracle firewall branch installs it (if missing), and Oracle's image already has it.

---

## D-036 · v2 · Where to watch: TMDB watch providers, one region at a time
**Problem:** a suggestion said *what* to watch and *why*, never *where*. TMDB already has it
(`/movie/{id}/watch/providers`, JustWatch data), so this is a mapping job, not a new data source.
**Decision:** `core/availability.py` fills `Recommendation.where_to_watch` with the **subscription**
(`flatrate`) providers for one country, shown as one line on each card: `▶ Netflix, JioHotstar (India)`.
A `gr.Dropdown` picks the country (default `IN`); the API takes `region` (default `IN`) and returns
`where_to_watch`. No deep links to the services — provider names only, as text.
- **One region, picked by hand, not geo-detected.** No GeoIP on the box and nginx passes no country
  header; a dropdown is honest about what it's showing and works for a VPN user too.
- **flatrate only.** "Included with your subscription" answers "can I watch this tonight?". Rent/buy
  would raise coverage from 414/515 to ~all, but it's a different question. 402 of 495 films stream in
  India, so the line is useful as is.
- **One response holds every country (~130), so the cache keeps all of them.** Switching region is then
  a dict lookup: `region.change` re-renders the same films with no retrieval, no LLM and no network
  (`switch_region`). That's why the dropdown is cheap enough to exist at all.
- **Prebuilt cache, not live per search.** Raw responses are 59 KB/movie (31 MB for the catalog);
  trimmed to flatrate names it's **1.0 MB for 515 movies**, so `scripts/fetch_providers.py` writes
  `data/providers.json` (62s for the catalog) and a search adds 0 ms. A miss — a film lazy ingestion
  just added — is fetched live, capped at `MAX_LIVE=10` per search.
- **`FETCH_THREADS = 2`, measured not guessed.** 10 calls: 2.6s serial, **1.6s with 2 threads**, but
  7–15s with 4+ — TMDB resets the connections and `Retry`'s backoff (1s, 2s, 4s…) then dominates.
- **`where_to_watch is None` means "not looked up"**, `[]` means "looked up, not streaming here".
  Without that split, an API client that never asked would make every card claim "not available".
- **`clean_names` cleans the raw list**, which is noisy: reseller entries ("HBO Max Amazon Channel",
  and lowercase "ARD Plus Apple TV channel", hence `re.I`), ads tiers ("Netflix Standard with Ads"),
  then the first 3 by `display_priority`. **With a fallback:** The Imitation Game in the US has two
  providers and *both* are reseller channels, so filtering alone would have called a streaming film
  unavailable — if the filters empty a non-empty list, name the service behind the first one
  ("Britbox Apple TV channel" → "Britbox").
- **JustWatch credit** under the grid: TMDB requires it wherever this data is shown.
**Trade-off:** availability drifts as licences expire, so `providers.json` needs a weekly re-run
(`deploy/README.md`). TTL is 7 days, which only matters for films fetched live.
**Verified:** `pytest` 107 pass (14 new). Full catalog fetched: 515 entries, 1.0 MB, 414 stream in IN.
Region switch redraws instantly with no TMDB traffic in the log.

---

## Open questions
- ~~**Recency skew**~~: resolved by D-010.
- **Phase 2 ranking test (first run, 20 movies), `scripts/try_blobs.py`:**
  - ✅ "romantic Shah Rukh Khan" → DDLJ, clear winner (0.71 vs 0.61): the cast-in-blob decision works. "epic space adventure" → Interstellar; "funny animated for kids" → Toy Story 5 / Minions / Zootopia 2.
  - ❌ "feel-good, no sad ending" → **Titanic #1**. Embeddings don't understand negation ("no sad ending" ≈ "sad ending"). That's the Phase 4 LLM re-rank's job, not the blob's.
  - ❌ "dark scary horror" → Minions & Monsters #3 (word overlap on "Monsters"), and Colony (a real horror film, 3 keywords) is missing from the top 3. This is the first evidence that thin keywords hurt.
  - Scores are tightly clustered (0.49–0.50 for the feel-good top 3), so separation is weak.
- **Thin keywords:** not about age. Colony (3 keywords) and The Death of Robin Hood (5) survive the cutoff. Check blob quality in Phase 2; a possible fix is a minimum keyword count.
- ~~**Dedup shortfall at scale**~~: resolved by D-025 (`_discover_mix`).
- ~~**Bigger LLM (D-018)**~~: tested in D-026 (qwen2.5:7b 85% vs llama3.2 62% on labeled verdicts, ~2x slower). The user made qwen the default.
